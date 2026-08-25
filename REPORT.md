# Report

## 1. Architecture

The target is `mock_app/`: a FastAPI + Jinja2 back-office console with no JSON API and
deliberately hostile markup (nested tables, no `id`/`data-testid` anywhere, generic class
names, `<a onclick>` instead of `<button>`) — a stand-in for the legacy systems this project
exists to automate, where visible text/accessible name is the only reliable way to identify
anything.

Everything else is four independent stages around that target:

- **`agent/`** — discovery: observe (Playwright CDP `Accessibility.getFullAXTree`, not
  screenshots or `aria_snapshot()` — the mock app's nested tables make the latter extremely
  noisy, since ARIA row/cell names are computed from all descendant text) → decide (Gemma 4
  `gemma4:e4b` via Ollama, grammar-constrained to a strict JSON action schema) → act (Playwright)
  → repeat until the model reports done/stuck or `max_steps`. Every step's raw response and
  observation goes to a transcript, kept separate from the eventual artifact.
- **`artifacts/`** — the typed `Capability` schema (Pydantic) and on-disk, versioned JSON
  storage. `agent/convert.py` turns a successful transcript into a `Capability`
  deterministically — no LLM call in that path.
- **`replay/`** — executes a saved `Capability` via Playwright using its locator strategy, zero
  LLM calls, ever. Returns a three-way result (success / business_outcome / hard_failure).
- **`escalation/`** — on a stuck replay, pauses the live browser (never closes it) and hands the
  session to a human over CDP.
- **`safety/`** — allowlist, risk classification, redaction — consulted by discovery *and*
  replay, not just one or the other.

Data flow end to end: discovery run → redacted transcript on disk + a `draft` `Capability` →
human review/approval → replay engine invoked with new inputs, no LLM, risky steps gated on
`approval_state` → on failure, optional escalation hands the same live session to a human.

## 2. Artifact schema

`Capability`/`Step`/`Locator`/`InputParam`/`OutputField` implement CLAUDE.md's skeleton, plus
deliberate additions: `Locator.role` (role is required information for `get_by_role`, not
optional context); `Step.value`/`output_binding`/`risky`; a `"select"` action for `<select>`
dropdowns; and a small checkpoint expression DSL (`text_contains:`, `text_not_contains:`,
`url_path_is:`, `url_path_contains:`) used for `Step.checkpoint`, `success_checkpoint`, and
`known_business_outcomes` keys alike. Keeping every assertion in this DSL rather than free
English means replay can evaluate it with zero ambiguity, and a human reviewer can read a
capability and know exactly what "success" or a given business outcome means without touching
the live app. `Locator.fallback_strategies` is a recursive chain (role → text → css_fallback,
matching the target's reality: no ids anywhere, so accessible name/visible text is the only
robust signal); `css_fallback` values may use a Playwright `xpath=` prefix, since a plain CSS
`:has-text()` on this markup matches on full descendant text and silently returns an ancestor
wrapper instead of the intended cell.

Storage is one JSON file per version, never overwritten — `save()` refuses to clobber an
existing version, so a new recording bumps `version` instead. `approval_state` (draft/approved)
is separate from `risk_level`/`Step.risky` (set once, at conversion time, from what discovery
required a human to confirm live) — replay reads the already-classified `risky` flag and gates
on `approval_state`, never re-derives risk from a locator string on every run.

## 3. Determinism & error handling

`replay/engine.py` makes zero LLM calls. Every failure path checks `known_business_outcomes`
before anything is classified `hard_failure`, so a legitimate result like "member not found" is
never conflated with a crash — verified live (`insufficient_funds`, `member_not_found`, and an
injected `?simulate=perm_denied` hard_failure all correctly and distinctly classified; see
`/evidence/replay/`). Locator resolution tries the primary strategy, then each
`fallback_strategies` entry in order, and only fails once every candidate is exhausted. A known,
documented recoverable interstitial (`?simulate=dialog`) gets exactly one dismiss-and-retry
attempt per step, action-aware: a `navigate` step re-checks its checkpoint rather than
re-issuing the same `?simulate=dialog` navigation and looping back into the interstitial it just
dismissed.

Real bugs found by actually replaying recorded artifacts, not by inspection: a CSS `:has-text()`
selector matched an ancestor wrapper instead of the intended table cell on nested markup
(switched to XPath); a locator templated from one specific record's literal text didn't
generalize to other records (added a substring-matching fallback); and one found by actually
recording the same flow repeatedly — the fresh discovery run originally captured for this
report's evidence (`transfer_funds` v4) contained a real redundancy: the model re-typed the
transfer amount and re-clicked Continue before reaching the confirm page, breaking replay
outright (the redundant `type` step's field no longer exists once the flow has already advanced
past it). It recurred independently in two more separate discovery runs recorded while verifying
this project's own README instructions — consistent enough to read as a real characteristic of
how Gemma 4 handles this specific multi-field form, not a fluke, and not something to leave as a
documented caveat: `agent/convert.py`'s `_dedupe_consecutive_repeats` only ever collapsed a
single step repeated back-to-back, not a repeated multi-step *block* (type → click, type →
click), which is exactly this pattern. Generalized it to check decreasing window sizes for a
repeated contiguous block after each step, largest first, and re-ran discovery again to confirm:
the same goal now produces a clean 5-step artifact with no redundancy, and it replays
successfully end to end once approved. `v3` — the earlier, human-reviewed version — is still
what the replay/escalation demonstrations use, since it predates the fix and was already
verified working; `v4`'s uncorrected redundancy is kept as-is in evidence as the honest record of
what triggered the fix, not backfilled to look clean.

A second, unrelated model-fidelity bug, found while verifying a later feature (a "capability
needs approval" notification, `escalation/notify.py`'s `notify_approval_needed`) rather than by
looking for it: three discovery runs in a row failed on a goal that had worked reliably for
weeks. Not random — the model's own recorded reasoning correctly identified the target
("the visible elements show a 'Continue' link"), but the structured `locator.value` it actually
emitted was `"Continue-"`, a stray trailing character absent from the real page. Grammar-
constrained JSON output only guarantees valid JSON syntax; it never guarantees a string field
reproduces real content verbatim. Fixed at two layers rather than one, since a fix only in the
live path wouldn't help a saved artifact carrying the same bad value: `agent/executor.py` now
retries once with trailing punctuation stripped if the exact locator value times out during
discovery, and `agent/convert.py` now also appends a stripped-value fallback `Locator` to the
saved `Step`, so replay — with no LLM and no retry logic of its own — self-heals via the same
`fallback_strategies` chain mechanism described above. Verified live: the identical hallucination
recurred during testing (`"Confirm Transfer-"` this time, on a different step) and was correctly
recovered from at both layers.

A third finding surfaced only by re-running the full demo path against a genuinely fresh
database rather than the long-lived development one: the multi-run stability demo's chosen input
member (`67890`) has only a checking account in the real seed data (`mock_app/db.py`) — it
appeared to have a savings account solely because of accumulated state from earlier
`open_sub_account` demo runs. Not a code defect, but a real demo-reliability trap; fixed by
pointing that specific demo input at member `12345` instead, who genuinely has both accounts in
the seed.

Every run — success or failure — writes a structured log plus a screenshot on failure, redacted
independently and field-aware (not a blanket pass, which both mangles non-sensitive
infrastructure metadata and still misses values that don't match the redaction patterns' exact
shape) before touching disk.

## 4. Heterogeneity & multi-tenant

Not built — scaling infrastructure was explicitly out of scope per CLAUDE.md ("design for it,
don't build it"). What the design already assumes:

`Capability.target_app` is a symbolic app identifier, never a base URL — every `navigate`
`Step.value` is a relative path, so the same capability should replay against any tenant's
instance of the *same* back-office app given a separately-resolved `base_url`. A multi-tenant
deployment would resolve `base_url` (and a per-tenant `Allowlist`) from a tenant registry at
invocation time, keyed by tenant ID, rather than baking either into the artifact. Both
`replay_capability()` and `run_discovery()` already take `base_url` as a parameter rather than a
global, so nothing in the execution path needs to change to run against N tenants — only an
outer dispatch layer, a queue for concurrent invocations, and per-tenant credential/allowlist
management, none of which this project builds.

Heterogeneity *across different back-office apps* (not just different tenants of the same app)
is a harder problem this design doesn't attempt: a capability recorded against one vendor's
markup isn't portable to a different vendor's app for the same goal, even if the goal is
semantically identical ("look up a member's balance"). That would need a canonicalization layer
mapping goals to app-specific capabilities — deliberately out of scope per CLAUDE.md
("do not attempt canonicalization/cross-tenant unless everything else is done with time to
spare"). A production version would also need per-tenant artifact namespacing (if the same
`capability_id` could genuinely diverge across tenants of the same app due to tenant-specific
customization) and periodic UI-drift detection, since nothing here currently notices that an
approved artifact has gone stale until a replay run fails against it.

## 5. Escalation & handoff

Who's "in control" is tracked explicitly, as a three-value enum (`escalation/models.py`):
`agent` (automation deciding/executing), `paused` (automation has stopped issuing actions but
has *not* closed the page — written the instant an `InterventionRequest` is created, before any
human shows up), `human` (an operator has connected via CDP and is acting directly). The
transition back to `agent` happens when the operator calls `resume`; the paused runner's own
poll loop picks that up.

Mechanism: the replay browser is launched with `--remote-debugging-port` and never closed on a
hard_failure or a blocked risky step. `escalation/operator.py` — a minimal CLI, explicitly
allowed to be mocked per spec — connects to that exact same live session via
`chromium.connect_over_cdp()` from a genuinely separate OS process, lets a human drive it with
real Playwright actions (click/type/goto, resolved by role), and marks the intervention resolved
to signal resume. `InterventionRequest`s live in a small SQLite queue (no real message broker,
per spec). On resume, the runner re-reads current page state and either continues to the next
step or re-checks `success_checkpoint`, capturing what the human did via a post-resolution
screenshot and free-text notes. Allowlist violations are deliberately *not*
escalation-eligible — overriding an explicit security policy is the wrong response to one,
unlike being genuinely stuck.

One real bug surfaced only by running this live end to end, not under test: the operator's
click-by-visible-text resolution silently matched a heading containing the target text instead
of the actual link (Playwright will click a non-interactive element without erroring), fixed by
scoping resolution to `role="link"`/`role="button"`. Also found empirically: launching Chromium
with a remote debugging port and then running any other task on the *same* asyncio event loop
stalls Playwright's own CDP communication indefinitely, which is why the operator must run as a
genuinely separate process — not just a test-isolation convenience, but the same constraint a
real external operator process would face.

One earlier finding here was wrong and worth correcting rather than quietly dropping: a single
live test session concluded a headed browser couldn't expose its page over the CDP debugging
port to a second client at all, and the CLI's default was flipped to headless on that basis. A
later, controlled retest — a fresh headed launch on an unused port, checked via `/json/list`,
`lsof`, and a real `connect_over_cdp` reconnect — showed the opposite: headed Chromium exposes
its page exactly the same way headless does. The original observation was almost certainly a
port collision with a stale process from this project's own heavy, repeated browser-launching
across many earlier experiments on overlapping port numbers, not a genuine Playwright/Chromium
limitation. The CLI still defaults to headless (a reasonable default regardless — no display
required, marginally faster), but the help text and this write-up no longer claim headed mode is
incompatible with escalation, because it isn't.

The operator originally had no way to find out an intervention existed except polling
`escalation.operator list`. `escalation/notify.py` fixes that: the instant an intervention is
created, it fires a local desktop notification (macOS via `osascript`, a terminal bell
elsewhere — zero external setup, verified live) and, only if `SLACK_WEBHOOK_URL` is set, posts
the same alert to Slack — the same integration point a real on-call page would use. Both are
best-effort and run off the event loop (`asyncio.to_thread`, matching how discovery's own
blocking `input()` confirmation is handled) — a notification failure must never stop the paused
run from waiting.

The same mechanism covers a second, earlier moment something needs a human: a freshly discovered
capability sitting unreviewed in `draft`. `notify_approval_needed` (also in `escalation/notify.py`)
fires from `agent/discovery.py` the instant a successful run saves a new artifact version — same
desktop/Slack channels, different wording, no separate infrastructure. Verifying this one live is
what surfaced the two bugs described in §3 above.

## 6. Safety

`safety/allowlist.yaml` (domain + route pattern + action-type) is consulted before *every*
action by both discovery and replay; anything outside it is refused and logged, never silently
skipped. Risk classification (`is_risky_action`: a narrow `confirm`/`submit`/`delete` keyword
match on the target's accessible name) is computed once and used consistently in three places —
discovery blocks a live risky action on real operator confirmation (`input()`, not
auto-approved by default); conversion carries that exact classification into `Step.risky`;
replay never re-derives it, only reads it and gates on the artifact's `approval_state` — verified
live against the real `transfer_funds` artifact (blocked while `draft`, succeeds once approved).

Redaction (`safety/redaction.py`) is field-aware regex, run independently on the discovery
transcript and the replay log before either touches disk — dollar amounts, ID-like numbers, the
mock app's known seed-data names, and credentials/tokens/bearer strings always. Deliberately not
a general PII/NER engine: a generic "two capitalized words" name pattern would also redact
ordinary UI copy like "Member Search" or "Confirm Transfer," which is a worse trade than
under-redacting names the utility doesn't know about — documented as a known limitation, not
silently pretended away. This caught a real leak during development: saved artifacts had been
embedding actual account numbers into `InputParam` descriptions; the affected artifacts were
regenerated to purge them.

## 7. Cuts

- **Phase 8 stretch**: only multi-run stability (`replay/stability.py`) was built; the
  agent-invocable capability endpoint was not, chosen as the lower-risk addition this late in the
  build (no new FastAPI surface, no dynamic schema derivation, nothing that could regress
  already-tested and already-demoed code).
- **`outputs` / `known_business_outcomes`**: left empty by automatic transcript→artifact
  conversion, by design — a single happy-path discovery run has no evidence of what error copy
  looks like, and there's no reliable non-LLM way to distinguish "this new text is a return
  value" from "this new text is decoration." Both are meant to be filled in by a human reviewer
  before approval, which is exactly why a freshly converted artifact starts in `draft`.
- **No canonicalization / cross-tenant dispatch** — explicitly out of scope per spec (see
  §4).
- **No general PII/NER redaction** — regex-based and scoped to this project's own data shapes,
  documented as a known limitation rather than a hidden gap.
- **Fallback paid-API LLM path** — a documented, wired-for env-var placeholder
  (`FALLBACK_LLM_PROVIDER` / API keys in `.env.example`) that was never exercised in code, since
  Gemma 4 completed every discovery flow this project needed without it.
- **No UI-drift / artifact staleness detection** — an approved artifact is trusted until a
  replay run against it actually fails; nothing periodically re-validates a capability against
  the live app on its own.
- **No multi-tenant artifact namespacing** — a single implicit tenant (the one mock app
  instance) throughout; see §4 for what a real version would need.
- **The intervention queue itself is still SQLite, not a real message broker** — per spec
  (§5 above). What *is* built on top of it now: a real notification the instant an intervention
  is created (`escalation/notify.py` — desktop always, Slack if configured), so this cut is
  narrower than it originally was — see §5.
