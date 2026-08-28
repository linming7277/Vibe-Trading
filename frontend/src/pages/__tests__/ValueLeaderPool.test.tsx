import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const apiMock = vi.hoisted(() => ({
  getValueIndustries: vi.fn(),
  getCurrentLeaderPool: vi.fn(),
  getLevel3IndustryLeaders: vi.fn(),
  getCompanyFinancialAnalysis: vi.fn(),
  getCompanyResearchConclusion: vi.fn(() => Promise.reject(new Error("not seeded"))),
  getCompanyResearchOverview: vi.fn(() => Promise.reject(new Error("not seeded"))),
  getCompanyPriceZones: vi.fn(() => Promise.reject(new Error("not seeded"))),
  getCompanyEntryResearch: vi.fn(() => Promise.reject(new Error("not seeded"))),
  getCompanyExitResearch: vi.fn(() => Promise.reject(new Error("not seeded"))),
  getCompanyCompactDailyBars: vi.fn(() => Promise.reject(new Error("not seeded"))),
  getTdxSecurityOverview: vi.fn(() => Promise.reject(new Error("no cached daily bars"))),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/components/value/FinancialAgentFloat", () => ({ FinancialAgentFloat: ({ open, target }: { open: boolean; target?: { stock_code?: string } | null }) => open ? <div data-testid="financial-agent-target">{target?.stock_code || "pool"}</div> : null }));
vi.mock("@/components/charts/CandlestickChart", () => ({ CandlestickChart: ({ data, compact }: { data: unknown[]; compact?: boolean }) => <div data-testid="mini-kline">{compact ? "紧凑日K" : "完整K线"}：{data.length}</div> }));

import { ValueLeaderPoolPage } from "../ValueLeaderPool";

const formula = {
  version: "value-leader-v2.0.0",
  comparison_scope: "仅在同行内部比较",
  leader_limit: 2,
  minimum_overall_coverage: .8,
  minimum_dimension_coverage: .5,
  normalization: "行业内缩尾后计算分位",
  product_label: "三级行业量化龙头候选",
  disclaimer: "筛选结果用于确定研究候选，不代表买入建议。",
  eligibility_rules: [],
  dimensions: [{
    key: "industry_position", label: "规模与经营地位", weight: .25,
    metrics: [{
      key: "market_cap", label: "总市值", weight: .4, unit: "亿元",
      higher_is_better: true, description: "公司当前总市值",
    }],
  }],
};

const industry = {
  industry_code: "I1", industry_name: "科技设备", industry_level: "三级", raw_industry_level: "三级",
  level: 3, is_terminal: true, tdx_class_code: "I1", level1_code: "L1", level1_name: "制造",
  level2_code: "L2", level2_name: "设备", level3_code: "I1", level3_name: "科技设备",
  terminal_level: 3, member_count: 4, as_of: "2026-08-19", source: "TDX",
};

function leader(code: string, name: string, rank: number, score: number) {
  return {
    ...industry,
    stock_code: code, stock_name: name, leader_rank: rank, leader_score: score,
    leader_formula_version: formula.version, component_scores: { industry_position: score },
    coverage: 1, eligibility_status: "eligible", eligibility_reasons: [], metric_applicability_notes: [],
    lifecycle_status: "ACTIVE", pool_id: "pool-1", first_entered_at: "2026-08-19",
    last_seen_at: "2026-08-19", as_of: "2026-08-19", raw_metric_available: 20, raw_metric_total: 20,
    explanation: {
      summary: `在4家可评分公司中排名第${rank}`, selected: rank <= 2,
      comparison_scope: "仅与科技设备行业内可评分公司比较", member_count: 4, eligible_count: 4,
      excluded_count: 0, rank, top_percent: rank * 25,
      strongest: [{ key: "industry_position", label: "规模与经营地位", score }],
      weakest: [{ key: "valuation", label: "同行相对估值", score: 40 }],
      sample_warning: "可评分公司少于5家，排名属于小样本结果。",
      score_interpretation: "行业内加权相对分，不是绝对质量分。",
    },
  };
}

function compactDailyBars(code: string, count = 126) {
  const bars = Array.from({ length: count }, (_, index) => ({ date: `2026-07-${String((index % 28) + 1).padStart(2, "0")}`, open: 10 + index / 10, high: 10.2 + index / 10, low: 9.8 + index / 10, close: 10 + index / 10, volume: 1000 + index }));
  return { market: "CN", stock_code: code, adjustment_type: "front", bars, bar_count: count, returned_bar_count: count, coverage_status: "PARTIAL", data_as_of: "2026-08-19", requested_as_of: null, source: "TongDaXin", source_version: "test", fetched_at: "2026-08-19T00:00:00Z" };
}

describe("ValueLeaderPoolPage", () => {
  it("explains the candidate pipeline and exposes the full peer ranking", async () => {
    const first = leader("000001.SZ", "甲公司", 1, 88);
    const second = leader("000002.SZ", "乙公司", 2, 82);
    apiMock.getValueIndustries.mockResolvedValue({
      items: [industry], total: 1, level1_total: 1, level2_total: 1, level3_total: 1, source: "TDX",
    });
    apiMock.getCurrentLeaderPool.mockResolvedValue({
      id: "pool-1", source_leader_run_id: "run-1", as_of: "2026-08-19", status: "COMPLETED",
      formula_version: formula.version, catalog_as_of: "2026-08-19", terminal_industry_count: 1,
      current_membership_count: 2, company_count: 2, new_count: 0, active_count: 2, out_count: 0,
      reentered_count: 0, diff: { entered: 0, stayed: 2, left: 0, reentered: 0 },
      created_at: "2026-08-19", members: [first, second], research_states: [], formula,
      industry_summaries: { I1: { member_count: 4, eligible_count: 4, excluded_count: 0, selected_count: 2, sample_warning: "可评分公司少于5家，排名属于小样本结果。" } },
    });
    apiMock.getLevel3IndustryLeaders.mockResolvedValue({
      industry, as_of: "2026-08-19", formula_version: formula.version, company_count: 4,
      eligible_count: 3, items: [first, second, leader("000003.SZ", "丙公司", 3, 70)],
      excluded_items: [{ ...leader("000004.SZ", "丁公司", 4, 0), eligibility_status: "ineligible", eligibility_reasons: ["MARKET_DATA_STALE"], eligibility_reason_labels: ["行情缺失或超过5个交易日未更新"] }],
      quality: { member_count: 4, eligible_count: 3, excluded_count: 1, selected_count: 2 },
      formula, total_ranked: 3, snapshot_status: "ready", comparison_scope: formula.comparison_scope,
    });

    const firstRender = render(<MemoryRouter><ValueLeaderPoolPage /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "三级行业量化龙头候选" })).toBeInTheDocument();
    expect(screen.getByText("1 基础资格校验")).toBeInTheDocument();
    expect(screen.getByText("4 每行业前2进入研究池")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /查看详细筛选说明/ })).toHaveAttribute("href", "/value/methodology");
    expect(screen.getByText("可评分公司少于5家，排名属于小样本结果。")).toBeInTheDocument();
    expect(screen.getByText("2 个量化候选席位", { exact: false })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /查看全行业排名与排除原因/ }));
    await waitFor(() => expect(apiMock.getLevel3IndustryLeaders).toHaveBeenCalledWith("I1", 100));
    expect(await screen.findByText("丙公司")).toBeInTheDocument();
    fireEvent.click(screen.getByText("未参与评分 1 家"));
    expect(screen.getByText("行情缺失或超过5个交易日未更新")).toBeInTheDocument();
    firstRender.unmount();
    render(<MemoryRouter><ValueLeaderPoolPage /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "三级行业量化龙头候选" })).toBeInTheDocument();
    expect(apiMock.getValueIndustries).toHaveBeenCalledTimes(1);
    expect(apiMock.getCurrentLeaderPool).toHaveBeenCalledTimes(1);
  });

  it("opens a compact leader quick view with a full company research entry", async () => {
    const first = leader("000001.SZ", "甲公司", 1, 88);
    apiMock.getValueIndustries.mockResolvedValue({
      items: [industry], total: 1, level1_total: 1, level2_total: 1, level3_total: 1, source: "TDX",
    });
    apiMock.getCurrentLeaderPool.mockResolvedValue({
      id: "pool-1", source_leader_run_id: "run-1", as_of: "2026-08-19", status: "COMPLETED",
      formula_version: formula.version, catalog_as_of: "2026-08-19", terminal_industry_count: 1,
      current_membership_count: 2, company_count: 2, new_count: 0, active_count: 2, out_count: 0,
      reentered_count: 0, diff: { entered: 0, stayed: 2, left: 0, reentered: 0 },
      created_at: "2026-08-19", members: [first, leader("000002.SZ", "乙公司", 2, 82)], research_states: [], formula,
      industry_summaries: { I1: { member_count: 4, eligible_count: 4, excluded_count: 0, selected_count: 2 } },
    });
    apiMock.getCompanyFinancialAnalysis.mockRejectedValue(new Error("no local snapshot"));
    apiMock.getCompanyPriceZones.mockResolvedValue({
      stock_code: "000001.SZ", as_of: "2026-08-19", current_price: 10, formula_version: "test",
      valuation: { status: "FAIR", fair_value_low: 7, fair_value_mid: 10, fair_value_high: 13, methods: [
        { name: "预测利润 + 同三级行业 PE 可比", status: "READY", fair_values: [11, 12, 13] },
        { name: "同三级行业 PB 可比", status: "READY", fair_values: [7, 8, 9] },
      ], message: "", limitations: [] },
      valuation_zones: [{ name: "低估关注区", low: 8, high: 9.5, kind: "UNDERVALUED" }],
      support_zones: [{ low: 9, high: 10.5, strength: "HIGH", reasons: ["历史重要低点", "历史出现 3 次"] }], resistance_zones: [],
      confluence_zones: [{ low: 9, high: 9.5, valuation_status: "FAIR", support_strength: "HIGH" }], upper_review_zones: [], thesis_status: null, plain_summary: "",
      historical_valuation: { historical_valuation_status: "EXPENSIVE", historical_percentiles: {
        pe_ttm: { status: "READY", percentile: 20 }, pb_mrq: { status: "READY", percentile: 40 }, dividend_yield: { status: "READY", percentile: 60 },
      } },
      data_quality: {},
    });
    apiMock.getCompanyEntryResearch.mockResolvedValue({
      stock_code: "000001.SZ", as_of: "2026-08-19", current_price: 10, entry_score: 55, entry_level: "WATCH", entry_level_label: "继续观察", confidence: "HIGH",
      valuation_score: 60, historical_valuation_score: 40, support_score: 40, thesis_score: 50, thesis_status: "FORMING", thesis_confidence: "MEDIUM", safety_gate: null,
      focus_zones: {}, reason_codes: ["VALUATION_FAIR", "HISTORICAL_VALUATION_EXPENSIVE", "HISTORICAL_SUPPORT", "THESIS_FORMING"], data_gaps: [],
      plain_explanation: "当前价格位于合理价值范围，历史估值偏高，价格位于历史支撑区域；公司核心逻辑正在形成。", formula_version: "test", weights: {},
    });
    apiMock.getCompanyCompactDailyBars.mockResolvedValue(compactDailyBars("000001.SZ"));

    render(<MemoryRouter><ValueLeaderPoolPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /查看 甲公司 研究摘要/ }));

    expect(await screen.findByRole("dialog", { name: "龙头快速判断" })).toBeInTheDocument();
    expect(screen.getByText("为什么入选")).toBeInTheDocument();
    expect(screen.getByText("价格判断")).toBeInTheDocument();
    expect(screen.getByText("合理价值中枢")).toBeInTheDocument();
    expect(screen.getByText("查看历史支撑与压力")).toBeInTheDocument();
    expect(screen.queryByText("最近历史支撑")).not.toBeInTheDocument();
    const priceDetails = screen.getByText("查看历史支撑与压力").closest("details") as HTMLDetailsElement;
    priceDetails.open = true;
    fireEvent(priceDetails, new Event("toggle"));
    expect(screen.getByText("最近历史支撑")).toBeInTheDocument();
    expect(screen.getAllByText("9.00 – 10.50 元").length).toBeGreaterThan(1);
    expect(screen.getByText("历史压力区域")).toBeInTheDocument();
    expect(screen.queryByText("历史估值")).not.toBeInTheDocument();
    expect(apiMock.getCompanyEntryResearch).not.toHaveBeenCalled();
    expect(apiMock.getCompanyExitResearch).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /打开公司研究/ })).toHaveAttribute("href", "/company/CN/000001.SZ?tab=overview");
    expect(await screen.findByTestId("mini-kline")).toHaveTextContent("紧凑日K：126");
    expect(apiMock.getTdxSecurityOverview).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /问投研主管/ })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "龙头快速判断" })).not.toBeInTheDocument());
  });
});
