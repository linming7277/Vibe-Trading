import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Today } from "../Today";

const apiMock = vi.hoisted(() => ({
  getValueDashboard: vi.fn(),
  getEmotionDashboard: vi.fn(),
  getTdxMarketOverview: vi.fn(),
  getTdxSectors: vi.fn(),
  screenTdxSecurities: vi.fn(),
  getTdxMarketRanks: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/hooks/useWorkspaceMarket", () => ({
  useWorkspaceMarket: () => ({ market: "CN", setMarket: vi.fn() }),
}));

describe("Today double-line dashboard", () => {
  beforeEach(() => {
    apiMock.getTdxMarketOverview.mockRejectedValue(new Error("optional"));
    apiMock.getTdxSectors.mockRejectedValue(new Error("optional"));
    apiMock.screenTdxSecurities.mockRejectedValue(new Error("optional"));
    apiMock.getTdxMarketRanks.mockRejectedValue(new Error("optional"));
  });

  it("keeps value, short-term and swing decisions clearly separated", async () => {
    apiMock.getValueDashboard.mockResolvedValue({
      strategy_line: "value",
      market: "CN",
      latest_run: { id: "value-run", status: "completed", formula_version: "value-v1" },
      regime: { regime: "balanced", coverage: 0.86, score: 61, formula_version: "macro-v1" },
      scores: [{ id: "score-1", subject_id: "食品饮料", base_score: 78, coverage: 0.9, formula_version: "sector-v1" }],
      signals: [{ id: "signal-v", symbol: "600519.SH", strategy_line: "value", horizon: "value", status: "proposed", valid_until: "2026-08-31", base_score: 82, position_cap: 0.12 }],
    });
    apiMock.getEmotionDashboard.mockResolvedValue({
      strategy_line: "emotion",
      market: "CN",
      latest_run: { id: "emotion-run", status: "completed", formula_version: "emotion-v1" },
      regime: { regime: "发酵", coverage: 0.91, score: 64, formula_version: "emotion-regime-v1" },
      scores: [{ id: "score-2", subject_id: "机器人", base_score: 73, coverage: 0.88, formula_version: "sector-heat-v1" }],
      signals: [
        { id: "signal-s", symbol: "000001.SZ", strategy_line: "emotion", horizon: "short", status: "proposed", valid_until: "2026-08-15", base_score: 76, position_cap: 0.08 },
        { id: "signal-w", symbol: "000002.SZ", strategy_line: "emotion", horizon: "swing", status: "proposed", valid_until: "2026-09-15", base_score: 71, position_cap: 0.1 },
      ],
    });

    render(<Today />, { wrapper: MemoryRouter });

    expect(await screen.findByRole("heading", { name: "今日双线总览" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "价值投资" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "情绪交易" })).toBeInTheDocument();
    expect(screen.getByText("食品饮料")).toBeInTheDocument();
    expect(screen.getByText("机器人")).toBeInTheDocument();
    expect(screen.getByText(/600519\.SH/)).toBeInTheDocument();
    expect(screen.getByText(/000001\.SZ/)).toBeInTheDocument();
    expect(screen.getByText(/000002\.SZ/)).toBeInTheDocument();
    expect(apiMock.getValueDashboard).toHaveBeenCalledWith("CN");
    expect(apiMock.getEmotionDashboard).toHaveBeenCalledWith("CN");
  });

  it("shows factual observations when deterministic scores are unavailable", async () => {
    apiMock.getValueDashboard.mockResolvedValue({ strategy_line: "value", market: "CN", latest_run: { status: "insufficient_data" }, regime: null, scores: [], signals: [] });
    apiMock.getEmotionDashboard.mockResolvedValue({ strategy_line: "emotion", market: "CN", latest_run: { status: "insufficient_data" }, regime: null, scores: [], signals: [] });
    apiMock.screenTdxSecurities.mockResolvedValue({ total: 1, items: [{ code: "601398.SH", name: "工商银行", price: 7.51, change_pct: -1.18 }], limit: 12, offset: 0, coverage: { quotes: 1, fundamentals: 1 } });
    apiMock.getTdxSectors.mockResolvedValue({ total: 1, categories: ["行业"], items: [{ code: "881386.SH", name: "银行", category: "行业", member_count: 42, up: 10, down: 30, change_pct: -0.5 }] });

    render(<Today />, { wrapper: MemoryRouter });

    expect(await screen.findByText("工商银行")).toBeInTheDocument();
    expect(screen.getByText("银行")).toBeInTheDocument();
    expect(screen.getAllByText(/尚未进入确定性评分/).length).toBeGreaterThan(0);
  });
});
