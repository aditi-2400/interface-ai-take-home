import httpx
import pytest

from escalation import notify


def test_notify_desktop_calls_osascript_on_darwin(monkeypatch, capsys):
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    calls = []
    monkeypatch.setattr(
        notify.subprocess, "run", lambda *a, **kw: calls.append((a, kw))
    )

    notify._notify_desktop("id-1", "transfer_funds", "blocked: risky step")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][0] == "osascript"
    assert "transfer_funds" in args[0][2]
    assert kwargs["check"] is True
    assert capsys.readouterr().out == ""


def test_notify_desktop_falls_back_to_terminal_bell_on_other_platforms(monkeypatch, capsys):
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")

    notify._notify_desktop("id-1", "transfer_funds", "blocked: risky step")

    out = capsys.readouterr().out
    assert "transfer_funds" in out
    assert "id-1" in out


def test_notify_desktop_falls_back_when_osascript_fails(monkeypatch, capsys):
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")

    def _raise(*a, **kw):
        raise FileNotFoundError("no osascript here")

    monkeypatch.setattr(notify.subprocess, "run", _raise)

    notify._notify_desktop("id-1", "transfer_funds", "blocked: risky step")

    out = capsys.readouterr().out
    assert "transfer_funds" in out


def test_notify_slack_does_nothing_without_webhook_url(monkeypatch):
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", None)
    called = []
    monkeypatch.setattr(notify.httpx, "post", lambda *a, **kw: called.append((a, kw)))

    notify._notify_slack("id-1", "transfer_funds", "blocked: risky step")

    assert called == []


def test_notify_slack_posts_to_webhook_when_configured(monkeypatch):
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    calls = []
    monkeypatch.setattr(notify.httpx, "post", lambda *a, **kw: calls.append((a, kw)))

    notify._notify_slack("id-1", "transfer_funds", "blocked: risky step")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "https://hooks.slack.test/x"
    assert "transfer_funds" in kwargs["json"]["text"]
    assert "id-1" in kwargs["json"]["text"]


def test_notify_slack_swallows_network_errors(monkeypatch):
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")

    def _raise(*a, **kw):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(notify.httpx, "post", _raise)

    notify._notify_slack("id-1", "transfer_funds", "blocked: risky step")  # must not raise


def test_notify_calls_both_channels(monkeypatch):
    desktop_calls = []
    slack_calls = []
    monkeypatch.setattr(notify, "_notify_desktop", lambda *a: desktop_calls.append(a))
    monkeypatch.setattr(notify, "_notify_slack", lambda *a: slack_calls.append(a))

    notify.notify("id-1", "transfer_funds", "blocked: risky step")

    assert desktop_calls == [("id-1", "transfer_funds", "blocked: risky step")]
    assert slack_calls == [("id-1", "transfer_funds", "blocked: risky step")]
