"""Entry point: `nullprint-mcp` (also `python -m nullprint_mcp`), stdio transport.

Nothing may be printed to stdout — stdout IS the MCP channel.
"""
from __future__ import annotations

import argparse

from .daemon import DEFAULT_HOST
from .server import build_server


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="nullprint-mcp",
        description="Nullprint MCP server (stdio) — drive browser identities from any MCP client.",
    )
    p.add_argument(
        "--daemon-url",
        help="full daemon base URL, e.g. http://127.0.0.1:7801. "
             "Default: read the running daemon's port from ~/.nullprint/daemon.json.",
    )
    p.add_argument("--port", type=int, help=f"shorthand for --daemon-url http://{DEFAULT_HOST}:<port>")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"host to use with --port (default {DEFAULT_HOST})")
    args = p.parse_args(argv)
    if args.daemon_url and args.port:
        p.error("pass either --daemon-url or --port, not both")
    if args.port is not None:
        args.daemon_url = f"http://{args.host}:{args.port}"
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    build_server(args.daemon_url).run(transport="stdio")


if __name__ == "__main__":
    main()
