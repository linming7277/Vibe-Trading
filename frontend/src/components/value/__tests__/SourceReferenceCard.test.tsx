import { fireEvent, render, screen } from "@testing-library/react";
import {
  SourceReferenceCard,
  claimTypeLabel,
  entryStatusLabel,
  exitStatusLabel,
  metricLabel,
  ownerFacingText,
  thesisStatusLabel,
} from "../SourceReferenceCard";

describe("SourceReferenceCard", () => {
  it("uses the unified Chinese labels for research and decision statuses", () => {
    expect(claimTypeLabel("FACT")).toBe("事实依据");
    expect(claimTypeLabel("INFERENCE")).toBe("分析判断");
    expect(claimTypeLabel("FORECAST")).toBe("情景预测");
    expect(claimTypeLabel("UNKNOWN")).toBe("暂无足够资料");
    expect(thesisStatusLabel("WEAKENING")).toBe("逻辑正在减弱");
    expect(entryStatusLabel("WATCH")).toBe("继续观察");
    expect(exitStatusLabel("CRITICAL_REVIEW")).toBe("需要重点复核");
    expect(metricLabel("revenue")).toBe("营业收入");
    expect(ownerFacingText("This claim supports the thesis after Review.")).toBe("This 研究观点 supports the 公司核心逻辑 after 研究复核.");
  });

  it("shows a consistent business-facing source view and keeps audit fields secondary", () => {
    render(
      <SourceReferenceCard
        researchContent="最近一期收入同比增长。"
        citations={[{
          source_key: "FIN_REV_2025",
          status: "RESOLVED",
          source_type: "FINANCIAL_HISTORY",
          source: "TDX PIT 财务",
          metric: "revenue",
          period: "2025",
          value: 300000000,
          unit: "CNY",
          data_as_of: "2026-08-17",
          source_snapshot_id: "snapshot-1",
          source_hash: "hash-1",
        }]}
      />,
    );

    fireEvent.click(screen.getByText("查看依据（1）"));
    expect(screen.getByText("通达信财务资料")).toBeInTheDocument();
    expect(screen.getByText("营业收入")).toBeInTheDocument();
    expect(screen.getByText("2026-08-17")).toBeInTheDocument();
    expect(screen.getByText("查看审计信息")).toBeInTheDocument();
  });
});
