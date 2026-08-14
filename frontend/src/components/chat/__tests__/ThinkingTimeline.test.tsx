import { act, fireEvent, render, screen } from "@testing-library/react";
import { ThinkingTimeline } from "../ThinkingTimeline";
import type { AgentActivity, StoredAgentMessage } from "@/stores/agent";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string | number>) => {
      const labels: Record<string, string> = {
        "agent.activity.verbs.working": "正在处理",
        "agent.activity.verbs.readingMarketData": "正在读取市场数据",
        "agent.activity.verbs.writingStrategy": "正在编写策略",
        "agent.activity.verbs.runningBacktest": "正在运行回测",
        "agent.activity.verbs.validatingNumbers": "正在复核数据",
        "agent.activity.done": "完成",
        "agent.activity.failed": "失败",
        "agent.activity.stopped": "已由你停止",
        "agent.activity.continue": "继续",
      };
      if (key === "agent.activity.steps") return `${values?.count} 个步骤`;
      if (key === "agent.activity.timeout") return `等待 ${values?.elapsed} 后已停止`;
      if (key === "toolProgress.step") return `步骤 ${values?.step} · ${values?.tool}`;
      return labels[key] ?? key;
    },
  }),
}));

function makeMsg(overrides: Partial<StoredAgentMessage> = {}): StoredAgentMessage {
  return {
    id: "msg-1",
    type: "tool_call",
    content: "",
    tool: "bash",
    status: "running",
    timestamp: 1000,
    ...overrides,
  };
}

function activityMessage(activity: AgentActivity): StoredAgentMessage {
  return makeMsg({
    id: `activity-${activity.attemptId}`,
    type: "thinking",
    meta: { activity },
    timestamp: activity.startedAt,
  });
}

describe("ThinkingTimeline", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("rebuilds a legacy completed group through ActivityLine", () => {
    render(<ThinkingTimeline messages={[
      makeMsg({ type: "tool_call", tool: "backtest", status: "ok" }),
      makeMsg({
        id: "result",
        type: "tool_result",
        tool: "backtest",
        status: "ok",
        elapsed_ms: 3200,
        content: "done",
      }),
    ]} />);

    expect(screen.getByText(/完成 · 1 个步骤 · 3s/)).toBeInTheDocument();
  });

  it("shows the latest legacy group as active", () => {
    vi.spyOn(Date, "now").mockReturnValue(2000);
    render(<ThinkingTimeline messages={[
      makeMsg({ tool: "backtest", status: "running" }),
    ]} isLatest />);

    expect(screen.getByText(/正在运行回测 · 运行回测/)).toBeInTheDocument();
  });

  it("auto-collapses a completed activity after 900ms", () => {
    vi.useFakeTimers();
    render(<ThinkingTimeline messages={[
      activityMessage({
        attemptId: "done-1",
        state: "done",
        verb: "working",
        steps: [{
          id: "tool-1",
          tool: "bash",
          arguments: {},
          status: "ok",
          timestamp: 1000,
        }],
        startedAt: 1000,
        endedAt: 2000,
      }),
    ]} />);

    const disclosure = screen.getByRole("button");
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
    act(() => vi.advanceTimersByTime(900));
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
  });

  it("expands a historical activity on demand", () => {
    vi.useFakeTimers();
    render(<ThinkingTimeline messages={[
      makeMsg({ type: "tool_call", tool: "bash", status: "ok" }),
      makeMsg({ id: "result", type: "tool_result", tool: "bash", status: "ok", elapsed_ms: 100 }),
    ]} />);
    act(() => vi.advanceTimersByTime(900));

    const disclosure = screen.getByRole("button");
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/运行命令/)).toBeVisible();
  });

  it("renders the durable failed state instead of inferring from row icons", () => {
    render(<ThinkingTimeline messages={[
      activityMessage({
        attemptId: "failed-1",
        state: "failed",
        verb: "readingMarketData",
        steps: [],
        startedAt: 1000,
        endedAt: 2500,
      }),
    ]} />);

    expect(screen.getByText("失败 · 0 个步骤 · 1s")).toBeInTheDocument();
  });

  it("keeps zero-tool turns as an activity row", () => {
    render(<ThinkingTimeline messages={[
      activityMessage({
        attemptId: "empty-1",
        state: "done",
        verb: "working",
        steps: [],
        startedAt: 1000,
        endedAt: 1000,
      }),
    ]} />);

    expect(screen.getByText("完成 · 0 个步骤 · 0s")).toBeInTheDocument();
  });

  it("delegates Continue for stopped turns", () => {
    const onContinue = vi.fn();
    const stopped: AgentActivity = {
      attemptId: "stopped-1",
      state: "stopped",
      verb: "working",
      steps: [],
      startedAt: 1000,
      endedAt: 4000,
    };
    render(
      <ThinkingTimeline
        messages={[activityMessage(stopped)]}
        onContinue={onContinue}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(onContinue).toHaveBeenCalledWith(stopped);
  });

  it("exposes polite, non-atomic status semantics", () => {
    render(<ThinkingTimeline messages={[
      activityMessage({
        attemptId: "done-a11y",
        state: "done",
        verb: "working",
        steps: [],
        startedAt: 1000,
        endedAt: 1000,
      }),
    ]} />);

    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("status")).toHaveAttribute("aria-atomic", "false");
  });
});
