"""0e121(xkshi 11-04 立项、11-06 排进 0.2.27):daemon.json 的端口上应答的不是这个数据目录的 daemon 时,MCP 要说清楚,
不能把别的程序的回包当成 daemon 的错误。判定同 0e120 / BUG-083:带 daemon.json 的 Key 调 GET /sessions,200 且回 JSON 数组才算。
只核从 daemon.json 找到的地址;--daemon-url / NULLPRINT_PORT 等用户明确指定的地址不核。同一个地址 + Key 核过一次就不再核。"""
import json

import httpx
import pytest

from nullprint_mcp import daemon as sup_daemon
from nullprint_mcp.daemon import DaemonError


@pytest.fixture
def discovered(monkeypatch, tmp_path):
    sup_daemon.set_daemon_url(None)
    for k in ("NULLPRINT_DAEMON_URL", "SUPLOGIN_DAEMON_URL", "NULLPRINT_PORT", "NULLPRINT_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    (tmp_path / "daemon.json").write_text(json.dumps({"port": 7801, "api_key": "np_abc", "pid": 1}))
    sup_daemon._verified.clear()
    yield tmp_path
    sup_daemon._verified.clear()


@pytest.mark.parametrize("answer", [httpx.Response(200, text="<html>hello</html>"),   # 别的程序
                                    httpx.Response(401, json={"detail": "invalid_api_key"}),   # 别的数据目录的 daemon
                                    httpx.Response(404, text="not found")])
def test_foreign_program_on_daemon_json_port_is_named(discovered, fake_daemon, answer):
    seen = fake_daemon(lambda req: answer)
    with pytest.raises(DaemonError) as e:
        sup_daemon.request("GET", "/profiles")
    msg = str(e.value)
    assert "7801" in msg and "daemon.json" in msg and "not the Nullprint daemon" in msg
    assert [r.url.path for r in seen] == ["/sessions"]                 # 没把真请求发给别的程序


def test_own_daemon_is_checked_once(discovered, fake_daemon):
    seen = fake_daemon(lambda req: httpx.Response(200, json=[] if req.url.path == "/sessions" else {"ok": True}))
    sup_daemon.request("GET", "/profiles")
    sup_daemon.request("GET", "/profiles")
    assert [r.url.path for r in seen] == ["/sessions", "/profiles", "/profiles"]
    assert seen[0].headers["X-API-Key"] == "np_abc"


def test_explicit_address_is_not_checked(fake_daemon, monkeypatch):
    sup_daemon.set_daemon_url("http://127.0.0.1:9999")
    try:
        seen = fake_daemon(lambda req: httpx.Response(200, json={"ok": True}))
        sup_daemon.request("GET", "/profiles")
        assert [r.url.path for r in seen] == ["/profiles"]
    finally:
        sup_daemon.set_daemon_url(None)
