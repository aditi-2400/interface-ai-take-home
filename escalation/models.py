"""Intervention records: what gets raised to a human, and the control-state
tracking CLAUDE.md asks for ("a simple state enum is sufficient: agent,
human, paused").

Who's "in control" at any moment, concretely:
- "agent": the automation is actively deciding/executing steps.
- "paused": the automation has stopped issuing actions on the page/context
  (on a hard_failure or a risky-action block) but has NOT closed it. Written
  the instant an InterventionRequest is created, before any human has shown
  up.
- "human": an operator has connected to the same live session (via
  escalation/operator.py) and is now acting on it directly.

The transition back to "agent" happens when the operator signals resume
(queue.mark_resolved) — replay/engine.py's wait loop picks that up, re-reads
whatever page state the human left behind, and continues or finalizes.
"""

from typing import Literal

from pydantic import BaseModel

ControlState = Literal["agent", "human", "paused"]


class InterventionRequest(BaseModel):
    intervention_id: str
    capability_id: str
    version: int
    step_index: int
    reason: str
    screenshot_path: str | None = None
    cdp_endpoint: str
    bound_inputs: dict[str, str]
    base_url: str
    run_dir: str
    created_at: str
    status: Literal["pending", "resolved", "expired"] = "pending"
    control_state: ControlState = "paused"
    resolved_at: str | None = None
    human_notes: str | None = None
