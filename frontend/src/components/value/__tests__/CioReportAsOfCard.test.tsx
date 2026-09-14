import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { CioReportAsOfCard } from "../CioReportAsOfCard";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getCioReport: vi.fn(),
  },
}));

function mockApi(value: Record<string, unknown> | Error) {
  vi.mocked(api.getCioReport).mockImplementation(() =>
    value instanceof Error ? Promise.reject(value) : Promise.resolve(value as never));
}

describe("CioReportAsOfCard", () => {
  beforeEach(() => {
    vi.mocked(api.getCioReport).mockReset();
  });

  it("两字段都有 → 渲染「价格截至」「叙述截至」两行", async () => {
    mockApi({ stock_code: "003012.SZ", price_as_of: "2026-09-11", narrative_as_of: "2026-09-07" });
    render(<CioReportAsOfCard stockCode="003012.SZ" />);
    await waitFor(() => expect(screen.getByText("价格截至 2026-09-11")).toBeTruthy());
    expect(screen.getByText("叙述截至 2026-09-07")).toBeTruthy();
  });

  it("字段缺失 → 不出现「价格截至」编造", async () => {
    mockApi({ stock_code: "003012.SZ" });
    render(<CioReportAsOfCard stockCode="003012.SZ" />);
    await waitFor(() => expect(screen.getByTestId("cio-as-of-card")).toBeTruthy());
    expect(screen.queryByText(/价格截至/)).toBeNull();
    expect(screen.queryByText(/叙述截至/)).toBeNull();
  });

  it("请求失败 → 显示读取失败且不编日期", async () => {
    mockApi(new Error("network down"));
    render(<CioReportAsOfCard stockCode="003012.SZ" />);
    await waitFor(() => expect(screen.getByText("CIO 报告暂时读取失败。")).toBeTruthy());
    expect(screen.queryByText(/价格截至/)).toBeNull();
  });

  it("正文不出现买入/卖出等交易词", async () => {
    mockApi({ stock_code: "003012.SZ", price_as_of: "2026-09-11", narrative_as_of: "2026-09-07" });
    const { container } = render(<CioReportAsOfCard stockCode="003012.SZ" />);
    await waitFor(() => expect(screen.getByText(/价格截至/)).toBeTruthy());
    for (const word of ["买入", "卖出", "加仓", "减仓", "止盈", "止损"]) {
      expect(container.textContent).not.toContain(word);
    }
  });
});
