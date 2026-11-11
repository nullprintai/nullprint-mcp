# Changelog

Both builds of nullprint-mcp (Python on PyPI, Node on npm) ship the same tools and the
same version number.

## 0.1.7 — unreleased

Needs Nullprint 0.2.31 or later. `session_screenshot` also runs on 0.2.18–0.2.30, but those versions shoot at
screen pixels, so on a high-DPI display a normal window goes over the size limit (the error says to update the
app to 0.2.31 or make the window smaller). Older versions ignore `nth`, so a selector that matches several
elements still fails.

- **New tool `session_screenshot`.** Returns the session's current page as an image (PNG; `full_page` for the
  whole scrollable page) plus `{format, width, height, full_page, bytes, note?}`. Size is capped before it
  reaches the agent: taken in CSS pixels, at most 2000 px on the longest side (the per-image limit the Claude
  API applies once a conversation carries many images). A bigger full page falls back to the viewport, a
  bigger viewport is an error asking for a smaller window, and a PNG over 1 MB is re-taken as JPEG; the note
  says which. Nothing is written to disk.
- **`session_click_selector` / `session_fill_selector` take an optional `nth`** (0-based) to pick one element
  when the selector matches several. Without it the selector must match exactly one; the error now says how
  many matched instead of Playwright's "strict mode violation" text, and an `nth` past the last match fails at
  once with "matched only N elements" instead of waiting for a timeout.
- README: a section on giving the agent only the Nullprint tools — an agent that may run commands or read
  files can read `~/.nullprint/daemon.json` and call the daemon directly.

## 0.1.6 — 2026-11-07

Needs Nullprint 0.2.27 or later for phone identities; everything else works with 0.2.18.

- **Phone identities.** `profile_create` takes `os` (`"windows"`, `"macos"`, `"linux"`, `"android"`, `"ios"`)
  and `mobile_model`. Android and iOS (simulated) identities need the Chrome kernel; without `mobile_model`
  a model is picked from the identity's seed, and if the OS has no default model the call is rejected with
  `mobile_model_required` and the message lists the model ids to choose from.
- **Local daemon connections never go through a proxy.** System proxy settings and `HTTP(S)_PROXY` /
  `ALL_PROXY` are ignored for 127.0.0.1 / localhost / ::1, so a proxy app without a loopback exception no
  longer turns every call into a 503. If the reply still doesn't look like the daemon's, the error says
  a proxy may be intercepting it and to check the proxy exceptions.
- **The daemon is checked before use.** The port in `daemon.json` must answer with this data directory's
  key; if another program holds the port, the error says so instead of sending requests to it. An address
  given explicitly with `--daemon-url`, `NULLPRINT_DAEMON_URL` or `NULLPRINT_PORT` is used as is.
- Package description now says "AI antidetect browser".

## 0.1.5 — 2026-10-23

Needs Nullprint 0.2.18 or later for the new tool; everything else works with 0.2.17.

- **New tool `session_dialog`.** Page dialogs (alert / confirm / prompt) in a session are no longer
  dismissed automatically by the browser driver; they stay open like in a real browser. While one is
  open, other session commands return a 409 `dialog_open` error with the dialog's type and message.
  `session_dialog(name, action="accept"|"dismiss", prompt_text=...)` answers it. "Leave site?" prompts
  caused by the agent's own navigation (`session_goto`, reload, closing the session) are still accepted.
- **`profile_start` explains a missing `cdp_endpoint`.** When the endpoint is empty, the result carries
  `cdp_unavailable` (`external_debug_off`, `external_debug_not_granted`, `not_running`, `starting`,
  `not_supported_for_engine`) and a one-line `cdp_note`, instead of leaving the agent to poll. External
  debug access must be switched on in the profile's settings (it needs a second verification).
- Error messages for 409 responses that carry a `reason` are reported as that reason, not as an
  authorisation problem.

## 0.1.4 — 2026-10-22

Security fixes. No tools were added or removed.

- **Identity names stay a single path segment.** The `name` argument of every tool used to
  reject only a literal `/` and `.` / `..`. Names containing `%2F`, `?` or `#` got through and,
  once the daemon decoded the URL, could reach daemon endpoints this server deliberately does
  not expose (for example cookie export), or turn `profile_delete` into an unrecoverable
  cookie wipe. Names are now checked against the same rules the Nullprint engine uses for
  identity names (no leading `.`, at most 120 characters, none of `/ \ : * ? " < > |`, no
  control characters, no Windows reserved names), plus `%` and `#`, and are escaped as one
  URL path segment (`quote(name, safe="")` / `encodeURIComponent`). A rejected name fails with
  "invalid identity name" and never reaches the daemon.
- **`session_goto` only opens public http(s) pages** (enforced by the Nullprint daemon,
  0.2.17 and later). `file://`, `javascript:`, `chrome://` and other schemes, `localhost`,
  loopback, private-network and link-local addresses are refused with a 422
  `url_not_allowed` error. This stops a prompt-injected page from making the agent read
  local files such as the daemon's API key. The same applies to `profile_create(urls=...)`.
  The daemon also no longer accepts the API key as a `?api_key=` query parameter; this
  server has always sent the `X-API-Key` header, so nothing changes for it.
