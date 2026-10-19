"""Daemon address discovery, and what an MCP client actually sees."""
import asyncio
import json

import httpx
import pytest

from nullprint_mcp import daemon as sup_daemon
from nullprint_mcp.__main__ import _parse_args
from nullprint_mcp.daemon import DaemonError
from nullprint_mcp.server import build_server


# --- daemon address discovery ---------------------------------------------

def test_explicit_url_wins_over_env(monkeypatch):
    monkeypatch.setenv("NULLPRINT_DAEMON_URL", "http://127.0.0.1:1111")
    sup_daemon.set_daemon_url("http://127.0.0.1:2222/")
    try:
        assert sup_daemon.base_url() == "http://127.0.0.1:2222"
    finally:
        sup_daemon.set_daemon_url(None)


def test_nullprint_port_env(monkeypatch):
    monkeypatch.delenv("NULLPRINT_DAEMON_URL", raising=False)
    monkeypatch.setenv("NULLPRINT_PORT", "7801")
    assert sup_daemon.base_url() == "http://127.0.0.1:7801"


def test_reads_port_from_daemon_json(monkeypatch, tmp_path):
    monkeypatch.delenv("NULLPRINT_DAEMON_URL", raising=False)
    monkeypatch.delenv("NULLPRINT_PORT", raising=False)
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    (tmp_path / "daemon.json").write_text(json.dumps({"port": 54321, "pid": 1}))
    assert sup_daemon.base_url() == "http://127.0.0.1:54321"


def test_missing_daemon_json_says_daemon_is_not_running(monkeypatch, tmp_path):
    monkeypatch.delenv("NULLPRINT_DAEMON_URL", raising=False)
    monkeypatch.delenv("NULLPRINT_PORT", raising=False)
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    with pytest.raises(DaemonError) as e:
        sup_daemon.base_url()
    assert "does not look like it is running" in str(e.value)
    assert "--daemon-url" in str(e.value)


# --- local API key -----------------------------------------------------------

def test_request_sends_api_key_from_daemon_json(monkeypatch, tmp_path, fake_daemon):
    monkeypatch.delenv("NULLPRINT_API_KEY", raising=False)
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    (tmp_path / "daemon.json").write_text(json.dumps({"port": 7801, "api_key": "np_abc"}))
    seen = fake_daemon(lambda req: httpx.Response(200, json={}))
    sup_daemon.request("GET", "/profiles")
    assert seen[0].headers["X-API-Key"] == "np_abc"


def test_api_key_env_overrides_daemon_json(monkeypatch, tmp_path, fake_daemon):
    monkeypatch.setenv("NULLPRINT_API_KEY", "np_env")
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    (tmp_path / "daemon.json").write_text(json.dumps({"port": 7801, "api_key": "np_abc"}))
    seen = fake_daemon(lambda req: httpx.Response(200, json={}))
    sup_daemon.request("GET", "/profiles")
    assert seen[0].headers["X-API-Key"] == "np_env"


def test_no_key_anywhere_sends_no_header(monkeypatch, tmp_path, fake_daemon):
    monkeypatch.delenv("NULLPRINT_API_KEY", raising=False)
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    seen = fake_daemon(lambda req: httpx.Response(200, json={}))
    sup_daemon.request("GET", "/profiles")
    assert "X-API-Key" not in seen[0].headers


def test_401_explains_the_api_key(monkeypatch, tmp_path, fake_daemon):
    monkeypatch.delenv("NULLPRINT_API_KEY", raising=False)
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    fake_daemon(lambda req: httpx.Response(401, json={"detail": {"reason": "invalid_api_key"}}))
    with pytest.raises(DaemonError) as e:
        sup_daemon.request("GET", "/profiles")
    assert e.value.status == 401
    assert "NULLPRINT_API_KEY" in str(e.value)


# --- CLI -------------------------------------------------------------------

def test_port_shorthand_builds_a_url():
    assert _parse_args(["--port", "7801"]).daemon_url == "http://127.0.0.1:7801"


def test_daemon_url_and_port_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        _parse_args(["--port", "7801", "--daemon-url", "http://127.0.0.1:7801"])


# --- what the MCP client sees ---------------------------------------------

def _flat(text):
    """Docstrings wrap; assertions about their wording should not care where."""
    return " ".join(text.split())


def _listed_tools():
    server = build_server("http://127.0.0.1:7801")
    try:
        return {t.name: t for t in asyncio.run(server.list_tools())}
    finally:
        sup_daemon.set_daemon_url(None)


def test_server_exposes_every_tool_with_a_description():
    by_name = _listed_tools()
    assert set(by_name) == {"profiles_list", "profile_start", "profile_stop",
                            "profile_create", "profile_settings", "profile_checkup", "proxy_check",
                            "profile_delete", "trash_list", "profile_restore",
                            "session_goto", "session_snapshot", "session_click_selector",
                            "session_fill_selector", "session_press", "session_eval",
                            "session_tabs", "session_switch_tab"}
    for tool in by_name.values():
        assert tool.description and len(tool.description) > 40


def test_delete_is_advertised_as_recoverable_and_purge_as_human_only():
    """The schema is the only thing an AI client reads before acting. If the
    'no permanent delete here' rule is not in the text, it does not exist."""
    by_name = _listed_tools()
    assert by_name["profile_delete"].inputSchema["required"] == ["name"]
    desc = _flat(by_name["profile_delete"].description)
    assert "recycle bin" in desc
    assert "NO tool here for permanent deletion" in desc
    assert "desktop app only" in desc
    assert _flat(by_name["profile_restore"].description).startswith(
        "Bring an identity back from the recycle bin")
    trash = _flat(by_name["trash_list"].description)
    assert "Read-only" in trash
    assert "profile_restore" in trash          # says what it is for
    assert by_name["trash_list"].inputSchema.get("properties", {}) == {}


def test_tool_schemas_require_only_what_is_mandatory():
    by_name = _listed_tools()
    assert by_name["profile_start"].inputSchema["required"] == ["name"]
    assert by_name["profile_start"].inputSchema["properties"]["headless"]["default"] is False
    assert by_name["profile_stop"].inputSchema["required"] == ["name"]
    assert by_name["profile_checkup"].inputSchema["required"] == ["name"]
    assert by_name["profile_checkup"].inputSchema["properties"]["live"]["default"] is False
    assert by_name["proxy_check"].inputSchema["required"] == ["url"]
    # name is the only thing you must decide to create an identity
    assert by_name["profile_create"].inputSchema["required"] == ["name"]


def test_profile_create_schema_offers_the_ledger_and_startup_tabs():
    props = _listed_tools()["profile_create"].inputSchema["properties"]
    assert set(props) == {"name", "engine", "proxy", "group", "tags", "note", "urls", "force_dark"}


def test_live_checkup_warns_that_it_starts_a_browser():
    """An AI must not reach for live=True casually — the docstring has to say so."""
    desc = _flat(_listed_tools()["profile_checkup"].description)
    assert "STARTS THE BROWSER" in desc
    assert "tens of seconds" in desc


def test_profile_start_warns_that_external_cdp_bypasses_the_guards():
    """The consistency guard is the product. If attaching from outside can defeat
    it, the tool description an AI reads has to say so before it attaches."""
    desc = _flat(_listed_tools()["profile_start"].description)
    assert "bypasses Nullprint's consistency guards" in desc
    assert "at your own risk" in desc
    assert "Only the Chrome browser core publishes one" in desc


def test_proxy_check_tells_the_reader_a_dead_proxy_is_data():
    desc = _flat(_listed_tools()["proxy_check"].description)
    assert "A dead proxy is an ANSWER, not a failure" in desc


def test_profile_stop_explains_the_two_kinds_of_running():
    desc = _flat(_listed_tools()["profile_stop"].description)
    assert "warm session" in desc and "window the desktop app launched" in desc
