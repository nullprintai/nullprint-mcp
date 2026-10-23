# nullprint-mcp (Node build)

An [MCP](https://modelcontextprotocol.io) server for [Nullprint](https://nullprint.ai), the multi-profile browser. It lets any MCP-capable AI agent (Claude Code, Claude Desktop, Cursor, Windsurf, Cline, OpenAI Codex, Kimi, …) create browser profiles, launch them, run consistency check-ups and drive the pages inside a running profile.

This is the Node.js build: a single bundled file with no runtime dependencies, so `npx nullprint-mcp` works wherever Node 18+ is present, with no Python or uv on the machine. The Python build on PyPI (`uvx nullprint-mcp`) exposes the same 18 tools with the same names, arguments and behaviour; pick whichever runtime your client already has.

The server is a thin layer over the local Nullprint daemon's HTTP API: every tool maps to one daemon endpoint. The daemon, the browser kernels and the fingerprint engine ship with the Nullprint desktop app and are not part of this package.

## Requirements

- Nullprint desktop app installed and running (Windows or macOS), signed in. Download: https://nullprint.ai
- Node.js 18 or newer on the same machine.

## Install

```bash
# Claude Code
claude mcp add nullprint -- npx -y nullprint-mcp

# OpenAI Codex
codex mcp add nullprint -- npx -y nullprint-mcp
```

Claude Desktop, Cursor, Windsurf, Cline, Kimi and other clients that use an `mcpServers` config file:

```json
{
  "mcpServers": {
    "nullprint": {
      "command": "npx",
      "args": ["-y", "nullprint-mcp"]
    }
  }
}
```

No port or key to configure: the server reads the running daemon's port and API key from `~/.nullprint/daemon.json`, which the desktop app writes on start-up. To point at a daemon elsewhere, pass `--daemon-url http://host:port` (or `--port 7801` for localhost) and set `NULLPRINT_API_KEY`. Resolution order, most explicit first:

1. `--daemon-url` / `--port`
2. `NULLPRINT_DAEMON_URL`
3. `NULLPRINT_PORT`
4. `~/.nullprint/daemon.json` (`NULLPRINT_HOME` changes the directory)

If the daemon is not running the tools say so plainly instead of throwing a connection trace.

## Tools

| Tool | What it does |
|---|---|
| `profiles_list` | List every profile with status (running / idle), exit and ledger metadata |
| `profile_start` | Open a browser session for a profile; returns the driving endpoints |
| `profile_stop` | Stop that profile's browser (session or app-launched window) |
| `profile_create` | Create a profile (fingerprint generated and frozen), optionally with proxy, ledger and start pages |
| `profile_settings` | Change a display setting of an existing profile — today `force_dark` (force dark web pages, off by default; merged into its launch settings) | `GET` + `PATCH /profiles/{name}` |
| `profile_checkup` | Consistency check-up, returns `{summary, checks}` |
| `proxy_check` | Stateless proxy test: exit IP, country, latency |
| `profile_delete` | Soft delete to the recycle bin (recoverable); refused while running |
| `trash_list` | Read-only list of recoverable profiles in the recycle bin |
| `profile_restore` | Restore from the recycle bin with fingerprint, proxy and logins intact |
| `session_goto` | Navigate the session's current page |
| `session_snapshot` | Accessibility tree of the current page (find selectors, read state) |
| `session_click_selector` | Click an element by CSS selector |
| `session_fill_selector` | Fill an input by CSS selector |
| `session_press` | Send a key (e.g. `Enter`) |
| `session_eval` | Run JavaScript on the page, returns `{ok, result}` |
| `session_tabs` | List the session's tabs and the active one |
| `session_switch_tab` | Bring a tab to the front |
| `session_dialog` | Answer the JavaScript dialog (alert / confirm / prompt / beforeunload) open on the page: `accept` or `dismiss` |

`session_*` tools drive the warm session opened by `profile_start`; on a profile without one they return 409 and tell the agent to start it first. Permanent deletion is deliberately not exposed: emptying the recycle bin is a human action in the desktop app.

## Development

```bash
npm install
npm run typecheck
npm test          # builds dist/index.js, then runs the stdio contract tests against a fake daemon
node dist/index.js --help
```

Nothing may be printed to stdout: stdout is the MCP channel. Diagnostics go to stderr.

## License

MIT. Copyright (c) 2026 HENGCHENG LLC.

---

## 中文说明

这是 [Nullprint](https://nullprint.ai)（多身份指纹浏览器）MCP 服务的 Node 版：单文件、零运行时依赖，只要机器上有 Node 18+，`npx nullprint-mcp` 就能跑，不需要 Python 或 uv。PyPI 上的 Python 版（`uvx nullprint-mcp`）提供同样的 17 个工具，名字、参数、行为完全一致，按你的客户端自带哪个运行时选。

**接入**：

```bash
claude mcp add nullprint -- npx -y nullprint-mcp      # Claude Code
codex mcp add nullprint -- npx -y nullprint-mcp       # OpenAI Codex
```

Claude Desktop、Cursor、Windsurf、Cline、Kimi 等走配置文件的客户端，在 `mcpServers` 里加 `{"command": "npx", "args": ["-y", "nullprint-mcp"]}`。

不用配端口和密钥：服务自动读桌面应用写在 `~/.nullprint/daemon.json` 里的端口和 API Key。要连别的机器上的 daemon，传 `--daemon-url` 并设 `NULLPRINT_API_KEY`。
