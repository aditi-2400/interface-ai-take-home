"""Explicit, configurable allowlist of permitted domains/routes and action types.

Both the discovery agent and the replay engine must consult this before
every action; anything outside it is refused and logged, never silently
allowed. Checked against the URL the action is about to act on/within — for
"navigate", that's the target being navigated to (checked before goto());
for every other action, the current page (checked before interacting).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

DEFAULT_ALLOWLIST_PATH = Path(__file__).parent / "allowlist.yaml"


@dataclass
class AllowlistDecision:
    allowed: bool
    reason: str | None = None


class Allowlist:
    def __init__(
        self,
        allowed_domains: list[str],
        allowed_route_patterns: list[str],
        allowed_action_types: list[str],
    ):
        self.allowed_domains = set(allowed_domains)
        self._route_res = [re.compile(p) for p in allowed_route_patterns]
        self.allowed_action_types = set(allowed_action_types)

    @classmethod
    def load(cls, path: Path = DEFAULT_ALLOWLIST_PATH) -> "Allowlist":
        data = yaml.safe_load(path.read_text())
        return cls(
            allowed_domains=data.get("allowed_domains", []),
            allowed_route_patterns=data.get("allowed_route_patterns", []),
            allowed_action_types=data.get("allowed_action_types", []),
        )

    def check(self, url: str, action_type: str) -> AllowlistDecision:
        if action_type not in self.allowed_action_types:
            return AllowlistDecision(
                False, f"action type {action_type!r} is not in the allowlist"
            )

        parsed = urlparse(url)
        if parsed.netloc not in self.allowed_domains:
            return AllowlistDecision(
                False, f"domain {parsed.netloc!r} is not in the allowlist"
            )

        if not any(r.match(parsed.path) for r in self._route_res):
            return AllowlistDecision(
                False, f"route {parsed.path!r} is not in the allowlist"
            )

        return AllowlistDecision(True)
