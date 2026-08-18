"""The replay result contract (CLAUDE.md's ReplayResult).

failure_detail is a typed model here rather than the plain dict CLAUDE.md's
skeleton shows — same JSON shape (step_index/expected/observed/
screenshot_path), just validated. A business_outcome and a hard_failure must
never be conflated: a business_outcome always carries outcome_code and never
failure_detail, and vice versa.
"""

from typing import Literal

from pydantic import BaseModel


class FailureDetail(BaseModel):
    step_index: int | None = None
    expected: str | None = None
    observed: str | None = None
    screenshot_path: str | None = None


class ReplayResult(BaseModel):
    status: Literal["success", "business_outcome", "hard_failure"]
    outputs: dict | None = None
    outcome_code: str | None = None
    failure_detail: FailureDetail | None = None
