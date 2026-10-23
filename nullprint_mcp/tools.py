"""The tools themselves.

Plain functions, undecorated: tests call them directly, `server.build_server()`
registers them. Each one maps onto daemon endpoints and adds nothing but
argument checking and a readable error.
"""
from __future__ import annotations

from urllib.parse import quote

from . import daemon
from .daemon import DaemonError

# Keys a daemon build may use for the live-debugging endpoint of a session.
# Accepting several keeps this tool stable whichever name the engine settles on.
_CDP_KEYS = ("cdp_endpoint", "ws_endpoint", "cdp_url", "webSocketDebuggerUrl")

_NO_CDP_NOTE = (
    "No CDP endpoint for this identity. Nullprint keeps external debugging off by "
    "default: turn it on per identity in the app (Edit -> Advanced -> External "
    "debugging, needs a second verification), then reopen the identity. The Firefox "
    "core never publishes one. Without it, drive the browser through the daemon's "
    "/sessions/{name}/* commands (goto, snapshot, click, type, screenshot, ...)."
)


# Same whitelist as the engine's own name check (engine/nullprint.py _check_name), plus
# % and #: a name must stay ONE path segment. '%2F', '?' and '#' used to slip through and,
# once the daemon decoded the URL, reach endpoints this server deliberately does not
# expose (security audit H-5, 2026-10-21).
_NAME_BAD = set('/\\:*?"<>|%#')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


def _identity_name(name: str) -> str:
    """Validate an identity name before it becomes a URL path segment."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required: the identity's name, e.g. 'bing-us'")
    clean = name.strip()
    if (clean.startswith(".") or len(clean) > 120
            or any(c in _NAME_BAD or ord(c) < 32 for c in clean)
            or clean.split(".")[0].upper() in _WIN_RESERVED):
        raise ValueError(f"invalid identity name {name!r}: it cannot start with '.', be longer than "
                         f"120 characters, or contain / \\ : * ? \" < > | % # or control characters")
    return clean


def _seg(clean: str) -> str:
    """An already-validated name as one escaped URL path segment."""
    return quote(clean, safe="")


def profiles_list() -> list[dict]:
    """List every identity in this installation, with its live status.

    Each entry carries: name, os, engine (which browser core backs it),
    created, proxy (the exit server only — never credentials), status
    ("running" when a browser is open for it, otherwise "idle"), and meta
    (the local ledger: group, tags, note, last_launched).

    Call this first to find the exact name to pass to profile_start /
    profile_stop; names are case-sensitive.
    """
    return daemon.request("GET", "/profiles").json()


def profile_start(name: str, headless: bool = False) -> dict:
    """Open a browser session for an identity and return the endpoint to drive it.

    The identity keeps its frozen fingerprint, its exit proxy, and its logged-in
    cookies — starting it does not re-randomize anything. Starting an identity
    that already has a session open is a no-op that returns the existing one.

    Args:
        name: identity name, exactly as profiles_list reports it.
        headless: run without a visible window. Default False (headed), which
            is what sites expect and what you want for anything login-shaped.

    Returns the session record: name, opened_at, idle_seconds, url (the page it
    is on), plus cdp_endpoint — a ws:// DevTools address you can attach your own
    automation to. It is only published for Chrome-core identities that have
    external debugging turned on in the app (off by default; Edit -> Advanced ->
    External debugging, needs a second verification, then reopen the identity).
    Otherwise it is None, cdp_unavailable gives the daemon's reason code
    (external_debug_off, external_debug_not_granted, not_supported_for_engine,
    starting) and cdp_note says what to do — the session commands work either way.

    Prefer the daemon's own commands over attaching to cdp_endpoint. A session
    driven from outside bypasses Nullprint's consistency guards, so an outside
    tool can set a viewport, locale, timezone, or header that contradicts the
    identity's frozen fingerprint and make it detectable. Attach at your own
    risk, and change nothing the identity did not already claim.

    Fails with a readable message if the identity does not exist (404), if too
    many sessions are already warm (429), or if the daemon is not running.
    """
    clean = _identity_name(name)
    session = daemon.request(
        "POST", f"/sessions/{_seg(clean)}", json={"headless": bool(headless)}
    ).json()

    out = dict(session)
    out["cdp_endpoint"] = next((session[k] for k in _CDP_KEYS if session.get(k)), None)
    if out["cdp_endpoint"] is None:
        out["cdp_note"] = session.get("cdp_hint") or _NO_CDP_NOTE   # BUG-024:daemon 说了原因就用它的
    return out


def profile_stop(name: str) -> dict:
    """Stop an identity's browser, however it was started.

    Cookies and login state live in the identity's own storage and survive the
    stop; whatever was typed into an open form does not. Stop identities you are
    done with — a running identity holds a warm browser and keeps its login
    state exposed.

    Two things can be running for one identity and this closes either: the warm
    session profile_start opens (the daemon drives it), and a window the desktop
    app launched on its own. The session is closed first; if there is none, the
    identity-level stop is used instead, which also terminates a launched window
    and is a no-op on an identity that is already idle.

    Args:
        name: identity name, exactly as profiles_list reports it.

    Returns {name, stopped, closed}: `stopped` is False when the identity was
    already idle, and `closed` lists what was actually shut down.

    Fails with a readable message if the identity does not exist, or if the
    daemon is not running.
    """
    clean = _identity_name(name)
    try:
        daemon.request("DELETE", f"/sessions/{_seg(clean)}")
        return {"name": clean, "stopped": True, "closed": ["session"]}
    except DaemonError as e:
        # 409 "session not open" is the usual answer for an identity that was
        # launched as a window rather than opened as a session; 404 is allowed
        # for too in case a daemon build reports it that way. Anything else
        # (daemon down, 422, ...) is a real failure and must not be swallowed.
        if e.status not in (404, 409):
            raise
    result = daemon.request("POST", f"/profiles/{_seg(clean)}/stop").json()
    closed = result.get("stopped") or []
    return {"name": clean, "stopped": bool(closed), "closed": closed,
            "status": result.get("status")}


def profile_create(name: str, engine: str | None = None, proxy: str | None = None,
                   group: str | None = None, tags: list[str] | None = None,
                   note: str | None = None, urls: list[str] | None = None,
                   force_dark: bool | None = None) -> dict:
    """Create a new identity. Its fingerprint is generated and frozen on the spot.

    You do not choose the fingerprint — the point of the product is that every
    identity gets one self-consistent, frozen set of traits that stays
    byte-identical across restarts. Do not create identities speculatively: they
    count against the installation's quota.

    Args:
        name: the new identity's name. Must be unique — creating a name that
            already exists fails rather than overwriting it.
        engine: which browser core backs the identity. Leave unset to use this
            installation's default; an unknown value is rejected with the list
            of accepted ones. The `engine` field in profiles_list shows what
            existing identities use.
        proxy: exit proxy URL, e.g. "http://user:pass@host:port" or
            "socks5://host:port" — any vendor, bring your own. Omit it and the
            identity runs on a direct connection. Test a URL with proxy_check
            before binding it here.
        group, tags, note: the local ledger — which account, email, or purpose
            this identity is bound to. Free text, searchable in the app, and
            returned under `meta` by profiles_list.
        urls: startup tabs — pages opened automatically when the identity starts.
        force_dark: personal display preference, OFF by default. True inverts
            web pages that have no dark theme of their own (Google Ads and the
            like) to dark; sites may notice the changed colors, so use it only
            on identities the user manages for themselves, never on accounts
            being built up. Leave unset to keep pages exactly as the sites
            render them. Chrome-engine identities only.

    Returns {name, os, created}. Fails with a readable message if the name is
    taken (409), if the quota is reached or the installation is unauthorized
    (403), or if an argument is rejected (422).
    """
    clean = _identity_name(name)
    body = {"name": clean, "engine": engine, "proxy": proxy,
            "group": group, "tags": tags, "note": note, "urls": urls}
    if force_dark is not None:
        body["launch"] = {"forceDark": bool(force_dark)}
    return daemon.request(
        "POST", "/profiles", json={k: v for k, v in body.items() if v is not None}
    ).json()


def profile_settings(name: str, force_dark: bool | None = None) -> dict:
    """Change a display setting of an existing identity. Only the settings you
    pass are changed; everything else in its launch settings stays as it is.

    Args:
        name: identity name, exactly as profiles_list reports it.
        force_dark: True = force dark web pages (inverts sites that have no dark
            theme; the identity's color scheme then reads as dark too). False =
            off, pages render exactly as the sites intend (the default). Sites
            may notice forced dark colors — reserve it for identities the user
            manages for themselves. Chrome-engine identities only. Takes effect
            the next time the identity starts.

    Returns {name, launch} with the launch settings now stored.
    """
    clean = _identity_name(name)
    if force_dark is None:
        raise ValueError("nothing to change: pass force_dark")
    current = daemon.request("GET", f"/profiles/{_seg(clean)}").json()
    launch = dict((current.get("meta") or {}).get("launch") or {})
    launch["forceDark"] = bool(force_dark)
    daemon.request("PATCH", f"/profiles/{_seg(clean)}", json={"launch": launch})
    return {"name": clean, "launch": launch}


def profile_checkup(name: str, live: bool = False) -> dict:
    """Run the consistency checkup on an identity — is its fingerprint coherent?

    This is the check competitors cannot make: every trait has one correct value
    given the identity's OS, browser core, and exit IP, so each item gets a
    verdict rather than just being displayed. Run it after changing an
    identity's proxy, and before trusting an identity with anything that matters.

    Args:
        name: identity name, exactly as profiles_list reports it.
        live: False (default) derives everything from the stored record — fast,
            no side effects. True additionally STARTS THE BROWSER to measure the
            real TLS/JA3 fingerprint and User-Agent against the browser core's
            baseline. That takes tens of seconds and opens a window, so use it
            when you actually need measured proof, not for a routine look.

    Returns {name, engine, live, summary, checks}. `summary` is "pass", "warn",
    or "fail" — the worst verdict among the checks. Each entry in `checks` has
    an id, a label, a verdict ("pass"/"warn"/"fail"/"skip"), and a detail
    string explaining it. "skip" means the check needs live=True, or does not
    apply (a direct-connection identity has no exit geography to align).
    """
    clean = _identity_name(name)
    return daemon.request(
        "GET", f"/profiles/{_seg(clean)}/checkup", params={"live": bool(live)}
    ).json()


def proxy_check(url: str) -> dict:
    """Test a proxy URL and report where it actually comes out. No identity needed.

    Use it before binding a proxy to an identity with profile_create: a dead or
    slow exit found here costs nothing, the same exit found halfway through a
    signup costs the account.

    Args:
        url: the proxy URL to test, e.g. "http://user:pass@host:port" or
            "socks5://host:port".

    Returns {ok: true, exit_ip, country, latency_ms} when the proxy works, or
    {ok: false, error} when it does not. A dead proxy is an ANSWER, not a
    failure — check the `ok` field rather than assuming success. Note that
    `country` can be null even on a working proxy if the exit IP cannot be
    geolocated.

    This tests a bare URL. To test the proxy an identity is already bound to,
    the daemon's per-identity check is a separate endpoint not yet exposed here.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url is required: the proxy to test, e.g. 'socks5://host:port'")
    return daemon.request("POST", "/proxy-check", json={"url": url.strip()}).json()


def profile_delete(name: str) -> dict:
    """Move an identity to the recycle bin. Recoverable — this is not erasure.

    The identity's browser storage, cookies and logged-in sessions go to the
    recycle bin intact and profile_restore brings them back unchanged. It stops
    counting against the installation's quota while it sits there, and it no
    longer appears in profiles_list.

    A running identity is refused (409) rather than killed under itself — stop
    it with profile_stop first, then delete. Deleting a name that is already in
    the recycle bin replaces the older copy, so the older one is lost.

    There is deliberately NO tool here for permanent deletion. Emptying the
    recycle bin destroys logged-in accounts with no way back, so it is a manual
    action in the desktop app only — a person does it, looking at what they are
    about to lose. Do not look for a way around this; tell the human instead.

    Args:
        name: identity name, exactly as profiles_list reports it.

    Fails with a readable message if the identity does not exist (404), or if it
    is still running (409).
    """
    clean = _identity_name(name)
    daemon.request("DELETE", f"/profiles/{_seg(clean)}")
    return {"name": clean, "deleted": True, "recoverable": True,
            "note": "in the recycle bin; profile_restore brings it back"}


def trash_list() -> list[dict]:
    """List the identities in the recycle bin — what is still recoverable.

    Read-only. This is how you find out what can be restored: deleted
    identities do not appear in profiles_list, so without this you could only
    restore a name you already knew. Pair it with profile_restore.

    Each entry carries name, engine, os, created, and deleted_at. Newest
    deletion first. An empty list means the recycle bin is empty.

    Nothing here is gone yet — everything listed still has its cookies and
    login state and comes back intact via profile_restore.
    """
    return daemon.request("GET", "/trash").json()


def profile_restore(name: str) -> dict:
    """Bring an identity back from the recycle bin, exactly as it was.

    Its frozen fingerprint, proxy binding, cookies and login state all come
    back — restoring is not re-creating, so nothing is regenerated and no
    logged-in account is lost.

    Args:
        name: the trashed identity's name.

    Returns {name, engine, created}. Fails with a readable message if nothing by
    that name is in the recycle bin (404), or if a live identity has taken the
    name since (409) — rename or delete that one first, then restore.
    """
    clean = _identity_name(name)
    return daemon.request("POST", f"/trash/{_seg(clean)}/restore").json()


def session_goto(name: str, url: str) -> dict:
    """Navigate a session's current page to a URL.

    Open the identity's session with profile_start first — this drives an
    already-open session, it does not open one itself.

    Args:
        name: identity name, exactly as profiles_list reports it.
        url: the URL to navigate to, e.g. "https://example.com/login".

    Returns {url}: the page's URL after navigation. Fails with a readable
    message if the session is not open (409 — call profile_start first) or
    the identity does not exist (404).
    """
    clean = _identity_name(name)
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url is required: the page to navigate to, e.g. 'https://example.com'")
    return daemon.request(
        "POST", f"/sessions/{_seg(clean)}/goto", json={"url": url.strip()}
    ).json()


def session_snapshot(name: str) -> dict:
    """Take an accessibility-tree snapshot of the session's current page.

    This is the primary way to see what is on a page before acting on it —
    read it to find the roles/names/selectors to pass to session_click_selector
    or session_fill_selector. Requires a session opened with profile_start first.

    Args:
        name: identity name, exactly as profiles_list reports it.

    Returns {snapshot}: the page's accessibility tree as text. Fails with a
    readable message if the session is not open (409 — call profile_start
    first) or the identity does not exist (404).
    """
    clean = _identity_name(name)
    return daemon.request("GET", f"/sessions/{_seg(clean)}/snapshot").json()


def session_click_selector(name: str, selector: str) -> dict:
    """Click an element on the session's current page, located by a CSS selector.

    Requires a session opened with profile_start first. `selector` is a CSS
    selector (e.g. "button#submit", "a.nav-link") — not an XPath, not a role/name
    pair (use session_snapshot to find one).

    Args:
        name: identity name, exactly as profiles_list reports it.
        selector: CSS selector for the element to click.

    Returns {ok: true}. Fails with a readable message if the session is not
    open (409 — call profile_start first), the identity does not exist (404),
    or the selector never matches / the click times out (502).
    """
    clean = _identity_name(name)
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("selector is required: a CSS selector, e.g. 'button#submit'")
    return daemon.request(
        "POST", f"/sessions/{_seg(clean)}/click_selector", json={"selector": selector.strip()}
    ).json()


def session_fill_selector(name: str, selector: str, text: str) -> dict:
    """Fill a text field on the session's current page, located by a CSS selector.

    Uses Playwright's fill(), which drives React/Vue-controlled inputs correctly
    (setting the DOM value directly does not). Requires a session opened with
    profile_start first. `selector` is a CSS selector, not an XPath.

    Args:
        name: identity name, exactly as profiles_list reports it.
        selector: CSS selector for the input to fill.
        text: the text to put in the field, replacing whatever was there.

    Returns {ok: true}. Fails with a readable message if the session is not
    open (409 — call profile_start first), the identity does not exist (404),
    or the selector never matches / the fill times out (502).
    """
    clean = _identity_name(name)
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("selector is required: a CSS selector, e.g. 'input[name=email]'")
    return daemon.request(
        "POST", f"/sessions/{_seg(clean)}/fill_selector",
        json={"selector": selector.strip(), "value": text},
    ).json()


def session_press(name: str, key: str) -> dict:
    """Press a keyboard key on the session's current page (whatever is focused).

    Requires a session opened with profile_start first. Use Playwright key
    names, e.g. "Enter", "Tab", "Escape", "Control+A".

    Args:
        name: identity name, exactly as profiles_list reports it.
        key: the key (or chord) to press.

    Returns {ok: true}. Fails with a readable message if the session is not
    open (409 — call profile_start first) or the identity does not exist (404).
    """
    clean = _identity_name(name)
    return daemon.request(
        "POST", f"/sessions/{_seg(clean)}/press", json={"key": key}
    ).json()


def session_eval(name: str, expr: str) -> dict:
    """Evaluate a JavaScript expression on the session's current page.

    Requires a session opened with profile_start first. A page-JS error is
    data to read, not a tool failure: the daemon returns {"ok": false,
    "error", "error_type"} instead of raising when the expression throws.

    Args:
        name: identity name, exactly as profiles_list reports it.
        expr: the JavaScript expression to evaluate in the page, e.g.
            "document.title" or "document.querySelectorAll('a').length".

    Returns the {ok, result} envelope: {"ok": true, "result": <value>} on
    success. `result` is whatever the expression evaluates to as JSON —
    string, number, boolean, object, array, or null — not necessarily a
    string; do not assume its type. Fails with a readable message (raises,
    rather than the {ok: false} envelope above) if the session is not open
    (409 — call profile_start first) or the identity does not exist (404).
    """
    clean = _identity_name(name)
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("expr is required: the JavaScript expression to evaluate")
    return daemon.request(
        "POST", f"/sessions/{_seg(clean)}/eval", json={"expr": expr}
    ).json()


def session_tabs(name: str) -> dict:
    """List the session's open tabs, with the currently active one marked.

    Requires a session opened with profile_start first. Use this to find the
    index to pass to session_switch_tab.

    Args:
        name: identity name, exactly as profiles_list reports it.

    Returns {tabs, active}: `tabs` is a list of {index, url}, `active` is the
    index of the tab currently in front. Fails with a readable message if the
    session is not open (409 — call profile_start first) or the identity does
    not exist (404).
    """
    clean = _identity_name(name)
    return daemon.request("GET", f"/sessions/{_seg(clean)}/tabs").json()


def session_switch_tab(name: str, index: int) -> dict:
    """Bring one of the session's open tabs to the front and make it the active one.

    Requires a session opened with profile_start first. Get `index` from
    session_tabs — indices are only valid as of that snapshot, a tab opened or
    closed since shifts them.

    Args:
        name: identity name, exactly as profiles_list reports it.
        index: the tab's index, from session_tabs.

    Returns {index, url} for the now-active tab. Fails with a readable message
    if the session is not open (409 — call profile_start first), the identity
    does not exist (404), or the index is out of range (422).
    """
    clean = _identity_name(name)
    return daemon.request(
        "POST", f"/sessions/{_seg(clean)}/switch_tab", json={"index": index}
    ).json()


def session_dialog(name: str, action: str, prompt_text: str | None = None) -> dict:
    """Answer the JavaScript dialog (alert, confirm, prompt, beforeunload) open on the session's page.

    Dialogs are left open, as in a normal browser: while one is open the page
    is blocked and the other session_* tools fail with 409 "a JavaScript
    <type> is open on the page (<message>)". Call this to answer it, then
    retry. A beforeunload that your own session_goto (or click) causes is
    accepted automatically and never shows up here.

    Args:
        name: identity name, exactly as profiles_list reports it.
        action: "accept" (OK / Leave) or "dismiss" (Cancel / Stay).
        prompt_text: for a prompt() dialog, the text to enter before
            accepting; omit it to accept the prompt's default value.

    Returns {ok, action, type, message} for the dialog that was answered.
    Fails with a readable message if no dialog is open (409), the session
    is not open (409 — call profile_start first) or the identity does not
    exist (404).
    """
    clean = _identity_name(name)
    act = action.strip().lower() if isinstance(action, str) else action
    if act not in ("accept", "dismiss"):
        raise ValueError("action must be 'accept' or 'dismiss'")
    body = {"action": act}
    if prompt_text is not None:
        body["prompt_text"] = prompt_text
    return daemon.request("POST", f"/sessions/{_seg(clean)}/dialog", json=body).json()


TOOLS = (profiles_list, profile_start, profile_stop,
         profile_create, profile_settings, profile_checkup, proxy_check,
         profile_delete, trash_list, profile_restore,
         session_goto, session_snapshot, session_click_selector,
         session_fill_selector, session_press, session_eval,
         session_tabs, session_switch_tab, session_dialog)
