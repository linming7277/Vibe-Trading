import { render, screen } from "@testing-library/react";
import { ToolProgressIndicator } from "../ToolProgressIndicator";
import type { ToolCallEntry } from "@/types/agent";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string | number>) => {
      if (key === "toolProgress.step") return `步骤 ${values?.step} · ${values?.tool}`;
      if (key === "toolProgress.toolsRunning") return `同时在做 ${values?.count} 件事`;
      if (key === "toolProgress.toolsCompleted") return `已完成 ${values?.count} 步`;
      if (key === "toolProgress.earlier") return `+${values?.count} 个较早步骤`;
      if (key === "toolProgress.etaSeconds") return `预计剩余 ${values?.seconds} 秒`;
      return key;
    },
  }),
}));

function makeTc(overrides: Partial<ToolCallEntry> = {}): ToolCallEntry {
  return {
    id: "tc-1",
    tool: "backtest",
    arguments: {},
    status: "running",
    timestamp: Date.now(),
    ...overrides,
  };
}

describe("ToolProgressIndicator", () => {
  it("keeps completed tools visible for the remainder of the attempt", () => {
    const tcs = [makeTc({ status: "ok" }), makeTc({ id: "tc-2", status: "error" })];
    render(<ToolProgressIndicator toolCalls={tcs} />);
    expect(screen.getAllByText(/运行回测/)).toHaveLength(2);
  });

  it("renders nothing for empty array", () => {
    const { container } = render(<ToolProgressIndicator toolCalls={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders single running tool", () => {
    const tcs = [makeTc({ elapsed_s: 5 })];
    render(<ToolProgressIndicator toolCalls={tcs} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText(/运行回测/)).toBeInTheDocument();
    expect(screen.getByText("5s")).toBeInTheDocument();
  });

  it("uses the shared elapsed format for persisted completed steps", () => {
    render(<ToolProgressIndicator toolCalls={[
      makeTc({ status: "ok", elapsed_s: undefined, elapsed_ms: 65_000 }),
    ]} />);

    expect(screen.getByText("1m 5s")).toHaveAttribute("aria-hidden", "true");
  });

  it("renders multiple running tools without a second status header", () => {
    const tcs = [
      makeTc({ id: "tc-1", tool: "bash" }),
      makeTc({ id: "tc-2", tool: "write_file" }),
    ];
    render(<ToolProgressIndicator toolCalls={tcs} />);
    expect(screen.queryByText("同时在做 2 件事")).not.toBeInTheDocument();
    expect(screen.getByText(/运行命令/)).toBeInTheDocument();
    expect(screen.getByText(/生成代码/)).toBeInTheDocument();
  });

  it("renders repeated tool names as distinct rows keyed by unique ids", () => {
    const tcs = [
      makeTc({ id: "backtest#1", status: "ok" }),
      makeTc({ id: "backtest#2", status: "running" }),
    ];
    render(<ToolProgressIndicator toolCalls={tcs} />);
    expect(screen.getAllByText(/运行回测/)).toHaveLength(2);
  });

  it("shows every row when ActivityLine is expanded", () => {
    const tcs = [
      makeTc({ id: "tc-1", tool: "bash" }),
      makeTc({ id: "tc-2", tool: "write_file" }),
      makeTc({ id: "tc-3", tool: "backtest" }),
      makeTc({ id: "tc-4", tool: "read_file" }),
    ];
    render(<ToolProgressIndicator toolCalls={tcs} />);

    expect(screen.getByText(/运行命令/)).toBeInTheDocument();
    expect(screen.getByText(/生成代码/)).toBeInTheDocument();
    expect(screen.getByText(/运行回测/)).toBeInTheDocument();
    expect(screen.getByText(/读取文件/)).toBeInTheDocument();
  });

  it("renders rows chronologically with the running call last", () => {
    const tcs = [
      makeTc({ id: "tc-1", tool: "bash", status: "ok" }),
      makeTc({ id: "tc-2", tool: "write_file", status: "error" }),
      makeTc({ id: "tc-3", tool: "backtest", status: "ok" }),
      makeTc({ id: "tc-4", tool: "read_file", status: "running" }),
    ];
    render(<ToolProgressIndicator toolCalls={tcs} />);

    const labels = screen
      .getAllByText(/运行命令|生成代码|运行回测|读取文件/)
      .map((node) => node.textContent);
    expect(labels).toEqual([
      "运行命令",
      "生成代码",
      "运行回测",
      "读取文件",
    ]);
  });

  it("coalesces consecutive successful calls of the same tool into one ×N row", () => {
    const tcs = [
      makeTc({ id: "bt-1", status: "ok", arguments: { symbol: "AAPL" }, elapsed_ms: 300 }),
      makeTc({ id: "bt-2", status: "ok", arguments: { symbol: "MSFT" }, elapsed_ms: 400 }),
      makeTc({ id: "sh-1", tool: "bash", status: "running" }),
    ];
    render(<ToolProgressIndicator toolCalls={tcs} />);

    expect(screen.getAllByText(/运行回测/)).toHaveLength(1);
    expect(screen.getByText("×2")).toBeInTheDocument();
    expect(screen.getByText("AAPL, MSFT")).toBeInTheDocument();
    expect(screen.getByText("0.7s")).toBeInTheDocument();
  });

  it("shows determinate progress bar when progress data exists", () => {
    const tcs = [
      makeTc({
        progress: { current: 5, total: 10, stage: "Processing" },
      }),
    ];
    render(<ToolProgressIndicator toolCalls={tcs} />);
    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.getByText("5/10")).toBeInTheDocument();
    // Should have a progressbar element
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("leaves announcements to ActivityLine and hides ticking elapsed numerals", () => {
    render(<ToolProgressIndicator toolCalls={[makeTc({ elapsed_s: 5 })]} />);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("5s")).toHaveAttribute("aria-hidden", "true");
  });
});
