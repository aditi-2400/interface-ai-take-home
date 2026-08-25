"""Notifies a human that a paused run needs attention.

Two channels, both best-effort — a notification failure must never break
the paused run it's about to start waiting on:

- Desktop notification (macOS via osascript, terminal-bell fallback
  everywhere else): zero external setup, real and demoable on the spot.
- Slack webhook, only if SLACK_WEBHOOK_URL is set: the same shape a real
  on-call page would take. Optional by design — this project doesn't own a
  Slack workspace to point at by default.
"""

import json
import os
import platform
import subprocess

import httpx

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def notify(intervention_id: str, capability_id: str, reason: str) -> None:
    _notify_desktop(intervention_id, capability_id, reason)
    _notify_slack(intervention_id, capability_id, reason)


def _notify_desktop(intervention_id: str, capability_id: str, reason: str) -> None:
    title = f"Escalation: {capability_id}"
    message = f"{reason} (id: {intervention_id})"
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


def _notify_slack(intervention_id: str, capability_id: str, reason: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    text = f":rotating_light: Escalation needed — {capability_id}: {reason} (id: {intervention_id})"
    try:
        httpx.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=5)
    except httpx.HTTPError:  # noqa: BLE001 - best-effort; never break the paused run over this
        pass
