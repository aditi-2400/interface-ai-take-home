"""Regex-based redaction, run on anything before it touches a log file.

Scope, deliberately: this redacts what's realistic to catch with regex over
this project's own data shapes — dollar amounts, ID-like numbers (account/
member IDs), the specific known names in the mock app's seed data, and
credentials/tokens (always, via keyword-adjacent matching). It is NOT a
general PII/NER engine — a generic "two capitalized words" name pattern
would also nuke half the UI copy ("Member Search", "Deposit Complete",
"Confirm Transfer"), which is a worse trade than under-redacting names the
utility doesn't know about. A production system handling real PII would
need a proper NER pass or an allowlist of business terms to exclude;
documented here as a known limitation, not silently pretended away.
"""

import re

# Matches both "$25.00" (as it appears in rendered page text) and a bare
# "25.00" (as it appears in a caller-supplied input value with no currency
# symbol at all) — an amount is an amount regardless of which context wrote it.
_AMOUNT_RE = re.compile(r"\$?\b[\d,]+\.\d{2}\b")
_ID_RE = re.compile(r"\b\d{4,10}\b")
_CREDENTIAL_RE = re.compile(
    r"(?i)\b(password|passwd|token|api[_-]?key|secret|authorization)\b\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.]+")

# Known names from the mock app's own seed data (mock_app/db.py). Exact-match
# only, by design — see module docstring for why a general name pattern
# isn't used.
_KNOWN_NAMES = ("Dana Whitfield", "Miguel Torres")
_NAME_RE = re.compile("|".join(re.escape(n) for n in _KNOWN_NAMES))


def redact(text: str) -> str:
    if not text:
        return text
    # Bearer tokens first: _CREDENTIAL_RE's value-capture is `\S+` (stops at
    # the first whitespace), so on "Authorization: Bearer abc123.def456" it
    # would only consume the word "Bearer" and leave the actual secret
    # (abc123.def456) untouched. Redacting the whole "Bearer <token>" span
    # up front avoids that gap.
    text = _BEARER_RE.sub("Bearer [REDACTED_CREDENTIAL]", text)
    text = _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}=[REDACTED_CREDENTIAL]", text)
    text = _NAME_RE.sub("[REDACTED_NAME]", text)
    text = _AMOUNT_RE.sub("[REDACTED_AMOUNT]", text)
    text = _ID_RE.sub("[REDACTED_ID]", text)
    return text


def redact_value(value):
    """Recursively redact strings inside a JSON-ish structure (dict/list/str/other)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value
