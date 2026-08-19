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
artifacts/    Capability artifact schema (Pydantic) + on-disk storage
replay/       Deterministic replay engine (no LLM calls) + multi-run stability check
escalation/   Human-in-the-loop handoff: intervention requests + live session control transfer
safety/       Allowlist, redaction, risk classification
evidence/     Logs, screenshots, and saved artifacts from discovery/replay runs
```

## Setup

Prerequisites: Python 3.13, [Ollama](https://ollama.com) running locally.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env   # defaults work as-is against the local mock app + Ollama

ollama pull gemma4:e4b
```

Start the mock app (leave running in its own terminal):

```bash
uvicorn mock_app.main:app --host 127.0.0.1 --port 8000
```

Seed data: member `12345` (Dana Whitfield, checking + savings) and member `67890` (Miguel
Torres, checking + savings) — see `mock_app/db.py` for exact starting balances.

## Demo path

All commands assume the mock app is running at `http://127.0.0.1:8000` and are run from the
repo root with the venv active.

**1. Discovery — real LLM driving a real browser, saves an artifact:**

```bash
python -m agent.discovery \
  --goal "Transfer \$10.00 from account 1001 to account 2001" \
  --start-url "http://127.0.0.1:8000/accounts/1001/transfer" \
  --capability-id transfer_funds \
  --description "Transfer funds between two accounts, including the required confirmation step." \
  --headless --auto-confirm-risky
```

Saves a transcript, screenshots, and (on success) a new `Capability` version to
`artifacts/store/transfer_funds/` and evidence to `/evidence/discovery/`. Drop
`--auto-confirm-risky` to confirm the risky "Confirm Transfer" click interactively instead.

**2. Replay — deterministic, no LLM call, new inputs:**

```bash
python -m replay.engine --capability-id transfer_funds \
  --input account_id=1001 --input destination_account_id=2001 \
  --input transfer_amount_in_dollars=5.00 \
  --base-url http://127.0.0.1:8000
```

**3. Replay hitting a real error** (business rule, no flags needed — just pass an amount over
the account's balance):

```bash
python -m replay.engine --capability-id transfer_funds \
  --input account_id=1001 --input destination_account_id=2001 \
  --input transfer_amount_in_dollars=999999.00 \
  --base-url http://127.0.0.1:8000
# -> {"status": "business_outcome", "outcome_code": "insufficient_funds", ...}
```

**4. Escalation — a human takes control of the live session and hands it back.** Needs the
artifact's risky step to be blocked (default: `approval_state="draft"`). In one terminal:

```bash
python -m replay.engine --capability-id open_sub_account \
  --input member_id=67890 --input new_account_type=savings --input initial_deposit_in_dollars=25 \
  --base-url http://127.0.0.1:8000 --enable-escalation --cdp-port 9222 --escalation-timeout 120
```

This pauses once it hits the blocked risky step. In a second terminal:

```bash
python -m escalation.operator list                 # find the pending intervention id
python -m escalation.operator take <intervention_id>
# at the operator> prompt:
#   show                 - see what the automation sees
#   click Confirm         - perform the real action, by role
#   resume some notes     - hand control back; the paused runner picks up and finishes
```

**5. Multi-run stability** (Phase 8 stretch — replays the same capability N times, reports a
flakiness signal):

```bash
python -m replay.stability --capability-id lookup_member_balance \
  --input search_by_name_or_member_id=67890 --base-url http://127.0.0.1:8000 --runs 5
```

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
