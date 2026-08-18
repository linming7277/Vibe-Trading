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
  getCompanyFinancialAnalysis: vi.fn(() => new Promise(() => {})),
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
    await user.click(screen.getByRole("tab", { name: "财务与估值" }));
    expect(screen.getByTestId("location")).toHaveTextContent("tab=financial");
    expect(screen.getByTestId("location")).toHaveTextContent("from=%2Fvalue%3Ffocus%3Dleaders");
    expect(screen.getByText("读取真实财务历史…")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /完整财报/ })).not.toBeInTheDocument();
  });
});
