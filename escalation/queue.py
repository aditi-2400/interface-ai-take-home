"""Simple SQLite-backed intervention queue — no real message broker, per spec."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from escalation.models import ControlState, InterventionRequest

DB_PATH = Path(__file__).parent / "interventions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS interventions (
    intervention_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


@contextmanager
def _connection() -> Generator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
    finally:
        conn.close()


def create(request: InterventionRequest) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT INTO interventions (intervention_id, status, data) VALUES (?, ?, ?)",
            (request.intervention_id, request.status, request.model_dump_json()),
        )
        conn.commit()


def get(intervention_id: str) -> InterventionRequest | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT data FROM interventions WHERE intervention_id = ?", (intervention_id,)
        ).fetchone()
    return InterventionRequest.model_validate_json(row["data"]) if row else None


def list_pending() -> list[InterventionRequest]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT data FROM interventions WHERE status = 'pending' ORDER BY intervention_id"
        ).fetchall()
    return [InterventionRequest.model_validate_json(r["data"]) for r in rows]


def _update(request: InterventionRequest) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE interventions SET status = ?, data = ? WHERE intervention_id = ?",
            (request.status, request.model_dump_json(), request.intervention_id),
        )
        conn.commit()


def set_control_state(intervention_id: str, control_state: ControlState) -> InterventionRequest:
    request = get(intervention_id)
    if request is None:
        raise ValueError(f"no such intervention: {intervention_id!r}")
    request.control_state = control_state
    _update(request)
    return request


def mark_resolved(intervention_id: str, human_notes: str | None = None) -> InterventionRequest:
    request = get(intervention_id)
    if request is None:
        raise ValueError(f"no such intervention: {intervention_id!r}")
    request.status = "resolved"
    request.control_state = "agent"
    request.resolved_at = datetime.now(timezone.utc).isoformat()
    request.human_notes = human_notes
    _update(request)
    return request


def mark_expired(intervention_id: str) -> InterventionRequest:
    request = get(intervention_id)
    if request is None:
        raise ValueError(f"no such intervention: {intervention_id!r}")
    request.status = "expired"
    _update(request)
    return request
