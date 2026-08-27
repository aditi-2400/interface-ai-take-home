# Adaptation Project Write-Up: Pointing the Core at MERIDIAN CORE

*Status: in progress — being written alongside the build. Sections below reflect what's
actually done and verified as of this point; still-open pieces are marked `[TBD]`.*

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

A thin FastAPI layer (`api/`), one process: `GET /capabilities` (catalog — every saved
capability at its latest version, via a new `storage.list_latest_capabilities()` helper),
`GET /capabilities/{id}` (optionally `?version=`), `POST /capabilities/{id}/invoke` (a raw
`{"inputs": {...}}` body in, the real `ReplayResult` back out untouched — it's already a plain
Pydantic model, so there's no translation layer between what replay produces and what the API
returns). Escalation is deliberately off by default on this endpoint — a synchronous HTTP request
blocking indefinitely on a human isn't a reasonable API contract; the CLI/`escalation.operator`
path is still how a live handoff gets demonstrated.

**One contract, two live targets, verified in the same process:** each capability declares its
own `target_app`, and a small config table (`api/config.py`) maps that to a base URL and
(for MERIDIAN) a session file — the invoke endpoint doesn't otherwise know or care which target
it's talking to. Confirmed live: invoking `meridian_balance_inquiry` through the API returned a
real, checkable result (`member_name: "Turing, Alan"`), and invoking the *original* mock-app
capability `lookup_member_balance` through the exact same running API, in the same call session,
returned its own correct result (`savings_balance: "$5000.00"`) — one API, two genuinely
different live targets, no special-casing in the route handlers themselves.

`GET /runs` / `GET /runs/{id}` reads back exactly what replay already writes to
`evidence/replay/*/log.json` — no new persistence system, and it comes back already redacted
(confirmed: a run's logged output showed `[REDACTED_NAME]`, same as any other evidence file).

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
  — worth stating plainly rather than claiming this is fully solved.

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
  through the new API in Swagger rather than assuming JSON output was fine as-is.
  `meridian_funds_transfer` — five distinct outcomes: `source_share_on_hold`,
  `insufficient_funds`, `same_share_transfer`, `zero_amount`, `invalid_amount_format`; the
  success path was independently verified by checking the real before/after share balances, not
  just trusting the reported status.
- **A genuinely shared, stateful target.** MERIDIAN is used concurrently by other candidates,
  with no "release hold" function — holds only accumulate. Directly observed: a session cookie
  going idle-stale mid-build (`/signon?reason=timeout`), and a share pool where "which shares are
  currently open" shifts between one check and the next. Handled by treating this as a feature,
  not a flaw to route around: real errors get mapped as declared business outcomes rather than
  hardcoding one "known good" input set, so demo-day drift degrades to a clean, correct result
  instead of a crash.
- **`[TBD]`**: the injectable error taxonomy (`?inject=validation|notfound|permission|timeout|maintenance|server`)
  hasn't been explicitly exercised yet — the natural (non-injected) errors above were prioritized
  first since they're what the recorded capabilities actually hit in normal use.

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

## What was deliberately left out / cut, and what's next

`[TBD — to be finalized once the remaining build is done]`. Known so far:
- Open Share and Update Member Information capabilities: not yet recorded. Brief's stated
  minimum is balance-check + transfer, which are both done; these two are "go through the rest
  of the surface" bonus coverage, prioritized after Place Hold.
- The injectable error-injection taxonomy (`?inject=`) hasn't been exercised — natural errors
  were prioritized since the recorded capabilities hit several of those for real already.
- Chatbot/dashboard: not yet built.
