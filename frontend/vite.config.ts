import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { API_PROXY_PATHS } from "./proxyPaths";

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
    plugins: [react()],
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
