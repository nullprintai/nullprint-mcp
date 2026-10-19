// Entry point: `nullprint-mcp` / `npx nullprint-mcp` (stdio transport).
// Nothing may be printed to stdout — stdout IS the MCP channel. Diagnostics go to stderr.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { DEFAULT_HOST, setDaemonUrl } from "./daemon.js";
import { TOOLS } from "./tools.js";

export const SERVER_NAME = "nullprint";
export const VERSION = "0.1.3";

export const INSTRUCTIONS = `Nullprint runs browser identities: each one is a separate browser
profile with its own frozen fingerprint, its own exit IP, and its own cookies, so
sites see independent people rather than one machine.

Typical flow: profiles_list to find the identity, profile_start to open its
browser, do the work, profile_stop when finished. Never leave a session open
after you are done — it holds a warm browser with live login state.

Identity names are case-sensitive and come from profiles_list; do not invent them.`;

const HELP = `usage: nullprint-mcp [--daemon-url URL | --port PORT [--host HOST]]

Nullprint MCP server (stdio) — drive browser identities from any MCP client.

  --daemon-url URL  full daemon base URL, e.g. http://127.0.0.1:7801.
                    Default: read the running daemon's port from ~/.nullprint/daemon.json.
  --port PORT       shorthand for --daemon-url http://${DEFAULT_HOST}:PORT
  --host HOST       host to use with --port (default ${DEFAULT_HOST})
  -h, --help        show this help
  -V, --version     show the version
`;

export function parseArgs(argv: string[]): { daemonUrl: string | null } {
  let daemonUrl: string | null = null;
  let port: string | null = null;
  let host = DEFAULT_HOST;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${a} needs a value`);
      return v;
    };
    if (a === "--daemon-url") daemonUrl = next();
    else if (a.startsWith("--daemon-url=")) daemonUrl = a.slice("--daemon-url=".length);
    else if (a === "--port") port = next();
    else if (a.startsWith("--port=")) port = a.slice("--port=".length);
    else if (a === "--host") host = next();
    else if (a.startsWith("--host=")) host = a.slice("--host=".length);
    else if (a === "-h" || a === "--help") {
      process.stderr.write(HELP);
      process.exit(0);
    } else if (a === "-V" || a === "--version") {
      process.stderr.write(`nullprint-mcp ${VERSION}\n`);
      process.exit(0);
    } else throw new Error(`unknown argument: ${a}\n${HELP}`);
  }
  if (daemonUrl && port) throw new Error("pass either --daemon-url or --port, not both");
  if (port !== null) {
    if (!/^\d+$/.test(port)) throw new Error(`--port must be a number, got ${port}`);
    daemonUrl = `http://${host}:${port}`;
  }
  return { daemonUrl };
}

export function buildServer(daemonUrl: string | null = null): McpServer {
  if (daemonUrl !== null) setDaemonUrl(daemonUrl);
  const server = new McpServer({ name: SERVER_NAME, version: VERSION }, { instructions: INSTRUCTIONS });
  for (const t of TOOLS) {
    server.registerTool(t.name, { description: t.description, inputSchema: t.shape }, async (args: Record<string, unknown>) => {
      try {
        const result = await t.handler(args ?? {});
        return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return { content: [{ type: "text" as const, text: (e as Error).message }], isError: true };
      }
    });
  }
  return server;
}

async function main(): Promise<void> {
  let args: { daemonUrl: string | null };
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (e) {
    process.stderr.write(`nullprint-mcp: ${(e as Error).message}\n`);
    process.exit(2);
  }
  const server = buildServer(args.daemonUrl);
  await server.connect(new StdioServerTransport());
}

const isMain = process.argv[1] && /nullprint-mcp|index\.js$/.test(process.argv[1]);
if (isMain) {
  main().catch((e) => {
    process.stderr.write(`nullprint-mcp: ${(e as Error).message}\n`);
    process.exit(1);
  });
}
