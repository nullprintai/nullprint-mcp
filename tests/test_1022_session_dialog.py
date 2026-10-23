"""0.2.18: JavaScript dialogs in a session are left for the user. While one is open, session
commands come back 409 {"reason": "dialog_open", "type", "message"}; session_dialog answers it
(POST /sessions/{name}/dialog), 409 {"reason": "no_dialog"} when nothing is open. The error an
AI client reads must say which dialog is open and how to clear it — not a licence sentence."""
import json

import httpx
import pytest

from nullprint_mcp import tools
from nullprint_mcp.daemon import DaemonError


def _json(payload, status=200):
    return lambda req: httpx.Response(status, json=payload)


def test_session_dialog_accepts(fake_daemon):
    seen = fake_daemon(_json({"ok": True, "action": "accept", "type": "confirm", "message": "Delete?"}))
    out = tools.session_dialog("bing-us", "accept")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us/dialog"
    assert json.loads(seen[0].content) == {"action": "accept"}
    assert out["type"] == "confirm"


def test_session_dialog_dismiss_and_prompt_text(fake_daemon):
    seen = fake_daemon(_json({"ok": True}))
    tools.session_dialog("bing-us", "dismiss")
    tools.session_dialog("bing-us", " ACCEPT ", prompt_text="Alice")
    assert json.loads(seen[0].content) == {"action": "dismiss"}
    assert json.loads(seen[1].content) == {"action": "accept", "prompt_text": "Alice"}


def test_session_dialog_rejects_bad_action_before_calling_daemon(fake_daemon):
    seen = fake_daemon(_json({"ok": True}))
    with pytest.raises(ValueError):
        tools.session_dialog("bing-us", "ok")
    with pytest.raises(ValueError):
        tools.session_dialog("a/b", "accept")
    assert seen == []


def test_session_dialog_no_dialog_open(fake_daemon):
    fake_daemon(_json({"detail": {"reason": "no_dialog"}}, status=409))
    with pytest.raises(DaemonError) as e:
        tools.session_dialog("bing-us", "accept")
    msg = str(e.value)
    assert "409" in msg and "no dialog is open" in msg and "license" not in msg


def test_command_blocked_by_open_dialog_says_how_to_clear_it(fake_daemon):
    fake_daemon(_json({"detail": {"reason": "dialog_open", "type": "confirm", "message": "Delete?"}}, status=409))
    with pytest.raises(DaemonError) as e:
        tools.session_click_selector("bing-us", "button#del")
    msg = str(e.value)
    assert "confirm" in msg and "Delete?" in msg and "session_dialog" in msg
    assert "license" not in msg
