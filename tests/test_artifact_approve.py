import json
from pathlib import Path

import pytest

from artifacts import approve as approve_module
from artifacts import storage
from artifacts.models import Capability, Locator, OutputField

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


def test_approve_adds_output_field(store, example_capability):
    example_capability = example_capability.model_copy(update={"outputs": []})
    store.save(example_capability)
    field = OutputField(
        name="savings_balance",
        type="decimal",
        extraction_locator=Locator(
            strategy="css_fallback",
            value="xpath=//td[normalize-space(text())='Savings']/following-sibling::td[1]",
        ),
    )
    approved = approve_module.approve(example_capability.capability_id, outputs=[field])
    assert approved.outputs == [field]


def test_approve_output_merge_replaces_same_name(store, example_capability):
    old_field = OutputField(
        name="balance", type="decimal", extraction_locator=Locator(strategy="text", value="old")
    )
    example_capability = example_capability.model_copy(update={"outputs": [old_field]})
    store.save(example_capability)
    new_field = OutputField(
        name="balance", type="decimal", extraction_locator=Locator(strategy="text", value="new")
    )
    approved = approve_module.approve(example_capability.capability_id, outputs=[new_field])
    assert approved.outputs == [new_field]


def test_parse_output_field_cli_arg():
    field = approve_module._parse_output_field(
        "savings_balance|decimal|css_fallback|-|xpath=//td[normalize-space(text())='Savings']"
        "/following-sibling::td[1]"
    )
    assert field.name == "savings_balance"
    assert field.type == "decimal"
    assert field.extraction_locator.strategy == "css_fallback"
    assert field.extraction_locator.role is None
    assert field.extraction_locator.value == (
        "xpath=//td[normalize-space(text())='Savings']/following-sibling::td[1]"
    )


def test_parse_output_field_with_role():
    field = approve_module._parse_output_field("member_name|string|role|heading|Member Name")
    assert field.extraction_locator.role == "heading"
    assert field.extraction_locator.value == "Member Name"


def test_parse_output_field_requires_five_parts():
    with pytest.raises(Exception):
        approve_module._parse_output_field("too|few|parts")


def test_approve_can_amend_an_already_approved_capability(store, example_capability):
    store.save(example_capability)
    approved = approve_module.approve(example_capability.capability_id)
    assert approved.success_checkpoint != "url_path_is:/menu"

    amended = approve_module.approve(
        approved.capability_id, approved.version, success_checkpoint="url_path_is:/menu"
    )
    assert amended.version == approved.version + 1
    assert amended.success_checkpoint == "url_path_is:/menu"


def test_parse_success_checkpoint_cli_arg():
    assert approve_module._parse_success_checkpoint("url_path_is:/menu") == "url_path_is:/menu"


def test_parse_success_checkpoint_rejects_unknown_type():
    with pytest.raises(Exception):
        approve_module._parse_success_checkpoint("not_a_real_type:/menu")
