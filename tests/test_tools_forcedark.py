"""0e13(老板 10-19):AI 控制时也能设「强制网页深色」—— profile_create 带 force_dark,新工具 profile_settings 改已有身份的 launch.forceDark
(先 GET 再合并 PATCH,不覆盖别的启动设置)。"""
import json

import httpx

from nullprint_mcp import tools


def _handler(seen_bodies, launch=None):
    def h(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/profiles":
            return httpx.Response(201, json={"name": json.loads(request.content)["name"], "os": "macos", "created": "2026-10-19"})
        if request.method == "GET" and request.url.path == "/profiles/shop-1":
            return httpx.Response(200, json={"name": "shop-1", "engine": "chromium", "meta": {"launch": launch or {}}})
        if request.method == "PATCH" and request.url.path == "/profiles/shop-1":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "nope"})
    return h


def test_profile_create_passes_force_dark_as_a_launch_setting(fake_daemon):
    seen = fake_daemon(_handler([]))
    tools.profile_create("dark-1", force_dark=True)
    assert json.loads(seen[-1].content) == {"name": "dark-1", "launch": {"forceDark": True}}
    tools.profile_create("plain-1")
    assert "launch" not in json.loads(seen[-1].content)                       # 不传 = 默认关,什么都不带


def test_profile_settings_merges_force_dark_into_existing_launch(fake_daemon):
    seen = fake_daemon(_handler([], launch={"colorScheme": "light", "blockImages": True}))
    out = tools.profile_settings("shop-1", force_dark=True)
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/profiles/shop-1"), ("PATCH", "/profiles/shop-1")]
    assert json.loads(seen[-1].content) == {"launch": {"colorScheme": "light", "blockImages": True, "forceDark": True}}   # 别的设置原样保留
    assert out["launch"]["forceDark"] is True
    tools.profile_settings("shop-1", force_dark=False)
    assert json.loads(seen[-1].content)["launch"]["forceDark"] is False
