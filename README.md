# nullprint-mcp

An [MCP](https://modelcontextprotocol.io) server for [Nullprint](https://nullprint.ai), the AI antidetect browser. It lets any MCP-capable AI agent (Claude Code, Claude Desktop, OpenAI Codex, Kimi, …) create browser profiles, launch them, run consistency check-ups and drive the pages inside a running profile.

<!-- mcp-name: io.github.nullprintai/nullprint-mcp -->

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
| `profile_settings` | Change a display setting of an existing profile — today `force_dark` (force dark web pages, off by default; merged into its launch settings) | `GET` + `PATCH /profiles/{name}` |
| `profile_checkup` | Consistency check-up, returns `{summary, checks}` | `GET /profiles/{name}/checkup?live=` |
| `proxy_check` | Stateless proxy test: exit IP, country, latency | `POST /proxy-check` |
| `profile_delete` | Soft delete to the recycle bin (recoverable); refused while running | `DELETE /profiles/{name}` |
| `trash_list` | Read-only list of recoverable profiles in the recycle bin | `GET /trash` |
| `profile_restore` | Restore from the recycle bin with fingerprint, proxy and logins intact | `POST /trash/{name}/restore` |
| `session_goto` | Navigate the session's current page | `POST /sessions/{name}/goto` |
| `session_snapshot` | Accessibility tree of the current page (find selectors, read state) | `GET /sessions/{name}/snapshot` |
| `session_screenshot` | Screenshot of the current page as an image (`full_page` optional; size-capped) | `GET /sessions/{name}/screenshot` |
| `session_click_selector` | Click an element by CSS selector (`nth` picks one of several matches) | `POST /sessions/{name}/click_selector` |
| `session_fill_selector` | Fill an input by CSS selector (`nth` picks one of several matches) | `POST /sessions/{name}/fill_selector` |
| `session_press` | Send a key (e.g. `Enter`) | `POST /sessions/{name}/press` |
| `session_eval` | Run JavaScript on the page, returns `{ok, result}` | `POST /sessions/{name}/eval` |
| `session_tabs` | List the session's tabs and the active one | `GET /sessions/{name}/tabs` |
| `session_switch_tab` | Bring a tab to the front | `POST /sessions/{name}/switch_tab` |
| `session_dialog` | Answer the JavaScript dialog (alert / confirm / prompt / beforeunload) open on the page: `accept` or `dismiss` | `POST /sessions/{name}/dialog` |

Notes that matter when writing prompts:

- **`session_*` tools need an open session.** They drive the warm session opened by `profile_start`; calling them on a profile without one returns 409. Selectors are CSS, not XPath or role/name; call `session_snapshot` first when you do not know a selector.
- **Selectors must match one element.** If a selector matches several, `session_click_selector` / `session_fill_selector` fail with "matched N elements"; write a more specific selector or pass `nth` (0-based).
- **`session_screenshot` is size-capped and needs Nullprint 0.2.31+.** Shots are in CSS pixels (older apps shoot at screen pixels, doubled on high-DPI displays), at most 2000 px on the longest side: a bigger full page comes back as the viewport, a bigger viewport is an error asking for a smaller window, and a PNG over 1 MB comes back as JPEG; the returned info says so. Use `session_snapshot` to read text, the screenshot to see layout.
- **Dialogs are left open, as in a normal browser.** While an alert / confirm / prompt is open the page is blocked and `session_*` tools fail with 409 naming the dialog; answer it with `session_dialog`, then retry. A beforeunload caused by your own `session_goto` or click is accepted automatically.
- **`session_eval` returns an envelope.** A page-side JavaScript error comes back as data (`{ok: false, error, error_type}`), not as a tool failure; `result` follows the expression's type.
- **`profile_stop` has two levels.** A profile can be "running" as a warm session or as a window launched from the app. The tool tries the session first and falls back to the app-level stop, which is idempotent for idle profiles. It returns `{name, stopped, closed}`.
- **`profile_create` does not hard-code a kernel.** Leave `engine` empty to use the app's default; unknown values come back as a 422 listing the options.
- **`proxy_check` failures are data.** An unreachable proxy returns `{ok: false, error}`; only an unreachable daemon raises.
- **`profile_start` returns `cdp_endpoint`** (a `ws://` DevTools address) for Chrome kernels with external debugging turned on in the app (off by default; Edit → Advanced → External debugging, needs a second verification, then reopen the profile), so external tools can attach. Otherwise it is `null`, with `cdp_unavailable` (reason code) and `cdp_note` saying why. A session driven from outside bypasses Nullprint's consistency guards: an outside tool can set a viewport, language or time zone that contradicts the profile's frozen fingerprint and make it detectable. Prefer the `session_*` tools; if you must attach, do not change anything the profile has not declared.

## Give the agent the Nullprint tools, not your shell

This server reads the daemon's port and API key from `~/.nullprint/daemon.json`. Any program running as the same OS user can read that file too, including an AI agent that is allowed to run shell commands or read files. With the key it can call the daemon's HTTP API directly, outside the tools listed here (and outside what this server deliberately leaves out). We saw it happen while recording our own demo: when the agent could not do what it wanted with the tools, it read `daemon.json` on its own and sent requests with that key. When an agent drives Nullprint, allow only the Nullprint tools (in Claude Code, the `mcp__nullprint__*` tools) and keep shell and file-reading tools off, or approve each such call yourself.

## What is deliberately not exposed

Permanently deleting a profile (`DELETE /trash/{name}`) destroys its logins and cannot be undone. It is not a tool on purpose: emptying the recycle bin is a human action in the desktop app. `profile_delete`'s description tells the agent to say so instead of working around it, and a test scans every tool's source to keep the purge endpoint out. Screenshot-to-disk and file-writing tools are left out for the same reason (local path safety); `session_screenshot` returns the image inline and writes nothing.

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

这是 [Nullprint](https://nullprint.ai)（AI 指纹浏览器）的 MCP 服务：Claude Code、Claude Desktop、OpenAI Codex、Kimi 等任何支持 MCP 的 AI 都可以用它建身份、启动浏览器、做一致性体检、驱动身份里的网页。它只是本地 daemon HTTP 接口上的薄薄一层，每个工具对应 daemon 的一个端点；daemon、浏览器内核和指纹引擎随 Nullprint 桌面应用发布，不在本包内。

**前提**：本机装好并登录 Nullprint 桌面应用；本机有 Python 3.11+，推荐用 [uv](https://docs.astral.sh/uv/)（首次运行自动装包）。

**接入**：

```bash
claude mcp add nullprint -- uvx nullprint-mcp      # Claude Code
codex mcp add nullprint -- uvx nullprint-mcp       # OpenAI Codex
```

Claude Desktop、Kimi 等走配置文件的客户端，在 `mcpServers` 里加 `{"command": "uvx", "args": ["nullprint-mcp"]}`。没有 uv 就 `pip install nullprint-mcp`，命令改为 `nullprint-mcp`。

不用配端口和密钥：服务自动读桌面应用写在 `~/.nullprint/daemon.json` 里的端口和 API Key。要连别的机器上的 daemon，传 `--daemon-url` 并设 `NULLPRINT_API_KEY`。

**20 个工具**：`profiles_list`、`profile_start`、`profile_stop`、`profile_create`、`profile_settings`、`profile_checkup`、`proxy_check`、`profile_delete`、`trash_list`、`profile_restore`，以及 10 个会话工具 `session_goto / snapshot / screenshot / click_selector / fill_selector / press / eval / tabs / switch_tab / dialog`（先 `profile_start` 再用）。`session_screenshot` 直接回图片（需要 Nullprint 0.2.31 及以上）（可整页，按 CSS 像素、长边不超过 2000 px：整页超了退回可视区域，可视区域超了报错请你把窗口改小，超过 1 MB 改 JPEG，不落盘）；选择器匹配到多个元素时报「matched N elements」，写得更具体或传 `nth`（从 0 数）。彻底删除身份故意不做成工具：清空回收站只能在桌面应用里由人操作。

**只把 Nullprint 工具放行给 AI**：本服务从 `~/.nullprint/daemon.json` 读 daemon 的端口和 API Key；MCP 客户端和 daemon 跑在同一个系统用户下时，这个文件同一用户的任何程序都能读到，包括被允许跑命令、读文件的 AI。拿到 Key 就能绕过这里的工具直接调 daemon 的 HTTP 接口（包括本服务故意不开放的）。我们自己录演示时就遇到过：AI 用工具做不到想做的事，自己去读了 `daemon.json`，拿 Key 发请求。让 AI 操作 Nullprint 时，只放行 Nullprint 的工具（Claude Code 里是 `mcp__nullprint__*`），命令行和读文件类工具关掉，或者每次由你自己批准。
