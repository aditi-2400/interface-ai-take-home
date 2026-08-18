from safety.redaction import redact, redact_value


def test_redacts_dollar_amounts():
    assert redact("Balance: $2,500.00") == "Balance: [REDACTED_AMOUNT]"


def test_redacts_id_like_numbers():
    assert redact("Member ID 12345") == "Member ID [REDACTED_ID]"


def test_redacts_known_seed_names():
    assert redact("View member 12345 (Dana Whitfield)") == (
        "View member [REDACTED_ID] ([REDACTED_NAME])"
    )
    assert redact("Miguel Torres has a checking account") == (
        "[REDACTED_NAME] has a checking account"
    )


def test_redacts_credentials_always():
    assert redact("password=hunter2") == "password=[REDACTED_CREDENTIAL]"
    assert redact("api_key: sk-abc123") == "api_key=[REDACTED_CREDENTIAL]"


def test_redacts_bearer_tokens():
    # Double-tagged (bearer pass + credential-keyword pass both fire on
    # "Authorization: Bearer ...") is fine — the point is the actual secret
    # (abc123.def456) never survives in the output.
    result = redact("Authorization: Bearer abc123.def456")
    assert "abc123.def456" not in result
    assert "REDACTED_CREDENTIAL" in result


def test_leaves_unrelated_text_alone():
    assert redact("Deposit Complete") == "Deposit Complete"
    assert redact("Confirm Transfer") == "Confirm Transfer"


def test_redact_empty_string():
    assert redact("") == ""
    assert redact(None) is None


def test_redact_value_recurses_through_dict_and_list():
    value = {
        "amount": "$100.00",
        "nested": {"member_id": "12345", "safe": "hello"},
        "list": ["$5.00", "plain text"],
        "number": 42,
    }
    result = redact_value(value)
    assert result["amount"] == "[REDACTED_AMOUNT]"
    assert result["nested"]["member_id"] == "[REDACTED_ID]"
    assert result["nested"]["safe"] == "hello"
    assert result["list"][0] == "[REDACTED_AMOUNT]"
    assert result["list"][1] == "plain text"
    assert result["number"] == 42
