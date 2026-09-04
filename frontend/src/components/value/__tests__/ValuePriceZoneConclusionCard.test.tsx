import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ValuePriceZoneConclusionCard, describePricePosition } from "../ValuePriceZoneConclusionCard";
import { api, type ValuePriceZones, type ValueStrategyState } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getValueStrategyState: vi.fn(),
    getCompanyPriceZones: vi.fn(),
  },
}));

const FORBIDDEN = ["买入", "卖出", "买点", "卖点", "建仓", "加仓", "减仓", "止盈", "止损", "下单", "目标价", "开仓", "平仓"];

function mockApi(strategy: ValueStrategyState | Error, zones: ValuePriceZones | Error) {
  vi.mocked(api.getValueStrategyState).mockImplementation(() =>
    strategy instanceof Error ? Promise.reject(strategy) : Promise.resolve(strategy));
  vi.mocked(api.getCompanyPriceZones).mockImplementation(() =>
    zones instanceof Error ? Promise.reject(zones) : Promise.resolve(zones));
}

function makeStrategy(overrides: Partial<ValueStrategyState> = {}): ValueStrategyState {
  return {
    stock_code: "600001.SH", stock_name: "测试股份", market: "CN", research_as_of: "2026-09-02",
    eligibility: { status: "IN_VALUE_SCOPE", label: "当前属于低估龙头研究范围", reason: "" },
    priority: { tier: "A", label: "重点研究", reasons: [] },
    price_attention: {
      primary: true, raw_level: "HIGH_ATTENTION", raw_label: "价格条件高度值得关注",
      effective_status: "HIGH_ATTENTION", effective_label: "价格条件高度值得关注", score: 80,
      valuation_reliability: { status: "RELIABLE", label: "可靠", peer_sample_count: 12, peer_sample_counts: [], flags: [], reasons: [] },
      reasons: [], cautions: [],
    },
    review_pressure: { primary: true, raw_level: "NORMAL", effective_status: "NORMAL", effective_label: "当前暂无明显复核压力", score: 0, reasons: [], cautions: [] },
    risk: { overall: "MEDIUM", trap: null, summary: null },
    thesis: { status: "FORMING", authority: "AI_PROVISIONAL", strategy_role: "EXPLANATORY_ONLY", caution: null },
    leader: { rank: 1, state: "READY", industry_name: null, as_of: null },
    freshness: {
      market_price_as_of: "2026-09-02", low_value_as_of: "2026-09-02", focus_as_of: "2026-09-02",
      historical_valuation_as_of: null, price_structure_as_of: null, risk_as_of: null, thesis_as_of: null, notice: null,
      price_structure: { status: "FRESH", label: "最新", last_bar_date: "2026-09-02", current_quote_date: "2026-09-02", gap_calendar_days: 0, gap_trading_days: null, gap_semantics: "CALENDAR_DAYS_FALLBACK" },
    },
    summary: "当前属于重点研究范围；价格条件高度值得关注。",
    primary_action: { status: "PRIORITY_RESEARCH", label: "优先开展研究" },
    reasons: [], cautions: [], formula_version: "value-strategy-state-projection-v1.0.0", read_only: true,
    ...overrides,
  } as ValueStrategyState;
}

function makeZones(overrides: Partial<ValuePriceZones> = {}): ValuePriceZones {
  return {
    stock_code: "600001.SH", as_of: "2026-09-02", current_price: 10.0, formula_version: "value-price-zones-v1",
    valuation: { status: "UNDERVALUED", fair_value_low: 12, fair_value_mid: 14, fair_value_high: 16, methods: [], message: "", limitations: [] },
    valuation_zones: [], support_zones: [{ low: 9.5, high: 10.5 }], resistance_zones: [],
    confluence_zones: [{ low: 12, high: 13 }], upper_review_zones: [{ low: 17, high: 18 }],
    thesis_status: null, plain_summary: "",
    data_quality: { daily_history: { status: "READY", message: "" }, historical_valuation: { status: "READY", message: "历史估值数据可用。" }, current_fundamentals: "READY", peer_comparables: { status: "READY", peer_count: 10 }, forecast: "READY", financial_snapshot: "READY" },
    ...overrides,
  } as ValuePriceZones;
}

beforeEach(() => { vi.clearAllMocks(); });

describe("ValuePriceZoneConclusionCard", () => {
  it("1. IN_VALUE_SCOPE + HIGH_ATTENTION + RELIABLE + FRESH → 主徽章用 effective_label，raw 只在折叠区", async () => {
    mockApi(makeStrategy(), makeZones());
    render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(screen.getByText("价格条件高度值得关注")).toBeInTheDocument());
    expect(screen.getByText("在范围内")).toBeInTheDocument();
    expect(screen.getByText("可靠")).toBeInTheDocument();
    expect(screen.getByText("最新")).toBeInTheDocument();
    expect(screen.queryByText(/计算明细/)).not.toBeNull();
    // raw_label 与 effective 相同时仍不应作为主徽章以外的重复主标题出现；
    // raw_level 代码只出现在折叠区。
    expect(document.querySelector("details")).not.toBeNull();
    expect(document.querySelector("details")!.textContent).toContain("HIGH_ATTENTION");
  });

  it("2. WEAK 可靠性 + raw HIGH_ATTENTION → 主徽章必须是估值复核（VALUATION_REVIEW_REQUIRED）", async () => {
    mockApi(makeStrategy({
      price_attention: {
        primary: true, raw_level: "HIGH_ATTENTION", raw_label: "价格条件高度值得关注",
        effective_status: "VALUATION_REVIEW_REQUIRED", effective_label: "估值显示较大折价，但依据偏弱，先核验估值",
        score: 80,
        valuation_reliability: { status: "WEAK", label: "偏弱", peer_sample_count: 3, peer_sample_counts: [], flags: [], reasons: ["可比同行样本偏少"] },
        reasons: [], cautions: ["估值依据偏弱，不能把原始高关注结果作为老板主结论"],
      },
    }), makeZones());
    render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(screen.getByText("估值显示较大折价，但依据偏弱，先核验估值")).toBeInTheDocument());
    expect(screen.getByText("偏弱")).toBeInTheDocument();
    expect(screen.queryByText("价格条件高度值得关注")).toBeNull();
    expect(document.querySelector("details")!.textContent).toContain("HIGH_ATTENTION");
  });

  it("3. INSUFFICIENT → DATA_REVIEW_REQUIRED，不渲染假的关注带结论", async () => {
    mockApi(makeStrategy({
      price_attention: {
        primary: true, raw_level: "HIGH_ATTENTION", raw_label: "价格条件高度值得关注",
        effective_status: "DATA_REVIEW_REQUIRED", effective_label: "估值依据不足，先核验估值数据", score: 80,
        valuation_reliability: { status: "INSUFFICIENT", label: "不足", peer_sample_count: 0, peer_sample_counts: [], flags: [], reasons: [] },
        reasons: [], cautions: ["估值依据不足，原始价格关注结果仅作为计算明细保留"],
      },
    }), makeZones());
    render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(screen.getByText("估值依据不足，先核验估值数据")).toBeInTheDocument());
    expect(screen.queryByText("现价落在关注带内")).toBeNull();
    expect(screen.getByText("现价落在观察带内")).toBeInTheDocument(); // 现价10落在观察带：事实性落点仍可陈述
  });

  it("4. STALE 且支撑类理由 → 以后端 effective 为准，前端不得把 raw HIGH_ATTENTION 升回主结论", async () => {
    mockApi(makeStrategy({
      price_attention: {
        primary: true, raw_level: "HIGH_ATTENTION", raw_label: "价格条件高度值得关注",
        effective_status: "WATCH", effective_label: "价格条件继续观察", score: 80,
        valuation_reliability: { status: "RELIABLE", label: "可靠", peer_sample_count: 12, peer_sample_counts: [], flags: [], reasons: [] },
        reasons: ["HISTORICAL_SUPPORT"], cautions: ["支撑数据已偏旧，不再作为高关注主理由"],
      },
      freshness: { ...makeStrategy().freshness, price_structure: { status: "STALE", label: "偏旧", last_bar_date: "2026-08-20", current_quote_date: "2026-09-02", gap_calendar_days: 13, gap_trading_days: null, gap_semantics: "CALENDAR_DAYS_FALLBACK" } },
    }), makeZones());
    render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(screen.getByText("价格条件继续观察")).toBeInTheDocument());
    expect(screen.getByText("偏旧")).toBeInTheDocument();
    expect(screen.queryByText("价格条件高度值得关注")).toBeNull();
  });

  it("5. 缺 fair_value 或 zones 为空 → 显示原因，不写 0、不编造关注带结论", async () => {
    mockApi(makeStrategy(), makeZones({
      current_price: null,
      valuation: { status: "INSUFFICIENT_DATA", fair_value_low: null, fair_value_mid: null, fair_value_high: null, methods: [], message: "可比同行样本不足，合理价值暂不可算。", limitations: [] },
      confluence_zones: [], support_zones: [], upper_review_zones: [],
    }));
    render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(screen.getByText("可比同行样本不足，合理价值暂不可算。")).toBeInTheDocument());
    expect(screen.getByText("资料不足")).toBeInTheDocument(); // 现价缺
    expect(screen.getByText("当前没有形成可解释的关注带。")).toBeInTheDocument();
    expect(screen.getByText("带不完整，无法判断落点")).toBeInTheDocument();
    expect(screen.queryByText(/^0(\.0)?$/)).toBeNull();
  });

  it("6. 页面禁词扫描：任何场景不出现交易词汇", async () => {
    mockApi(makeStrategy(), makeZones());
    const { container } = render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(screen.getByText("价格条件高度值得关注")).toBeInTheDocument());
    const text = container.textContent ?? "";
    for (const word of FORBIDDEN) expect(text, `不得出现禁词：${word}`).not.toContain(word);
  });

  it("7. 单接口失败时另一半仍渲染", async () => {
    mockApi(new Error("策略接口挂了"), makeZones());
    const first = render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(first.getByText(/策略结论暂不可用/)).toBeInTheDocument());
    expect(first.getByText("现价落在观察带内")).toBeInTheDocument();
    first.unmount();

    mockApi(makeStrategy(), new Error("价格带接口挂了"));
    const second = render(<ValuePriceZoneConclusionCard stockCode="600001.SH" />);
    await waitFor(() => expect(second.getByText(/价格带暂不可用/)).toBeInTheDocument());
    expect(second.getByText("价格条件高度值得关注")).toBeInTheDocument();
  });
});

describe("describePricePosition 边界", () => {
  it("闭区间与开区间沿用后端语义", () => {
    expect(describePricePosition(makeZones({ current_price: 12.5 }))).toBe("现价落在关注带内");
    expect(describePricePosition(makeZones({ current_price: 10.0 }))).toBe("现价落在观察带内");
    expect(describePricePosition(makeZones({ current_price: 18.5 }))).toBe("现价落在复核带内"); // ≥复核带low(17)即算（沿用既有规则）
    expect(describePricePosition(makeZones({ current_price: 14.0 }))).toBe("现价未落入关注/观察/复核带"); // 带在但价不在任何带内
    expect(describePricePosition(makeZones({ current_price: 9.0 }))).toBe("现价低于合理价值带下限");
    expect(describePricePosition(makeZones({ current_price: 14.0, confluence_zones: [], support_zones: [], upper_review_zones: [] }))).toBe("带不完整，无法判断落点"); // 三带全空且不低于下限
    expect(describePricePosition(makeZones({ current_price: null }))).toBe("带不完整，无法判断落点");
  });
});
