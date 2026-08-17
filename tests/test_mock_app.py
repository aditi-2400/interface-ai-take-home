import pytest
from fastapi.testclient import TestClient

from mock_app import db
from mock_app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_bank.db")
    with TestClient(app) as c:
        yield c


def test_member_search_found_by_id(client):
    resp = client.get("/members/search", params={"query": "12345"})
    assert resp.status_code == 200
    assert "View Member" in resp.text


def test_member_search_found_by_name(client):
    resp = client.get("/members/search", params={"query": "Miguel"})
    assert resp.status_code == 200
    assert "View Member" in resp.text


def test_member_search_not_found(client):
    resp = client.get("/members/search", params={"query": "Nobody Here"})
    assert resp.status_code == 200
    assert "No members found" in resp.text


def test_member_detail_found(client):
    resp = client.get("/members/12345")
    assert resp.status_code == 200
    assert "Dana Whitfield" in resp.text
    assert "1001" in resp.text
    assert "1002" in resp.text


def test_member_detail_not_found(client):
    resp = client.get("/members/99999")
    assert resp.status_code == 404
    assert "Member Not Found" in resp.text


def test_deposit_flow_success(client):
    submit = client.post("/accounts/1001/deposit", data={"amount": "100.50"})
    assert submit.status_code == 200
    assert "Confirm Deposit" in submit.text
    assert 'value="10050"' in submit.text

    confirm = client.post("/accounts/1001/deposit/confirm", data={"amount_cents": "10050"})
    assert confirm.status_code == 200
    assert "Deposit Complete" in confirm.text
    assert "$2600.50" in confirm.text


@pytest.mark.parametrize("amount", ["-50", "0"])
def test_deposit_validation_error(client, amount):
    resp = client.post("/accounts/1001/deposit", data={"amount": amount})
    assert resp.status_code == 200
    assert "must be a positive dollar amount" in resp.text


def test_deposit_account_not_found(client):
    resp = client.get("/accounts/9999/deposit")
    assert resp.status_code == 404
    assert "Account Not Found" in resp.text


def test_transfer_flow_success(client):
    submit = client.post(
        "/accounts/1001/transfer", data={"to_account_id": "2001", "amount": "25.00"}
    )
    assert submit.status_code == 200
    assert "Confirm Transfer" in submit.text

    confirm = client.post(
        "/accounts/1001/transfer/confirm", data={"to_account_id": "2001", "amount_cents": "2500"}
    )
    assert confirm.status_code == 200
    assert "Transfer Complete" in confirm.text

    from_member = client.get("/members/12345")
    to_member = client.get("/members/67890")
    assert "$2475.00" in from_member.text
    assert "$175.00" in to_member.text


def test_transfer_insufficient_funds(client):
    resp = client.post(
        "/accounts/2001/transfer", data={"to_account_id": "1001", "amount": "99999"}
    )
    assert resp.status_code == 200
    assert "Insufficient funds" in resp.text


def test_transfer_destination_not_found(client):
    resp = client.post("/accounts/1001/transfer", data={"to_account_id": "9999", "amount": "50"})
    assert resp.status_code == 200
    assert "was not found" in resp.text


def test_sub_account_creation_flow(client):
    submit = client.post(
        "/members/67890/sub-accounts",
        data={"account_type": "savings", "initial_deposit": "500"},
    )
    assert submit.status_code == 200
    assert "Confirm New Sub-Account" in submit.text

    confirm = client.post(
        "/members/67890/sub-accounts/confirm",
        data={"account_type": "savings", "initial_deposit_cents": "50000"},
    )
    assert confirm.status_code == 200
    assert "Sub-Account Opened" in confirm.text

    member = client.get("/members/67890")
    assert "Savings" in member.text


def test_sub_account_invalid_deposit(client):
    resp = client.post(
        "/members/67890/sub-accounts",
        data={"account_type": "savings", "initial_deposit": "-10"},
    )
    assert resp.status_code == 200
    assert "must be zero or a positive" in resp.text


def test_simulate_perm_denied(client):
    resp = client.get("/members/12345", params={"simulate": "perm_denied"})
    assert resp.status_code == 200
    assert "Permission Denied" in resp.text


def test_simulate_timeout(client):
    resp = client.get("/members/12345", params={"simulate": "timeout"})
    assert resp.status_code == 200
    assert "Session Expired" in resp.text


def test_simulate_dialog_dismiss_resumes_flow(client):
    interrupted = client.post(
        "/accounts/1001/deposit", params={"simulate": "dialog"}, data={"amount": "42.00"}
    )
    assert interrupted.status_code == 200
    assert "Unexpected Confirmation Required" in interrupted.text
    assert 'action="/accounts/1001/deposit"' in interrupted.text
    assert 'value="42.00"' in interrupted.text

    resumed = client.post("/accounts/1001/deposit", data={"amount": "42.00"})
    assert resumed.status_code == 200
    assert "Confirm Deposit" in resumed.text
