"""Nullprint MCP server — an open-protocol tool layer over the local daemon.

MCP is an open protocol, so any MCP client (Claude Code, OpenAI Codex, Kimi,
...) drives Nullprint identities with the same server. This package is a thin
HTTP client: the daemon owns all behavior, every tool maps onto daemon
endpoints and passes daemon errors through as readable messages.

Run it as `nullprint-mcp` (stdio transport).
"""

__all__ = ["daemon", "tools", "server"]
