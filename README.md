# nullprint-mcp

An [MCP](https://modelcontextprotocol.io) server for [Nullprint](https://nullprint.ai), the multi-profile browser. It lets any MCP-capable AI agent (Claude Code, Claude Desktop, OpenAI Codex, Kimi, …) create browser profiles, launch them, run consistency check-ups and drive the pages inside a running profile.

The server is a thin layer over the local Nullprint daemon's HTTP API: every tool maps to one daemon endpoint. The daemon, the browser kernels and the fingerprint engine ship with the Nullprint desktop app and are not part of this package.

## Requirements

- Nullprint desktop app installed and running (Windows or macOS), signed in. Download: https://nullprint.ai
- Python 3.11+ on the same machine. The easiest way to run the server is [uv](https://docs.astral.sh/uv/), which installs the package on first use.

## Install

```bash
# Claude Code
claude mcp add nullprint -- uvx nullprint-mcp

# OpenAI Codex
codex mcp add nullprint -- uvx nullprint-mcp
```

Claude Desktop, Kimi and other clients that use an `mcpServers` config file:

```json
{
  "mcpServers": {
    "nullprint": {
      "command": "uvx",
      "args": ["nullprint-mcp"]
    }
  }
}
```

Without uv: `pip install nullprint-mcp`, then use `nullprint-mcp` as the command (no args).

No port or key to configure: the server reads the running daemon's port and API key from `~/.nullprint/daemon.json`, which the desktop app writes on start-up. To point at a daemon elsewhere, pass `--daemon-url http://host:port` (or `--port 7801` for localhost) and set `NULLPRINT_API_KEY`. Resolution order, most explicit first:

1. `--daemon-url` / `--port`
2. `NULLPRINT_DAEMON_URL`
3. `NULLPRINT_PORT`
4. `~/.nullprint/daemon.json` (`NULLPRINT_HOME` changes the directory)

If the daemon is not running the tools say so plainly instead of throwing a connection trace.

## Tools

| Tool | What it does | Daemon endpoint |
|---|---|---|
| `profiles_list` | List every profile with status (running / idle), exit and ledger metadata | `GET /profiles` |
| `profile_start` | Open a browser session for a profile; returns the driving endpoints | `POST /sessions/{name}` |
| `profile_stop` | Stop that profile's browser (session or app-launched window) | `DELETE /sessions/{name}` → falls back to `POST /profiles/{name}/stop` |
| `profile_create` | Create a profile (fingerprint generated and frozen), optionally with proxy, ledger and start pages | `POST /profiles` |
| `profile_checkup` | Consistency check-up, returns `{summary, checks}` | `GET /profiles/{name}/checkup?live=` |
| `proxy_check` | Stateless proxy test: exit IP, country, latency | `POST /proxy-check` |
| `profile_delete` | Soft delete to the recycle bin (recoverable); refused while running | `DELETE /profiles/{name}` |
| `trash_list` | Read-only list of recoverable profiles in the recycle bin | `GET /trash` |
| `profile_restore` | Restore from the recycle bin with fingerprint, proxy and logins intact | `POST /trash/{name}/restore` |
| `session_goto` | Navigate the session's current page | `POST /sessions/{name}/goto` |
| `session_snapshot` | Accessibility tree of the current page (find selectors, read state) | `GET /sessions/{name}/snapshot` |
| `session_click_selector` | Click an element by CSS selector | `POST /sessions/{name}/click_selector` |
| `session_fill_selector` | Fill an input by CSS selector | `POST /sessions/{name}/fill_selector` |
| `session_press` | Send a key (e.g. `Enter`) | `POST /sessions/{name}/press` |
| `session_eval` | Run JavaScript on the page, returns `{ok, result}` | `POST /sessions/{name}/eval` |
| `session_tabs` | List the session's tabs and the active one | `GET /sessions/{name}/tabs` |
| `session_switch_tab` | Bring a tab to the front | `POST /sessions/{name}/switch_tab` |

Notes that matter when writing prompts:

- **`session_*` tools need an open session.** They drive the warm session opened by `profile_start`; calling them on a profile without one returns 409. Selectors are CSS, not XPath or role/name; call `session_snapshot` first when you do not know a selector.
- **`session_eval` returns an envelope.** A page-side JavaScript error comes back as data (`{ok: false, error, error_type}`), not as a tool failure; `result` follows the expression's type.
- **`profile_stop` has two levels.** A profile can be "running" as a warm session or as a window launched from the app. The tool tries the session first and falls back to the app-level stop, which is idempotent for idle profiles. It returns `{name, stopped, closed}`.
- **`profile_create` does not hard-code a kernel.** Leave `engine` empty to use the app's default; unknown values come back as a 422 listing the options.
- **`proxy_check` failures are data.** An unreachable proxy returns `{ok: false, error}`; only an unreachable daemon raises.
- **`profile_start` returns `cdp_endpoint`** (a `ws://` DevTools address) for Chrome kernels, so external tools can attach. A session driven from outside bypasses Nullprint's consistency guards: an outside tool can set a viewport, language or time zone that contradicts the profile's frozen fingerprint and make it detectable. Prefer the `session_*` tools; if you must attach, do not change anything the profile has not declared.

## What is deliberately not exposed

Permanently deleting a profile (`DELETE /trash/{name}`) destroys its logins and cannot be undone. It is not a tool on purpose: emptying the recycle bin is a human action in the desktop app. `profile_delete`'s description tells the agent to say so instead of working around it, and a test scans every tool's source to keep the purge endpoint out. Screenshot-to-disk and file-writing tools are left out for the same reason (local path safety).

## Development

```bash
pip install -e ".[dev]"
pytest tests
nullprint-mcp --help
```

Tools live in `nullprint_mcp/tools.py` (the `TOOLS` tuple); `server.py` and `__main__.py` do not change when a tool is added. Nothing may be printed to stdout: stdout is the MCP channel.

## License

MIT. Copyright (c) 2026 HENGCHENG LLC.

---

## 中文说明

这是 [Nullprint](https://nullprint.ai)（多身份指纹浏览器）的 MCP 服务：Claude Code、Claude Desktop、OpenAI Codex、Kimi 等任何支持 MCP 的 AI 都可以用它建身份、启动浏览器、做一致性体检、驱动身份里的网页。它只是本地 daemon HTTP 接口上的薄薄一层，每个工具对应 daemon 的一个端点；daemon、浏览器内核和指纹引擎随 Nullprint 桌面应用发布，不在本包内。

**前提**：本机装好并登录 Nullprint 桌面应用；本机有 Python 3.11+，推荐用 [uv](https://docs.astral.sh/uv/)（首次运行自动装包）。

**接入**：

```bash
claude mcp add nullprint -- uvx nullprint-mcp      # Claude Code
codex mcp add nullprint -- uvx nullprint-mcp       # OpenAI Codex
```

Claude Desktop、Kimi 等走配置文件的客户端，在 `mcpServers` 里加 `{"command": "uvx", "args": ["nullprint-mcp"]}`。没有 uv 就 `pip install nullprint-mcp`，命令改为 `nullprint-mcp`。

不用配端口和密钥：服务自动读桌面应用写在 `~/.nullprint/daemon.json` 里的端口和 API Key。要连别的机器上的 daemon，传 `--daemon-url` 并设 `NULLPRINT_API_KEY`。

**17 个工具**：`profiles_list`、`profile_start`、`profile_stop`、`profile_create`、`profile_checkup`、`proxy_check`、`profile_delete`、`trash_list`、`profile_restore`，以及 8 个会话工具 `session_goto / snapshot / click_selector / fill_selector / press / eval / tabs / switch_tab`（先 `profile_start` 再用）。彻底删除身份故意不做成工具：清空回收站只能在桌面应用里由人操作。
