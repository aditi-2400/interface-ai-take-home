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
```

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
  --base-url http://127.0.0.1:8000 --enable-escalation --cdp-port 9222 --escalation-timeout 120
```

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
