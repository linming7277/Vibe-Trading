import './i18n';
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { Toaster } from "sonner";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { router } from "./router";
import "highlight.js/styles/github-dark-dimmed.min.css";
import "./index.css";

// Vite emits this event when a route-level chunk referenced by an already-open
// page was replaced by a newer deployment. Reload once to fetch the current
// index/chunk manifest instead of leaving the user on React Router's error page.
const CHUNK_RELOAD_KEY = "hengzhi:chunk-reload-at";
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  let lastReload = 0;
  try {
    lastReload = Number(window.sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0);
    if (Date.now() - lastReload < 30_000) return;
    window.sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
  } catch {
    // Storage may be disabled. Reloading is still the safest recovery.
  }
  window.location.reload();
});

const prefetchMiniEquityChart = () => {
  void import("@/components/charts/MiniEquityChart");
};

const idleWindow = window as Window & {
  requestIdleCallback?: (
    callback: IdleRequestCallback,
    options?: IdleRequestOptions,
  ) => number;
};

if (typeof idleWindow.requestIdleCallback === "function") {
  idleWindow.requestIdleCallback(prefetchMiniEquityChart, { timeout: 2000 });
} else {
  window.setTimeout(prefetchMiniEquityChart, 0);
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <RouterProvider router={router} />
      <Toaster position="bottom-right" richColors closeButton duration={3500} />
    </ErrorBoundary>
  </StrictMode>
);
