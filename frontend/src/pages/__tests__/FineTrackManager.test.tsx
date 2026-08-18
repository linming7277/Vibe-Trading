import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { FineTrackManager } from "../FineTrackManager";

const industry = {
  industry_code: "881321.SH", industry_name: "半导体设备", industry_level: "TDX_RESEARCH_TERMINAL",
  raw_industry_level: "TDX_RESEARCH_LEVEL_3", level: 3 as const, is_terminal: true,
  tdx_class_code: "X", level1_code: "L1", level1_name: "电子", level2_code: "L2",
  level2_name: "半导体", level3_code: "881321.SH", level3_name: "半导体设备",
  terminal_level: 3 as const, member_count: 25, as_of: "2026-08-17", source: {},
};

vi.mock("@/lib/api", () => ({ api: {
  getFineTrackIndustries: vi.fn(async () => ({ items: [industry], total: 1 })),
  getAllLevel3Leaders: vi.fn(async () => ({
    as_of: "2026-08-14", total: 1, snapshot_status: "ready", items: { "881321.SH": [{
      level1_code: "L1", level1_name: "电子", level2_code: "L2", level2_name: "半导体",
      level3_code: "881321.SH", level3_name: "半导体设备", stock_code: "002371.SZ",
      stock_name: "北方华创", leader_rank: 1, leader_score: 76.8109,
      leader_formula_version: "value-leader-v2.0.0", coverage: 1,
      eligibility_status: "eligible", eligibility_reasons: [], metric_applicability_notes: [],
      as_of: "2026-08-14", component_scores: {
        industry_position: 97.4, profitability: 72.2, growth_stability: 76.6,
        cash_flow: 77.7, valuation: 59.8, governance_risk: 59.1,
      },
    }] },
  })),
  getCompanyFinancialAnalysis: vi.fn(async () => ({
    feature_status: "READY", forecast_status: "PARTIAL", historical_cutoff: "2026-06-30",
    data_gaps: [], feature: { trends: { growth_trend: "IMPROVING" }, data_quality: { annual_period_count: 5 } },
  })),
  getCompanyFinancialDossier: vi.fn(async () => ({ chat_entries: [] })),
} }));

describe("Value Line level3 leaders", () => {
  it("shows every terminal industry with its concrete top leader", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><FineTrackManager /></MemoryRouter>);
    expect(await screen.findByText("北方华创")).toBeInTheDocument();
    expect(screen.getByText("76.8")).toBeInTheDocument();
    expect(screen.getByText("半导体设备")).toBeInTheDocument();
    expect(screen.getByText(/显示 1 \/ 1 个三级/)).toBeInTheDocument();
    expect(screen.getByText(/不用于跨行业比较/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看 北方华创 研究摘要" }));
    expect(await screen.findByRole("dialog", { name: "公司研究摘要" })).toBeInTheDocument();
    expect(screen.getByText("为什么入选")).toBeInTheDocument();
    expect(screen.getByText("当前亮点")).toBeInTheDocument();
    expect(screen.getByText("重点复核")).toBeInTheDocument();
    expect(screen.getByText("关键财务与研究进度")).toBeInTheDocument();
    expect(screen.getByText("部分可用")).toBeInTheDocument();
    const companyHref = screen.getByRole("link", { name: /打开公司研究/ }).getAttribute("href") || "";
    expect(new URL(companyHref, "http://localhost").searchParams.get("from")).toBe("/value");
    expect(new URL(companyHref, "http://localhost").searchParams.get("tab")).toBe("overview");
    expect(screen.queryByRole("link", { name: /完整财报/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /问财报研究员/ }));
    expect(await screen.findByRole("region", { name: "财报研究员对话" })).toBeInTheDocument();
    expect(screen.getByText("北方华创 · 财报研究员")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("问 北方华创 的财报…")).toBeInTheDocument();
  });
});
