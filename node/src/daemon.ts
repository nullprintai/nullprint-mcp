// HTTP client for the local Nullprint daemon. Mirrors mcp/nullprint_mcp/daemon.py:
// same address discovery order, same API-key source, same readable error messages.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const DEFAULT_HOST = "127.0.0.1";
export const TIMEOUT_MS = 130_000; // a headed launch can take a while; same budget as the Python server

export class DaemonError extends Error {
  status: number | null;
  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "DaemonError";
    this.status = status;
  }
}

let pinnedUrl: string | null = null;

/** Pin the daemon address (from --daemon-url / --port). null restores discovery. */
export function setDaemonUrl(url: string | null): void {
  pinnedUrl = url ? url.replace(/\/+$/, "") : null;
}

/** NULLPRINT_HOME if set, else ~/.nullprint (where the desktop app keeps daemon.json). */
export function runtimeDir(): string {
  const home = process.env.NULLPRINT_HOME;
  if (home) return home.startsWith("~") ? path.join(os.homedir(), home.slice(1)) : home;
  return path.join(os.homedir(), ".nullprint");
}

function readDaemonJson(): Record<string, unknown> | null {
  const file = path.join(runtimeDir(), "daemon.json");
  let text: string;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return null;
  }
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    throw new DaemonError(`${file} is not valid JSON — the daemon may have been killed mid-write. Restart the daemon, or pass --daemon-url.`);
  }
}

/**
 * Resolve the daemon's base URL, most explicit source first:
 * 1. --daemon-url / --port  2. NULLPRINT_DAEMON_URL (SUPLOGIN_DAEMON_URL still honoured)
 * 3. NULLPRINT_PORT  4. <runtime dir>/daemon.json, which the running daemon writes.
 */
export function baseUrl(): string {
  if (pinnedUrl) return pinnedUrl;
  const envUrl = process.env.NULLPRINT_DAEMON_URL || process.env.SUPLOGIN_DAEMON_URL;
  if (envUrl) return envUrl.replace(/\/+$/, "");
  const envPort = process.env.NULLPRINT_PORT;
  if (envPort) return `http://${DEFAULT_HOST}:${envPort}`;
  const info = readDaemonJson();
  if (!info) {
    throw new DaemonError(
      `the Nullprint daemon does not look like it is running: no ${path.join(runtimeDir(), "daemon.json")}. ` +
        "Start the daemon (the desktop app starts it for you), or point this server at one with --daemon-url http://127.0.0.1:<port>.",
    );
  }
  const port = info.port;
  if (!port) throw new DaemonError(`${path.join(runtimeDir(), "daemon.json")} has no 'port' field; restart the daemon or pass --daemon-url.`);
  return `http://${DEFAULT_HOST}:${port}`;
}

/** daemon.json's api_key; NULLPRINT_API_KEY overrides it (for --daemon-url to another machine). */
export function apiKey(): string | null {
  const env = process.env.NULLPRINT_API_KEY;
  if (env) return env;
  try {
    const info = readDaemonJson();
    const k = info?.api_key;
    return typeof k === "string" && k ? k : null;
  } catch {
    return null;
  }
}

const STATUS_HINT: Record<number, string> = {
  400: "bad request",
  401: "API key missing or wrong — the daemon.json api_key is read automatically; set NULLPRINT_API_KEY when using --daemon-url",
  403: "not authorized (license invalid or profile quota reached)",
  404: "not found",
  409: "conflict — it is already in that state, or busy",
  422: "the daemon rejected the arguments",
  429: "too many warm sessions; stop one first",
  502: "the browser worker failed to answer",
  503: "the daemon could not reach its backend",
};

function licenseText(d: Record<string, unknown>): string {
  const used = d.used, limit = d.limit;
  const counts = used != null && limit != null ? ` (${used} of ${limit} identities used)` : "";
  if (d.reason === "quota_exceeded") {
    return "profile quota reached" + counts + " — delete an identity you no longer need, or raise the plan's limit.";
  }
  return `this installation is not authorized (license state: ${d.reason})` + counts + ". Authorize it from the desktop app, then retry.";
}

function detail(status: number, text: string): string {
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return text.trim().slice(0, 400) || "(empty response body)";
  }
  const d = body && typeof body === "object" && "detail" in (body as object) ? (body as { detail: unknown }).detail : body;
  if (d && typeof d === "object" && !Array.isArray(d)) {
    const r = (d as Record<string, unknown>).reason;
    if (r === "api_key_required" || r === "invalid_api_key") return String(r);
    if ("reason" in (d as object)) return licenseText(d as Record<string, unknown>);
  }
  return typeof d === "string" ? d : JSON.stringify(d);
}

export interface RequestOptions {
  json?: unknown;
  params?: Record<string, string | number | boolean>;
}

/** Call the daemon. Throws DaemonError with a readable message on any failure; returns the parsed JSON body (or null). */
export async function request(method: string, pathname: string, opts: RequestOptions = {}): Promise<unknown> {
  const url = baseUrl();
  const headers: Record<string, string> = { Accept: "application/json" };
  const k = apiKey();
  if (k) headers["X-API-Key"] = k;
  let target = `${url}${pathname}`;
  if (opts.params) {
    const q = new URLSearchParams();
    for (const [key, v] of Object.entries(opts.params)) q.set(key, String(v));
    target += `?${q.toString()}`;
  }
  let body: string | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetch(target, { method, headers, body, signal: ctl.signal });
  } catch (e) {
    const err = e as Error & { cause?: { code?: string; message?: string } };
    const why = err.name === "AbortError" ? "timed out" : `${err.cause?.code || err.name}: ${err.cause?.message || err.message}`;
    throw new DaemonError(
      `cannot reach the Nullprint daemon at ${url} (${why}). Check that it is running, or pass --daemon-url with the right address.`,
    );
  } finally {
    clearTimeout(timer);
  }
  const text = await resp.text();
  if (resp.ok) {
    if (!text.trim()) return null;
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  const hint = STATUS_HINT[resp.status] || "the daemon refused the request";
  throw new DaemonError(`daemon ${method} ${pathname} -> ${resp.status} (${hint}): ${detail(resp.status, text)}`, resp.status);
}
