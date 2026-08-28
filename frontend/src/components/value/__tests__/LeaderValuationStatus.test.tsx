import { render, screen } from "@testing-library/react";
import { deriveLeaderValuationStatus, LeaderValuationStatusBadge } from "../LeaderValuationStatus";

describe("LeaderValuationStatus", () => {
  it.each([
    ["VERY_CHEAP", "DEEPLY_UNDERVALUED", "深度低估"],
    ["UNDERVALUED", "UNDERVALUED", "低估关注"],
    ["FAIR", "FAIR", "合理观察"],
    ["EXPENSIVE", "OVERVALUED", "估值偏高"],
    ["VERY_EXPENSIVE", "DEEPLY_OVERVALUED", "明显偏高"],
  ] as const)("maps %s to the independent price-state badge", (historical, expected, label) => {
    const status = deriveLeaderValuationStatus({
      historical_valuation: { historical_valuation_status: historical },
      valuation: { status: "FAIR" },
    } as never);
    expect(status).toBe(expected);
    render(<LeaderValuationStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("does not infer a price state when valuation data is unavailable", () => {
    expect(deriveLeaderValuationStatus(null)).toBe("INSUFFICIENT_DATA");
    render(<LeaderValuationStatusBadge status="INSUFFICIENT_DATA" />);
    expect(screen.getByText("数据不足")).toBeInTheDocument();
  });
});
