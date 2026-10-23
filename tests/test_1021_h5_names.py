"""安全体检 H-5(2026-10-21):名字参数不能拼出别的 daemon 接口。

以前只拒绝字面的 '/' 和 '.' / '..';'%2F'、'?'、'#' 能过,名字原样拼进 URL,
uvicorn 解码后路由到 MCP 故意不开放的接口(如 /profiles/{n}/cookies/export)。
现在按 engine 的 _check_name 做白名单,另拒 % ? # \\;拼 URL 时整段转义。"""
import httpx
import pytest

from nullprint_mcp import tools


@pytest.mark.parametrize("bad", [
    "a%2F..%2Fsettings", "victim%2Fcookies%2Fexport?", "victim?x=1", "victim#frag",
    "a\\b", ".hidden", "a:b", "a*b", 'a"b', "a<b", "a>b", "a|b", "a\x01b", "CON", "lpt1.txt", "x" * 121,
])
def test_1021_h5_bad_names_never_reach_the_daemon(fake_daemon, bad):
    seen = fake_daemon(lambda req: httpx.Response(200, json={}))
    for call in (lambda: tools.profile_checkup(bad), lambda: tools.profile_delete(bad),
                 lambda: tools.session_snapshot(bad), lambda: tools.profile_start(bad)):
        with pytest.raises(ValueError):
            call()
    assert seen == []


def test_1021_h5_name_is_one_escaped_path_segment(fake_daemon):
    seen = fake_daemon(lambda req: httpx.Response(200, json={"items": []}))
    tools.profile_checkup("my shop 1")
    assert seen[0].url.raw_path.decode().startswith("/profiles/my%20shop%201/checkup")
