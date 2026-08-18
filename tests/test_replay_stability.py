"""Aggregation logic tested against a stubbed replay_capability (fast, no
browser); one live smoke test exercises the real thing end to end.
"""

import pytest

import replay.engine
import replay.stability
from artifacts.models import Capability, Step
from replay.result import FailureDetail, ReplayResult
from replay.stability import run_stability_check


def _capability_stub() -> Capability:
    return Capability(
        capability_id="test_stability",
        version=1,
        target_app="x",
        description="test fixture",
        inputs=[],
        steps=[Step(action="navigate", value="/")],
        success_checkpoint="text_contains:x",
        outputs=[],
        risk_level="safe",
    )


@pytest.mark.asyncio
async def test_run_stability_check_all_success(monkeypatch):
    async def fake_replay(capability, raw_inputs, base_url, headless=True):
        return ReplayResult(status="success", outputs={})

    monkeypatch.setattr(replay.stability, "replay_capability", fake_replay)
    report = await run_stability_check(_capability_stub(), {}, "http://x", runs=3)

    assert report.runs == 3
    assert report.outcome_counts == {"success": 3}
    assert report.flaky is False
    assert [r.run_index for r in report.results] == [0, 1, 2]


@pytest.mark.asyncio
async def test_run_stability_check_flags_flaky_mixed_outcomes(monkeypatch):
    outcomes = iter(
        [
            ReplayResult(status="success", outputs={}),
            ReplayResult(status="hard_failure", failure_detail=FailureDetail(observed="boom")),
            ReplayResult(status="success", outputs={}),
        ]
    )

    async def fake_replay(capability, raw_inputs, base_url, headless=True):
        return next(outcomes)

    monkeypatch.setattr(replay.stability, "replay_capability", fake_replay)
    report = await run_stability_check(_capability_stub(), {}, "http://x", runs=3)

    assert report.flaky is True
    assert report.outcome_counts == {"success": 2, "hard_failure": 1}
    assert report.results[1].failure_reason == "boom"


@pytest.mark.live
@pytest.mark.asyncio
async def test_stability_live_smoke_lookup_member_balance(tmp_path, monkeypatch):
    from artifacts import storage

    monkeypatch.setattr(replay.stability, "EVIDENCE_ROOT", tmp_path / "stability")
    monkeypatch.setattr(replay.engine, "EVIDENCE_ROOT", tmp_path / "replay")

    capability = storage.load_latest("lookup_member_balance")
    report = await run_stability_check(
        capability,
        {"search_by_name_or_member_id": "67890"},
        "http://127.0.0.1:8000",
        runs=3,
    )

    assert report.runs == 3
    assert report.flaky is False
    assert report.outcome_counts.get("success") == 3

    saved_path = replay.stability._save_report(report)
    assert saved_path.exists()
