import { describe, expect, it } from "vitest";
import { API_PROXY_PATHS } from "../../proxyPaths";

describe("Vite API proxy config", () => {
  it("proxies channel runtime endpoints", () => {
    expect(API_PROXY_PATHS).toContain("/channels");
  });

  it("proxies settings endpoints", () => {
    expect(API_PROXY_PATHS).toContain("/settings/llm");
    expect(API_PROXY_PATHS).toContain("/settings/data-sources");
  });

  it("proxies authentication endpoints", () => {
    expect(API_PROXY_PATHS).toContain("/auth");
  });
});
