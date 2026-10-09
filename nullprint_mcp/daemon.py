"""HTTP plumbing for the daemon: where it lives, and how its errors read.

The daemon binds a RANDOM free port by default and records it in
`<runtime dir>/daemon.json` (`NULLPRINT_PORT` pins it instead). There is no
fixed default port to hard-code, so the address is resolved per call — that
also means a daemon restart on a new port is picked up without restarting this
server.
"""
from __future__ import annotations

import json
import os
import pathlib

import httpx

DEFAULT_HOST = "127.0.0.1"
TIMEOUT = 130            # a headed launch can take a while; same budget the CLI uses

_daemon_url: str | None = None   # set by --daemon-url / --port at startup


class DaemonError(RuntimeError):
    """Anything that stops a tool from answering: daemon down, or daemon said no.

    The message is written for whoever reads the tool result — a human or an
    AI client — so it names the address, the status, and what to do next.
    `status` is the HTTP status when the daemon answered, None when it did not,
    so a tool can branch on it (profile_stop falls back on 404/409).
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_daemon_url(url: str | None) -> None:
    """Pin the daemon address (from --daemon-url / --port). None restores discovery."""
    global _daemon_url
    _daemon_url = url.rstrip("/") if url else None


def runtime_dir() -> pathlib.Path:
    """NULLPRINT_HOME if set, else ~/.nullprint (where the desktop app keeps daemon.json)."""
    home = os.environ.get("NULLPRINT_HOME")
    if home:
        return pathlib.Path(home).expanduser()
    return pathlib.Path.home() / ".nullprint"


def base_url() -> str:
    """Resolve the daemon's base URL, most explicit source first.

    1. `--daemon-url` / `--port` (set_daemon_url)
    2. `NULLPRINT_DAEMON_URL` (`SUPLOGIN_DAEMON_URL` still honoured for old setups)
    3. `NULLPRINT_PORT` (the daemon's own port pin)
    4. `<runtime dir>/daemon.json`, which the running daemon writes
    """
    if _daemon_url:
        return _daemon_url
    env_url = os.environ.get("NULLPRINT_DAEMON_URL") or os.environ.get("SUPLOGIN_DAEMON_URL")
    if env_url:
        return env_url.rstrip("/")
    env_port = os.environ.get("NULLPRINT_PORT")
    if env_port:
        return f"http://{DEFAULT_HOST}:{env_port}"

    path = runtime_dir() / "daemon.json"
    try:
        info = json.loads(path.read_text())
    except OSError:
        raise DaemonError(
            f"the Nullprint daemon does not look like it is running: no {path}. "
            "Start the daemon (the desktop app starts it for you), or point this "
            "server at one with --daemon-url http://127.0.0.1:<port>."
        ) from None
    except ValueError:
        raise DaemonError(
            f"{path} is not valid JSON — the daemon may have been killed mid-write. "
            "Restart the daemon, or pass --daemon-url."
        ) from None
    port = info.get("port")
    if not port:
        raise DaemonError(f"{path} has no 'port' field; restart the daemon or pass --daemon-url.")
    return f"http://{DEFAULT_HOST}:{port}"


def api_key() -> str | None:
    """daemon.json 里的 api_key;NULLPRINT_API_KEY 可覆盖(配合 --daemon-url 指向别的 daemon)。"""
    env = os.environ.get("NULLPRINT_API_KEY")
    if env:
        return env
    try:
        return json.loads((runtime_dir() / "daemon.json").read_text()).get("api_key")
    except (OSError, ValueError):
        return None


_STATUS_HINT = {
    401: "API key missing or wrong — the daemon.json api_key is read automatically; "
         "set NULLPRINT_API_KEY when using --daemon-url",
    403: "not authorized (license invalid or profile quota reached)",
    404: "not found",
    409: "conflict — it is already in that state, or busy",
    422: "the daemon rejected the arguments",
    429: "too many warm sessions; stop one first",
    502: "the browser worker failed to answer",
    503: "the daemon could not reach its backend",
}


def _license_text(d: dict) -> str:
    """The license/quota 403 carries a {reason, limit, used} dict, not a sentence.
    Say it in words — an AI client that reads 'quota_exceeded' should not have to
    guess whether deleting something would help."""
    used, limit = d.get("used"), d.get("limit")
    counts = f" ({used} of {limit} identities used)" if None not in (used, limit) else ""
    if d.get("reason") == "quota_exceeded":
        return ("profile quota reached" + counts +
                " — delete an identity you no longer need, or raise the plan's limit.")
    return (f"this installation is not authorized (license state: {d.get('reason')})"
            + counts + ". Authorize it from the desktop app, then retry.")


def _detail(resp: httpx.Response) -> str:
    """Pull the readable part out of a FastAPI error body."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:400] or "(empty response body)"
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict) and detail.get("reason") in ("api_key_required", "invalid_api_key"):
        return detail["reason"]           # 401 的说明已在 _STATUS_HINT 里,别当成授权问题
    if isinstance(detail, dict) and "reason" in detail:
        return _license_text(detail)
    return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)


def request(method: str, path: str, **kw) -> httpx.Response:
    """Call the daemon. Raises DaemonError with a readable message on any failure."""
    url = base_url()
    headers = dict(kw.pop("headers", {}) or {})
    k = api_key()
    if k:
        headers["X-API-Key"] = k
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.request(method, f"{url}{path}", headers=headers, **kw)
    except httpx.HTTPError as e:
        raise DaemonError(
            f"cannot reach the Nullprint daemon at {url} ({type(e).__name__}: {e}). "
            "Check that it is running, or pass --daemon-url with the right address."
        ) from None
    if resp.is_success:
        return resp
    hint = _STATUS_HINT.get(resp.status_code, "the daemon refused the request")
    raise DaemonError(
        f"daemon {method} {path} -> {resp.status_code} ({hint}): {_detail(resp)}",
        status=resp.status_code,
    )
