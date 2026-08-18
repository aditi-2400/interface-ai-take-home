import pytest

from escalation import queue
from escalation.models import InterventionRequest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", tmp_path / "interventions.db")


def _request(**overrides) -> InterventionRequest:
    defaults = dict(
        intervention_id="test-123",
        capability_id="transfer_funds",
        version=1,
        step_index=3,
        reason="locator not found",
        cdp_endpoint="http://localhost:9222",
        bound_inputs={"account_id": "1001"},
        base_url="http://127.0.0.1:8000",
        run_dir="/tmp/run",
        created_at="2026-08-18T00:00:00Z",
    )
    defaults.update(overrides)
    return InterventionRequest(**defaults)


def test_create_then_get_round_trips():
    req = _request()
    queue.create(req)
    loaded = queue.get("test-123")
    assert loaded == req


def test_get_unknown_returns_none():
    assert queue.get("nonexistent") is None


def test_list_pending_only_includes_pending():
    queue.create(_request(intervention_id="a"))
    queue.create(_request(intervention_id="b"))
    queue.mark_resolved("a")
    pending = queue.list_pending()
    assert [r.intervention_id for r in pending] == ["b"]


def test_mark_resolved_updates_status_and_control_state():
    queue.create(_request())
    resolved = queue.mark_resolved("test-123", human_notes="clicked confirm manually")
    assert resolved.status == "resolved"
    assert resolved.control_state == "agent"
    assert resolved.human_notes == "clicked confirm manually"
    assert resolved.resolved_at is not None


def test_mark_expired_updates_status_only():
    queue.create(_request())
    expired = queue.mark_expired("test-123")
    assert expired.status == "expired"
    assert expired.control_state == "paused"  # unchanged - no human ever took over


def test_set_control_state():
    queue.create(_request())
    updated = queue.set_control_state("test-123", "human")
    assert updated.control_state == "human"
    assert queue.get("test-123").control_state == "human"


def test_mark_resolved_unknown_raises():
    with pytest.raises(ValueError, match="no such intervention"):
        queue.mark_resolved("nonexistent")
