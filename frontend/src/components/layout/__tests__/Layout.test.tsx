import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { Layout } from "../Layout";

const apiMock = vi.hoisted(() => ({
  listSessions: vi.fn().mockResolvedValue([{ session_id: "session-1", title: "贵州茅台研究" }]),
  refreshDashboard: vi.fn().mockResolvedValue({ message: "已刷新" }),
  createStrategyRun: vi.fn().mockResolvedValue({ id: "run-1" }),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/hooks/useDarkMode", () => ({ useDarkMode: () => ({ dark: false, toggle: vi.fn() }) }));
vi.mock("@/stores/agent", () => ({
  useAgentStore: (selector: (state: { sseStatus: string; sseRetryAttempt: number }) => unknown) =>
    selector({ sseStatus: "connected", sseRetryAttempt: 0 }),
}));

function renderLayout(path = "/value") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<div>Page content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("双策略 Layout", () => {
  beforeEach(() => {
    apiMock.listSessions.mockClear();
    apiMock.refreshDashboard.mockClear();
    apiMock.createStrategyRun.mockClear();
    window.localStorage.clear();
  });

  it("shows six business entries, two system entries, search and main landmark", () => {
    renderLayout();
    expect(screen.getByRole("complementary", { name: "恒值投资侧边栏" })).toBeInTheDocument();
    const primary = screen.getByRole("navigation", { name: "主导航" });
    const utility = screen.getByRole("navigation", { name: "系统导航" });
    expect(within(primary).getAllByRole("link")).toHaveLength(5);
    expect(within(utility).getAllByRole("link")).toHaveLength(2);
    expect(within(primary).getByRole("link", { name: "价值投资" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "恒值投资" })).toHaveAttribute("href", "/value");
    expect(screen.getByRole("textbox", { name: "搜索证券" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByText("跳到主要内容")).toHaveAttribute("href", "#main");
  });

  it.each([
    ["市场行情", "/market/overview"],
    ["价值投资", "/value"],
    ["情绪交易", "/emotion/temperature"],
    ["模拟验证", "/simulation/accounts"],
    ["AI 研究", "/ai/agent"],
  ])("uses the expected default route for %s", (label, href) => {
    renderLayout();
    expect(within(screen.getByRole("navigation", { name: "主导航" })).getByRole("link", { name: label })).toHaveAttribute("href", href);
  });

  it.each([
    ["/market/ranks", "市场行情", "市场行情二级导航", "行情榜单"],
    ["/market/sectors", "市场行情", "市场行情二级导航", "板块行情"],
    ["/value", "价值投资", "价值投资二级导航", "价值龙头"],
    ["/emotion/swing", "情绪交易", "情绪交易二级导航", "波段候选"],
    ["/simulation/compare", "模拟验证", "模拟验证二级导航", "归因对比"],
    ["/ai/reports", "AI 研究", "AI 研究二级导航", "研究报告"],
  ])("maps %s to its primary and secondary navigation", (path, primaryLabel, secondaryLabel, tabLabel) => {
    renderLayout(path);
    expect(within(screen.getByRole("navigation", { name: "主导航" })).getByRole("link", { name: primaryLabel })).toHaveAttribute("aria-current", "page");
    expect(within(screen.getByRole("navigation", { name: secondaryLabel })).getByRole("link", { name: tabLabel })).toHaveAttribute("aria-current", "page");
  });

  it.each([
    ["/company/CN/600519.SH", "价值投资"],
    ["/ai/reports/report-1", "AI 研究"],
    ["/ai/committees/committee-1", "AI 研究"],
    ["/simulation/runs/run-1", "模拟验证"],
    ["/market/sectors/BK001", "市场行情"],
    ["/reports/report-1", "AI 研究"],
    ["/committee/committee-1", "AI 研究"],
    ["/runs/run-1", "模拟验证"],
  ])("keeps %s in its parent section while hiding secondary navigation", (path, primaryLabel) => {
    renderLayout(path);
    expect(within(screen.getByRole("navigation", { name: "主导航" })).getByRole("link", { name: primaryLabel })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("navigation", { name: /二级导航/ })).not.toBeInTheDocument();
  });

  it("keeps data and models plus settings in the system navigation", () => {
    renderLayout("/models/data");
    const utility = screen.getByRole("navigation", { name: "系统导航" });
    expect(within(utility).getByRole("link", { name: "数据与模型" })).toHaveAttribute("aria-current", "page");
    expect(within(utility).getByRole("link", { name: "设置" })).toHaveAttribute("href", "/settings");
    expect(screen.getAllByRole("link", { name: "设置" })).toHaveLength(1);
    expect(within(screen.getByRole("navigation", { name: "数据与模型二级导航" })).getAllByRole("link")).toHaveLength(6);
  });

  it("preserves accessible labels and the saved preference when collapsed", () => {
    renderLayout();
    fireEvent.click(screen.getByRole("button", { name: "收起侧栏" }));
    expect(screen.getByRole("complementary", { name: "恒值投资侧边栏" })).toHaveClass("w-[68px]");
    expect(screen.getByRole("link", { name: "价值投资" })).toHaveAttribute("title", "价值投资");
    expect(window.localStorage.getItem("hz-sidebar")).toBe("collapsed");
  });

  it("loads sessions only inside AI conversations", async () => {
    renderLayout("/ai/agent");
    expect(await screen.findByText("贵州茅台研究")).toBeInTheDocument();
    expect(apiMock.listSessions).toHaveBeenCalledTimes(1);
  });

  it("does not request sessions on business pages", () => {
    renderLayout("/value");
    expect(apiMock.listSessions).not.toHaveBeenCalled();
    expect(screen.queryByText("最近会话")).not.toBeInTheDocument();
  });

  it("supports market switching without exposing a language selector", () => {
    renderLayout("/market/overview");
    fireEvent.click(screen.getByRole("button", { name: "港股" }));
    expect(window.localStorage.getItem("hengzhi-market")).toBe("HK");
    expect(screen.queryByRole("button", { name: "语言" })).not.toBeInTheDocument();
  });

  it("does not crash when local storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError"); });
    expect(() => renderLayout()).not.toThrow();
  });
});
