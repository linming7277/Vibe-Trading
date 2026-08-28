import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { vi } from "vitest";
import { BusinessResearchCard, FinancialAnalysis, FinancialAnalysisContent, ThesisReviewCard } from "../FinancialAnalysis";

const snapshot = {
  id: "financial-1", stock_code: "601899.SH", stock_name: "紫金矿业", as_of: "2026-08-14",
  historical_cutoff: "2026-08-14", financial_feature_version: "value-financial-feature-v1.0.0",
  forecast_version: "value-financial-forecast-v1.0.0", feature_status: "READY", forecast_status: "READY",
  analysis_status: "COMPLETED", agent_provider: "openai", agent_model: "gpt-test",
  agent_error: "", source_hash: "hash", data_gaps: [],
  identity: { stock_code: "601899.SH", stock_name: "紫金矿业", level1_name: "有色金属", level2_name: "贵金属", level3_name: "黄金", leader_rank: 1, leader_score: 80.2, leader_formula_version: "value-leader-v2.0.0", metric_applicability_notes: [], data_dates: { quote_as_of: "2026-08-21T11:05:00+08:00", valuation_as_of: "2026-08-19T09:48:00+08:00", financial_report_date: "2026-03-31", financial_announcement_date: "2026-04-30", leader_as_of: "2026-08-17" } },
  history: [{ report_date: "2025-12-31", announcement_date: "2026-03-30", revenue: 300_000_000_000, net_profit: 30_000_000_000, operating_cash_flow: 35_000_000_000, roe: 18 }],
  feature: { status: "READY", growth: {}, profitability: { roe: [], gross_margin: { status: "AVAILABLE", items: [] }, net_margin: [] }, cash_flow: { operating_cash_flow: [], cash_conversion: [], ocf_to_revenue: [] }, balance_sheet: { total_assets: [], equity: [], debt_ratio: [] }, capital_expenditure: { capex: [], capex_to_revenue: [] }, trends: { growth_trend: "IMPROVING", profitability_trend: "STABLE", cash_flow_trend: "IMPROVING", balance_sheet_trend: "STABLE" }, latest_changes: [], data_quality: { coverage: 1, missing_fields: [], annual_period_count: 6, cautions: [] } },
  forecast: { status: "READY", disclaimer: "情景推演，不代表确定预测。", assumption_notes: ["确定性约束"], scenarios: {
    BEAR: { scenario: "BEAR", label: "谨慎", revenue_growth_assumptions: [2, 2, 2], margin_assumptions: [8, 8, 8], forecast: [{ year: "2026E", revenue: 306_000_000_000, net_profit: 24_480_000_000 }], assumption_notes: [] },
    BASE: { scenario: "BASE", label: "基准", revenue_growth_assumptions: [8, 8, 8], margin_assumptions: [10, 10, 10], forecast: [{ year: "2026E", revenue: 324_000_000_000, net_profit: 32_400_000_000 }], assumption_notes: [] },
    BULL: { scenario: "BULL", label: "乐观", revenue_growth_assumptions: [14, 14, 14], margin_assumptions: [12, 12, 12], forecast: [{ year: "2026E", revenue: 342_000_000_000, net_profit: 41_040_000_000 }], assumption_notes: [] },
  } }, analysis: {
    executive_summary: "财务趋势已完成确定性核验。", historical_performance: {}, latest_changes: [], financial_strengths: [], financial_risks: [],
    forecast_analysis: { bear: "", base: "", bull: "", key_assumptions: [] }, key_metrics_to_monitor: [], confidence: "HIGH", data_gaps: [],
    claims: [{ type: "FACT", statement: "2025 年净利润已记录", evidence_keys: ["FIN_NET_PROFIT_2025"], citations: [{ source_key: "FIN_NET_PROFIT_2025", status: "RESOLVED", source_type: "FINANCIAL_HISTORY", source: "TDX PIT 财务", metric: "net_profit", period: "2025", value: 30_000_000_000, unit: "CNY", data_as_of: "2026-08-17", source_snapshot_id: "financial-1", source_hash: "0123456789abcdef" }] }],
    analysis_metadata: { traceability_status: "COMPLETE", citation_stats: { claims_total: 1, claims_with_citations: 1, resolved_source_keys: 1, unresolved_source_keys: 0 } },
  },
};

const businessSnapshot = {
  id: "business-1", stock_code: "601899.SH", company_name: "紫金矿业",
  data_as_of: "2026-08-21T10:00:00+08:00", main_business: "矿产开发,金属冶炼",
  products: ["矿产开发", "金属冶炼"],
  product_note: "产品名称暂从主营业务原文展示；现有资料没有产品收入占比，不能判断哪个产品贡献最大。",
  business_model: "UNKNOWN", business_changes: ["UNKNOWN：缺少可比较的历史经营资料。"],
  source_hash: "business-hash", module_version: "financial-researcher-business-v1.0.0",
  data_quality: { status: "PARTIAL", field_statuses: { main_business: "READY", products: "PARTIAL", business_model: "MISSING", business_changes: "MISSING" }, missing_fields: ["business_model", "business_changes"], limitations: ["没有产品收入占比时，不判断产品贡献大小。"] },
  analysis_status: "NOT_RUN", analysis: null, traceability_status: "NOT_APPLICABLE",
  citation_stats: { claims_total: 0, required_claims: 0, resolved_required_claims: 0, unresolved: 0 },
};

const businessAnalyzed = {
  ...businessSnapshot, analysis_status: "COMPLETED", traceability_status: "COMPLETE",
  business_model: "公司主要通过开发矿产并销售冶炼后的金属产品获得收入。",
  analysis: { summary: "公司目前主要做矿产开发和金属冶炼。现有资料没有产品收入占比，还要继续观察各类产品的收入和利润。", claims: [{ type: "FACT", topic: "MAIN_BUSINESS", text: "公司披露的主营业务包括矿产开发和金属冶炼。", source_keys: ["BUSINESS_CURRENT_MAIN_BUSINESS"], confidence: "HIGH", citations: [{ source_key: "BUSINESS_CURRENT_MAIN_BUSINESS", status: "RESOLVED", source_type: "TDX_BUSINESS_PROFILE", source_id: "fundamentals:601899.SH", data_as_of: "2026-08-21T10:00:00+08:00", field: "main_business", value: "矿产开发,金属冶炼", source_hash: "profile-hash", profile_role: "CURRENT" }] }] },
  citation_stats: { claims_total: 1, required_claims: 1, resolved_required_claims: 1, unresolved: 0 },
};

const overview = {
  company: { market: "CN", stock_code: "601899.SH", stock_name: "紫金矿业" },
  business_summary: { status: "PARTIAL", snapshot_id: "business-1", main_business: "矿产开发,金属冶炼", description: "公司主要业务包括：矿产开发、金属冶炼。", products: ["矿产开发", "金属冶炼"], business_model: "资料不足，暂时无法可靠判断公司主要通过什么方式获得收入。", changes: ["目前缺少前后两期可比较的经营资料，暂时无法判断经营方向是否发生明显变化。"], claims: [] },
  financial_summary: { status: "READY", snapshot_id: "financial-1", analysis_status: "COMPLETED", items: [{ category: "revenue", text: "最近一期收入同比增长，反映销售规模正在变化。", source_keys: ["FIN_REV"], citations: [{ source_key: "FIN_REV", status: "RESOLVED", source_type: "FINANCIAL_HISTORY", source: "TDX PIT 财务", data_as_of: "2026-08-17", value: 1 }] }], claims: [] },
  supporting_evidence: [], challenging_evidence: [], neutral_evidence_count: 0,
  thesis: null, review: null, watch_items: [], data_status: { financial: "READY", business: "PARTIAL", thesis: "NOT_CREATED", review: "NOT_CREATED" },
};

vi.mock("@/lib/api", () => ({ api: {
  getCompanyFinancialAnalysis: vi.fn(async () => snapshot),
  analyzeCompanyFinancials: vi.fn(async () => snapshot),
  getCompanyFinancialDossier: vi.fn(async () => ({ chat_entries: [], archive_summary: { chat_entry_count: 0 } })),
  getCompanyBusinessResearch: vi.fn(async () => businessSnapshot),
  analyzeCompanyBusinessResearch: vi.fn(async () => businessAnalyzed),
  getCompanyResearchOverview: vi.fn(async () => overview),
  getCompanyThesis: vi.fn(async () => ({ status: "OK", thesis: { thesis_id: "thesis-1", version: 1, status: "FORMING", confidence: "MEDIUM" } })),
  getCompanyThesisReview: vi.fn(async () => ({ status: "EXISTING", review: { review_id: "review-1", review_status: "PENDING", is_stale: true, recommended_status: "UNCHANGED", recommended_confidence: "MEDIUM", support_count: 1, challenge_count: 0, neutral_count: 0, review_reason: "当前证据支持。", evidence_set_hash: "hash", metadata: { evidence_source_summary: { AGENT_FINANCIAL: 1 } }, created_at: "2026-08-21" } })),
  refreshCompanyThesisReview: vi.fn(async () => ({ status: "EXISTING", review: { review_id: "review-1", review_status: "PENDING", is_stale: false, recommended_status: "UNCHANGED", recommended_confidence: "MEDIUM", support_count: 1, challenge_count: 0, neutral_count: 0, review_reason: "当前证据支持。", evidence_set_hash: "hash", metadata: {}, created_at: "2026-08-21" } })),
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

  it("expands a Claim into its deterministic source citation", async () => {
    render(<MemoryRouter><FinancialAnalysisContent stockCode="601899.SH" /></MemoryRouter>);
    const source = await screen.findByText("查看依据（1）");
    fireEvent.click(source);
    expect(await screen.findByText("通达信财务资料")).toBeInTheDocument();
    expect(screen.getByText("数据日期")).toBeInTheDocument();
    expect(screen.getByText("原始值")).toBeInTheDocument();
    expect(screen.getByText("查看审计信息")).toBeInTheDocument();
  });

  it("shows independent quote, valuation, financial and leader dates", async () => {
    render(<MemoryRouter><FinancialAnalysisContent stockCode="601899.SH" /></MemoryRouter>);
    expect(await screen.findByText("2026-08-21 11:05")).toBeInTheDocument();
    expect(screen.getByText("2026-08-19 09:48")).toBeInTheDocument();
    expect(screen.getByText("2026-03-31")).toBeInTheDocument();
    expect(screen.getByText("2026-04-30")).toBeInTheDocument();
    expect(screen.getAllByText("2026-08-17").length).toBeGreaterThan(0);
  });

  it("shows a stale research review and only refreshes it after an explicit click", async () => {
    render(<MemoryRouter><ThesisReviewCard stockCode="601899.SH" /></MemoryRouter>);
    expect(await screen.findByText("已有新的研究证据，当前研究复核已过期。请显式重新生成。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新生成复核" }));
    expect(await screen.findByText("建议逻辑状态：逻辑基本稳定 · 置信度：中等")).toBeInTheDocument();
  });

  it("shows company business facts and expands a sourced business Claim", async () => {
    render(<MemoryRouter><BusinessResearchCard stockCode="601899.SH" /></MemoryRouter>);
    expect(await screen.findByText("公司主要做什么")).toBeInTheDocument();
    expect(screen.getByText("矿产开发、金属冶炼")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成经营研究" }));
    expect(await screen.findByText(/公司目前主要做矿产开发和金属冶炼/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("查看依据（1）").at(-1)!);
    expect((await screen.findAllByText("经营资料")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("主营业务").length).toBeGreaterThan(0);
  });
});
