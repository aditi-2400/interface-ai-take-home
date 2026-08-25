import pytest

from mock_app.money import cents_to_dollars, parse_dollar_amount


@pytest.mark.parametrize(
    "cents,expected",
    [
        (12345, "$123.45"),
        (0, "$0.00"),
        (-500, "-$5.00"),
        (105, "$1.05"),
        (10_000_00, "$10000.00"),  # documents actual behavior: no thousands separator
    ],
)
def test_cents_to_dollars(cents, expected):
    assert cents_to_dollars(cents) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("25.00", 2500),
        ("$25.00", 2500),
        ("$1,234.56", 123456),
        ("  25.00  ", 2500),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_dollar_amount(raw, expected):
    assert parse_dollar_amount(raw) == expected


def test_parse_dollar_amount_parses_negative_rather_than_rejecting_it():
    # Rejecting non-positive amounts is the caller's job (see
    # mock_app/routers/accounts.py's `amount_cents is None or amount_cents <= 0`
    # checks) - this function itself must not silently swallow negatives.
    assert parse_dollar_amount("-5.00") == -500


def test_parse_dollar_amount_half_cent_rounding():
    # Verified empirically (not assumed): float imprecision means 1.005 * 100
    # evaluates to just under 100.5, so Python's round() lands on 100, not 101.
    assert parse_dollar_amount("1.005") == 100
