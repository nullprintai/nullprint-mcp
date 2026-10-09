"""D3 tools: profile_delete (soft) and profile_restore.

Permanent deletion (DELETE /trash/{name}) is deliberately NOT exposed as a
tool — see test_no_tool_can_permanently_destroy_an_identity.
"""
import httpx
import pytest

from nullprint_mcp import tools
from nullprint_mcp.daemon import DaemonError

RESTORED = {"name": "dir-01", "engine": "chromium", "created": "2026-09-02T09:00:00+08:00"}


def _unreachable(req):
    raise httpx.ConnectError("Connection refused", request=req)


# --- profile_delete --------------------------------------------------------

def test_profile_delete_soft_deletes_and_says_it_is_recoverable(fake_daemon):
    seen = fake_daemon(lambda req: httpx.Response(204))
    out = tools.profile_delete(" dir-01 ")
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/profiles/dir-01"
    assert out["name"] == "dir-01"
    assert out["deleted"] is True
    assert out["recoverable"] is True
    assert "profile_restore" in out["note"]


def test_profile_delete_never_touches_the_trash_endpoint(fake_daemon):
    """The purge path must not be reachable from this tool, even accidentally."""
    seen = fake_daemon(lambda req: httpx.Response(204))
    tools.profile_delete("dir-01")
    assert all(not r.url.path.startswith("/trash") for r in seen)


def test_profile_delete_refuses_a_running_identity(fake_daemon):
    fake_daemon(lambda req: httpx.Response(
        409, json={"detail": "profile is running, close it first: bing-us"}))
    with pytest.raises(DaemonError) as e:
        tools.profile_delete("bing-us")
    assert "409" in str(e.value)
    assert "close it first" in str(e.value)


def test_profile_delete_unknown_identity_is_readable(fake_daemon):
    fake_daemon(lambda req: httpx.Response(404, json={"detail": "no such profile: ghost"}))
    with pytest.raises(DaemonError) as e:
        tools.profile_delete("ghost")
    assert "404" in str(e.value) and "no such profile: ghost" in str(e.value)


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "..", None])
def test_profile_delete_rejects_bad_names_before_calling_daemon(fake_daemon, bad):
    seen = fake_daemon(lambda req: httpx.Response(204))
    with pytest.raises(ValueError):
        tools.profile_delete(bad)
    assert seen == []


def test_profile_delete_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.profile_delete("dir-01")
    assert "cannot reach the Nullprint daemon" in str(e.value)


# --- trash_list ------------------------------------------------------------

TRASHED = [
    {"name": "dir-01", "engine": "chromium", "os": "macos",
     "created": "2026-09-02T09:00:00+08:00", "deleted_at": "2026-09-21T18:00:00+08:00"},
    {"name": "old-02", "engine": "firefox", "os": "macos",
     "created": "2026-08-11T09:00:00+08:00", "deleted_at": "2026-09-20T10:00:00+08:00"},
]


def test_trash_list_returns_what_can_be_restored(fake_daemon):
    seen = fake_daemon(lambda req: httpx.Response(200, json=TRASHED))
    assert tools.trash_list() == TRASHED
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/trash"


def test_trash_list_is_read_only(fake_daemon):
    """It must never mutate — a discovery tool that deletes would be a trap."""
    seen = fake_daemon(lambda req: httpx.Response(200, json=TRASHED))
    tools.trash_list()
    assert [r.method for r in seen] == ["GET"]


def test_trash_list_empty_bin(fake_daemon):
    fake_daemon(lambda req: httpx.Response(200, json=[]))
    assert tools.trash_list() == []


def test_trash_list_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.trash_list()
    assert "cannot reach the Nullprint daemon" in str(e.value)


# --- profile_restore -------------------------------------------------------

def test_profile_restore_brings_the_identity_back(fake_daemon):
    seen = fake_daemon(lambda req: httpx.Response(200, json=RESTORED))
    assert tools.profile_restore(" dir-01 ") == RESTORED
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/trash/dir-01/restore"


def test_profile_restore_not_in_trash_is_readable(fake_daemon):
    fake_daemon(lambda req: httpx.Response(404, json={"detail": "not in trash: ghost"}))
    with pytest.raises(DaemonError) as e:
        tools.profile_restore("ghost")
    assert "404" in str(e.value) and "not in trash: ghost" in str(e.value)


def test_profile_restore_name_collision_is_readable(fake_daemon):
    fake_daemon(lambda req: httpx.Response(409, json={
        "detail": "profile already exists: dir-01 (rename or delete it first)"}))
    with pytest.raises(DaemonError) as e:
        tools.profile_restore("dir-01")
    assert "409" in str(e.value)
    assert "rename or delete it first" in str(e.value)


@pytest.mark.parametrize("bad", ["", "   ", "a/b", None])
def test_profile_restore_rejects_bad_names_before_calling_daemon(fake_daemon, bad):
    seen = fake_daemon(lambda req: httpx.Response(200, json=RESTORED))
    with pytest.raises(ValueError):
        tools.profile_restore(bad)
    assert seen == []


def test_profile_restore_daemon_unreachable(fake_daemon):
    fake_daemon(_unreachable)
    with pytest.raises(DaemonError) as e:
        tools.profile_restore("dir-01")
    assert "cannot reach the Nullprint daemon" in str(e.value)


# --- the product decision, pinned -----------------------------------------

def test_no_tool_can_permanently_destroy_an_identity():
    """Product decision: irreversible destruction is not given an AI tool face.
    If someone later wires DELETE /trash/{name} into TOOLS, this fails."""
    import inspect

    from nullprint_mcp import tools as t

    for fn in t.TOOLS:
        src = inspect.getsource(fn)
        assert 'request("DELETE", f"/trash' not in src, f"{fn.__name__} purges the recycle bin"
        assert '"/trash/{clean}"' not in src, f"{fn.__name__} purges the recycle bin"
