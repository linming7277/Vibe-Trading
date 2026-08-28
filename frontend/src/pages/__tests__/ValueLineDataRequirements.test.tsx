import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const apiMock = vi.hoisted(() => ({
  getTdxStatus: vi.fn(),
  getValueDataStatus: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

import { ValueLineDataRequirements } from "../ValueLineDataRequirements";

describe("ValueLineDataRequirements", () => {
  it("lists value-line inputs, their use and the current cache boundary", async () => {
    apiMock.getTdxStatus.mockResolvedValue({
      available: true, tdx_home: "C:/zd_zyb", client_process_running: true, recent_jobs: [],
      modules: [
        { code: "sectors", label: "板块", description: "", status: "ready", progress: 1, total: 1, item_count: 587, message: "已更新", error: "", metadata: {}, updated_at: "2026-08-28T16:00:00+08:00" },
        { code: "quote", label: "行情", description: "", status: "ready", progress: 1, total: 1, item_count: 5552, message: "已更新", error: "", metadata: {}, updated_at: "2026-08-28T16:00:00+08:00" },
        { code: "fundamental", label: "财务估值", description: "", status: "partial", progress: 1, total: 1, item_count: 5000, message: "覆盖待补", error: "", metadata: {}, updated_at: "2026-08-27T16:00:00+08:00" },
      ],
    });
    apiMock.getValueDataStatus.mockResolvedValue({
      professional_finance: { status: "ready", file_count: 8, first_period: "2018-12-31", last_period: "2026-03-31" },
      modules: [
        { code: "financial_history", label: "专业财务", status: "ready", progress: 1, total: 1, item_count: 149596, message: "已验证", error: "", metadata: {}, last_success_at: "2026-08-28T16:10:00+08:00" },
        { code: "macro", label: "宏观", status: "partial", progress: 1, total: 1, item_count: 80, message: "部分发布时间待核验", error: "", metadata: {} },
        { code: "policy", label: "政策", status: "ready", progress: 1, total: 1, item_count: 40, message: "已保存官方文件", error: "", metadata: {} },
        { code: "market_history", label: "历史行情", status: "ready", progress: 1, total: 1, item_count: 5552, message: "已更新", error: "", metadata: {} },
      ],
      recent_jobs: [],
    });

    render(<MemoryRouter><ValueLineDataRequirements /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "价值线资料清单" })).toBeInTheDocument();
    expect(screen.getByText("专业财务历史（按公告日）")).toBeInTheDocument();
    expect(screen.getByText("中国宏观指标与发布时间")).toBeInTheDocument();
    expect(screen.getByText("公司定期报告与经营资料")).toBeInTheDocument();
    expect(screen.queryByText("需要专业财务包")).not.toBeInTheDocument();
    expect(screen.getAllByText("已就绪").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "打开数据中心" })).toHaveAttribute("href", "/models/data");
  });
});
