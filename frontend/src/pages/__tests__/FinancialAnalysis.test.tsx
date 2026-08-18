import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { vi } from "vitest";
import { FinancialAnalysis } from "../FinancialAnalysis";

const snapshot = {
  id: "financial-1", stock_code: "601899.SH", stock_name: "紫金矿业", as_of: "2026-08-14",
  historical_cutoff: "2026-08-14", financial_feature_version: "value-financial-feature-v1.0.0",
  forecast_version: "value-financial-forecast-v1.0.0", feature_status: "READY", forecast_status: "READY",
  analysis_status: "CONFIGURATION_REQUIRED", agent_provider: "openai", agent_model: "gpt-test",
  agent_error: "", source_hash: "hash", data_gaps: [],
  identity: { stock_code: "601899.SH", stock_name: "紫金矿业", level1_name: "有色金属", level2_name: "贵金属", level3_name: "黄金", leader_rank: 1, leader_score: 80.2, leader_formula_version: "value-leader-v2.0.0", metric_applicability_notes: [] },
  history: [{ report_date: "2025-12-31", announcement_date: "2026-03-30", revenue: 300_000_000_000, net_profit: 30_000_000_000, operating_cash_flow: 35_000_000_000, roe: 18 }],
  feature: { status: "READY", growth: {}, profitability: { roe: [], gross_margin: { status: "AVAILABLE", items: [] }, net_margin: [] }, cash_flow: { operating_cash_flow: [], cash_conversion: [], ocf_to_revenue: [] }, balance_sheet: { total_assets: [], equity: [], debt_ratio: [] }, capital_expenditure: { capex: [], capex_to_revenue: [] }, trends: { growth_trend: "IMPROVING", profitability_trend: "STABLE", cash_flow_trend: "IMPROVING", balance_sheet_trend: "STABLE" }, latest_changes: [], data_quality: { coverage: 1, missing_fields: [], annual_period_count: 6, cautions: [] } },
  forecast: { status: "READY", disclaimer: "情景推演，不代表确定预测。", assumption_notes: ["确定性约束"], scenarios: {
    BEAR: { scenario: "BEAR", label: "谨慎", revenue_growth_assumptions: [2, 2, 2], margin_assumptions: [8, 8, 8], forecast: [{ year: "2026E", revenue: 306_000_000_000, net_profit: 24_480_000_000 }], assumption_notes: [] },
    BASE: { scenario: "BASE", label: "基准", revenue_growth_assumptions: [8, 8, 8], margin_assumptions: [10, 10, 10], forecast: [{ year: "2026E", revenue: 324_000_000_000, net_profit: 32_400_000_000 }], assumption_notes: [] },
    BULL: { scenario: "BULL", label: "乐观", revenue_growth_assumptions: [14, 14, 14], margin_assumptions: [12, 12, 12], forecast: [{ year: "2026E", revenue: 342_000_000_000, net_profit: 41_040_000_000 }], assumption_notes: [] },
  } }, analysis: null,
};

vi.mock("@/lib/api", () => ({ api: {
  getCompanyFinancialAnalysis: vi.fn(async () => snapshot),
  analyzeCompanyFinancials: vi.fn(async () => snapshot),
} }));

describe("FinancialAnalysis", () => {
  function LocationProbe() {
    const location = useLocation();
    return <div data-testid="location">{location.pathname}{location.search}</div>;
  }

  it("redirects the legacy full-financial URL into the company research financial tab", async () => {
    const companyPath = "/company/CN/601899.SH?tab=financial&from=%2Fvalue";
    render(<MemoryRouter initialEntries={[`/value/company/601899.SH/financial?as_of=2026-08-14&from=${encodeURIComponent(companyPath)}&parent_from=${encodeURIComponent("/value")}&parent_label=${encodeURIComponent("黄金龙头")}`]}><Routes><Route path="/value/company/:stockCode/financial" element={<FinancialAnalysis />} /><Route path="/company/:market/:symbol" element={<LocationProbe />} /></Routes></MemoryRouter>);
    expect(await screen.findByTestId("location")).toHaveTextContent("/company/CN/601899.SH");
    expect(screen.getByTestId("location")).toHaveTextContent("tab=financial");
    expect(screen.getByTestId("location")).toHaveTextContent("as_of=2026-08-14");
    expect(screen.getByTestId("location")).toHaveTextContent("from=%2Fvalue");
  });
});
