"""Maps a capability's target_app to where it actually lives and how to
authenticate against it, so the invoke endpoint doesn't need to know
anything about a specific target beyond this table.
"""

import os
from pathlib import Path
from typing import NamedTuple

EVIDENCE_SESSIONS_DIR = Path(__file__).parent.parent / "evidence" / "sessions"

MOCK_APP_BASE_URL = os.environ.get("MOCK_APP_BASE_URL", "http://127.0.0.1:8000")
MERIDIAN_BASE_URL = os.environ.get("MERIDIAN_BASE_URL", "https://web-sample.interface-hiring.com")


class TargetConfig(NamedTuple):
    base_url: str
    session_state_path: Path | None


TARGET_CONFIGS: dict[str, TargetConfig] = {
    "meridian-trust-core-banking": TargetConfig(base_url=MOCK_APP_BASE_URL, session_state_path=None),
    "meridian-core-live": TargetConfig(
        base_url=MERIDIAN_BASE_URL,
        session_state_path=EVIDENCE_SESSIONS_DIR / "meridian-core-live.json",
    ),
}


def target_config_for(target_app: str) -> TargetConfig:
    try:
        return TARGET_CONFIGS[target_app]
    except KeyError:
        raise ValueError(f"No known target config for target_app={target_app!r}") from None
