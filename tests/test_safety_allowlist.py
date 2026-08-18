import pytest

from safety.allowlist import Allowlist, DEFAULT_ALLOWLIST_PATH


@pytest.fixture()
def allowlist():
    return Allowlist(
        allowed_domains=["127.0.0.1:8000"],
        allowed_route_patterns=[r"^/members(/.*)?$", r"^/accounts(/.*)?$"],
        allowed_action_types=["click", "type", "navigate"],
    )


def test_allows_covered_domain_route_and_action(allowlist):
    decision = allowlist.check("http://127.0.0.1:8000/members/12345", "click")
    assert decision.allowed is True


def test_blocks_disallowed_domain(allowlist):
    decision = allowlist.check("http://evil.example.com/members/12345", "click")
    assert decision.allowed is False
    assert "domain" in decision.reason


def test_blocks_disallowed_route(allowlist):
    decision = allowlist.check("http://127.0.0.1:8000/admin/danger", "click")
    assert decision.allowed is False
    assert "route" in decision.reason


def test_blocks_disallowed_action_type(allowlist):
    decision = allowlist.check("http://127.0.0.1:8000/members/12345", "extract")
    assert decision.allowed is False
    assert "action type" in decision.reason


def test_root_path_not_covered_by_this_fixture(allowlist):
    decision = allowlist.check("http://127.0.0.1:8000/", "click")
    assert decision.allowed is False


def test_real_allowlist_file_loads_and_covers_mock_app_routes():
    allowlist = Allowlist.load(DEFAULT_ALLOWLIST_PATH)
    for path, action in [
        ("http://127.0.0.1:8000/members/search", "navigate"),
        ("http://127.0.0.1:8000/members/12345", "click"),
        ("http://127.0.0.1:8000/accounts/1001/transfer", "type"),
        ("http://127.0.0.1:8000/accounts/1001/transfer/confirm", "click"),
    ]:
        decision = allowlist.check(path, action)
        assert decision.allowed is True, f"{path} {action}: {decision.reason}"


def test_real_allowlist_file_blocks_other_domains():
    allowlist = Allowlist.load(DEFAULT_ALLOWLIST_PATH)
    decision = allowlist.check("http://example.com/members/12345", "click")
    assert decision.allowed is False
