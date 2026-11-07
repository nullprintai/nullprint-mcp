"""0.2.27(xkshi 11-06,老板员工机上撞到):系统 / 环境代理没把 127.0.0.1 列进例外时,MCP 连本机 daemon 的请求进了代理,
代理回空 503,报成「daemon could not reach its backend」。连回环地址(127.0.0.1 / localhost / ::1)一律不走代理;
回包不是 daemon 的格式(空 body / 非 JSON)时提示可能被系统代理拦截。"""
import http.server
import json
import threading

import httpx
import pytest

from nullprint_mcp import daemon as sup_daemon
from nullprint_mcp.daemon import DaemonError

DEAD = "http://127.0.0.1:9"     # 没人听的端口:请求真进了代理就连不上


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps([] if self.path.startswith(("/sessions", "/profiles")) else {"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def real_daemon(monkeypatch, tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k in ("NULLPRINT_DAEMON_URL", "SUPLOGIN_DAEMON_URL", "NULLPRINT_PORT", "NULLPRINT_API_KEY", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(k, DEAD)
    monkeypatch.setenv("NULLPRINT_HOME", str(tmp_path))
    (tmp_path / "daemon.json").write_text(json.dumps({"port": srv.server_address[1], "api_key": "np_abc", "pid": 1}))
    sup_daemon._verified.clear()
    yield srv.server_address[1]
    sup_daemon._verified.clear()
    srv.shutdown()


def test_local_daemon_is_reached_even_with_proxy_env(real_daemon):
    assert sup_daemon.request("GET", "/profiles").json() == []          # 走 daemon.json + 0e121 核 Key


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_explicit_local_url_skips_proxy_too(real_daemon, host):
    sup_daemon.set_daemon_url(f"http://{host}:{real_daemon}")
    try:
        assert sup_daemon.request("GET", "/profiles").json() == []
    finally:
        sup_daemon.set_daemon_url(None)


def test_non_daemon_answer_hints_at_a_system_proxy(fake_daemon):
    fake_daemon(lambda req: httpx.Response(503, text=""))
    with pytest.raises(DaemonError) as e:
        sup_daemon.request("GET", "/profiles")
    msg = str(e.value)
    assert "503" in msg and "system proxy" in msg
