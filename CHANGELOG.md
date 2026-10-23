# Changelog

Both builds of nullprint-mcp (Python on PyPI, Node on npm) ship the same tools and the
same version number.

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
