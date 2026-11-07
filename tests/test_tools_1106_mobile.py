"""0e106 / 0e107(老板 11-06 批):profile_create 能建手机身份 —— os / mobile_model 原样传给 POST /profiles,不传就不带。"""
import json

from test_tools_forcedark import _handler

from nullprint_mcp import tools


def test_profile_create_passes_os_and_mobile_model(fake_daemon):
    seen = fake_daemon(_handler([]))
    tools.profile_create("phone-1", engine="chromium", os="android", mobile_model="redmi-note-8-pro-a11")
    assert json.loads(seen[-1].content) == {"name": "phone-1", "engine": "chromium", "os": "android",
                                            "mobile_model": "redmi-note-8-pro-a11"}
    tools.profile_create("plain-1")
    assert json.loads(seen[-1].content) == {"name": "plain-1"}
