import pytest

from safety.risk import is_risky_action


@pytest.mark.parametrize(
    "name", ["Confirm Transfer", "confirm deposit", "Submit Application", "Delete Account"]
)
def test_click_on_risky_keyword_is_risky(name):
    assert is_risky_action("click", name) is True


@pytest.mark.parametrize("name", ["Continue", "Cancel", "View Member", "Search"])
def test_click_on_safe_target_is_not_risky(name):
    assert is_risky_action("click", name) is False


def test_non_click_actions_are_never_risky():
    assert is_risky_action("type", "Confirm Transfer") is False
    assert is_risky_action("navigate", "Confirm Transfer") is False
    assert is_risky_action("select", "Delete Account") is False


def test_missing_target_name_is_not_risky():
    assert is_risky_action("click", None) is False
    assert is_risky_action("click", "") is False
