# CLAUDE.md — Computer-Use Automation System (interface.ai Take-Home)

You are building a take-home project for interface.ai. Full spec is in the attached PDF
(`Assignment_A___Computer-Use_Automation_System.pdf`) — read it fully before starting and
treat it as the source of truth if anything here is ambiguous or conflicting.

## What this project is, in one paragraph

Banks run legacy back-office software with no API. Build a system that (1) uses an LLM to
accomplish a natural-language goal by driving a real UI, (2) records the successful run as a
typed, reusable "capability" artifact, (3) replays that artifact later **without the LLM**,
deterministically, with proper error/outcome handling, and (4) escalates to a human — handing
off the *live* session — when it gets stuck. Everything runs against a small mock bank app you
build yourself (no real bank system, no public site scraping).

## Ground rules

- The discovery run MUST be real: an actual LLM call driving a real browser against a real
  running app. Do not fake, mock, or hand-write the "recording." Save evidence.
- Everywhere else, mocking/stubbing is fine if documented (e.g. the operator console UI).
- Don't build scaling infrastructure (queues, clusters, multi-tenant plumbing). Design for it,
  don't build it — that's a write-up section, not code.
- Keep secrets out of the repo. Use a `.env` file, gitignored, with a `.env.example` checked in.
- Prefer a thin-but-real version of every requirement over a polished subset. If you have to
  cut something, cut depth, not a whole capability, and document the cut in REPORT.md.
- Stop and ask me before making any decision that changes scope significantly (e.g. switching
  frameworks, dropping a core requirement, choosing a different LLM provider than agreed).

## Tech stack (fixed — do not deviate without asking)

- Python, FastAPI (mock bank app + orchestration/agent-facing endpoint)
- Jinja2 server-side templates for the mock app (NOT React/Vue — no client-side rendering,
  no JSON API on the mock app itself; see "why no API" reasoning in the PDF)
- SQLite for the mock app's data
- Playwright (Python, async) for browser automation
- Pydantic v2 for all schemas (artifact, steps, results)
- Gemma 3n (via Ollama, local) as the discovery-loop LLM, using a strict JSON structured
  output schema for actions — never free-text parsing of the model's decisions. Chosen over
  FunctionGemma because the discovery loop needs general multi-step reasoning over messy
  accessibility-tree observations, not just function-call formatting, and Gemma 3n is the more
  versatile model for that combined task.
  - Time-box the Gemma 3n attempt: give it a real, honest effort to complete the chosen flow
    (recommend: transfer) end to end. If it's still failing after a focused debugging session —
    hallucinated locators, lost multi-step state, looping near confirmation — fall back to a
    paid API model (Claude Sonnet or GPT-4o) for the discovery run specifically, and document
    the fallback decision and what was observed in REPORT.md. Do not let this eat the whole
    build; replay/schema/escalation are weighted more heavily than which model produced the
    recording.
- Artifacts stored as versioned JSON files on disk (no DB needed for artifacts)
- pytest for tests

## Build order — follow this sequence, don't jump ahead

Work through phases in order. After each phase, show me what you built and pause before
continuing if the phase involved a design decision not already pinned down in this doc.

### Phase 0 — Repo scaffold
- `/mock_app` — the FastAPI bank app
- `/agent` — discovery loop, LLM prompting, action schema
- `/artifacts` — artifact schema (Pydantic models) + storage
- `/replay` — deterministic replay engine
- `/escalation` — handoff/intervention mechanism
- `/safety` — allowlist, redaction, risk classification
- `/evidence` — output dir for logs, screenshots, saved artifacts (gitignored contents except
  the one committed demo example)
- `README.md`, `REPORT.md` (stub with the 7 required headings), `.env.example`, `.gitignore`

### Phase 1 — Mock bank app (`/mock_app`)
Build a small server-rendered app simulating a legacy bank back-office console. No JSON API
routes on this app — every route returns rendered HTML. See "Mock app spec" below for exact
routes, data model, and required "hostile markup" characteristics. Get this fully working and
manually click-testable in a browser before touching the agent.

### Phase 2 — Artifact schema (`/artifacts`)
Define the Pydantic models for a Capability artifact (see "Artifact schema" section below) before
writing the agent loop, since the agent's discovery output needs to conform to this from the
start. Include a JSON schema export and one hand-written example artifact for reference/testing.

### Phase 3 — Discovery agent loop (`/agent`)
- Input: natural-language goal + target app URL/entry point.
- Loop: observe (Playwright accessibility tree snapshot, not raw screenshots, as the primary
  signal — fall back to screenshot only if accessibility tree is insufficient) → LLM decides one
  action (strict JSON schema: action type, locator info, input value, reasoning) → execute via
  Playwright → repeat until goal met or stopping condition (max steps / timeout / dead-end).
- LLM: Gemma 3n via Ollama, local, prompted for strict JSON output matching the action schema
  (use Ollama's structured output / grammar-constrained generation if available, don't rely on
  the model to freelance valid JSON unaided). Keep the accessibility-tree observation trimmed
  to the relevant subtree where possible, since smaller models are more sensitive to long,
  noisy context than frontier models.
- If Gemma 3n cannot complete the flow after a genuine, time-boxed debugging effort, switch
  the discovery run to a paid API model and note this explicitly as a documented decision, not
  a silent swap.
- Every step's raw model reasoning + observation goes into a structured transcript log,
  separate from the eventual artifact.
- On success, convert the transcript into a Capability artifact (Phase 2 schema) — this
  conversion step is deterministic code, not another LLM call.
- Run this for real against the mock app for at least one full flow (recommend: transfer,
  since it has validation + two-step confirm) and save the evidence to `/evidence/discovery/`.

### Phase 4 — Deterministic replay engine (`/replay`)
- Input: a saved artifact + input parameters (typed, validated against the artifact's declared
  inputs).
- Executes the recorded steps via Playwright using the artifact's locator strategy — NO LLM
  calls in this path, ever.
- Locator strategy: try primary locator, fall back through the artifact's declared fallback
  chain, fail the step only if all are exhausted.
- After each step, check the step's checkpoint if declared. After the final step, check the
  capability's success_checkpoint.
- Return a three-way structured result — success / business_outcome / hard_failure — per the
  "Replay result contract" section below. Never conflate a legitimate business outcome (e.g.
  "member not found") with a crash.
- Produce evidence on every run: structured log + at least a screenshot on failure.

### Phase 5 — Error taxonomy + injected failures
- Add the error-injection hooks to the mock app (see spec below: `?simulate=timeout`,
  `?simulate=perm_denied`, `?simulate=dialog`, plus real business-rule errors like insufficient
  funds / member not found).
- Run replay against at least one scenario that hits a `business_outcome` and one that hits a
  `hard_failure` (or a recoverable condition that gets retried). Save both to
  `/evidence/replay/`.

### Phase 6 — Safety & guardrails (`/safety`)
- `allowlist.yaml`: permitted domains/routes + permitted action types. Both the discovery
  agent and the replay engine must consult this before every action; refuse and log if an
  action falls outside it.
- Per-action risk classification: mark irreversible/risky actions (submit, confirm, delete)
  distinctly. During discovery, require explicit confirmation before executing a risky action.
  During replay, gate risky actions on the artifact's `approval_state` (draft vs approved).
- Redaction: write a redaction utility (regex-based is fine) and run it on everything before it
  touches a log file or the artifact — account numbers, names, amounts if you decide those
  count as sensitive, credentials/tokens always. Keep raw LLM transcripts (if retained at all)
  separately from the artifact and redact them independently.

### Phase 7 — Escalation & handoff (`/escalation`)
- Launch Playwright's browser with a remote debugging port so the browser context can be
  reconnected to externally, rather than closing it on failure.
- On a hard_failure or a risky-action block, write an `InterventionRequest` record (goal,
  capability id, step index, reason, screenshot path, session connection info) to a simple
  queue (a JSON file or SQLite table is enough — no real message broker).
  Pausing means: the runner stops issuing actions on that page/context but does NOT close it.
- Build a minimal operator surface (a CLI script or one plain HTML page is fine — explicitly
  allowed to be mocked) that connects to the *same* browser session via the CDP endpoint,
  lets a person interact manually, and on "resume," signals back to the runner.
- On resume, the runner re-reads the current page state and either continues the remaining
  steps or finalizes the result, recording what the human did as part of the evidence.
- Document clearly, in code comments and REPORT.md, who is "in control" at any given moment
  and how that's tracked (a simple state enum is sufficient: `agent`, `human`, `paused`).

### Phase 8 — Optional stretch (only after Phase 0–7 are solid)
Pick at most one:
- Agent-invocable capability endpoint: a small FastAPI endpoint that lists artifacts as
  callable capabilities with typed args (derived from the artifact schema) and invokes replay
  on call. Show one real invocation end to end.
- Multi-run stability: replay the same artifact N times, report a flakiness signal.
Do not attempt both. Do not attempt canonicalization/cross-tenant unless everything else is
done with time to spare.

### Phase 9 — Write-up and evidence packaging
- Fill in `REPORT.md` using the exact 7 required headings from the PDF (Architecture, Artifact
  schema, Determinism & error handling, Heterogeneity & multi-tenant, Escalation & handoff,
  Safety, Cuts). ~1–3 pages total, not per section.
- Fill in `README.md`: setup instructions, required env vars, and the exact commands to (a)
  run the agent on a goal and (b) replay the resulting artifact.
- Confirm `/evidence/` contains: one saved example artifact, discovery run logs, and replay
  run logs including at least one error/exceptional-state replay.
- Do a final pass: remove dead code, make sure nothing in the repo has real secrets, make sure
  the demo commands in README actually work from a clean checkout.

## Mock app spec (build exactly this unless told otherwise)

Routes (all HTML responses, no JSON):
```
GET  /members/search?query=
GET  /members/{id}
GET  /accounts/{id}/deposit
POST /accounts/{id}/deposit
POST /accounts/{id}/deposit/confirm
GET  /accounts/{id}/transfer
POST /accounts/{id}/transfer
POST /accounts/{id}/transfer/confirm
GET  /members/{id}/sub-accounts/new
POST /members/{id}/sub-accounts
POST /members/{id}/sub-accounts/confirm
```

Data: members, each with one or more accounts and balances, in SQLite. Seed at least one
member with multiple accounts, one nonexistent member ID for not-found testing.

Business-rule errors (real validation logic, not fake):
- transfer amount > balance → insufficient_funds
- transfer to nonexistent account → account_not_found
- deposit negative/zero amount → validation_error
- search for nonexistent member → member_not_found

Injectable failure modes via query param, for deterministic replay-error testing:
- `?simulate=timeout` → artificial delay then a session-expired-style page
- `?simulate=perm_denied` → render a permission-denied interstitial instead of the requested page
- `?simulate=dialog` → render an unexpected confirmation interstitial mid-flow

Markup must be deliberately hostile:
- Nested `<table>` layouts, not divs/flexbox
- No `id` or `data-testid` attributes anywhere
- Generic reused class names (`class="row"`, `class="cell"`)
- Action controls as `<a href="#" onclick="...">` rather than semantic `<button>`
- Visible text / `aria-label` should be the only reliable way to identify an element — this is
  intentional and is what forces the locator strategy onto the accessibility tree

## Artifact schema (implement as Pydantic models)

```python
class Locator(BaseModel):
    strategy: Literal["role", "text", "css_fallback"]
    value: str
    fallback_strategies: list["Locator"] = []

class Step(BaseModel):
    action: Literal["click", "type", "navigate", "wait_for", "extract"]
    locator: Locator | None
    input_binding: str | None   # references an InputParam.name
    checkpoint: str | None      # assertion to verify after this step

class InputParam(BaseModel):
    name: str
    type: str
    required: bool
    description: str

class OutputField(BaseModel):
    name: str
    type: str
    extraction_locator: Locator

class Capability(BaseModel):
    capability_id: str
    version: int
    target_app: str
    description: str
    inputs: list[InputParam]
    steps: list[Step]
    success_checkpoint: str
    outputs: list[OutputField]
    risk_level: Literal["safe", "risky"]
    known_business_outcomes: dict[str, str]   # pattern -> outcome code
    approval_state: Literal["draft", "approved"] = "draft"
```

## Replay result contract (implement as Pydantic models)

```python
class ReplayResult(BaseModel):
    status: Literal["success", "business_outcome", "hard_failure"]
    outputs: dict | None = None
    outcome_code: str | None = None     # e.g. "member_not_found", "insufficient_funds"
    failure_detail: dict | None = None  # step_index, expected, observed, screenshot_path
```

Never return a bare boolean or raise an unstructured exception for an expected business
result — a "no such member" result must be indistinguishable, in terms of code cleanliness,
from a success case. Only genuine crashes / unrecoverable states should be `hard_failure`.

## Definition of done

The full thread must run end to end from a clean checkout:
1. `run agent` on a real goal against the mock app → completes it live, saves an artifact +
   discovery evidence
2. `run replay` on that saved artifact with new inputs → deterministic result, no LLM call
3. A second replay run that deliberately hits an injected error → correctly classified as
   business_outcome or hard_failure, with evidence saved
4. A forced escalation scenario → human takes control of the *same* live session, does
   something, hands control back, run resumes/finalizes
5. REPORT.md and README.md complete per the spec
6. No secrets in the repo, `.env.example` present, allowlist enforced, redaction demonstrated
