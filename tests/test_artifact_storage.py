import json
from pathlib import Path

import pytest

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


def test_save_then_load_round_trips(store, example_capability):
    path = store.save(example_capability)
    assert path.exists()
    loaded = store.load(example_capability.capability_id, example_capability.version)
    assert loaded == example_capability


def test_save_refuses_to_overwrite_existing_version(store, example_capability):
    store.save(example_capability)
    with pytest.raises(FileExistsError):
        store.save(example_capability)


def test_latest_version_tracks_multiple_versions(store, example_capability):
    store.save(example_capability)
    v2 = example_capability.model_copy(update={"version": 2})
    store.save(v2)
    assert store.latest_version(example_capability.capability_id) == 2
    assert store.load_latest(example_capability.capability_id) == v2


def test_latest_version_none_for_unknown_capability(store):
    assert store.latest_version("nonexistent") is None


def test_load_latest_raises_for_unknown_capability(store):
    with pytest.raises(FileNotFoundError):
        store.load_latest("nonexistent")


def test_list_capability_ids(store, example_capability):
    assert store.list_capability_ids() == []
    store.save(example_capability)
    assert store.list_capability_ids() == [example_capability.capability_id]


def test_list_latest_capabilities_returns_full_objects_at_latest_version(store, example_capability):
    store.save(example_capability)
    v2 = example_capability.model_copy(update={"version": 2})
    store.save(v2)

    other = example_capability.model_copy(update={"capability_id": "other_capability"})
    store.save(other)

    latest = store.list_latest_capabilities()
    assert {c.capability_id: c.version for c in latest} == {
        example_capability.capability_id: 2,
        "other_capability": 1,
    }


def test_list_latest_capabilities_empty_store(store):
    assert store.list_latest_capabilities() == []
