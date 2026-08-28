# Adaptation Project Write-Up: Pointing the Core at MERIDIAN CORE

## What adapting to this target actually took

The core loop (discover → convert → replay, with safety/evidence/escalation) survived intact —
`escalation/*.py`, `replay/checkpoint.py`, the locator fallback/retry logic, and the LLM
structured-output layer needed zero changes to point at a real, different, hosted target instead
of the local mock app. What actually needed real work:

- **Session persistence.** `replay_capability()` and `run_discovery()` both used to launch a
  fresh, throwaway browser per call. MERIDIAN's cookie-based login (`POST /signon` sets `MC_SID`)
  needed a session reusable across separate capability invocations. Added two optional,
  defaulted-to-`None` kwargs (`load_storage_state_from`/`save_storage_state_to`) to both
  functions — a Playwright `storage_state` file, not a long-lived shared browser process.
  Zero-risk choice: no existing caller or test needed to change.
- **A positional locator fallback.** MERIDIAN's form fields aren't linked to their labels via
  `<label>` or `aria-label` at all — confirmed live, their real accessible name is a genuine
  empty string, not just missing. A role+name locator can never resolve such a field. Added a
  positional strategy (`nth`) across the discovery schema, observation, executor, replay's
  `Locator` model, and conversion: an unlabeled field is found by its position among same-role
  elements, while a borrowed nearby-label string is still used for description/naming. This
  needed touching six files, but it's additive — every existing (named) locator path is
  unaffected.
- **Risk classification needed a real rule change, not just a keyword.** MERIDIAN's three
  irreversible actions each use their own wording for the final confirm button — "Post
  Transfer", "Open Share", "Apply Hold" — none of which matched the existing keyword list
  (`confirm`/`submit`/`delete`). This wasn't cosmetic: since replay gates a risky step on
  `approval_state`, an unflagged step meant a *draft, unreviewed* capability could execute a
  real transfer with no gate at all — confirmed this was genuinely happening on the first
  recorded (unfixed) version. Fixed with a structural rule instead of chasing more keywords: a
  click immediately after a "Continue" click is risky, whatever it's actually labeled — true
  across all three of MERIDIAN's flows, and more likely to generalize to whatever the remaining
  capabilities turn out to use.
- **Two live LLM bugs, unrelated to the target's markup but only surfaced by it.** Claude Sonnet
  5 was silently producing an extended-thinking block on every discovery call; on MERIDIAN's
  harder pages that thinking consumed the entire token budget before producing an answer.
  Fixed by explicitly disabling it. Separately, a conversion-time checkpoint-derivation helper
  (`_diff_new_text`) checked exact element-name equality against the prior page instead of
  substring containment — matching what checkpoint *evaluation* actually does at replay time —
  so a phrase already embedded in MERIDIAN's shared footer legend (`F5=Main Menu`, present on
  every page) could wrongly look like new, distinguishing text. Fixed to match replay's own
  substring semantics.
- **Config only, no code:** `safety/allowlist.yaml` (new domain + routes), `safety/redaction.py`
  (MERIDIAN's five real demo member names added to the known-names list).

Net: the load-bearing 70-80% of the system (schema, storage, safety enforcement mechanism,
escalation, discovery loop, checkpoint DSL) is completely unchanged. What changed is additive in
every case — new optional parameters, a new locator strategy, a broadened risk rule — not a
rewrite of anything that already worked.

## Capability API / task contract

A thin FastAPI layer (`api/`), one process, with these routes:

- `GET /capabilities` — catalog: every saved capability at its latest version, via a new
  `storage.list_latest_capabilities()` helper.
- `GET /capabilities/{id}` — a single capability (optionally `?version=`).
- `POST /capabilities/{id}/invoke` — a raw `{"inputs": {...}}` body in, the real `ReplayResult`
  back out untouched (it's already a plain Pydantic model, so there's no translation layer
  between what replay produces and what the API returns). Escalation is deliberately off by
  default here — not because the request would hang forever (a client can always give up on its
  own), but because nothing on the server side would know or care if it did: the handler keeps
  polling the intervention queue and holding the live session open regardless of whether the
  original connection is still there, so a client timing out just abandons the wait rather than
  cancelling it, and any later resolution has no one left to hand the result to. A request/reply
  API has no principled duration to pick for "wait for a person" — the CLI/`escalation.operator`
  path is still how a live handoff gets demonstrated, since there the waiting process and the
  human are the same party.
- `GET /runs` / `GET /runs/{id}` — reads back exactly what replay already writes to
  `evidence/replay/*/log.json`, no new persistence system, and it comes back already redacted
  (confirmed: a run's logged output showed `[REDACTED_NAME]`, same as any other evidence file).

**One contract, two live targets, verified in the same process:** each capability declares its
own `target_app`, and a small config table (`api/config.py`) maps that to a base URL and
(for MERIDIAN) a session file — the invoke endpoint doesn't otherwise know or care which target
it's talking to. Confirmed live: invoking `meridian_balance_inquiry` through the API returned a
real, checkable result (`member_name: "Turing, Alan"`), and invoking the *original* mock-app
capability `lookup_member_balance` through the exact same running API, in the same call session,
returned its own correct result (`savings_balance: "$5000.00"`) — one API, two genuinely
different live targets, no special-casing in the route handlers themselves.

**Dashboard.** Same Jinja2 pattern as `mock_app` and the chat page, three pages:

- `GET /dashboard` — capability catalog.
- `GET /dashboard/runs` — run history.
- `GET /dashboard/runs/{id}` — a run's detail: per-step results and a real screenshot link for
  failures, served read-only from the evidence directory via a static-file mount.

It reads exactly what the engine already writes,
computing nothing new, so safety/evidence guarantees (redaction, etc.) carry over automatically —
see the dashboard bullet under "safety, evidence, and escalation" below.

Real bug found via live testing (reported directly, not caught by the existing tests): run
history wasn't actually ordered by recency. It sorted by the run's directory name
(`{capability_id}_{timestamp}`), which groups by capability name alphabetically first and only
orders by time within each one — a run named `aaa_cap_...` from yesterday would sort before
`zzz_cap_...` from five minutes ago. Fixed to sort by each run's own logged `started_at`. Added a
test that would have caught this (two runs with names that sort one way alphabetically but the
opposite way in time) — the existing test only checked set membership, never order, which is
exactly why this slipped through.

**Chatbot.** One LLM call maps a free-text message to a capability + args (`decide_capability_choice`,
added next to discovery's `decide_next_action` — the two now share one provider-dispatch core in
`agent/llm.py` instead of duplicating the Ollama/Anthropic logic for a second Pydantic model), then
invokes it through `invoke_capability()` — the exact same function `POST /capabilities/{id}/invoke`
calls, not a second copy. The reply is a deterministic per-status template, not a second LLM call.

Two real things only showed up once this was actually exercised live, not from reading the code:
- **Anthropic's structured output rejects open-ended objects outright** — `inputs: dict[str, str]`
  failed with "additionalProperties: object is not supported... set to false" on the very first
  real call. Every object in a structured-output schema needs a fixed property set for Claude, no
  free-form dicts allowed at all. Fixed by making `inputs` a list of `{name, value}` pairs instead
  — a fixed shape that says the same thing, valid for both providers.
- **The catalog now has two near-duplicate-sounding capabilities pointed at two different real
  systems** (`lookup_member_balance` vs `meridian_balance_inquiry` — near-identical descriptions,
  neither mentioning which system it's for). First live test picked the wrong one for a MERIDIAN
  member number. Fixed by showing `target_app` in the catalog and telling the model explicitly not
  to just pick the first plausible match when more than one fits — confirmed live it now asks a
  clarifying question when genuinely ambiguous, rather than guessing wrong, and correctly combines
  a clarifying answer with the original request in the next turn instead of dropping it. Still
  genuinely non-deterministic which path it takes on the very first ambiguous turn (ask vs. guess)
  — worth stating plainly rather than claiming this is fully solved. Improved this further after
  live testing kept landing on the wrong capability for a bare member number with no other
  context: told the model the two systems' member-number formats are themselves a reliable
  signal (MERIDIAN's are 6 digits starting with 10; the mock app's look different, e.g. 12345 or
  67890) — retested the identical ambiguous message 5 times after this and it picked correctly
  all 5, versus guessing wrong before.
- **A second real chatbot bug, found the same way (live testing through the actual page, not a
  script): it silently truncated a share ID.** Given "Transfer $1 from 100234-MMKT-7 to
  100234-MMKT-9," the model extracted `from_share="MMKT-7"` — stripping the member-number prefix
  it had already captured separately, reasoning (wrongly) that repeating it looked redundant.
  The real dropdown option is the full compound string, so the transfer failed with a genuine
  Playwright timeout (`select_option` found no matching option) — traced from the actual saved
  run log, not guessed from the chat reply alone. Fixed with an explicit prompt rule: copy
  ID-like values exactly as given, even when part looks repetitive. Also tightened how a
  `hard_failure` renders in chat — it was dumping an entire page's raw text into the reply
  (confirmed live, an unredacted-looking wall of share data), now it's a short, capped snippet
  with the failing step number instead.
- **A third chatbot bug, and an honest correction of a first guess.** Asking it something
  unrelated to banking (e.g. "what's the weather") intermittently failed. First theory, from
  eyeballing the error text, was an invalid `\'` JSON escape - plausible-looking, and a real
  defensive fix either way, but re-tracing the *actual* raw model output byte-for-byte (not the
  error message text) showed that wasn't it: `clarification_needed` had reused
  `action_schema.MAX_FREE_TEXT_LENGTH` (150 chars) - the right size for a short UI element value,
  far too short for a real conversational reply sentence. A longer, more thorough clarifying
  question failed Pydantic validation outright; a shorter one happened to pass, which is exactly
  what made it look randomly intermittent instead of a fixed-length bug. Gave it its own,
  generous limit instead of reusing a constant meant for something else.

**A conversational front door, not just an endpoint.** The brief calls this out specifically as a
"minimal conversational front door," which a raw JSON `POST /chat` isn't — added `GET /chat`, a
plain server-rendered page (same Jinja2 pattern as `mock_app`, no build step, minimal JS) that
calls the same `POST /chat` via `fetch()` and renders the conversation. Verified in a real
browser via Playwright, not just checked as returned HTML text: sent a real message, watched a
real reply render, including a full capability result (`member_name: Lovelace, Ada`, matching
independently-verified data) — not just that the page loads.

## How this drives the legacy UI reliably, and how runtime/exceptional states are handled

- **The per-transaction hidden token** (`<input type="hidden" name="_token">`, present on every
  MERIDIAN form) needed no new engine code. Playwright drives a real rendered browser and clicks
  real submit buttons on real `<form>` elements — a native browser form submission includes every
  field, hidden ones included, automatically. (This looked like a problem during early manual
  `curl`-based reconnaissance, where *I*, not Playwright, had to manually re-collect and re-post
  the token — that concern doesn't apply to how the actual agent/replay path works, since it
  never constructs raw HTTP requests.)
- **Review→post two-step confirmation** (Transfer/Open Share/Hold: form → `/…/review` →
  confirm/post) needed no new pattern either — the original take-home's mock app already proved
  this exact two-step shape works end to end.
- **`<select>` dropdown interaction** (MERIDIAN's From/To Share pickers run up to 26 options for
  one member) was already fully supported by the existing schema/executor from the original
  build — no new code.
- **Real business-rule errors are mapped, not just injected ones.** Every outcome below was
  found live against the real target, not fabricated: `meridian_sign_on` — `invalid_credentials`
  (checkpoint corrected from the ambiguous auto-derived `text_contains:Main Menu` to
  `url_path_is:/menu`, since the original matched MERIDIAN's own footer legend present even on
  the failed-login page). `meridian_balance_inquiry` — `member_not_found`, plus a real output
  extraction (`member_name`) verified against actual member data, not just a passing checkpoint.
  Also added a `shares_summary` output after re-reading the brief's chatbot section, which
  explicitly expects "balances" in the structured result — a single balance number doesn't fit
  this target's data shape (a member can have 20+ shares). `OutputField` extraction only ever
  pulled one element's text before this; added a real `extract_all` mode (one string per matching
  element, not just the first) so this returns a genuine list — one entry per share row — instead
  of a single value or one unreadable blob. Found and fixed a real bug doing this: the log
  redaction step assumed every output was a plain string and crashed on the first list-valued one
  (`safety.redact` called directly on a `list`) — fixed to redact each list entry individually.
  Also cleaned up each row's own internal tab characters, found by actually looking at the result
  through the new API in Swagger rather than assuming JSON output was fine as-is. Checked the
  zero-shares edge case too: there's no way to create a new member on MERIDIAN (not one of the
  seven documented functions, confirmed by actually trying), so no real member with zero shares
  exists to test against — verified instead with a synthetic page reproducing the exact real
  markup structure. Confirmed `shares_summary` returns a clean empty list (`[]`), not an error:
  Playwright's `all_inner_texts()` returns an empty list on a zero-match locator by design, and
  the rest of the pipeline (extraction, redaction, JSON serialization) already handles a list
  with no assumption that it's non-empty.
  `meridian_funds_transfer` — five distinct outcomes: `source_share_on_hold`,
  `insufficient_funds`, `same_share_transfer`, `zero_amount`, `invalid_amount_format`; the
  success path was independently verified by checking the real before/after share balances, not
  just trusting the reported status. Also added `confirmation_number`/`from_share_new_balance`/
  `to_share_new_balance` outputs, useful for the chatbot to report something concrete rather than
  a bare "Done" — MERIDIAN's own post-transfer confirmation page already shows both updated
  balances, labeled by the share ID itself, so extraction uses the existing `{param}` locator
  substitution (already built for a different purpose, reused here for the first time) to target
  a row whose label is a runtime input value, not a fixed string. Verified twice: the math checks
  out against the balances before the call, and the live page afterward shows the identical
  numbers. Also tested the mirror case of `source_share_on_hold` — a transfer *into* a HOLD
  share — and confirmed it succeeds rather than getting blocked. Not a bug: the real business
  rule text says a HOLD share "cannot be debited," specifically about withdrawing from it, not
  crediting it, matching how a hold normally works in real banking (stop money leaving, not money
  arriving). No business outcome added for this, same reasoning as the "already on hold" finding
  below for Place Hold — there's no real rejection here to map.
- **A genuinely shared, stateful target.** MERIDIAN is used concurrently by other candidates,
  with no "release hold" function — holds only accumulate. Directly observed: a session cookie
  going idle-stale mid-build (`/signon?reason=timeout`), and a share pool where "which shares are
  currently open" shifts between one check and the next. Handled by treating this as a feature,
  not a flaw to route around: real errors get mapped as declared business outcomes rather than
  hardcoding one "known good" input set, so demo-day drift degrades to a clean, correct result
  instead of a crash.
- **Not yet exercised**: the injectable error taxonomy (`?inject=validation|notfound|permission|timeout|maintenance|server`)
  — the natural (non-injected) errors above were prioritized first since they're what the
  recorded capabilities actually hit in normal use. Listed again under "What was deliberately
  left out" below.
- **Checked `?inject=maintenance` specifically, and found the existing recovery mechanism
  wouldn't actually work here, not just that it's untested.** `replay/recovery.py`'s dismiss
  logic was built against the mock app's `?simulate=dialog` convention, whose dismiss link
  re-issues whatever request it intercepted — you end up back where you meant to go. Confirmed
  live: MERIDIAN's real maintenance interstitial's `"Continue"` link goes to a *fixed* URL
  (`/menu`), not back to the original target. So this isn't a one-line fix (recognizing
  `"Continue"` as an additional known dismiss link) — the engine would need to remember where it
  was actually headed before the interstitial intercepted it, then re-navigate there after
  dismissal, instead of blindly retrying the next step on whatever page `/menu` turns out to be.
  Documented rather than built under time pressure: a real, understood gap, not a superficial
  string-matching fix that would look done without actually recovering correctly.

## How safety, evidence, and escalation guarantees survive

- **Escalation subsystem is untouched.** `escalation/models.py`, `queue.py`, `operator.py`,
  `notify.py` — confirmed fully target-agnostic (no mock-app-specific text or URLs anywhere in
  those four files). The CDP-based handoff mechanism operates on "a live Playwright page,"
  which doesn't care what site is loaded.
- **The risk-gating bug above was found and fixed before it could matter for a demo** — see
  above. `meridian_funds_transfer`'s Post step is now correctly `risky: true`, confirmed live
  that a draft version of it blocks replay with an explicit message instead of executing.
- **Redaction** extended (not rebuilt) with MERIDIAN's real member names, same exact-match
  approach the original module already used and documented as a known limitation.
- **A full live escalation cycle, verified end to end on `meridian_place_hold`.** Recorded with
  a supervisor session (a teller can't reach a successful flow to record from — see below),
  producing a capability whose "Apply Hold" step is correctly `risky: true`. Replaying the still-
  draft version with escalation enabled genuinely paused before applying the hold; a human then
  reconnected to that *same* live browser via CDP, clicked the real button, and resumed — the
  paused runner picked this up and finished on its own, with the escalation ID recorded in the
  run's log. Independently confirmed the hold was actually applied afterward (not just a passing
  status). Nothing in `escalation/*.py` needed to change to make this work on the new target.
- **`meridian_place_hold` also gives two outcomes from one capability.** Submitting it as a
  regular teller doesn't prompt for a supervisor password inline — it's a hard block:
  *"SUPERVISOR OVERRIDE REQUIRED... a supervisor must sign on to complete this request."* This
  matches the brief's own example ("a teller attempting a supervisor-only Place Hold") exactly,
  mapped as a real business outcome (`supervisor_override_required`), independently confirmed the
  blocked attempt left the target share genuinely untouched.
- **Checked for an "already on hold" outcome and didn't fabricate one.** Tried placing a hold on
  a share already `HOLD`, end to end (review → post, as supervisor) — MERIDIAN doesn't reject it
  at all; it just succeeds again idempotently with a fresh confirmation number. No business
  outcome added for this, since there's no real rejection behavior to document.
- **The dashboard preserves these guarantees by construction, not by re-implementing them.** It
  only reads what the engine already writes (`log.json`, screenshots) and renders them — it
  computes nothing new. Confirmed live: a run's dashboard page shows the same `[REDACTED_NAME]`
  a raw `log.json` file would, and the run-detail page for a genuine past `hard_failure`
  (the "Continue-" hallucination bug found earlier in this project) renders its real screenshot
  correctly through a read-only static-file mount over the evidence directory.
- **A real gap, stated plainly rather than glossed over: none of the new API/chatbot/dashboard
  endpoints authenticate their own callers.** The existing safety layer (allowlist, risk-gating,
  redaction) governs what the *browser automation* is allowed to do on the target site — it says
  nothing about who's allowed to call *this* API at all. Right now the only thing limiting this
  is that the server binds to `127.0.0.1` (localhost only), which is an accident of the run
  command, not a designed boundary — it would disappear the moment this ran with `--host 0.0.0.0`
  or behind a reverse proxy. A sharper version of the same gap: the MERIDIAN session file
  (`evidence/sessions/meridian-core-live.json`) is one shared file, not one per caller. Signing in
  as a different operator or branch doesn't add a session alongside the existing one — it
  overwrites the same file, so only one MERIDIAN identity is ever active for the whole system at
  once. Confirmed live: signing in as `teller1` at `WEST-014` genuinely produced a session
  authenticated at that branch (checked the real signed-in footer, not just a success status),
  but every other capability call, from any caller, then also runs as that same operator and
  branch until someone signs in again as something else. Concretely: demoing a successful Place
  Hold (needs `super1`) followed by the permission-denied outcome (needs `teller1`) requires a
  fresh sign-on in between, or the second call just succeeds again as the still-signed-in
  supervisor instead of demonstrating the block. Deliberately left unaddressed here, consistent
  with the brief's own instruction not to build scaling/multi-tenant infrastructure — but unlike
  that instruction, this isn't just a scaling concern, it's a real access-control gap a
  production version would need to close first.
- **The same gap has a concurrent-access angle, not just a sequential one — and here the two
  halves of the answer are on different footing.** The chatbot's own conversation history is
  correctly isolated (`_HISTORY` is a plain dict keyed by `session_id`, confirmed by reading the
  code — two different chat sessions never see each other's turns). The underlying MERIDIAN
  identity is not: two genuinely simultaneous invocations, from any combination of callers, would
  both load the same cookie, drive the live target as the same operator at the same moment, and
  both race to write `storage_state` back to the same file afterward — whichever finishes last
  wins, silently. This part is reasoned from how the code works, not confirmed by an actual
  concurrent test — stated as such rather than presented as verified, unlike everything else in
  this document that's marked "confirmed live."
- **Considered, and deliberately rejected: letting the chatbot approve a capability.** It would
  have been easy to add — "approve X" as just another capability the dispatcher could call. Not
  built on purpose: `approval_state` exists specifically to mean "a human looked at the recorded
  steps and vouched for them" before anything irreversible runs unattended. If the chatbot could
  flip that switch on request, a draft capability could be approved and invoked in the same
  conversation, with no human ever having actually reviewed it — combined with the API-auth gap
  above, an unauthenticated caller could go from "unreviewed" to "executed" in one sitting. That
  would undo the exact safety property this project is careful to build correctly everywhere
  else. Kept the gate a deliberately separate, out-of-band step (`artifacts.approve`, run by a
  person, not the chatbot) instead.

## What was deliberately left out / cut, and what's next

All three required new layers (capability API, chatbot with a real front-door page, dashboard)
are built and verified live. What's left is genuinely optional, in priority order if more time
is available:
- **Open Share and Update Member Information capabilities** — not yet recorded. The brief's
  stated minimum is balance-check + transfer, both done, plus Place Hold and Sign On recorded
  beyond that; these two are "go through the rest of the surface" bonus coverage.
- **The injectable error-injection taxonomy** (`?inject=validation|notfound|permission|timeout|maintenance|server`)
  hasn't been exercised at all — every mapped business outcome so far is a *natural* error
  (wrong password, insufficient funds, a real HOLD share, a genuine permission block), not an
  injected one. Given how many natural errors this target already surfaces for real, this was a
  reasonable trade to make, but it's a real gap in taxonomy coverage worth being upfront about.
  `?inject=maintenance` specifically was checked (not just skipped) and found to need a real
  engine change, not a config tweak — see above.
- **Shares as typed per-field records, not just a list of strings.** Already solved: getting
  *every* share back at all (not just the first one) — `extract_all` mode, shipped and used by
  `meridian_balance_inquiry`'s `shares_summary`. What's still missing is the finer-grained version
  of that same idea: each share as a real `{share_id, type, balance, status}` record with
  separately typed fields, instead of one `" | "`-joined string per row that a caller would have
  to parse back apart. That would need a genuinely new output shape (a list of objects, not a
  list of strings) — a smaller, real remaining gap, not the whole feature being unbuilt.
- **Run history has no pagination** — with a few hundred runs accumulated from this build's own
  live testing, `/dashboard/runs` is a very long single page. Functionally correct (nothing is
  hidden or lost), just not something a longer-lived deployment would want as-is.
- **Chatbot session state is a plain in-memory dict** — fine for a single demo session, not
  something that would survive a server restart or scale to concurrent users. Explicitly out of
  scope per the brief's own "no scaling infrastructure" instruction.
