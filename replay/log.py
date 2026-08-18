"""Structured, saved log of a replay run — separate from the ReplayResult
returned to the caller, mirroring agent/transcript.py's split for discovery.
"""

from pydantic import BaseModel

from replay.result import ReplayResult


class ReplayStepLog(BaseModel):
    step_index: int
    action: str
    ok: bool
    error: str | None = None
    duration_seconds: float
    recovered_from_interstitial: bool = False


class ReplayRunLog(BaseModel):
    capability_id: str
    version: int
    inputs: dict
    base_url: str
    started_at: str
    finished_at: str | None = None
    steps: list[ReplayStepLog] = []
    result: ReplayResult | None = None
