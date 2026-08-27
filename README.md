# Computer-Use Automation System

A take-home project for interface.ai: a system that (1) uses an LLM to accomplish a
natural-language goal by driving a real UI, (2) records a successful run as a typed, reusable
"capability" artifact, (3) replays that artifact later without the LLM, deterministically, and
(4) escalates to a human — handing off the live session — when it gets stuck. Everything runs
against a small mock bank back-office app built for this project.

Status: Phases 0–8 complete (mock app, artifact schema, discovery agent, deterministic replay,
error taxonomy, safety guardrails, escalation & handoff, multi-run stability). See `REPORT.md`
for the full design write-up.

## Project structure

```
mock_app/     FastAPI + Jinja2 mock bank back-office app (server-rendered, no JSON API)
agent/        Discovery loop: observe -> LLM decides -> act, against a live browser
artifacts/    Capability artifact schema (Pydantic), on-disk storage, and approve.py (human review)
replay/       Deterministic replay engine (no LLM calls) + multi-run stability check
escalation/   Human-in-the-loop handoff: intervention requests + live session control transfer
safety/       Allowlist, redaction, risk classification
evidence/     Logs, screenshots, and saved artifacts from discovery/replay runs
api/          Capability API, chatbot, and dashboard - one FastAPI app (see "Adaptation project"
              below) fronting the same replay engine, for the live MERIDIAN CORE target
```

See the "Adaptation project" section near the end of this file for MERIDIAN CORE, the capability
API, the chatbot, and the dashboard specifically — everything above this point documents the
original take-home against the local mock app.

## Setup

Prerequisites: Python 3.13.

```bash
python3.13 -m venv .venv   # use python3.13 explicitly - a bare `python3` may resolve to
source .venv/bin/activate  # something much older (e.g. macOS's bundled Python) depending on
pip install -r requirements.txt   # what's first on PATH, and this codebase needs 3.10+ syntax.
playwright install chromium

cp .env.example .env   # defaults work as-is against the local mock app
```

Start the mock app (leave running in its own terminal):

```bash
uvicorn mock_app.main:app --host 127.0.0.1 --port 8000
```

Seed data: member `12345` (Dana Whitfield, checking + savings) and member `67890` (Miguel
Torres, checking + savings) — see `mock_app/db.py` for exact starting balances.

Everything below assumes the mock app is running at `http://127.0.0.1:8000` and is run from the
repo root with the venv active.

## Demo path, part 1: replay, escalation, stability (no LLM needed)

The repo ships three ready-to-use capabilities in `artifacts/store/` — `transfer_funds`
(approved, with business outcomes declared), `open_sub_account` (draft, so its risky step is
still blocked — needed for the escalation demo), and `lookup_member_balance` (safe/read-only).
None of what follows needs Ollama or any LLM call.

Every command below pins `--version` explicitly to these specific shipped versions. Omitting it
falls back to whatever's *latest* on disk (`artifacts/storage.py`'s default), which silently
becomes whatever you most recently recorded and approved yourself — e.g. approving a fresh
`open_sub_account` recording moves "latest" to that new, now-approved version, and the escalation
demo would stop triggering at all (nothing to block, since it's no longer draft) with no error to
explain why.

**1. Replay — deterministic, new inputs:**

```bash
python -m replay.engine --capability-id transfer_funds --version 3 \
  --input account_id=1001 --input destination_account_id=2001 \
  --input transfer_amount_in_dollars=5.00 \
  --base-url http://127.0.0.1:8000
```

**2. Replay hitting a real business-rule error** (just pass an amount over the account's
balance — no flags needed, this is genuine app validation, not a stub):

```bash
python -m replay.engine --capability-id transfer_funds --version 3 \
  --input account_id=1001 --input destination_account_id=2001 \
  --input transfer_amount_in_dollars=999999.00 \
  --base-url http://127.0.0.1:8000
# -> {"status": "business_outcome", "outcome_code": "insufficient_funds", ...}
```

**3. Escalation — a human takes control of the live session and hands it back.** In one
terminal:

```bash
python -m replay.engine --capability-id open_sub_account --version 1 \
  --input member_id=67890 --input new_account_type=savings --input initial_deposit_in_dollars=25 \
  --base-url http://127.0.0.1:8000 --enable-escalation --cdp-port 9222
```

No `--escalation-timeout` on purpose — it defaults to waiting indefinitely (confirmed live: passing
a value here means the runner gives up and closes the browser once it elapses, which is easy to
exceed while actually reading a screenshot and deciding what to do — a real `TargetClosedError`
on the operator side if that happens mid-decision, not a bug, just the timeout doing its job).

This pauses once it hits the blocked risky step (the artifact ships as `draft`, so this happens
every time) — a desktop notification fires at that point too (`escalation/notify.py`; also posts
to Slack if `SLACK_WEBHOOK_URL` is set). In a second terminal:

```bash
python -m escalation.operator list                 # find the pending intervention id
python -m escalation.operator take <intervention_id>
# at the operator> prompt:
#   show                 - see what the automation sees
#   click Confirm         - perform the real action, by role
#   resume some notes     - hand control back; the paused runner picks up and finishes
```

**4. Multi-run stability** (Phase 8 stretch — replays the same capability N times, reports a
flakiness signal; `lookup_member_balance` is read-only, so repeating it doesn't change any
account balance):

```bash
python -m replay.stability --capability-id lookup_member_balance --version 3 \
  --input search_by_name_or_member_id=12345 --base-url http://127.0.0.1:8000 --runs 5
```

Uses member `12345` (the seeded member with both a checking and a savings account) rather than
`67890`, which only has a checking account in the pristine seed data — `67890`'s savings account
seen elsewhere in this project's history only ever existed because of an earlier `open_sub_account`
demo run creating one, not because it's actually part of the seed.

## Demo path, part 2: record your own capability (needs Ollama, or Claude as a fallback)

```bash
ollama pull gemma4:e4b   # once
```

**5. Discovery — a real LLM driving a real browser, saves a new artifact:**

```bash
python -m agent.discovery \
  --goal "Transfer \$10.00 from account 1001 to account 2001" \
  --start-url "http://127.0.0.1:8000/accounts/1001/transfer" \
  --capability-id transfer_funds \
  --description "Transfer funds between two accounts, including the required confirmation step." \
  --headless --auto-confirm-risky
```

Saves a transcript, screenshots, and (on success) a new draft `Capability` version to
`artifacts/store/transfer_funds/` and evidence to `/evidence/discovery/` — a desktop notification
fires at that point too (`escalation/notify.py`'s `notify_approval_needed`), since a fresh draft
sitting unreviewed is exactly the kind of thing a human should be told about. Drop
`--auto-confirm-risky` to confirm the risky "Confirm Transfer" click interactively instead.
`known_business_outcomes` is deliberately left empty by conversion (a single run has no evidence
of what error copy looks like) — that, and moving the artifact out of `draft`, is a human
reviewer's job:

**Fallback to Claude instead of Gemma 4:** set `FALLBACK_LLM_PROVIDER=anthropic` and
`ANTHROPIC_API_KEY` in `.env`, then run the exact same discovery command above — no other flags
needed, `agent/llm.py` dispatches automatically. This is a real, documented decision (see
REPORT.md), not a hypothetical option: it's what this project actually fell back to after a
specific scenario proved genuinely unreliable on Gemma 4.

**6. Approve it** (the step that would otherwise be manual JSON editing):

```bash
python -m artifacts.approve --capability-id transfer_funds \
  --known-business-outcome "text_contains:Insufficient funds=insufficient_funds" \
  --known-business-outcome "text_contains:was not found=account_not_found"
```

`--output` works the same way, repeatable, for declaring a value to extract (e.g. a balance) —
`NAME|TYPE|LOCATOR_STRATEGY|ROLE_OR_DASH|EXTRACT_MODE|LOCATOR_VALUE`, where `EXTRACT_MODE` is
`-` for a single value or `all` for one string per matching element (e.g. one per table row), e.g.:

```bash
python -m artifacts.approve --capability-id lookup_member_balance --version 3 \
  --known-business-outcome "text_contains:No members found=member_not_found" \
  --output "savings_balance|decimal|css_fallback|-|-|xpath=//td[normalize-space(text())='Savings']/following-sibling::td[1]"
```

Pinned to `--version 3` for the same reason as part 1's commands: unlike the `transfer_funds`
example above (which chains off step 5's fresh discovery run), this one has no discovery step
in front of it — running it more than once locally moves "latest" to an already-approved
version, and the command would fail with "already approved" on a second run.

Replaying this new version (part 1's commands, pointed at whatever version this just created)
should now hit both business outcomes correctly and succeed cleanly on the risky confirm step.
Earlier in this project, Gemma 4 repeatedly recorded a redundant re-type-and-re-click on this
exact flow that broke its own replay a few steps later — real enough (observed independently
three separate times) that it wasn't just documented as a caveat: `agent/convert.py`'s dedup
pass was generalized to collapse a repeated multi-step block, not just a single repeated step,
which fixed it (see REPORT.md's "Determinism & error handling" section). `artifacts/store/
transfer_funds/v3.json` (what part 1's demos actually use) predates that fix and was reviewed by
hand instead — draft review existing precisely to catch this kind of thing is still the real
safety net here, fixed root cause or not.

## Tests

```bash
pytest                 # fast tests only (default; no live server/browser needed)
pytest -m live          # live tests: requires the mock app running on :8000
```

## Environment variables

See `.env.example` for the full list with comments. Copy it to `.env` and fill in values before
running anything that needs them — the checked-in defaults work as-is against the local mock app
and a local Ollama instance with `gemma4:e4b` pulled.

## Evidence

`/evidence/` ships one curated example of each required scenario (see `.gitignore` for exactly
which runs are committed; everything else there is real but untracked runtime output):

- `discovery/` — one real, live discovery run (`transfer_funds`)
- `replay/` — a clean success, a business_outcome (`insufficient_funds`), a hard_failure from an
  injected `?simulate=perm_denied`, a success that transparently recovered from an injected
  `?simulate=dialog` interstitial, and the escalation demo (a human resolving a blocked risky
  step on the live session)
- `stability/` — one multi-run stability report

## Adaptation project: MERIDIAN CORE, the capability API, chatbot, and dashboard

A second brief pointed this same core at a real, live, hosted legacy target —
`web-sample.interface-hiring.com` ("MERIDIAN CORE") — and required wrapping it in a capability
API, a chatbot, and a dashboard. See `ADAPTATION_REPORT.md` for the full write-up (what adapting
actually took, real bugs found doing it, what's deliberately left out). Everything below is
additional to the mock-app demo path above, not a replacement for it — both targets work through
the exact same `replay.engine`/capability API, and the capability catalog contains both.

Needs `FALLBACK_LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` set in `.env` for any live
discovery or chatbot use (Claude is the tested path here, same documented fallback decision as
the mock-app side — Ollama is wired the same way but untested against this specific target).

### Start the API (capability API + chatbot + dashboard, one process)

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8001 --reload
```
- Swagger / capability API: http://127.0.0.1:8001/docs
- Chat: http://127.0.0.1:8001/chat
- Dashboard: http://127.0.0.1:8001/dashboard

Only bound to `127.0.0.1` — see `ADAPTATION_REPORT.md`'s safety section for why that matters and
what it doesn't protect against.

### Sign in to MERIDIAN (needed before any other MERIDIAN capability)

```bash
python -m replay.engine --capability-id meridian_sign_on --version 3 \
  --base-url https://web-sample.interface-hiring.com \
  --input operator_id=teller1 --input password=password --input branch=MAIN-001 \
  --save-storage-state evidence/sessions/meridian-core-live.json
```

For a supervisor-only action (Place Hold succeeding, not just being blocked), sign in as
`super1`/`password` instead — same command, different `operator_id`. MERIDIAN times out on idle;
if a later call unexpectedly redirects to `/signon`, just re-run this.

### Replay MERIDIAN capabilities directly (no LLM)

MERIDIAN is a real, shared, stateful demo instance other candidates are also using — which shares
are currently `HOLD` and their exact IDs shift over time, so check the live member page (or run
`meridian_balance_inquiry` first and read its `shares_summary` output) rather than assuming a
fixed share ID still applies.

```bash
# Balance + full shares list for a real member
python -m replay.engine --capability-id meridian_balance_inquiry --version 4 \
  --base-url https://web-sample.interface-hiring.com --input value=100987 \
  --load-storage-state evidence/sessions/meridian-core-live.json \
  --save-storage-state evidence/sessions/meridian-core-live.json

# Transfer between two of a member's shares (fill in real, currently-open share IDs)
python -m replay.engine --capability-id meridian_funds_transfer --version 4 \
  --base-url https://web-sample.interface-hiring.com \
  --input member_id=100987 --input from_share=<real share id> --input to_share=<real share id> \
  --input value=1.00 --input memo=demo \
  --load-storage-state evidence/sessions/meridian-core-live.json \
  --save-storage-state evidence/sessions/meridian-core-live.json

# Place a hold - needs a super1 session (see sign-on above) to actually succeed
python -m replay.engine --capability-id meridian_place_hold --version 2 \
  --base-url https://web-sample.interface-hiring.com \
  --input member_id=100987 --input share=<real share id> --input reason_code=FRAUD --input notes=demo \
  --load-storage-state evidence/sessions/meridian-core-live.json \
  --save-storage-state evidence/sessions/meridian-core-live.json
```

### Escalation demo (CLI only — the chatbot/API can't reach this, see write-up for why)

Terminal A — `--version 1` is intentionally still `draft`, so its risky "Apply Hold" step blocks:

```bash
python -m replay.engine --capability-id meridian_place_hold --version 1 \
  --base-url https://web-sample.interface-hiring.com \
  --input member_id=100987 --input share=<real share id> --input reason_code=FRAUD --input notes=demo \
  --load-storage-state evidence/sessions/meridian-core-live.json \
  --enable-escalation --cdp-port 9222
```

Terminal B:

```bash
python -m escalation.operator list                 # find the pending intervention id
python -m escalation.operator take <intervention_id>
# at the operator> prompt: show / click Apply Hold / resume some notes
```

### Capability API

```bash
curl http://127.0.0.1:8001/capabilities
curl -X POST http://127.0.0.1:8001/capabilities/meridian_balance_inquiry/invoke \
  -H "Content-Type: application/json" -d '{"inputs": {"value": "100987"}}'
```

### Chatbot

Open http://127.0.0.1:8001/chat and type a request in plain English — e.g. "Look up member
100987's balance on MERIDIAN." Or call the same endpoint it uses directly:

```bash
curl -X POST http://127.0.0.1:8001/chat -H "Content-Type: application/json" \
  -d '{"message": "Look up member 100987 balance on MERIDIAN", "session_id": "demo"}'
```

### Dashboard

Open http://127.0.0.1:8001/dashboard for the capability catalog, `/dashboard/runs` for history
(optionally `?capability_id=...`), and click through to a run for its per-step results and a real
screenshot on failure.

### One shared session, one identity at a time

Every MERIDIAN call — CLI, API, or chatbot — reads/writes the same session file
(`evidence/sessions/meridian-core-live.json`). Signing in as a different operator or branch
overwrites it rather than adding a session alongside the existing one. Demoing Place Hold's
success (needs `super1`) followed by its permission-denied outcome (needs `teller1`) needs a
fresh sign-on in between, or the second call just succeeds again as the still-signed-in
supervisor. Full reasoning, and why this and the lack of API authentication are documented gaps
rather than built features, is in `ADAPTATION_REPORT.md`.
