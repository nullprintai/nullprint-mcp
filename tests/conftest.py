"""Put mcp/ on sys.path so `import nullprint_mcp` works however pytest is invoked,
and give every test a fake daemon so none of them needs a real one running."""
import pathlib
import sys

import httpx
import pytest

_MCP_DIR = str(pathlib.Path(__file__).resolve().parents[1])
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from nullprint_mcp import daemon as sup_daemon   # noqa: E402

FAKE_URL = "http://127.0.0.1:7801"


@pytest.fixture(autouse=True)
def pinned_daemon_url(monkeypatch):
    """Pin the address so no test reads the real ~/.nullprint/daemon.json."""
    monkeypatch.setenv("NULLPRINT_DAEMON_URL", FAKE_URL)
    monkeypatch.setattr(sup_daemon, "_daemon_url", None)


@pytest.fixture
def fake_daemon(monkeypatch):
    """Install a request handler as the daemon. Returns a list of seen requests.

    The handler may return an httpx.Response or raise (e.g. httpx.ConnectError)
    to simulate a daemon that is not running.
    """
    def install(handler):
        seen = []

        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        real_client = httpx.Client
        monkeypatch.setattr(
            sup_daemon.httpx, "Client",
            lambda **kw: real_client(transport=httpx.MockTransport(wrapped), **kw),
        )
        return seen

    return install
