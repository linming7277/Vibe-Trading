import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "fs";
import path from "path";
import { API_PROXY_PATHS } from "./proxyPaths";

type ClientAccess = {
  ip: string;
  first_seen: string;
  last_seen: string;
  request_count: number;
};

const ACCESS_REFRESH_MS = 5_000;
const ACCESS_RETENTION_MS = 24 * 60 * 60 * 1_000;
const ACCESS_LIMIT = 50;

function normalizedClientIp(address: string | undefined): string | null {
  if (!address) return null;
  const normalized = address.replace(/^::ffff:/, "");
  return normalized === "::1" ? "127.0.0.1" : normalized;
}

function writeClientAccess(accessFile: string, ip: string, now: Date): void {
  try {
    const cutoff = now.getTime() - ACCESS_RETENTION_MS;
    let clients: ClientAccess[] = [];
    if (existsSync(accessFile)) {
      const parsed: unknown = JSON.parse(readFileSync(accessFile, "utf8"));
      if (parsed && typeof parsed === "object" && Array.isArray((parsed as { clients?: unknown }).clients)) {
        clients = (parsed as { clients: unknown[] }).clients.filter((item): item is ClientAccess => (
          Boolean(item) && typeof item === "object" && typeof (item as ClientAccess).ip === "string" &&
          typeof (item as ClientAccess).last_seen === "string"
        )).filter((item) => Date.parse(item.last_seen) >= cutoff);
      }
    }
    const timestamp = now.toISOString();
    const current = clients.find((item) => item.ip === ip);
    if (current) {
      current.last_seen = timestamp;
      current.request_count = Math.max(0, Number(current.request_count) || 0) + 1;
    } else {
      clients.push({ ip, first_seen: timestamp, last_seen: timestamp, request_count: 1 });
    }
    clients.sort((left, right) => Date.parse(right.last_seen) - Date.parse(left.last_seen));
    mkdirSync(path.dirname(accessFile), { recursive: true });
    const temporary = `${accessFile}.${process.pid}.tmp`;
    writeFileSync(temporary, JSON.stringify({ updated_at: timestamp, clients: clients.slice(0, ACCESS_LIMIT) }, null, 2), "utf8");
    renameSync(temporary, accessFile);
  } catch {
    // Access telemetry must never interrupt Vite, proxying, or a user request.
  }
}

function clientAccessPlugin() {
  const accessFile = path.resolve(__dirname, "../.launcher/access-clients.json");
  const recentlyWritten = new Map<string, number>();
  return {
    name: "hengzhi-client-access-monitor",
    configureServer(server: { middlewares: { use: (handler: (req: { socket: { remoteAddress?: string } }, res: unknown, next: () => void) => void) => void } }) {
      server.middlewares.use((req, _res, next) => {
        const ip = normalizedClientIp(req.socket.remoteAddress);
        const now = Date.now();
        if (ip && now - (recentlyWritten.get(ip) || 0) >= ACCESS_REFRESH_MS) {
          recentlyWritten.set(ip, now);
          writeClientAccess(accessFile, ip, new Date(now));
        }
        next();
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_URL || "http://127.0.0.1:8899";
  const allowedHosts = (env.VITE_ALLOWED_HOST || "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
  const apiProxy = {
    target: apiTarget,
    changeOrigin: true,
    // The browser origin is the LAN URL (for example
    // http://192.168.110.49:5899), while the Vite proxy talks to FastAPI on
    // loopback. Forwarding that origin makes the backend's cross-site guard
    // reject POSTs from the financial agent even though this is a same-origin
    // request from the user's point of view. The proxy is local and already
    // changes Host, so remove Origin before forwarding to FastAPI.
    configure(proxy) {
      proxy.on("proxyReq", (proxyReq) => {
        proxyReq.removeHeader("origin");
      });
    },
  };
  const apiProxyWithHtmlFallback = {
    ...apiProxy,
    bypass(req: { headers: { accept?: string } }) {
      if (req.headers.accept?.includes("text/html")) {
        return "/index.html";
      }
    },
  };

  return {
    plugins: [react(), clientAccessPlugin()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    server: {
      host: "0.0.0.0",
      port: 5899,
      ...(allowedHosts.length ? { allowedHosts } : {}),
      proxy: {
        ...Object.fromEntries(API_PROXY_PATHS.map((p) => [p, apiProxy])),
        // SPA RunDetail page — only the two-segment ``/runs/{id}``
        // form should fall back to ``index.html`` on browser navigation.
        // ``/runs/{id}/code`` and ``/runs/{id}/pine`` are API-only and
        // must keep proxying to the backend even when Accept is text/html.
        "^/runs/[^/]+/?$": apiProxyWithHtmlFallback,
        "/runs": apiProxy,
        "/reports": apiProxyWithHtmlFallback,
        "/correlation": apiProxyWithHtmlFallback,
        "^/alpha(?:/|$)": apiProxy,
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-react": ["react", "react-dom", "react-router"],
            "vendor-charts": ["echarts"],
          },
        },
      },
    },
  };
});
