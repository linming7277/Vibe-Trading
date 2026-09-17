import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AskSupervisorDrawer } from "../AskSupervisorDrawer";

vi.mock("@/lib/api", () => ({ api: {} }));

describe("AskSupervisorDrawer", () => {
  it("open=true → 渲染抽屉与内嵌 /ai/agent 对话", () => {
    render(<AskSupervisorDrawer open={true} onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: "问投研主管" })).toBeTruthy();
    const frame = screen.getByTitle("问投研主管") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe("/ai/agent");
    expect(screen.getByText("直接提问，不离开当前页面。")).toBeTruthy();
  });

  it("open=false → 不渲染任何内容", () => {
    const { container } = render(<AskSupervisorDrawer open={false} onClose={() => {}} />);
    expect(container.textContent).toBe("");
  });

  it("关闭按钮触发 onClose", () => {
    let closed = false;
    render(<AskSupervisorDrawer open={true} onClose={() => { closed = true; }} />);
    screen.getByRole("button", { name: "关闭" }).click();
    expect(closed).toBe(true);
  });
});
