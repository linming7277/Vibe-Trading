import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import { DecisionFlow } from "@/components/workspace/DecisionFlow";

describe("DecisionFlow", () => {
  beforeEach(() => localStorage.clear());

  it("renders the five decision stages in order", () => {
    render(<MemoryRouter><DecisionFlow current={3} /></MemoryRouter>);
    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "1宏观", "2行业", "3龙头", "4深度研究", "5买卖点",
    ]);
  });

  it("restores the selected context and links forward", () => {
    localStorage.setItem("hz-decision-flow-v1", JSON.stringify({
      macro_headline: "流动性改善", macro_stance: "risk-on",
      sector_code: "酿酒", sector_name: "酿酒",
      symbol: "600519.SH", company_name: "贵州茅台",
    }));
    render(<MemoryRouter><DecisionFlow current={4} /></MemoryRouter>);
    expect(screen.getByText("risk-on")).toBeInTheDocument();
    expect(screen.getByText("酿酒")).toBeInTheDocument();
    expect(screen.getByText("贵州茅台")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /深度研究/ })).toHaveAttribute("href", expect.stringContaining("600519.SH"));
  });
});
