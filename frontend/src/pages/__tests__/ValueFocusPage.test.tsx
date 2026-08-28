import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const apiMock = vi.hoisted(() => ({
  getLowValueLeaders: vi.fn(),
  getLowValueLeaderEvents: vi.fn(),
  getCompanyFinancialAnalysis: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/components/value/LeaderCompanyQuickView", () => ({
  LeaderCompanyQuickView: ({ leader, onClose }: { leader: { stock_code: string }; onClose: () => void }) => <div role="dialog" aria-label="龙头快速判断">{leader.stock_code}<button type="button" onClick={onClose}>关闭</button></div>,
}));

import { sortLowValueLeaders, ValueFocusPage } from "../ValueFocusPage";

const item = {
  id: "lvpool_1", market: "CN", stock_code: "002371.SZ", company_name: "北方华创", industry_code: "L3-1", industry_name: "半导体设备",
  leader_rank: 1, leader_score: 86.2, current_price: 300, fair_value_low: 310, fair_value_mid: 360, fair_value_high: 400,
  valuation_status: "DEEPLY_UNDERVALUED", historical_valuation_status: "CHEAP", support_status: "AVAILABLE", entry_level: "ATTENTION",
  pool_status: "ACTIVE", source_pool_id: "pool-1", source_as_of: "2026-08-24", entered_at: "2026-08-24T00:00:00Z", removed_at: null,
  updated_at: "2026-08-24T01:00:00Z", enter_reason: "DEEPLY_UNDERVALUED", remove_reason: null, metadata: {},
  risk_overall: "HIGH", value_trap_risk: "HIGH_TRAP_RISK", material_risk_count: 2,
  top_risk_types: ["FINANCIAL_PROFIT_DECLINE", "FINANCIAL_CASH_FLOW"], risk_summary: "发现 1 项明显风险、1 项需要继续观察的问题。",
  risk_as_of: "2026-08-24", risk_snapshot_status: "READY",
};

describe("ValueFocusPage", () => {
  it("reads the automatic low-value leader pool and opens Quick View", async () => {
    apiMock.getLowValueLeaders.mockResolvedValue({ items: [item], total: 1, last_evaluated_at: item.updated_at });
    apiMock.getLowValueLeaderEvents.mockResolvedValue({
      event_date: "2026-08-24", entered: 1, exited: 1, total: 2,
      items: [
        { id: "event-enter", market: "CN", stock_code: item.stock_code, company_name: item.company_name, industry_code: item.industry_code, industry_name: item.industry_name, event_type: "ENTER_LOW_VALUE", before_status: "FAIR", after_status: "DEEPLY_UNDERVALUED", current_price: 300, fair_value_mid: 360, valuation_status: "DEEPLY_UNDERVALUED", event_date: "2026-08-24", source_as_of: "2026-08-24", metadata: {}, created_at: "2026-08-24T01:00:00Z" },
        { id: "event-exit", market: "CN", stock_code: "600519.SH", company_name: "贵州茅台", industry_code: "L3-2", industry_name: "白酒", event_type: "EXIT_LOW_VALUE", before_status: "UNDERVALUED", after_status: "FAIR", current_price: 1400, fair_value_mid: 1350, valuation_status: "FAIR", event_date: "2026-08-24", source_as_of: "2026-08-24", metadata: {}, created_at: "2026-08-24T01:00:00Z" },
      ],
    });
    const first = render(<MemoryRouter><ValueFocusPage /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "低估龙头池" })).toBeInTheDocument();
    expect(apiMock.getLowValueLeaders).toHaveBeenCalledTimes(1);
    expect(apiMock.getLowValueLeaderEvents).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "今日变化" })).toBeInTheDocument();
    expect(screen.getByText("新增 1")).toBeInTheDocument();
    expect(screen.getByText("退出 1")).toBeInTheDocument();
    expect(screen.getByText("🟠 新进入低估区域")).toBeInTheDocument();
    expect(screen.getByText("🔵 退出低估区域")).toBeInTheDocument();
    expect(screen.getByText("深度低估")).toBeInTheDocument();
    expect(screen.getByText("当前价格")).toBeInTheDocument();
    expect(screen.getByText("合理价值中枢")).toBeInTheDocument();
    expect(screen.getByText("风险复核：有明显风险需要核验")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "问投研主管" })[0]).toHaveAttribute("href", "/ai/agent");
    expect(screen.getByRole("link", { name: /打开公司研究/ })).toHaveAttribute("href", expect.stringContaining("from_label=%E4%BD%8E%E4%BC%B0%E9%BE%99%E5%A4%B4%E6%B1%A0"));
    fireEvent.click(screen.getByRole("heading", { name: /北方华创/ }));
    expect(await screen.findByRole("dialog", { name: "龙头快速判断" })).toHaveTextContent("002371.SZ");
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByText("🟠 新进入低估区域"));
    expect(await screen.findByRole("dialog", { name: "龙头快速判断" })).toHaveTextContent("002371.SZ");
    expect(apiMock.getCompanyFinancialAnalysis).not.toHaveBeenCalled();
    first.unmount();
    render(<MemoryRouter><ValueFocusPage /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "低估龙头池" })).toBeInTheDocument();
    expect(apiMock.getLowValueLeaders).toHaveBeenCalledTimes(1);
    expect(apiMock.getLowValueLeaderEvents).toHaveBeenCalledTimes(1);
  });

  it("uses the low-value empty state", async () => {
    apiMock.getLowValueLeaders.mockResolvedValue({ items: [], total: 0, last_evaluated_at: "2026-08-24T01:00:00Z" });
    apiMock.getLowValueLeaderEvents.mockResolvedValue({ event_date: null, entered: 0, exited: 0, items: [], total: 0 });
    render(<MemoryRouter><ValueFocusPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "刷新结果" }));
    expect(await screen.findByText("当前没有进入低估区域的行业龙头")).toBeInTheDocument();
    expect(screen.queryByText("还没有重点关注公司")).not.toBeInTheDocument();
  });

  it("uses deterministic research-priority ordering without changing pool members", () => {
    const deepHighQuality = { ...item, id: "deep-high", stock_code: "000002.SZ", company_name: "深度质量", leader_score: 90, current_price: 90, fair_value_mid: 100, historical_valuation_status: "NORMAL" };
    const deepWideDiscount = { ...item, id: "deep-wide", stock_code: "000003.SZ", company_name: "深度空间", leader_score: 80, current_price: 50, fair_value_mid: 100, historical_valuation_status: "NORMAL" };
    const deepNarrowDiscount = { ...item, id: "deep-narrow", stock_code: "000004.SZ", company_name: "深度较近", leader_score: 80, current_price: 75, fair_value_mid: 100, historical_valuation_status: "CHEAP" };
    const underValued = { ...item, id: "under", stock_code: "000001.SZ", company_name: "低估但高分", valuation_status: "UNDERVALUED", leader_score: 99 };
    const source = [underValued, deepNarrowDiscount, deepWideDiscount, deepHighQuality];

    const ranked = sortLowValueLeaders(source);

    expect(ranked.map((row) => row.stock_code)).toEqual(["000002.SZ", "000003.SZ", "000004.SZ", "000001.SZ"]);
    expect(source.map((row) => row.stock_code)).toEqual(["000001.SZ", "000004.SZ", "000003.SZ", "000002.SZ"]);

    const historyTie = sortLowValueLeaders([
      { ...item, id: "normal", stock_code: "000006.SZ", leader_score: 80, current_price: 70, fair_value_mid: 100, historical_valuation_status: "NORMAL", support_status: "INSUFFICIENT_DATA" },
      { ...item, id: "cheap", stock_code: "000005.SZ", leader_score: 80, current_price: 70, fair_value_mid: 100, historical_valuation_status: "CHEAP", support_status: "AVAILABLE" },
    ]);
    expect(historyTie.map((row) => row.stock_code)).toEqual(["000005.SZ", "000006.SZ"]);
    expect(sortLowValueLeaders(historyTie).map((row) => row.stock_code)).toEqual(["000005.SZ", "000006.SZ"]);
  });
});
