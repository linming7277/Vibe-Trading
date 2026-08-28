import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { vi } from "vitest";
import { CompanyResearch } from "../CompanyResearch";

vi.mock("@/lib/api", () => ({ api: {
  getTdxSecurityOverview: vi.fn(async () => ({
    code: "002371.SZ", name: "北方华创", source: "通达信", as_of: "2026-08-17T16:00:00+08:00",
    quote: { price: 388.5, change_pct: 1.2, volume_lots: 1000 },
    fundamental: { pe_ttm: 35, pb_mrq: 5, report_date: "2026-06-30", revenue_10k: 1000, net_profit_10k: 100 },
    detail: { snapshot: {} }, sectors: [], klines: [], professional_finance_available: true,
    cache: { stale: false },
  })),
  getCompanyDossier: vi.fn(async () => { throw new Error("not built"); }),
  refreshTdxSecurity: vi.fn(),
  getTdxKline: vi.fn(),
  researchCompany: vi.fn(),
  createCommittee: vi.fn(),
  getCompanyResearchOverview: vi.fn(async () => ({
    company: { market: "CN", stock_code: "002371.SZ", stock_name: "北方华创" },
    business_summary: { status: "UNKNOWN", snapshot_id: null, changes: [], claims: [] },
    financial_summary: { status: "UNKNOWN", snapshot_id: null, analysis_status: "NOT_CREATED", items: [], claims: [] },
    supporting_evidence: [], challenging_evidence: [], neutral_evidence_count: 0,
    thesis: null, review: null, watch_items: [],
    data_status: { financial: "UNKNOWN", business: "UNKNOWN", thesis: "NOT_CREATED", review: "NOT_CREATED" },
  })),
  getCompanyThesisEvidence: vi.fn(async () => null),
  getCompanyRiskResearch: vi.fn(async () => null),
  getCompanyPriceZones: vi.fn(async () => ({
    stock_code: "002371.SZ", as_of: "2026-08-17", current_price: 388.5, formula_version: "test",
    valuation: { status: "INSUFFICIENT_DATA", fair_value_low: null, fair_value_mid: null, fair_value_high: null, methods: [], message: "资料不足", limitations: [] },
    valuation_zones: [], support_zones: [], resistance_zones: [], confluence_zones: [], upper_review_zones: [], thesis_status: null,
    plain_summary: "当前资料不足。",
    data_quality: { daily_history: { status: "MISSING", bars: 0 }, historical_valuation: { status: "MISSING", message: "未保存历史估值。" }, current_fundamentals: "READY", peer_comparables: { status: "MISSING", peer_count: 0 }, forecast: "MISSING", financial_snapshot: "MISSING" },
  })),
  getCompanyResearchConclusion: vi.fn(async () => ({
    company: { market: "CN", stock_code: "002371.SZ", stock_name: "北方华创" }, thesis: null,
    entry: { available: false, level: null, label: "入场研究数据不足", confidence: null, data_gaps: [] },
    exit: { available: false, level: null, label: "退出研究数据不足", confidence: null, data_gaps: [] },
    fair_value_range: null, focus_zone: null, evidence_counts: { support: 0, challenge: 0 }, research_conclusion: "当前资料不足。", data_status: {}, formula_version: "test",
  })),
  getCompanyEntryResearch: vi.fn(async () => ({ entry_level_label: "继续观察", focus_zones: {}, entry_level: "WATCH" })),
  getCompanyExitResearch: vi.fn(async () => ({ exit_level_label: "暂未出现明显退出压力", focus_zones: {}, exit_level: "NORMAL" })),
  getCompanyFinancialAnalysis: vi.fn(() => new Promise(() => {})),
  getCompanyBusinessResearch: vi.fn(async () => ({ main_business: "资料不足", products: [], product_note: "", business_model: "资料不足", business_changes: [], data_as_of: null, data_quality: { status: "UNKNOWN", limitations: [] }, analysis: null })),
  getCompanyFinancialDossier: vi.fn(async () => ({ archive_summary: { chat_entry_count: 0 }, chat_entries: [] })),
} }));

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

describe("CompanyResearch navigation context", () => {
  it("keeps the source page and writes tab changes into the URL", async () => {
    const user = userEvent.setup();
    const from = "/value?focus=leaders";
    render(<MemoryRouter initialEntries={[`/company/CN/002371.SZ?from=${encodeURIComponent(from)}&from_label=${encodeURIComponent("半导体设备龙头")}&sector_name=${encodeURIComponent("半导体设备")}&tab=overview`]}><Routes><Route path="/company/:market/:symbol" element={<><CompanyResearch /><LocationProbe /></>} /></Routes></MemoryRouter>);

    expect(await screen.findByText("北方华创 · 公司研究")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回半导体设备龙头" })).toHaveAttribute("href", from);
    expect(screen.getAllByRole("tab").map((item) => item.textContent)).toEqual(["总览", "为什么是龙头", "经营与财务", "估值", "风险与待验证", "核心逻辑", "更多研究"]);
    await user.click(screen.getByRole("tab", { name: "经营与财务" }));
    expect(screen.getByTestId("location")).toHaveTextContent("tab=operations");
    expect(screen.getByTestId("location")).toHaveTextContent("from=%2Fvalue%3Ffocus%3Dleaders");
    expect(screen.getByText("查看经营概览与最近变化")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /完整财报/ })).not.toBeInTheDocument();
  });
});
