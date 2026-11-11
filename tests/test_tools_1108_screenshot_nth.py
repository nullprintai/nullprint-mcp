"""0e160 / 0e161 (xkshi 11-08, found while recording the MCP demo GIF):

- session_screenshot: the agent can SEE the page. PNG by default, optional full_page; capped before it
  reaches the agent's context — shot at CSS pixels (scale=css, so a high-DPI window is not doubled), longest
  side at most 2000 px (the per-image limit the Claude API applies once a request carries many images): a
  bigger full page falls back to the viewport, a bigger viewport is a readable error; an image over the byte
  budget is re-taken as JPEG.
- session_click_selector / session_fill_selector: optional nth (0-based) when a selector matches several
  elements; not passed = not sent (strict mode on the daemon).
"""
import asyncio
import json
import struct

import httpx
import pytest

from nullprint_mcp import daemon as sup_daemon
from nullprint_mcp import tools
from nullprint_mcp.daemon import DaemonError
from nullprint_mcp.server import build_server


def _png(w, h, pad=0):
    """Just enough PNG for the size check: signature + IHDR with the given width / height."""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\0\0\0\0"
            + b"\0" * pad)


def _jpeg(pad=0):
    return b"\xff\xd8\xff\xe0" + b"\0" * pad


def _shots(full=None, viewport=None, jpeg=None):
    """Fake /screenshot: answers by the query (format / full_page)."""
    def handler(req):
        q = req.url.params
        if q.get("format") == "jpeg":
            return httpx.Response(200, content=jpeg, headers={"content-type": "image/jpeg"})
        body = full if q.get("full_page") == "true" else viewport
        return httpx.Response(200, content=body, headers={"content-type": "image/png"})
    return handler


def _info(out):
    return json.loads(out[1])


# --- session_screenshot --------------------------------------------------------

def test_1108_0e160_screenshot_returns_png_of_the_viewport(fake_daemon):
    seen = fake_daemon(_shots(viewport=_png(1280, 720, 100)))
    out = tools.session_screenshot("bing-us")
    assert seen[0].method == "GET" and seen[0].url.path == "/sessions/bing-us/screenshot"
    assert seen[0].url.params["full_page"] == "false" and seen[0].url.params["format"] == "png"
    assert seen[0].url.params["scale"] == "css"
    img = out[0]
    assert img.data == _png(1280, 720, 100) and img._mime_type == "image/png"
    info = _info(out)
    assert info["format"] == "png" and (info["width"], info["height"]) == (1280, 720)
    assert info["full_page"] is False and "note" not in info


def test_1108_0e160_screenshot_full_page(fake_daemon):
    seen = fake_daemon(_shots(full=_png(1280, 1900, 100)))
    out = tools.session_screenshot("bing-us", full_page=True)
    assert seen[0].url.params["full_page"] == "true" and len(seen) == 1
    assert _info(out)["full_page"] is True and _info(out)["height"] == 1900


def test_1108_0e160_full_page_too_tall_falls_back_to_viewport(fake_daemon):
    seen = fake_daemon(_shots(full=_png(1280, 2001, 100), viewport=_png(1280, 720, 100)))
    out = tools.session_screenshot("bing-us", full_page=True)
    assert [r.url.params["full_page"] for r in seen] == ["true", "false"]
    info = _info(out)
    assert info["full_page"] is False and info["height"] == 720
    assert "1280x2001" in info["note"] and "viewport" in info["note"]
    assert all(r.url.params["scale"] == "css" for r in seen)


def test_1108_0e160_viewport_over_the_limit_is_a_readable_error(fake_daemon):
    """A viewport over 2000 px (a huge window, or an older app that shoots at screen pixels on high-DPI
    displays and ignores scale=css): nothing is returned that would make the API reject the turn."""
    seen = fake_daemon(_shots(viewport=_png(2880, 1800, 100), full=_png(2880, 9000, 100)))
    for full in (False, True):
        with pytest.raises(ValueError) as e:
            tools.session_screenshot("bing-us", full_page=full)
        msg = str(e.value)
        assert "2880x1800" in msg and "2000" in msg and "smaller" in msg and "0.2.31" in msg


def test_1108_0e160_oversized_png_is_retaken_as_jpeg(fake_daemon):
    big = _png(1280, 1800, tools.SCREENSHOT_MAX_BYTES)
    seen = fake_daemon(_shots(full=big, jpeg=_jpeg(1000)))
    out = tools.session_screenshot("bing-us", full_page=True)
    assert seen[-1].url.params["format"] == "jpeg" and seen[-1].url.params["full_page"] == "true"
    assert seen[-1].url.params["scale"] == "css"
    assert int(seen[-1].url.params["quality"]) <= 80
    assert out[0]._mime_type == "image/jpeg" and out[0].data == _jpeg(1000)
    info = _info(out)
    assert info["format"] == "jpeg" and (info["width"], info["height"]) == (1280, 1800)
    assert "JPEG" in info["note"]


def test_1108_0e160_still_too_big_as_jpeg_is_a_readable_error(fake_daemon):
    fake_daemon(_shots(full=_png(1280, 1800, tools.SCREENSHOT_MAX_BYTES),
                       jpeg=_jpeg(tools.SCREENSHOT_MAX_BYTES)))
    with pytest.raises(ValueError) as e:
        tools.session_screenshot("bing-us", full_page=True)
    assert "too large" in str(e.value) and "full_page" in str(e.value)


def test_1108_0e160_screenshot_session_not_open(fake_daemon):
    fake_daemon(lambda req: httpx.Response(409, json={"detail": "session not open: bing-us"}))
    with pytest.raises(DaemonError) as e:
        tools.session_screenshot("bing-us")
    assert "409" in str(e.value) and "session not open" in str(e.value)


def test_1108_0e160_server_returns_image_content(fake_daemon):
    """Through the MCP server: an image block the client can show, plus the info as text."""
    fake_daemon(_shots(viewport=_png(1280, 720, 10)))
    server = build_server("http://127.0.0.1:7801")
    try:
        res = asyncio.run(server.call_tool("session_screenshot", {"name": "bing-us"}))
    finally:
        sup_daemon.set_daemon_url(None)
    blocks = res[0] if isinstance(res, tuple) else res
    kinds = [b.type for b in blocks]
    assert kinds == ["image", "text"]
    assert blocks[0].mimeType == "image/png"
    assert json.loads(blocks[1].text)["width"] == 1280


# --- nth on the selector tools ----------------------------------------------------

def test_1108_0e161_click_and_fill_pass_nth_only_when_given(fake_daemon):
    seen = fake_daemon(lambda req: httpx.Response(200, json={"ok": True}))
    tools.session_click_selector("bing-us", "button.b")
    tools.session_click_selector("bing-us", "button.b", nth=1)
    tools.session_fill_selector("bing-us", "input[name=search]", "x")
    tools.session_fill_selector("bing-us", "input[name=search]", "x", nth=0)
    bodies = [json.loads(r.content) for r in seen]
    assert "nth" not in bodies[0] and bodies[1]["nth"] == 1
    assert "nth" not in bodies[2] and bodies[3]["nth"] == 0


def test_1108_0e161_negative_nth_rejected_locally(fake_daemon):
    seen = fake_daemon(lambda req: httpx.Response(200, json={"ok": True}))
    with pytest.raises(ValueError):
        tools.session_fill_selector("bing-us", "input", "x", nth=-1)
    with pytest.raises(ValueError):
        tools.session_click_selector("bing-us", "button", nth=-2)
    assert seen == []


def test_1108_0e161_daemon_selector_errors_reach_the_agent(fake_daemon):
    """Review 1: the daemon's "matched N elements" / "matched only N" text must reach the agent, not just the status."""
    many = "selector 'b' matched 2 elements; make it more specific, or pass nth (0-based) to pick one of them"
    short = "selector 'b' matched only 2 elements; nth=5 is out of range (0-based, use 0 to 1)"
    fake_daemon(lambda req: httpx.Response(502, json={
        "detail": "SelectorMatchError: " + (short if "nth" in json.loads(req.content) else many)}))
    for nth, text in ((None, many), (5, short)):
        with pytest.raises(DaemonError) as e:
            tools.session_click_selector("bing-us", "b", nth=nth)
        assert text in str(e.value) and "502" in str(e.value)
