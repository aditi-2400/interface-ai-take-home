import json
from pathlib import Path

import pytest

from artifacts import approve as approve_module
from artifacts import storage
from artifacts.models import Capability

EXAMPLE_PATH = Path(__file__).parent.parent / "artifacts" / "examples" / "transfer_v1.json"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORE_DIR", tmp_path / "store")
    return storage


@pytest.fixture()
def example_capability() -> Capability:
    return Capability.model_validate(json.loads(EXAMPLE_PATH.read_text()))


def test_approve_saves_new_version_with_approved_state(store, example_capability):
    store.save(example_capability)
    approved = approve_module.approve(example_capability.capability_id, example_capability.version)

    assert approved.approval_state == "approved"
    assert approved.version == example_capability.version + 1
    assert store.load_latest(example_capability.capability_id) == approved

    original = store.load(example_capability.capability_id, example_capability.version)
    assert original.approval_state == "draft"


def test_approve_defaults_to_latest_version(store, example_capability):
    store.save(example_capability)
    approved = approve_module.approve(example_capability.capability_id)
    assert approved.version == example_capability.version + 1


def test_approve_unknown_capability_raises(store):
    with pytest.raises(ValueError, match="No saved capability"):
        approve_module.approve("nonexistent")


def test_approve_already_approved_raises(store, example_capability):
    store.save(example_capability)
    approved = approve_module.approve(example_capability.capability_id)
    with pytest.raises(ValueError, match="already approved"):
        approve_module.approve(approved.capability_id, approved.version)


def test_approve_merges_known_business_outcomes(store, example_capability):
    example_capability = example_capability.model_copy(update={"known_business_outcomes": {}})
    store.save(example_capability)
    approved = approve_module.approve(
        example_capability.capability_id,
        known_business_outcomes={"text_contains:Insufficient funds": "insufficient_funds"},
    )
    assert approved.known_business_outcomes == {
        "text_contains:Insufficient funds": "insufficient_funds"
    }


def test_parse_known_business_outcome_cli_arg():
    assert approve_module._parse_known_business_outcome(
        "text_contains:Insufficient funds=insufficient_funds"
    ) == ("text_contains:Insufficient funds", "insufficient_funds")


def test_parse_known_business_outcome_requires_equals():
    with pytest.raises(Exception):
        approve_module._parse_known_business_outcome("no-equals-sign-here")
