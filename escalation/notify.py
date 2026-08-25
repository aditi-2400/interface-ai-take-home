"""Notifies a human that something needs their attention.

Two channels, both best-effort — a notification failure must never break
whatever it's reporting on:

- Desktop notification (macOS via osascript, terminal-bell fallback
  everywhere else): zero external setup, real and demoable on the spot.
- Slack webhook, only if SLACK_WEBHOOK_URL is set: the same shape a real
  on-call page would take. Optional by design — this project doesn't own a
  Slack workspace to point at by default.

Two entry points, for the two real moments something needs a human:
notify() (a paused replay run) and notify_approval_needed() (a freshly
discovered capability sitting in draft, unreviewed).
"""

import json
import os
import platform
import subprocess

import httpx

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def notify(intervention_id: str, capability_id: str, reason: str) -> None:
    title = f"Escalation: {capability_id}"
    message = f"{reason} (id: {intervention_id})"
    _notify_desktop(title, message)
    _notify_slack(title, message, prefix=":rotating_light: Escalation needed")


def notify_approval_needed(capability_id: str, version: int) -> None:
    title = f"Needs approval: {capability_id}"
    message = f"v{version} was just recorded and is still in draft."
    _notify_desktop(title, message)
    _notify_slack(title, message, prefix=":memo: Capability needs review")


def _notify_desktop(title: str, message: str) -> None:
    if platform.system() == "Darwin":
        script = (
            f"display notification {json.dumps(message)} "
            f'with title {json.dumps(title)} sound name "Ping"'
        )
        try:
            subprocess.run(["osascript", "-e", script], check=True, timeout=3, capture_output=True)
            return
        except Exception:  # noqa: BLE001 - best-effort; fall through to the terminal bell
            pass
    print(f"\a[{title}] {message}")


def _notify_slack(title: str, message: str, prefix: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    text = f"{prefix} — {title}: {message}"
    try:
        httpx.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=5)
    except httpx.HTTPError:  # noqa: BLE001 - best-effort; never break the caller over this
        pass
