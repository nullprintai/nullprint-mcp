// Node build of nullprint-mcp: run the bundled dist/index.js over stdio against a fake daemon
// and check the same contract the Python tests check (17 tools, stop fallback, readable errors,
// no purge tool, stdout stays clean).
import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const DIST = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dist", "index.js");

function fakeDaemon(routes) {
  const seen = [];
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const url = new URL(req.url, "http://x");
      seen.push({ method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), body: body ? JSON.parse(body) : null, key: req.headers["x-api-key"] });
      const r = routes[`${req.method} ${url.pathname}`];
      if (!r) {
        res.writeHead(404, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ detail: "no such route" }));
      }
      const [status, payload] = typeof r === "function" ? r(seen[seen.length - 1]) : r;
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(payload));
    });
  });
  return new Promise((resolve) => srv.listen(0, "127.0.0.1", () => resolve({ srv, port: srv.address().port, seen })));
}

class Client {
  constructor(extraArgs = [], env = {}) {
    this.proc = spawn(process.execPath, [DIST, ...extraArgs], { env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
    this.buf = "";
    this.pending = new Map();
    this.stderr = "";
    this.proc.stderr.on("data", (d) => (this.stderr += d));
    this.proc.stdout.on("data", (d) => {
      this.buf += d;
      let i;
      while ((i = this.buf.indexOf("\n")) >= 0) {
        const line = this.buf.slice(0, i);
        this.buf = this.buf.slice(i + 1);
        if (!line.trim()) continue;
        const msg = JSON.parse(line); // every stdout line must be JSON-RPC
        if (msg.id !== undefined && this.pending.has(msg.id)) {
          this.pending.get(msg.id)(msg);
          this.pending.delete(msg.id);
        }
      }
    });
    this.id = 0;
  }
  send(method, params) {
    const id = ++this.id;
    this.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    return new Promise((resolve) => this.pending.set(id, resolve));
  }
  notify(method, params) {
    this.proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
  }
  async init() {
    const r = await this.send("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "0" } });
    this.notify("notifications/initialized", {});
    return r;
  }
  call(name, args = {}) {
    return this.send("tools/call", { name, arguments: args });
  }
  close() {
    this.proc.kill();
  }
}

test("bundle exists and is executable", () => {
  assert.ok(fs.existsSync(DIST), "run npm run build first");
  assert.ok(fs.readFileSync(DIST, "utf8").startsWith("#!/usr/bin/env node"));
});

test("initialize + tools/list: server name nullprint, exactly the 17 tools, no purge", async () => {
  const c = new Client([], { NULLPRINT_DAEMON_URL: "http://127.0.0.1:1" });
  const init = await c.init();
  assert.equal(init.result.serverInfo.name, "nullprint");
  assert.match(init.result.instructions, /profiles_list/);
  const tools = (await c.send("tools/list", {})).result.tools;
  assert.deepEqual(
    tools.map((t) => t.name),
    ["profiles_list", "profile_start", "profile_stop", "profile_create", "profile_settings", "profile_checkup", "proxy_check", "profile_delete", "trash_list", "profile_restore",
      "session_goto", "session_snapshot", "session_click_selector", "session_fill_selector", "session_press", "session_eval", "session_tabs", "session_switch_tab", "session_dialog"],
  );
  for (const t of tools) assert.ok(t.inputSchema && t.inputSchema.type === "object", t.name);
  assert.ok(!tools.some((t) => /purge|trash_delete|empty/.test(t.name)));
  const src = fs.readFileSync(DIST, "utf8");
  assert.ok(!/\/trash\/\$\{[^}]+\}"\s*\)/.test(src) || !/DELETE",\s*`\/trash\//.test(src), "permanent deletion must never be wired in");
  assert.match(tools.find((t) => t.name === "profile_start").description, /bypasses Nullprint's consistency guards/);
  c.close();
});

test("profiles_list sends the API key and returns the daemon body", async () => {
  const { srv, port, seen } = await fakeDaemon({ "GET /profiles": [200, [{ name: "a", status: "idle" }]] });
  const c = new Client(["--port", String(port)], { NULLPRINT_API_KEY: "np_test" });
  await c.init();
  const r = await c.call("profiles_list");
  assert.equal(r.result.isError, undefined);
  assert.deepEqual(JSON.parse(r.result.content[0].text), [{ name: "a", status: "idle" }]);
  assert.equal(seen[0].key, "np_test");
  c.close();
  srv.close();
});

test("profile_stop falls back to the identity-level stop on 409 but not on other errors", async () => {
  const { srv, port, seen } = await fakeDaemon({
    "DELETE /sessions/win": [409, { detail: "session not open" }],
    "POST /profiles/win/stop": [200, { stopped: ["launch"], status: "idle" }],
    "DELETE /sessions/bad": [422, { detail: "nope" }],
  });
  const c = new Client(["--daemon-url", `http://127.0.0.1:${port}`]);
  await c.init();
  const r = await c.call("profile_stop", { name: "win" });
  assert.deepEqual(JSON.parse(r.result.content[0].text), { name: "win", stopped: true, closed: ["launch"], status: "idle" });
  assert.deepEqual(seen.map((s) => `${s.method} ${s.path}`), ["DELETE /sessions/win", "POST /profiles/win/stop"]);
  const bad = await c.call("profile_stop", { name: "bad" });
  assert.equal(bad.result.isError, true);
  assert.match(bad.result.content[0].text, /422 \(the daemon rejected the arguments\): nope/);
  c.close();
  srv.close();
});

test("BUG-024: profile_start passes the daemon's cdp_unavailable and uses cdp_hint as cdp_note", async () => {
  const hint = "External debugging is off for this profile (the default). ...";
  const { srv, port } = await fakeDaemon({
    "POST /sessions/off": [201, { name: "off", url: "about:blank", cdp_endpoint: null, cdp_unavailable: "external_debug_off", cdp_hint: hint }],
    "POST /sessions/old": [201, { name: "old", url: "about:blank" }],
  });
  const c = new Client(["--port", String(port)]);
  try {
    await c.init();
    const off = JSON.parse((await c.call("profile_start", { name: "off" })).result.content[0].text);
    assert.equal(off.cdp_endpoint, null);
    assert.equal(off.cdp_unavailable, "external_debug_off");
    assert.equal(off.cdp_note, hint);
    const old = JSON.parse((await c.call("profile_start", { name: "old" })).result.content[0].text);
    assert.match(old.cdp_note, /external debugging/);
  } finally {
    c.close();
    srv.close();
  }
});

test("profile_start adds cdp_endpoint / cdp_note; profile_create drops unset fields; checkup passes live", async () => {
  const { srv, port, seen } = await fakeDaemon({
    "POST /sessions/ff": [200, { name: "ff", url: "about:blank" }],
    "POST /sessions/ch": [200, { name: "ch", ws_endpoint: "ws://127.0.0.1:9/devtools" }],
    "POST /profiles": (s) => [201, { name: s.body.name, os: "windows", created: "2026-10-10" }],
    "GET /profiles/ch/checkup": [200, { summary: "pass", checks: [] }],
  });
  const c = new Client(["--port", String(port)]);
  await c.init();
  const ff = JSON.parse((await c.call("profile_start", { name: "ff" })).result.content[0].text);
  assert.equal(ff.cdp_endpoint, null);
  assert.match(ff.cdp_note, /No CDP endpoint/);
  const ch = JSON.parse((await c.call("profile_start", { name: "ch" })).result.content[0].text);
  assert.equal(ch.cdp_endpoint, "ws://127.0.0.1:9/devtools");
  assert.equal(ch.cdp_note, undefined);
  await c.call("profile_create", { name: " new-id ", proxy: "socks5://h:1" });
  assert.deepEqual(seen.at(-1).body, { name: "new-id", proxy: "socks5://h:1" });
  await c.call("profile_checkup", { name: "ch", live: true });
  assert.deepEqual(seen.at(-1).query, { live: "true" });
  c.close();
  srv.close();
});

test("readable errors: bad name, quota 403, daemon down", async () => {
  const { srv, port } = await fakeDaemon({ "POST /profiles": [403, { detail: { reason: "quota_exceeded", used: 5, limit: 5 } }] });
  const c = new Client(["--port", String(port)]);
  await c.init();
  const bad = await c.call("session_goto", { name: "a/b", url: "https://x" });
  assert.equal(bad.result.isError, true);
  assert.match(bad.result.content[0].text, /invalid identity name/);
  const quota = await c.call("profile_create", { name: "q" });
  assert.match(quota.result.content[0].text, /profile quota reached \(5 of 5 identities used\)/);
  c.close();
  srv.close();
  const down = new Client(["--port", "1"]);
  await down.init();
  const r = await down.call("trash_list");
  assert.equal(r.result.isError, true);
  assert.match(r.result.content[0].text, /cannot reach the Nullprint daemon at http:\/\/127\.0\.0\.1:1/);
  down.close();
});

// 安全体检 H-5(2026-10-21):'%2F' / '?' / '#' / '\' 曾经能过,名字原样拼进 URL,daemon 解码后路由到 MCP 不开放的接口
test("1021 H-5: names that would leave their path segment never reach the daemon; good names are escaped", async () => {
  const { srv, port, seen } = await fakeDaemon({ "GET /profiles/my%20shop/checkup": [200, { summary: "pass" }] });
  const c = new Client(["--port", String(port)]);
  try {
    await c.init();
    for (const bad of ["a%2F..%2Fsettings", "victim%2Fcookies%2Fexport?", "v?x", "v#x", "a\\b", ".x", "a:b", "CON", "x".repeat(121)]) {
      for (const tool of ["profile_checkup", "profile_delete", "session_snapshot"]) {
        const r = await c.call(tool, { name: bad });
        assert.equal(r.result.isError, true, `${tool}(${bad})`);
        assert.match(r.result.content[0].text, /invalid identity name/);
      }
    }
    assert.equal(seen.length, 0);
    const ok = await c.call("profile_checkup", { name: "my shop" });
    assert.notEqual(ok.result.isError, true);
    assert.equal(seen[0].path, "/profiles/my%20shop/checkup");
  } finally {
    c.close();
    srv.close();
  }
});

test("no daemon.json and no env: says the daemon is not running", async () => {
  const home = fs.mkdtempSync(path.join(process.env.TMPDIR || "/tmp", "np-home-"));
  const c = new Client([], { NULLPRINT_HOME: home, NULLPRINT_DAEMON_URL: "", SUPLOGIN_DAEMON_URL: "", NULLPRINT_PORT: "" });
  await c.init();
  const r = await c.call("profiles_list");
  assert.equal(r.result.isError, true);
  assert.match(r.result.content[0].text, /does not look like it is running: no .*daemon\.json/);
  c.close();
});

test("--help goes to stderr, stdout stays empty; bad args exit 2", async () => {
  const run = (args) =>
    new Promise((resolve) => {
      const p = spawn(process.execPath, [DIST, ...args]);
      let out = "", err = "";
      p.stdout.on("data", (d) => (out += d));
      p.stderr.on("data", (d) => (err += d));
      p.on("close", (code) => resolve({ code, out, err }));
    });
  const h = await run(["--help"]);
  assert.equal(h.code, 0);
  assert.equal(h.out, "");
  assert.match(h.err, /--daemon-url/);
  const b = await run(["--bogus"]);
  assert.equal(b.code, 2);
  const both = await run(["--port", "1", "--daemon-url", "http://x"]);
  assert.equal(both.code, 2);
  assert.match(both.err, /not both/);
});

test("profile_create takes force_dark as launch.forceDark; profile_settings merges it into the existing launch settings", async () => {
  const { srv, port, seen } = await fakeDaemon({
    "POST /profiles": (s) => [201, { name: s.body.name, os: "macos", created: "2026-10-19" }],
    "GET /profiles/shop-1": [200, { name: "shop-1", engine: "chromium", meta: { launch: { colorScheme: "light", blockImages: true } } }],
    "PATCH /profiles/shop-1": [200, { ok: true }],
  });
  const c = new Client(["--port", String(port)]);
  await c.init();
  await c.call("profile_create", { name: "dark-1", force_dark: true });
  assert.deepEqual(seen.at(-1).body, { name: "dark-1", launch: { forceDark: true } });
  await c.call("profile_create", { name: "plain-1" });
  assert.deepEqual(seen.at(-1).body, { name: "plain-1" });
  const out = JSON.parse((await c.call("profile_settings", { name: "shop-1", force_dark: true })).result.content[0].text);
  assert.deepEqual(seen.slice(-2).map((s) => [s.method, s.path]), [["GET", "/profiles/shop-1"], ["PATCH", "/profiles/shop-1"]]);
  assert.deepEqual(seen.at(-1).body, { launch: { colorScheme: "light", blockImages: true, forceDark: true } });
  assert.equal(out.launch.forceDark, true);
  c.close();
  srv.close();
});

// 0.2.18:会话里的网页弹窗留给用户;弹窗开着时命令 409 dialog_open,session_dialog 处理它(同 Python 版)
test("1022 session_dialog answers the open dialog; dialog_open / no_dialog errors say what to do", async () => {
  const { srv, port, seen } = await fakeDaemon({
    "POST /sessions/shop-1/dialog": (s) =>
      s.body.action === "dismiss" ? [409, { detail: { reason: "no_dialog" } }] : [200, { ok: true, action: "accept", type: "confirm", message: "Delete?" }],
    "POST /sessions/shop-1/click_selector": [409, { detail: { reason: "dialog_open", type: "confirm", message: "Delete?" } }],
  });
  const c = new Client(["--port", String(port)]);
  try {
    await c.init();
    const blocked = await c.call("session_click_selector", { name: "shop-1", selector: "button#del" });
    assert.equal(blocked.result.isError, true);
    assert.match(blocked.result.content[0].text, /409 .*JavaScript confirm is open on the page \("Delete\?"\).*session_dialog/);
    assert.doesNotMatch(blocked.result.content[0].text, /license/);
    const ok = JSON.parse((await c.call("session_dialog", { name: "shop-1", action: "accept", prompt_text: "Alice" })).result.content[0].text);
    assert.equal(ok.type, "confirm");
    assert.deepEqual(seen.at(-1).body, { action: "accept", prompt_text: "Alice" });
    const none = await c.call("session_dialog", { name: "shop-1", action: " Dismiss " });
    assert.deepEqual(seen.at(-1).body, { action: "dismiss" });
    assert.equal(none.result.isError, true);
    assert.match(none.result.content[0].text, /409 .*no dialog is open/);
    const n = seen.length;
    const bad = await c.call("session_dialog", { name: "shop-1", action: "ok" });
    assert.equal(bad.result.isError, true);
    assert.match(bad.result.content[0].text, /action must be 'accept' or 'dismiss'/);
    assert.equal(seen.length, n);
  } finally {
    c.close();
    srv.close();
  }
});
