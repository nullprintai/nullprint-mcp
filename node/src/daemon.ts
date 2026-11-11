// HTTP client for the local Nullprint daemon. Mirrors mcp/nullprint_mcp/daemon.py:
// same address discovery order, same API-key source, same readable error messages.
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
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
  return resolve()[0];
}

/** [base URL, found via daemon.json?] */
function resolve(): [string, boolean] {
  if (pinnedUrl) return [pinnedUrl, false];
  const envUrl = process.env.NULLPRINT_DAEMON_URL || process.env.SUPLOGIN_DAEMON_URL;
  if (envUrl) return [envUrl.replace(/\/+$/, ""), false];
  const envPort = process.env.NULLPRINT_PORT;
  if (envPort) return [`http://${DEFAULT_HOST}:${envPort}`, false];
  const info = readDaemonJson();
  if (!info) {
    throw new DaemonError(
      `the Nullprint daemon does not look like it is running: no ${path.join(runtimeDir(), "daemon.json")}. ` +
        "Start the daemon (the desktop app starts it for you), or point this server at one with --daemon-url http://127.0.0.1:<port>.",
    );
  }
  const port = info.port;
  if (!port) throw new DaemonError(`${path.join(runtimeDir(), "daemon.json")} has no 'port' field; restart the daemon or pass --daemon-url.`);
  return [`http://${DEFAULT_HOST}:${port}`, true];
}

const NOT_DAEMON =
  "the answer is not in the Nullprint daemon's format, so it probably did not come from the daemon — a system proxy " +
  "(or HTTP_PROXY / HTTPS_PROXY) may have intercepted the request; check the proxy settings, e.g. add 127.0.0.1 and " +
  "localhost to its exceptions";

function isLoopback(url: string): boolean {
  const host = new URL(url).hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (host === "localhost") return true;
  if (net.isIPv4(host)) return host.startsWith("127.");
  return host === "::1";
}

/**
 * One HTTP call to the daemon → {status, text}. 连本机 daemon(127.0.0.1 / localhost / ::1)不走代理:Node 设了
 * NODE_USE_ENV_PROXY=1 时 fetch 和 http 的默认 agent 都按 HTTP(S)_PROXY 走代理,代理没把回环地址列进例外就会回空 503
 * (0.2.27,员工机)。回环地址用自己新建的 http.Agent(不读代理环境);别的地址照旧用 fetch。
 */
async function call(url: string, init: { method?: string; headers?: Record<string, string>; body?: string; timeoutMs: number }): Promise<{ status: number; text: string; bytes: Buffer }> {
  if (!(url.startsWith("http:") && isLoopback(url))) {
    const resp = await fetch(url, { method: init.method, headers: init.headers, body: init.body, signal: AbortSignal.timeout(init.timeoutMs) });
    const bytes = Buffer.from(await resp.arrayBuffer());
    return { status: resp.status, text: bytes.toString("utf8"), bytes };
  }
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method: init.method || "GET", headers: init.headers, agent: new http.Agent() }, (res) => {
      const chunks: Buffer[] = []; // 0e160:截图是二进制,先收字节再按需转文字
      res.on("data", (c: Buffer) => chunks.push(c));
      res.on("end", () => {
        const bytes = Buffer.concat(chunks);
        resolve({ status: res.statusCode || 0, text: bytes.toString("utf8"), bytes });
      });
      res.on("error", reject);
    });
    req.setTimeout(init.timeoutMs, () => req.destroy(Object.assign(new Error("timed out"), { name: "AbortError" })));
    req.on("error", (e: NodeJS.ErrnoException) => reject(Object.assign(new Error(e.message), { name: e.name, cause: e })));
    if (init.body !== undefined) req.write(init.body);
    req.end();
  });
}

// 0e121:daemon.json 找到的地址核过是自己的 daemon 就记下(地址 + Key),同一进程里不再核
const verified = new Set<string>();

/**
 * 0e121(同 0e120 / BUG-083 的判定):daemon.json 的端口上应答的必须是这个数据目录的 daemon——带它的 Key 调要鉴权的
 * GET /sessions,200 且回 JSON 数组才算。daemon 退出后端口被别的程序占了、或者是另一个数据目录的 daemon,都说清楚。
 */
async function checkOwnDaemon(url: string, key: string | null): Promise<void> {
  const id = `${url} ${key ?? ""}`;
  if (verified.has(id)) return;
  const file = path.join(runtimeDir(), "daemon.json");
  let resp: { status: number; text: string };
  try {
    resp = await call(`${url}/sessions`, { headers: key ? { "X-API-Key": key } : {}, timeoutMs: TIMEOUT_MS });
  } catch {
    throw new DaemonError(
      `cannot reach the Nullprint daemon at ${url} (the port recorded in ${file}). It is probably not running — ` +
        "open the Nullprint desktop app (it starts the daemon and rewrites daemon.json), then retry.",
    );
  }
  let ours = false;
  try {
    ours = resp.status === 200 && Array.isArray(JSON.parse(resp.text));
  } catch {
    ours = false;
  }
  if (!ours) {
    throw new DaemonError(
      `the port recorded in ${file} (${url.split(":").pop()}) is answered by something that is not the Nullprint daemon ` +
        "of this data directory — the daemon has probably exited and another program now uses that port, or another " +
        "Nullprint data directory's daemon is there. Open the Nullprint desktop app (it restarts the daemon on a free " +
        "port and rewrites daemon.json), then retry; or pass --daemon-url with the right address.",
    );
  }
  verified.add(id);
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
    if (r === "dialog_open") {
      const dd = d as Record<string, unknown>;
      return `a JavaScript ${dd.type || "dialog"} is open on the page (${JSON.stringify(dd.message || "")}); the page is blocked until it is answered — call session_dialog with action 'accept' or 'dismiss', then retry.`;
    }
    if (r === "no_dialog") return "no dialog is open on the session's page.";
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
  const text = (await send(method, pathname, opts)).toString("utf8");
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Same as request, but returns the raw body (0e160: screenshots). */
export async function requestBytes(method: string, pathname: string, opts: RequestOptions = {}): Promise<Buffer> {
  return send(method, pathname, opts);
}

async function send(method: string, pathname: string, opts: RequestOptions): Promise<Buffer> {
  const [url, discovered] = resolve();
  const headers: Record<string, string> = { Accept: "application/json" };
  const k = apiKey();
  if (k) headers["X-API-Key"] = k;
  if (discovered) await checkOwnDaemon(url, k);
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
  let resp: { status: number; text: string; bytes: Buffer };
  try {
    resp = await call(target, { method, headers, body, timeoutMs: TIMEOUT_MS });
  } catch (e) {
    verified.delete(`${url} ${k ?? ""}`); // 连不上了:下次重核(daemon 可能换了端口)
    const err = e as Error & { cause?: { code?: string; message?: string } };
    const why = err.name === "AbortError" || err.name === "TimeoutError" ? "timed out" : `${err.cause?.code || err.name}: ${err.cause?.message || err.message}`;
    throw new DaemonError(
      `cannot reach the Nullprint daemon at ${url} (${why}). Check that it is running, or pass --daemon-url with the right address.`,
    );
  }
  const text = resp.text;
  if (resp.status >= 200 && resp.status < 300) return resp.bytes;
  let hint = STATUS_HINT[resp.status] || "the daemon refused the request";
  try {
    JSON.parse(text);
  } catch {
    // daemon 的错误都是 JSON;空 body / 非 JSON 多半不是 daemon 回的(0.2.27:系统代理回空 503,原提示误导人)
    hint = NOT_DAEMON;
  }
  throw new DaemonError(`daemon ${method} ${pathname} -> ${resp.status} (${hint}): ${detail(resp.status, text)}`, resp.status);
}
