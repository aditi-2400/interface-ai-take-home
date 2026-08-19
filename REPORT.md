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
generalize to other records (added a substring-matching fallback); and — recorded honestly as
evidence, not hidden — the fresh discovery run captured for this report's evidence
(`transfer_funds` v4) contains a real, unfixed redundancy: the model re-typed the transfer
amount and re-clicked Continue before reaching the confirm page, which breaks replay outright
(the redundant `type` step's locator no longer exists once the flow has already advanced). It
was left as-is rather than hand-fixed — fabricating a clean recording would defeat the point of
using this as real evidence — and the earlier, human-reviewed `v3` is used for the replay/
escalation demonstrations instead. That gap is exactly what `approval_state="draft"` exists to
catch before a capability is trusted for reuse.

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

Two real bugs surfaced only by running this live end to end, not under test: the operator's
click-by-visible-text resolution silently matched a heading containing the target text instead
of the actual link (Playwright will click a non-interactive element without erroring), fixed by
scoping resolution to `role="link"`/`role="button"`; and a browser launched headed (the CLI's
original default) doesn't expose its page over the CDP debugging port to a second client at all
— only headless does — fixed by flipping the CLI's default. Also found empirically: launching
Chromium with a remote debugging port and then running any other task on the *same* asyncio
event loop stalls Playwright's own CDP communication indefinitely, which is why the operator
must run as a genuinely separate process — not just a test-isolation convenience, but the same
constraint a real external operator process would face.

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
