"""The three D1 tools: success, daemon unreachable, and 404, for each."""
import json

import httpx
import pytest

from nullprint_mcp import tools
from nullprint_mcp.daemon import DaemonError

PROFILES = [
    {"name": "bing-us", "os": "macos", "engine": "chromium", "created": "2026-09-01T10:00:00+08:00",
     "proxy": "http://gw.example:8000", "status": "running",
     "meta": {"group": "投放", "tags": ["us"], "note": "绑 a@example.com"}},
    {"name": "dir-01", "os": "windows", "engine": "firefox", "created": "2026-09-02T09:00:00+08:00",
     "proxy": None, "status": "idle", "meta": {}},
]

SESSION = {"name": "bing-us", "opened_at": "2026-09-21T11:00:00+08:00",
           "idle_seconds": 0.0, "url": "about:blank"}


def _json(payload, status=200):
    return lambda req: httpx.Response(status, json=payload)


def _unreachable(req):
    raise httpx.ConnectError("Connection refused", request=req)


def _not_found(detail):
    return lambda req: httpx.Response(404, json={"detail": detail})


def _route(table):
    """Dispatch on (method, path) — profile_stop talks to two endpoints."""
    def handler(req):
        key = (req.method, req.url.path)
        if key not in table:
            raise AssertionError(f"unexpected request: {key}")
        return table[key](req)
    return handler


# --- profiles_list ---------------------------------------------------------

def test_profiles_list_returns_daemon_payload(fake_daemon):
    seen = fake_daemon(_json(PROFILES))
    assert tools.profiles_list() == PROFILES
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/profiles"


def test_profiles_list_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.profiles_list()
    assert "cannot reach the Nullprint daemon" in str(e.value)
    assert "127.0.0.1:7801" in str(e.value)


def test_profiles_list_surfaces_daemon_error_body(fake_daemon):
    """A reason-only 403 (no counts) must still read as a sentence, not 'None of None'."""
    fake_daemon(lambda req: httpx.Response(403, json={"detail": {"reason": "quota_exceeded"}}))
    with pytest.raises(DaemonError) as e:
        tools.profiles_list()
    assert "403" in str(e.value)
    assert "profile quota reached — delete an identity" in str(e.value)
    assert "None" not in str(e.value)


# --- profile_start ---------------------------------------------------------

def test_profile_start_opens_session_headed_by_default(fake_daemon):
    seen = fake_daemon(_json(SESSION, 201))
    out = tools.profile_start("bing-us")
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/sessions/bing-us"
    assert json.loads(seen[0].content) == {"headless": False}
    assert out["name"] == "bing-us"
    assert out["url"] == "about:blank"


def test_profile_start_passes_headless_through(fake_daemon):
    seen = fake_daemon(_json(SESSION, 201))
    tools.profile_start("bing-us", headless=True)
    assert json.loads(seen[0].content) == {"headless": True}


def test_profile_start_reports_cdp_endpoint_when_daemon_publishes_one(fake_daemon):
    endpoint = "ws://127.0.0.1:9222/devtools/browser/abc"
    fake_daemon(_json({**SESSION, "cdp_endpoint": endpoint}, 201))
    out = tools.profile_start("bing-us")
    assert out["cdp_endpoint"] == endpoint
    assert "cdp_note" not in out


def test_profile_start_explains_missing_cdp_endpoint(fake_daemon):
    fake_daemon(_json(SESSION, 201))
    out = tools.profile_start("bing-us")
    assert out["cdp_endpoint"] is None
    assert "/sessions/{name}/*" in out["cdp_note"]


def test_profile_start_unknown_identity_is_readable(fake_daemon):
    fake_daemon(_not_found("profile not found: ghost"))
    with pytest.raises(DaemonError) as e:
        tools.profile_start("ghost")
    assert "404" in str(e.value)
    assert "not found" in str(e.value)
    assert "profile not found: ghost" in str(e.value)


def test_profile_start_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.profile_start("bing-us")
    assert "cannot reach the Nullprint daemon" in str(e.value)


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "..", None])
def test_profile_start_rejects_bad_names_before_calling_daemon(fake_daemon, bad):
    seen = fake_daemon(_json(SESSION, 201))
    with pytest.raises(ValueError):
        tools.profile_start(bad)
    assert seen == []


# --- profile_stop ----------------------------------------------------------

def test_profile_stop_closes_a_warm_session_without_falling_back(fake_daemon):
    seen = fake_daemon(_route({("DELETE", "/sessions/bing-us"): lambda r: httpx.Response(204)}))
    assert tools.profile_stop(" bing-us ") == {
        "name": "bing-us", "stopped": True, "closed": ["session"]}
    assert len(seen) == 1


def test_profile_stop_falls_back_to_identity_stop_for_a_launched_window(fake_daemon):
    """No warm session is a 409, not a 404 — that is the normal path for an
    identity the desktop app launched as a window."""
    seen = fake_daemon(_route({
        ("DELETE", "/sessions/bing-us"):
            lambda r: httpx.Response(409, json={"detail": "session not open: bing-us"}),
        ("POST", "/profiles/bing-us/stop"):
            lambda r: httpx.Response(200, json={"name": "bing-us", "stopped": ["launch"],
                                                "status": "idle"}),
    }))
    assert tools.profile_stop("bing-us") == {
        "name": "bing-us", "stopped": True, "closed": ["launch"], "status": "idle"}
    assert [(r.method, r.url.path) for r in seen] == [
        ("DELETE", "/sessions/bing-us"), ("POST", "/profiles/bing-us/stop")]


def test_profile_stop_falls_back_on_404_too(fake_daemon):
    fake_daemon(_route({
        ("DELETE", "/sessions/bing-us"): _not_found("no session"),
        ("POST", "/profiles/bing-us/stop"):
            lambda r: httpx.Response(200, json={"name": "bing-us", "stopped": ["session"],
                                                "status": "idle"}),
    }))
    assert tools.profile_stop("bing-us")["stopped"] is True


def test_profile_stop_reports_an_already_idle_identity_honestly(fake_daemon):
    fake_daemon(_route({
        ("DELETE", "/sessions/dir-01"): lambda r: httpx.Response(409, json={"detail": "not open"}),
        ("POST", "/profiles/dir-01/stop"):
            lambda r: httpx.Response(200, json={"name": "dir-01", "stopped": [],
                                                "status": "idle"}),
    }))
    out = tools.profile_stop("dir-01")
    assert out["stopped"] is False and out["closed"] == []


def test_profile_stop_unknown_identity_is_readable(fake_daemon):
    """Both endpoints disown it: the fallback's 404 is what reaches the caller."""
    fake_daemon(_route({
        ("DELETE", "/sessions/ghost"): lambda r: httpx.Response(409, json={"detail": "not open"}),
        ("POST", "/profiles/ghost/stop"): _not_found("profile not found: ghost"),
    }))
    with pytest.raises(DaemonError) as e:
        tools.profile_stop("ghost")
    assert "404" in str(e.value) and "profile not found: ghost" in str(e.value)


def test_profile_stop_does_not_swallow_a_real_failure(fake_daemon):
    """A 422 is not 'no session here' — it must not trigger the fallback."""
    seen = fake_daemon(_route({
        ("DELETE", "/sessions/bing-us"): lambda r: httpx.Response(422, json={"detail": "bad name"}),
    }))
    with pytest.raises(DaemonError) as e:
        tools.profile_stop("bing-us")
    assert "422" in str(e.value)
    assert len(seen) == 1


def test_profile_stop_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.profile_stop("bing-us")
    assert "cannot reach the Nullprint daemon" in str(e.value)
