// The 17 tools, one per daemon endpoint. Names, arguments, descriptions and behaviour mirror
// mcp/nullprint_mcp/tools.py exactly so an agent sees the same server whichever build it runs.
import { z } from "zod";
import { DaemonError, request } from "./daemon.js";

const CDP_KEYS = ["cdp_endpoint", "ws_endpoint", "cdp_url", "webSocketDebuggerUrl"] as const;
const NO_CDP_NOTE =
  "No CDP endpoint for this identity. Nullprint keeps external debugging off by default: turn it on per identity in the app " +
  "(Edit -> Advanced -> External debugging, needs a second verification), then reopen the identity. The Firefox core never publishes one. " +
  "Without it, drive the browser through the daemon's /sessions/{name}/* commands (goto, snapshot, click, type, screenshot, ...).";

// Same whitelist as the engine's own name check (engine/nullprint.py _check_name), plus % and #:
// a name must stay ONE path segment. '%2F', '?' and '#' used to slip through and, once the daemon
// decoded the URL, reach endpoints this server deliberately does not expose (security audit H-5, 2026-10-21).
const NAME_BAD = new Set('/\\:*?"<>|%#');
const WIN_RESERVED = new Set(["CON", "PRN", "AUX", "NUL", ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((i) => [`COM${i}`, `LPT${i}`])]);

/** Validate an identity name before it becomes a URL path segment. */
export function identityName(name: unknown): string {
  if (typeof name !== "string" || !name.trim()) throw new Error("name is required: the identity's name, e.g. 'bing-us'");
  const clean = name.trim();
  if (
    clean.startsWith(".") || clean.length > 120 ||
    [...clean].some((c) => NAME_BAD.has(c) || c.charCodeAt(0) < 32) ||
    WIN_RESERVED.has(clean.split(".")[0].toUpperCase())
  ) {
    throw new Error(
      `invalid identity name ${JSON.stringify(name)}: it cannot start with '.', be longer than 120 characters, ` +
        `or contain / \\ : * ? " < > | % # or control characters`,
    );
  }
  return clean;
}

function nonEmpty(v: unknown, msg: string): string {
  if (typeof v !== "string" || !v.trim()) throw new Error(msg);
  return v.trim();
}

const NAME = z.string().describe("identity name, exactly as profiles_list reports it");

export interface ToolDef {
  name: string;
  description: string;
  // zod raw shape; {} for no-argument tools
  shape: Record<string, z.ZodTypeAny>;
  handler: (args: Record<string, unknown>) => Promise<unknown>;
}

export const TOOLS: ToolDef[] = [
  {
    name: "profiles_list",
    description: `List every identity in this installation, with its live status.

Each entry carries: name, os, engine (which browser core backs it), created, proxy (the exit server only — never credentials), status ("running" when a browser is open for it, otherwise "idle"), and meta (the local ledger: group, tags, note, last_launched).

Call this first to find the exact name to pass to profile_start / profile_stop; names are case-sensitive.`,
    shape: {},
    handler: () => request("GET", "/profiles"),
  },
  {
    name: "profile_start",
    description: `Open a browser session for an identity and return the endpoint to drive it.

The identity keeps its frozen fingerprint, its exit proxy, and its logged-in cookies — starting it does not re-randomize anything. Starting an identity that already has a session open is a no-op that returns the existing one.

Returns the session record: name, opened_at, idle_seconds, url (the page it is on), plus cdp_endpoint — a ws:// DevTools address you can attach your own automation to. It is only published for Chrome-core identities that have external debugging turned on in the app (off by default; Edit -> Advanced -> External debugging, needs a second verification, then reopen the identity). Otherwise it is null, cdp_unavailable gives the daemon's reason code (external_debug_off, external_debug_not_granted, not_supported_for_engine, starting) and cdp_note says what to do — the session commands work either way.

Prefer the daemon's own commands over attaching to cdp_endpoint. A session driven from outside bypasses Nullprint's consistency guards, so an outside tool can set a viewport, locale, timezone, or header that contradicts the identity's frozen fingerprint and make it detectable. Attach at your own risk, and change nothing the identity did not already claim.

Fails with a readable message if the identity does not exist (404), if too many sessions are already warm (429), or if the daemon is not running.`,
    shape: {
      name: NAME,
      headless: z.boolean().optional().describe("run without a visible window. Default false (headed), which is what sites expect and what you want for anything login-shaped."),
    },
    handler: async (a) => {
      const clean = identityName(a.name);
      const session = (await request("POST", `/sessions/${encodeURIComponent(clean)}`, { json: { headless: Boolean(a.headless) } })) as Record<string, unknown>;
      const out: Record<string, unknown> = { ...session };
      const cdp = CDP_KEYS.map((k) => session[k]).find((v) => v);
      out.cdp_endpoint = cdp ?? null;
      if (!cdp) out.cdp_note = (typeof session.cdp_hint === "string" && session.cdp_hint) || NO_CDP_NOTE;   // BUG-024:daemon 说了原因就用它的
      return out;
    },
  },
  {
    name: "profile_stop",
    description: `Stop an identity's browser, however it was started.

Cookies and login state live in the identity's own storage and survive the stop; whatever was typed into an open form does not. Stop identities you are done with — a running identity holds a warm browser and keeps its login state exposed.

Two things can be running for one identity and this closes either: the warm session profile_start opens (the daemon drives it), and a window the desktop app launched on its own. The session is closed first; if there is none, the identity-level stop is used instead, which also terminates a launched window and is a no-op on an identity that is already idle.

Returns {name, stopped, closed}: \`stopped\` is false when the identity was already idle, and \`closed\` lists what was actually shut down.

Fails with a readable message if the identity does not exist, or if the daemon is not running.`,
    shape: { name: NAME },
    handler: async (a) => {
      const clean = identityName(a.name);
      try {
        await request("DELETE", `/sessions/${encodeURIComponent(clean)}`);
        return { name: clean, stopped: true, closed: ["session"] };
      } catch (e) {
        // 409 "session not open" is the usual answer for an identity launched as a window; 404 allowed too.
        // Anything else (daemon down, 422, ...) is a real failure and must not be swallowed.
        if (!(e instanceof DaemonError) || (e.status !== 404 && e.status !== 409)) throw e;
      }
      const result = (await request("POST", `/profiles/${encodeURIComponent(clean)}/stop`)) as Record<string, unknown>;
      const closed = (result.stopped as unknown[]) || [];
      return { name: clean, stopped: closed.length > 0, closed, status: result.status };
    },
  },
  {
    name: "profile_create",
    description: `Create a new identity. Its fingerprint is generated and frozen on the spot.

You do not choose the fingerprint — the point of the product is that every identity gets one self-consistent, frozen set of traits that stays byte-identical across restarts. Do not create identities speculatively: they count against the installation's quota.

Returns {name, os, created}. Fails with a readable message if the name is taken (409), if the quota is reached or the installation is unauthorized (403), or if an argument is rejected (422).`,
    shape: {
      name: z.string().describe("the new identity's name. Must be unique — creating a name that already exists fails rather than overwriting it."),
      engine: z.string().optional().describe("which browser core backs the identity. Leave unset to use this installation's default; an unknown value is rejected with the list of accepted ones."),
      proxy: z.string().optional().describe('exit proxy URL, e.g. "http://user:pass@host:port" or "socks5://host:port" — any vendor. Omit for a direct connection. Test it with proxy_check first.'),
      group: z.string().optional().describe("local ledger: which account, email, or purpose this identity is bound to"),
      tags: z.array(z.string()).optional().describe("local ledger tags"),
      note: z.string().optional().describe("local ledger note, free text"),
      urls: z.array(z.string()).optional().describe("startup tabs — pages opened automatically when the identity starts"),
      force_dark: z.boolean().optional().describe("personal display preference, OFF by default. true inverts web pages that have no dark theme of their own (Google Ads and the like) to dark; sites may notice the changed colors, so use it only on identities the user manages for themselves, never on accounts being built up. Leave unset to keep pages exactly as the sites render them. Chrome-engine identities only."),
    },
    handler: (a) => {
      const clean = identityName(a.name);
      const body: Record<string, unknown> = { name: clean, engine: a.engine, proxy: a.proxy, group: a.group, tags: a.tags, note: a.note, urls: a.urls };
      for (const k of Object.keys(body)) if (body[k] == null) delete body[k];
      if (a.force_dark != null) body.launch = { forceDark: Boolean(a.force_dark) };
      return request("POST", "/profiles", { json: body });
    },
  },
  {
    name: "profile_settings",
    description: `Change a display setting of an existing identity. Only the settings you pass are changed; everything else in its launch settings stays as it is.

force_dark: true = force dark web pages (inverts sites that have no dark theme; the identity's color scheme then reads as dark too). false = off, pages render exactly as the sites intend (the default). Sites may notice forced dark colors — reserve it for identities the user manages for themselves. Chrome-engine identities only. Takes effect the next time the identity starts.

Returns {name, launch} with the launch settings now stored.`,
    shape: {
      name: NAME,
      force_dark: z.boolean().optional().describe("true = force dark web pages; false = off (default). Chrome-engine identities only."),
    },
    handler: async (a) => {
      const clean = identityName(a.name);
      if (a.force_dark == null) throw new Error("nothing to change: pass force_dark");
      const current = (await request("GET", `/profiles/${encodeURIComponent(clean)}`)) as { meta?: { launch?: Record<string, unknown> } };
      const launch = { ...((current.meta && current.meta.launch) || {}), forceDark: Boolean(a.force_dark) };
      await request("PATCH", `/profiles/${encodeURIComponent(clean)}`, { json: { launch } });
      return { name: clean, launch };
    },
  },
  {
    name: "profile_checkup",
    description: `Run the consistency checkup on an identity — is its fingerprint coherent?

Every trait has one correct value given the identity's OS, browser core, and exit IP, so each item gets a verdict rather than just being displayed. Run it after changing an identity's proxy, and before trusting an identity with anything that matters.

Returns {name, engine, live, summary, checks}. \`summary\` is "pass", "warn", or "fail" — the worst verdict among the checks. Each entry in \`checks\` has an id, a label, a verdict ("pass"/"warn"/"fail"/"skip"), and a detail string explaining it. "skip" means the check needs live=true, or does not apply.`,
    shape: {
      name: NAME,
      live: z.boolean().optional().describe("false (default) derives everything from the stored record — fast, no side effects. true additionally STARTS THE BROWSER to measure the real TLS/JA3 fingerprint and User-Agent; takes tens of seconds and opens a window."),
    },
    handler: (a) => request("GET", `/profiles/${encodeURIComponent(identityName(a.name))}/checkup`, { params: { live: Boolean(a.live) } }),
  },
  {
    name: "proxy_check",
    description: `Test a proxy URL and report where it actually comes out. No identity needed.

Use it before binding a proxy to an identity with profile_create: a dead or slow exit found here costs nothing, the same exit found halfway through a signup costs the account.

Returns {ok: true, exit_ip, country, latency_ms} when the proxy works, or {ok: false, error} when it does not. A dead proxy is an ANSWER, not a failure — check the \`ok\` field rather than assuming success. \`country\` can be null even on a working proxy if the exit IP cannot be geolocated.`,
    shape: { url: z.string().describe('the proxy URL to test, e.g. "http://user:pass@host:port" or "socks5://host:port"') },
    handler: (a) => request("POST", "/proxy-check", { json: { url: nonEmpty(a.url, "url is required: the proxy to test, e.g. 'socks5://host:port'") } }),
  },
  {
    name: "profile_delete",
    description: `Move an identity to the recycle bin. Recoverable — this is not erasure.

The identity's browser storage, cookies and logged-in sessions go to the recycle bin intact and profile_restore brings them back unchanged. It stops counting against the installation's quota while it sits there, and it no longer appears in profiles_list.

A running identity is refused (409) rather than killed under itself — stop it with profile_stop first, then delete. Deleting a name that is already in the recycle bin replaces the older copy, so the older one is lost.

There is deliberately NO tool here for permanent deletion. Emptying the recycle bin destroys logged-in accounts with no way back, so it is a manual action in the desktop app only — a person does it, looking at what they are about to lose. Do not look for a way around this; tell the human instead.

Fails with a readable message if the identity does not exist (404), or if it is still running (409).`,
    shape: { name: NAME },
    handler: async (a) => {
      const clean = identityName(a.name);
      await request("DELETE", `/profiles/${encodeURIComponent(clean)}`);
      return { name: clean, deleted: true, recoverable: true, note: "in the recycle bin; profile_restore brings it back" };
    },
  },
  {
    name: "trash_list",
    description: `List the identities in the recycle bin — what is still recoverable.

Read-only. Deleted identities do not appear in profiles_list, so without this you could only restore a name you already knew. Pair it with profile_restore.

Each entry carries name, engine, os, created, and deleted_at. Newest deletion first. An empty list means the recycle bin is empty. Nothing here is gone yet — everything listed still has its cookies and login state and comes back intact via profile_restore.`,
    shape: {},
    handler: () => request("GET", "/trash"),
  },
  {
    name: "profile_restore",
    description: `Bring an identity back from the recycle bin, exactly as it was.

Its frozen fingerprint, proxy binding, cookies and login state all come back — restoring is not re-creating, so nothing is regenerated and no logged-in account is lost.

Returns {name, engine, created}. Fails with a readable message if nothing by that name is in the recycle bin (404), or if a live identity has taken the name since (409) — rename or delete that one first, then restore.`,
    shape: { name: z.string().describe("the trashed identity's name") },
    handler: (a) => request("POST", `/trash/${encodeURIComponent(identityName(a.name))}/restore`),
  },
  {
    name: "session_goto",
    description: `Navigate a session's current page to a URL.

Open the identity's session with profile_start first — this drives an already-open session, it does not open one itself.

Returns {url}: the page's URL after navigation. Fails with a readable message if the session is not open (409 — call profile_start first) or the identity does not exist (404).`,
    shape: { name: NAME, url: z.string().describe('the URL to navigate to, e.g. "https://example.com/login"') },
    handler: (a) =>
      request("POST", `/sessions/${encodeURIComponent(identityName(a.name))}/goto`, { json: { url: nonEmpty(a.url, "url is required: the page to navigate to, e.g. 'https://example.com'") } }),
  },
  {
    name: "session_snapshot",
    description: `Take an accessibility-tree snapshot of the session's current page.

This is the primary way to see what is on a page before acting on it — read it to find the roles/names/selectors to pass to session_click_selector or session_fill_selector. Requires a session opened with profile_start first.

Returns {snapshot}: the page's accessibility tree as text. Fails with a readable message if the session is not open (409 — call profile_start first) or the identity does not exist (404).`,
    shape: { name: NAME },
    handler: (a) => request("GET", `/sessions/${encodeURIComponent(identityName(a.name))}/snapshot`),
  },
  {
    name: "session_click_selector",
    description: `Click an element on the session's current page, located by a CSS selector.

Requires a session opened with profile_start first. \`selector\` is a CSS selector (e.g. "button#submit", "a.nav-link") — not an XPath, not a role/name pair (use session_snapshot to find one).

Returns {ok: true}. Fails with a readable message if the session is not open (409 — call profile_start first), the identity does not exist (404), or the selector never matches / the click times out (502).`,
    shape: { name: NAME, selector: z.string().describe("CSS selector for the element to click") },
    handler: (a) =>
      request("POST", `/sessions/${encodeURIComponent(identityName(a.name))}/click_selector`, {
        json: { selector: nonEmpty(a.selector, "selector is required: a CSS selector, e.g. 'button#submit'") },
      }),
  },
  {
    name: "session_fill_selector",
    description: `Fill a text field on the session's current page, located by a CSS selector.

Uses Playwright's fill(), which drives React/Vue-controlled inputs correctly (setting the DOM value directly does not). Requires a session opened with profile_start first. \`selector\` is a CSS selector, not an XPath.

Returns {ok: true}. Fails with a readable message if the session is not open (409 — call profile_start first), the identity does not exist (404), or the selector never matches / the fill times out (502).`,
    shape: {
      name: NAME,
      selector: z.string().describe("CSS selector for the input to fill"),
      text: z.string().describe("the text to put in the field, replacing whatever was there"),
    },
    handler: (a) =>
      request("POST", `/sessions/${encodeURIComponent(identityName(a.name))}/fill_selector`, {
        json: { selector: nonEmpty(a.selector, "selector is required: a CSS selector, e.g. 'input[name=email]'"), value: a.text },
      }),
  },
  {
    name: "session_press",
    description: `Press a keyboard key on the session's current page (whatever is focused).

Requires a session opened with profile_start first. Use Playwright key names, e.g. "Enter", "Tab", "Escape", "Control+A".

Returns {ok: true}. Fails with a readable message if the session is not open (409 — call profile_start first) or the identity does not exist (404).`,
    shape: { name: NAME, key: z.string().describe("the key (or chord) to press") },
    handler: (a) => request("POST", `/sessions/${encodeURIComponent(identityName(a.name))}/press`, { json: { key: a.key } }),
  },
  {
    name: "session_eval",
    description: `Evaluate a JavaScript expression on the session's current page.

Requires a session opened with profile_start first. A page-JS error is data to read, not a tool failure: the daemon returns {"ok": false, "error", "error_type"} instead of raising when the expression throws.

Returns the {ok, result} envelope: {"ok": true, "result": <value>} on success. \`result\` is whatever the expression evaluates to as JSON — string, number, boolean, object, array, or null — not necessarily a string; do not assume its type. Fails with a readable message if the session is not open (409 — call profile_start first) or the identity does not exist (404).`,
    shape: { name: NAME, expr: z.string().describe('the JavaScript expression to evaluate in the page, e.g. "document.title"') },
    handler: (a) =>
      request("POST", `/sessions/${encodeURIComponent(identityName(a.name))}/eval`, { json: { expr: nonEmpty(a.expr, "expr is required: the JavaScript expression to evaluate") } }),
  },
  {
    name: "session_tabs",
    description: `List the session's open tabs, with the currently active one marked.

Requires a session opened with profile_start first. Use this to find the index to pass to session_switch_tab.

Returns {tabs, active}: \`tabs\` is a list of {index, url}, \`active\` is the index of the tab currently in front. Fails with a readable message if the session is not open (409 — call profile_start first) or the identity does not exist (404).`,
    shape: { name: NAME },
    handler: (a) => request("GET", `/sessions/${encodeURIComponent(identityName(a.name))}/tabs`),
  },
  {
    name: "session_switch_tab",
    description: `Bring one of the session's open tabs to the front and make it the active one.

Requires a session opened with profile_start first. Get \`index\` from session_tabs — indices are only valid as of that snapshot, a tab opened or closed since shifts them.

Returns {index, url} for the now-active tab. Fails with a readable message if the session is not open (409 — call profile_start first), the identity does not exist (404), or the index is out of range (422).`,
    shape: { name: NAME, index: z.number().int().describe("the tab's index, from session_tabs") },
    handler: (a) => request("POST", `/sessions/${encodeURIComponent(identityName(a.name))}/switch_tab`, { json: { index: a.index } }),
  },
  {
    name: "session_dialog",
    description: `Answer the JavaScript dialog (alert, confirm, prompt, beforeunload) open on the session's page.

Dialogs are left open, as in a normal browser: while one is open the page is blocked and the other session_* tools fail with 409 "a JavaScript <type> is open on the page (<message>)". Call this to answer it, then retry. A beforeunload that your own session_goto (or click) causes is accepted automatically and never shows up here.

Returns {ok, action, type, message} for the dialog that was answered. Fails with a readable message if no dialog is open (409), the session is not open (409 — call profile_start first) or the identity does not exist (404).`,
    shape: {
      name: NAME,
      action: z.string().describe('"accept" (OK / Leave) or "dismiss" (Cancel / Stay)'),
      prompt_text: z.string().optional().describe("for a prompt() dialog, the text to enter before accepting; omit it to accept the prompt's default value"),
    },
    handler: (a) => {
      const clean = identityName(a.name);
      const act = typeof a.action === "string" ? a.action.trim().toLowerCase() : a.action;
      if (act !== "accept" && act !== "dismiss") throw new Error("action must be 'accept' or 'dismiss'");
      const json: Record<string, unknown> = { action: act };
      if (a.prompt_text !== undefined && a.prompt_text !== null) json.prompt_text = a.prompt_text;
      return request("POST", `/sessions/${encodeURIComponent(clean)}/dialog`, { json });
    },
  },
];
