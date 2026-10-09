"""The MCP server object: name, instructions, and the tool registry."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import daemon
from .tools import TOOLS

SERVER_NAME = "nullprint"

INSTRUCTIONS = """Nullprint runs browser identities: each one is a separate browser
profile with its own frozen fingerprint, its own exit IP, and its own cookies, so
sites see independent people rather than one machine.

Typical flow: profiles_list to find the identity, profile_start to open its
browser, do the work, profile_stop when finished. Never leave a session open
after you are done — it holds a warm browser with live login state.

Identity names are case-sensitive and come from profiles_list; do not invent them."""


def build_server(daemon_url: str | None = None) -> FastMCP:
    """Construct the server. daemon_url pins the daemon address; None discovers it."""
    if daemon_url is not None:
        daemon.set_daemon_url(daemon_url)
    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool()(fn)
    return server
