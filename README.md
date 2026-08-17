# Computer-Use Automation System

A take-home project for interface.ai: a system that (1) uses an LLM to accomplish a
natural-language goal by driving a real UI, (2) records a successful run as a typed, reusable
"capability" artifact, (3) replays that artifact later without the LLM, deterministically, and
(4) escalates to a human — handing off the live session — when it gets stuck. Everything runs
against a small mock bank back-office app built for this project.

Status: scaffold only (Phase 0). Setup instructions, environment variables, and the exact demo
commands below will be filled in as each phase lands.

## Project structure

```
mock_app/     FastAPI + Jinja2 mock bank back-office app (server-rendered, no JSON API)
agent/        Discovery loop: observe -> LLM decides -> act, against a live browser
artifacts/    Capability artifact schema (Pydantic) + on-disk storage
replay/       Deterministic replay engine (no LLM calls)
escalation/   Human-in-the-loop handoff: intervention requests + live session control transfer
safety/       Allowlist, redaction, risk classification
evidence/     Logs, screenshots, and saved artifacts from discovery/replay runs
```

## Setup

_TBD — filled in as each phase is built. Tech stack is pinned in `CLAUDE.md`: Python, FastAPI,
Jinja2, SQLite, Playwright (Python, async), Pydantic v2, Gemma 3n via Ollama (local), pytest._

## Demo path

_TBD — the exact command(s) to run the agent on a goal, then replay the resulting artifact,
will be added once Phases 1–4 are complete._

## Environment variables

See `.env.example` for the full list with comments. Copy it to `.env` and fill in values before
running anything that needs them.
