import { API_PROXY_PATHS } from "../../../proxyPaths";

describe("Vite API proxy coverage", () => {
  it("proxies every deterministic strategy API family before SPA fallback", () => {
    expect(API_PROXY_PATHS).toEqual(expect.arrayContaining([
      "/strategy-runs",
      "/strategy",
      "/decision-chains",
      "/signals",
      "/committees",
      "/paper",
      "/tdx",
    ]));
  });
});
