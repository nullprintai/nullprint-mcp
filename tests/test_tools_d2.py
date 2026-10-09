"""D2 tools: profile_create, profile_checkup, proxy_check."""
import json

import httpx
import pytest

from nullprint_mcp import tools
from nullprint_mcp.daemon import DaemonError

CREATED = {"name": "dir-02", "os": "macos", "created": "2026-09-21T10:00:00+08:00"}

CHECKUP = {
    "name": "cc-01", "engine": "chromium", "live": False, "summary": "warn",
    "checks": [
        {"id": "os_host", "label": "OS 与宿主一致", "verdict": "pass", "detail": "macos"},
        {"id": "geo", "label": "时区/语言据出口对齐", "verdict": "warn", "detail": "尚未启动"},
        {"id": "ja3", "label": "TLS/JA3 与内核基准一致", "verdict": "skip", "detail": "?live 实测"},
    ],
}


def _json(payload, status=200):
    return lambda req: httpx.Response(status, json=payload)


def _unreachable(req):
    raise httpx.ConnectError("Connection refused", request=req)


# --- profile_create --------------------------------------------------------

def test_profile_create_sends_only_the_fields_given(fake_daemon):
    seen = fake_daemon(_json(CREATED, 201))
    assert tools.profile_create("dir-02") == CREATED
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/profiles"
    assert json.loads(seen[0].content) == {"name": "dir-02"}


def test_profile_create_passes_the_full_ledger_and_startup_tabs(fake_daemon):
    seen = fake_daemon(_json(CREATED, 201))
    tools.profile_create("dir-02", engine="chromium", proxy="socks5://host:1080",
                         group="投放", tags=["us", "warm"], note="绑 a@example.com",
                         urls=["https://example.com", "https://mail.example.com"])
    assert json.loads(seen[0].content) == {
        "name": "dir-02", "engine": "chromium", "proxy": "socks5://host:1080",
        "group": "投放", "tags": ["us", "warm"], "note": "绑 a@example.com",
        "urls": ["https://example.com", "https://mail.example.com"]}


def test_profile_create_leaves_engine_to_the_installation_default(fake_daemon):
    """Never hard-code a core: the daemon decides the default engine for this install."""
    seen = fake_daemon(_json(CREATED, 201))
    tools.profile_create("dir-02", note="x")
    assert "engine" not in json.loads(seen[0].content)


def test_profile_create_quota_403_reads_as_a_sentence(fake_daemon):
    fake_daemon(lambda req: httpx.Response(
        403, json={"detail": {"reason": "quota_exceeded", "limit": 5, "used": 5}}))
    with pytest.raises(DaemonError) as e:
        tools.profile_create("dir-02")
    msg = str(e.value)
    assert "profile quota reached" in msg
    assert "5 of 5 identities used" in msg
    assert "delete an identity you no longer need" in msg
    assert "quota_exceeded" not in msg        # the raw enum never reaches the reader


def test_profile_create_unlicensed_403_reads_as_a_sentence(fake_daemon):
    fake_daemon(lambda req: httpx.Response(
        403, json={"detail": {"reason": "expired", "limit": 5, "used": 1}}))
    with pytest.raises(DaemonError) as e:
        tools.profile_create("dir-02")
    assert "not authorized" in str(e.value) and "expired" in str(e.value)


def test_profile_create_duplicate_name_is_readable(fake_daemon):
    fake_daemon(lambda req: httpx.Response(409, json={"detail": "profile exists: dir-02"}))
    with pytest.raises(DaemonError) as e:
        tools.profile_create("dir-02")
    assert "409" in str(e.value) and "profile exists: dir-02" in str(e.value)


def test_profile_create_unknown_engine_surfaces_the_accepted_list(fake_daemon):
    fake_daemon(lambda req: httpx.Response(
        422, json={"detail": "unknown engine: 'nope' (have ['firefox', 'chromium'])"}))
    with pytest.raises(DaemonError) as e:
        tools.profile_create("dir-02", engine="nope")
    assert "have [" in str(e.value)


@pytest.mark.parametrize("bad", ["", "   ", "a/b", None])
def test_profile_create_rejects_bad_names_before_calling_daemon(fake_daemon, bad):
    seen = fake_daemon(_json(CREATED, 201))
    with pytest.raises(ValueError):
        tools.profile_create(bad)
    assert seen == []


def test_profile_create_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.profile_create("dir-02")
    assert "cannot reach the Nullprint daemon" in str(e.value)


# --- profile_checkup -------------------------------------------------------

def test_profile_checkup_returns_summary_and_checks(fake_daemon):
    seen = fake_daemon(_json(CHECKUP))
    out = tools.profile_checkup("cc-01")
    assert out == CHECKUP
    assert out["summary"] == "warn"
    assert len(out["checks"]) == 3
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/profiles/cc-01/checkup"


def test_profile_checkup_is_static_by_default(fake_daemon):
    seen = fake_daemon(_json(CHECKUP))
    tools.profile_checkup("cc-01")
    assert seen[0].url.params["live"] == "false"


def test_profile_checkup_live_asks_for_a_measured_run(fake_daemon):
    seen = fake_daemon(_json({**CHECKUP, "live": True, "summary": "fail"}))
    assert tools.profile_checkup("cc-01", live=True)["summary"] == "fail"
    assert seen[0].url.params["live"] == "true"


def test_profile_checkup_unknown_identity_is_readable(fake_daemon):
    fake_daemon(lambda req: httpx.Response(404, json={"detail": "profile not found: ghost"}))
    with pytest.raises(DaemonError) as e:
        tools.profile_checkup("ghost")
    assert "404" in str(e.value) and "profile not found: ghost" in str(e.value)


def test_profile_checkup_rejects_a_blank_name(fake_daemon):
    seen = fake_daemon(_json(CHECKUP))
    with pytest.raises(ValueError):
        tools.profile_checkup("  ")
    assert seen == []


def test_profile_checkup_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.profile_checkup("cc-01")
    assert "cannot reach the Nullprint daemon" in str(e.value)


# --- proxy_check -----------------------------------------------------------

def test_proxy_check_reports_the_exit(fake_daemon):
    seen = fake_daemon(_json({"ok": True, "exit_ip": "203.0.113.7",
                              "country": "US", "latency_ms": 412}))
    out = tools.proxy_check(" socks5://host:1080 ")
    assert out == {"ok": True, "exit_ip": "203.0.113.7", "country": "US", "latency_ms": 412}
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/proxy-check"
    assert json.loads(seen[0].content) == {"url": "socks5://host:1080"}


def test_proxy_check_returns_a_dead_proxy_as_data_not_an_error(fake_daemon):
    """The daemon never 500s here — {ok:false} is an answer the AI must read."""
    fake_daemon(_json({"ok": False, "error": "ConnectTimeout: timed out"}))
    out = tools.proxy_check("socks5://dead:1080")
    assert out["ok"] is False
    assert "ConnectTimeout" in out["error"]


def test_proxy_check_returns_a_malformed_url_as_data(fake_daemon):
    fake_daemon(_json({"ok": False, "error": "bad proxy url: missing scheme"}))
    assert tools.proxy_check("host:1080")["ok"] is False


def test_proxy_check_tolerates_an_ungeolocatable_exit(fake_daemon):
    fake_daemon(_json({"ok": True, "exit_ip": "203.0.113.7",
                       "country": None, "latency_ms": 88}))
    assert tools.proxy_check("socks5://host:1080")["country"] is None


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_proxy_check_rejects_an_empty_url_before_calling_daemon(fake_daemon, bad):
    seen = fake_daemon(_json({"ok": True}))
    with pytest.raises(ValueError):
        tools.proxy_check(bad)
    assert seen == []


def test_proxy_check_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.proxy_check("socks5://host:1080")
    assert "cannot reach the Nullprint daemon" in str(e.value)
