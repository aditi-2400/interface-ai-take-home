"""Structured transcript logging for a discovery run.

Kept entirely separate from the eventual Capability artifact (per CLAUDE.md:
"a typed, versioned description... decoupled from the raw model transcript").
The transcript preserves every raw model response and observation for
audit/debugging; the artifact only keeps what a replay actually needs.
"""

from typing import Literal

from pydantic import BaseModel

from agent.action_schema import AgentAction
from agent.observe import Observation


class TranscriptStep(BaseModel):
    step_index: int
    observation: Observation
    raw_llm_response: str
    action: AgentAction
    execution_ok: bool
    execution_error: str | None = None
    duration_seconds: float


class Transcript(BaseModel):
    goal: str
    start_url: str
    model_used: str
    started_at: str
    finished_at: str | None = None
    steps: list[TranscriptStep] = []
    outcome: Literal["success", "stuck", "max_steps_exceeded", "error"] | None = None
    final_summary: str | None = None
