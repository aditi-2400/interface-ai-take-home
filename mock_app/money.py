def cents_to_dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100}.{cents % 100:02d}"


def parse_dollar_amount(raw: str) -> int | None:
    """Parse a dollar-amount string into integer cents, or None if unparseable."""
    raw = (raw or "").strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        dollars = float(raw)
    except ValueError:
        return None
    return round(dollars * 100)
