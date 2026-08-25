import httpx
import pytest

from escalation import notify


def test_notify_desktop_calls_osascript_on_darwin(monkeypatch, capsys):
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    calls = []
    monkeypatch.setattr(
        notify.subprocess, "run", lambda *a, **kw: calls.append((a, kw))
    )

    notify._notify_desktop("Escalation: transfer_funds", "blocked: risky step")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][0] == "osascript"
    assert "transfer_funds" in args[0][2]
    assert kwargs["check"] is True
    assert capsys.readouterr().out == ""


def test_notify_desktop_falls_back_to_terminal_bell_on_other_platforms(monkeypatch, capsys):
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")

    notify._notify_desktop("Escalation: transfer_funds", "blocked: risky step")

    out = capsys.readouterr().out
    assert "transfer_funds" in out
    assert "blocked: risky step" in out


def test_notify_desktop_falls_back_when_osascript_fails(monkeypatch, capsys):
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")

    def _raise(*a, **kw):
        raise FileNotFoundError("no osascript here")

    monkeypatch.setattr(notify.subprocess, "run", _raise)

    notify._notify_desktop("Escalation: transfer_funds", "blocked: risky step")

    out = capsys.readouterr().out
    assert "transfer_funds" in out


def test_notify_slack_does_nothing_without_webhook_url(monkeypatch):
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", None)
    called = []
    monkeypatch.setattr(notify.httpx, "post", lambda *a, **kw: called.append((a, kw)))

    notify._notify_slack("Escalation: transfer_funds", "blocked: risky step", prefix=":rotating_light:")

    assert called == []


def test_notify_slack_posts_to_webhook_when_configured(monkeypatch):
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    calls = []
    monkeypatch.setattr(notify.httpx, "post", lambda *a, **kw: calls.append((a, kw)))

    notify._notify_slack("Escalation: transfer_funds", "blocked: risky step", prefix=":rotating_light:")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "https://hooks.slack.test/x"
    assert "transfer_funds" in kwargs["json"]["text"]
    assert "blocked: risky step" in kwargs["json"]["text"]
    assert ":rotating_light:" in kwargs["json"]["text"]


def test_notify_slack_swallows_network_errors(monkeypatch):
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")

    def _raise(*a, **kw):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(notify.httpx, "post", _raise)

    notify._notify_slack("t", "m", prefix="p")  # must not raise


def test_notify_calls_both_channels_with_escalation_wording(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_notify_desktop", lambda *a: calls.append(("desktop", a)))
    monkeypatch.setattr(notify, "_notify_slack", lambda *a, **kw: calls.append(("slack", a, kw)))

    notify.notify("id-1", "transfer_funds", "blocked: risky step")

    assert calls[0][0] == "desktop"
    assert "transfer_funds" in calls[0][1][0]  # title
    assert "blocked: risky step" in calls[0][1][1]  # message
    assert "id-1" in calls[0][1][1]
    assert calls[1][0] == "slack"


def test_notify_approval_needed_calls_both_channels_with_review_wording(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_notify_desktop", lambda *a: calls.append(("desktop", a)))
    monkeypatch.setattr(notify, "_notify_slack", lambda *a, **kw: calls.append(("slack", a, kw)))

    notify.notify_approval_needed("transfer_funds", 7)

    assert calls[0][0] == "desktop"
    title, message = calls[0][1]
    assert "transfer_funds" in title
    assert "approval" in title.lower()
    assert "v7" in message
    assert "draft" in message
    assert calls[1][0] == "slack"
    assert calls[1][2]["prefix"] == ":memo: Capability needs review"
