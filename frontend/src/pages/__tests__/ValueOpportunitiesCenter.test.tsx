import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const apiMock = vi.hoisted(() => ({
  getFocusSelection: vi.fn(),
  getValueSignals: vi.fn(),
  getValueAutomation: vi.fn(),
  getValueStrategyEventBatches: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

import { ValueFocusSelectionPage } from "../ValueFocusSelection";

const item = (stockCode: string, companyName: string, tier: "A" | "B" | "C") => ({
  tier, stock_code: stockCode, company_name: companyName, industry_code: "L3-1", industry_name: "测试行业",
  leader_rank: 1, leader_score: 88, valuation_status: "DEEPLY_UNDERVALUED", current_price: 10,
  fair_value_mid: 20, discount_to_mid: 0.5, historical_valuation_status: "CHEAP", support_status: "AVAILABLE",
  entry_level: "WATCH", risk_status: tier === "C" ? "HIGH" : "MEDIUM", value_trap_risk: "MEDIUM_TRAP_RISK",
  thesis_status: "FORMING", thesis_authority: "AI_PROVISIONAL", financial_status: "READY",
  business_profile_status: "PARTIAL", peer_count: 3,
  focus_reasons: ["当前处于深度低估区域"], focus_cautions: ["当前风险为中等，需要继续观察"],
  primary_demotion_reason: tier === "C" ? "存在已确认高等级风险，需要先完成重点复核。" : null,
  source_dates: { low_value_pool: "2026-08-27", risk_snapshot: "2026-08-27", preparation: "2026-08-27", thesis_source: "2026-08-27", thesis_created: "2026-08-27" },
});

describe("ValueFocusSelectionPage", () => {
  it("uses focus selection without requesting legacy strategy signals", async () => {
    apiMock.getFocusSelection.mockResolvedValue({
      research_as_of: "2026-08-27", total_low_value: 3, hard_c_count: 1, soft_demote_count: 1,
      A_count: 1, B_count: 1, C_count: 1, A: [item("605108.SH", "同庆楼", "A")],
      B: [item("600210.SH", "河钢资源", "B")], C: [item("000544.SZ", "中原环保", "C")],
      selection_boundary: "仅用于研究优先级。", read_only: true,
    });
    apiMock.getValueAutomation.mockResolvedValue({ enabled: true, last_status: "completed", run_time: "16:45", timezone: "Asia/Shanghai" });
    apiMock.getValueStrategyEventBatches.mockResolvedValue({ items: [], count: 0 });

    render(<MemoryRouter><ValueFocusSelectionPage /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "机会与风险" })).toBeInTheDocument();
    expect(apiMock.getFocusSelection).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "重点研究" })).toBeInTheDocument();
    expect(screen.getByText("同庆楼")).toBeInTheDocument();
    expect(screen.queryByText("河钢资源")).not.toBeInTheDocument();
    expect(screen.queryByText("中原环保")).not.toBeInTheDocument();
    expect(apiMock.getValueSignals).not.toHaveBeenCalled();
    expect(apiMock.getValueStrategyEventBatches).toHaveBeenCalledWith(undefined, 10);
    expect(screen.getByRole("heading", { name: "价值线日终任务状态" })).toBeInTheDocument();
    expect(screen.getByText("低估陷阱：中等")).toBeInTheDocument();
    expect(screen.getByText(/AI 初步逻辑，待人工复核/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /继续观察/ }));
    expect(await screen.findByText("河钢资源")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /暂缓优先研究/ }));
    expect(await screen.findByText("中原环保")).toBeInTheDocument();
  });
});
