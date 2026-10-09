"""Session-driving tools: goto, snapshot, click_selector, fill_selector, press,
eval, tabs, switch_tab. Every daemon endpoint here needs a warm session opened
with profile_start first; a 409 is the daemon's way of saying that."""
import json

import httpx
import pytest

from nullprint_mcp import tools
from nullprint_mcp.daemon import DaemonError


def _json(payload, status=200):
    return lambda req: httpx.Response(status, json=payload)


def _unreachable(req):
    raise httpx.ConnectError("Connection refused", request=req)


def _session_not_open(name):
    return lambda req: httpx.Response(409, json={"detail": f"session not open: {name}"})


# --- session_goto ------------------------------------------------------------

def test_session_goto_navigates(fake_daemon):
    seen = fake_daemon(_json({"url": "https://example.com/"}))
    out = tools.session_goto("bing-us", "https://example.com")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us/goto"
    assert json.loads(seen[0].content) == {"url": "https://example.com"}
    assert out == {"url": "https://example.com/"}


def test_session_goto_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_goto("bing-us", "https://example.com")
    assert "409" in str(e.value) and "session not open" in str(e.value)


def test_session_goto_rejects_a_blank_url(fake_daemon):
    seen = fake_daemon(_json({"url": "https://example.com/"}))
    with pytest.raises(ValueError):
        tools.session_goto("bing-us", "  ")
    assert seen == []


def test_session_goto_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.session_goto("bing-us", "https://example.com")
    assert "cannot reach the Nullprint daemon" in str(e.value)


# --- session_snapshot ---------------------------------------------------------

def test_session_snapshot_returns_the_accessibility_tree(fake_daemon):
    seen = fake_daemon(_json({"snapshot": "- button \"Sign in\""}))
    out = tools.session_snapshot("bing-us")
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/sessions/bing-us/snapshot"
    assert out == {"snapshot": "- button \"Sign in\""}


def test_session_snapshot_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_snapshot("bing-us")
    assert "409" in str(e.value) and "session not open" in str(e.value)


# --- session_click_selector ---------------------------------------------------

def test_session_click_selector_clicks(fake_daemon):
    seen = fake_daemon(_json({"ok": True}))
    out = tools.session_click_selector("bing-us", "button#submit")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us/click_selector"
    assert json.loads(seen[0].content) == {"selector": "button#submit"}
    assert out == {"ok": True}


def test_session_click_selector_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_click_selector("bing-us", "button#submit")
    assert "409" in str(e.value) and "session not open" in str(e.value)


def test_session_click_selector_rejects_a_blank_selector(fake_daemon):
    seen = fake_daemon(_json({"ok": True}))
    with pytest.raises(ValueError):
        tools.session_click_selector("bing-us", "")
    assert seen == []


# --- session_fill_selector -----------------------------------------------------

def test_session_fill_selector_fills(fake_daemon):
    seen = fake_daemon(_json({"ok": True}))
    out = tools.session_fill_selector("bing-us", "input[name=email]", "a@example.com")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us/fill_selector"
    assert json.loads(seen[0].content) == {
        "selector": "input[name=email]", "value": "a@example.com"}
    assert out == {"ok": True}


def test_session_fill_selector_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_fill_selector("bing-us", "input[name=email]", "a@example.com")
    assert "409" in str(e.value) and "session not open" in str(e.value)


def test_session_fill_selector_rejects_a_blank_selector(fake_daemon):
    seen = fake_daemon(_json({"ok": True}))
    with pytest.raises(ValueError):
        tools.session_fill_selector("bing-us", "  ", "a@example.com")
    assert seen == []


# --- session_press -------------------------------------------------------------

def test_session_press_presses_a_key(fake_daemon):
    seen = fake_daemon(_json({"ok": True}))
    out = tools.session_press("bing-us", "Enter")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us/press"
    assert json.loads(seen[0].content) == {"key": "Enter"}
    assert out == {"ok": True}


def test_session_press_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_press("bing-us", "Enter")
    assert "409" in str(e.value) and "session not open" in str(e.value)


# --- session_eval ----------------------------------------------------------

def test_session_eval_returns_the_ok_result_envelope(fake_daemon):
    seen = fake_daemon(_json({"ok": True, "result": "Example Domain"}))
    out = tools.session_eval("bing-us", "document.title")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us/eval"
    assert json.loads(seen[0].content) == {"expr": "document.title"}
    assert out == {"ok": True, "result": "Example Domain"}


def test_session_eval_reports_a_js_error_as_data_not_an_exception(fake_daemon):
    """A throwing expression is a normal 200 with ok:false, not a DaemonError."""
    fake_daemon(_json({"ok": False, "error": "ReferenceError: nope is not defined",
                       "error_type": "ReferenceError"}))
    out = tools.session_eval("bing-us", "nope()")
    assert out["ok"] is False
    assert "ReferenceError" in out["error"]


def test_session_eval_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_eval("bing-us", "document.title")
    assert "409" in str(e.value) and "session not open" in str(e.value)


def test_session_eval_rejects_a_blank_expr(fake_daemon):
    seen = fake_daemon(_json({"ok": True, "result": None}))
    with pytest.raises(ValueError):
        tools.session_eval("bing-us", "")
    assert seen == []


# --- session_tabs / session_switch_tab -----------------------------------------

def test_session_tabs_lists_open_tabs(fake_daemon):
    payload = {"tabs": [{"index": 0, "url": "https://a.example"},
                        {"index": 1, "url": "https://b.example"}], "active": 1}
    seen = fake_daemon(_json(payload))
    out = tools.session_tabs("bing-us")
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/sessions/bing-us/tabs"
    assert out == payload


def test_session_tabs_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_tabs("bing-us")
    assert "409" in str(e.value) and "session not open" in str(e.value)


def test_session_switch_tab_switches(fake_daemon):
    seen = fake_daemon(_json({"index": 1, "url": "https://b.example"}))
    out = tools.session_switch_tab("bing-us", 1)
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us/switch_tab"
    assert json.loads(seen[0].content) == {"index": 1}
    assert out == {"index": 1, "url": "https://b.example"}


def test_session_switch_tab_session_not_open(fake_daemon):
    fake_daemon(_session_not_open("bing-us"))
    with pytest.raises(DaemonError) as e:
        tools.session_switch_tab("bing-us", 0)
    assert "409" in str(e.value) and "session not open" in str(e.value)


def test_session_switch_tab_out_of_range_index(fake_daemon):
    fake_daemon(lambda req: httpx.Response(422, json={"detail": "no tab at index 5 (have 2)"}))
    with pytest.raises(DaemonError) as e:
        tools.session_switch_tab("bing-us", 5)
    assert "422" in str(e.value) and "no tab at index 5" in str(e.value)
