import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { MemoryRouter } from "react-router";
import { AgentSettings } from "../AgentSettings";

const { settings, update } = vi.hoisted(() => ({
  settings: ["research_lead", "macro_policy", "industry", "company", "valuation", "risk", "financial_analyst"].map((role) => ({
    role,
    base_url: "https://models.example.com/v1",
    model: "gpt-5.5",
    api_key_configured: false,
    enabled: true,
    ready: true,
    updated_at: "2026-08-17T00:00:00Z",
  })),
  update: vi.fn(async (role: string, body: Record<string, unknown>) => ({
    role,
    base_url: body.base_url,
    model: body.model,
    api_key_configured: Boolean(body.api_key),
    enabled: body.enabled,
    ready: true,
    updated_at: "2026-08-17T01:00:00Z",
  })),
}));

vi.mock("@/lib/api", () => ({ api: {
  getResearchAgentConnections: vi.fn(async () => ({ items: settings })),
  updateResearchAgentConnection: update,
} }));

describe("AgentSettings", () => {
  beforeEach(() => { update.mockClear(); });

  it("configures URL, model and API key directly for each researcher", async () => {
    render(<MemoryRouter><AgentSettings /></MemoryRouter>);

    expect(await screen.findByText("投研主管")).toBeInTheDocument();
    expect(screen.getByText("风险研究员")).toBeInTheDocument();
    expect(screen.getByText("财报研究员")).toBeInTheDocument();
    expect(screen.getAllByRole("row")[2]).toHaveTextContent("财报研究员");
    expect(screen.queryByText("行业研究员")).not.toBeInTheDocument();
    expect(screen.queryByText("细分赛道研究员")).not.toBeInTheDocument();
    expect(screen.queryByText("公司研究员")).not.toBeInTheDocument();
    expect(screen.getAllByLabelText(/ URL$/)).toHaveLength(5);
    expect(screen.getAllByLabelText(/模型$/)).toHaveLength(5);
    expect(screen.getAllByLabelText(/ API Key$/)).toHaveLength(5);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(document.querySelectorAll("article")).toHaveLength(0);
    expect(screen.queryByText(/Provider/i)).not.toBeInTheDocument();
    expect(screen.queryByText("接口类型")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("宏观政策研究员 URL"), { target: { value: "https://research.example.com/v1" } });
    fireEvent.change(screen.getByLabelText("宏观政策研究员模型"), { target: { value: "deepseek-v4-pro" } });
    fireEvent.change(screen.getByLabelText("宏观政策研究员 API Key"), { target: { value: "test-secret" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "宏观政策研究员启用状态" }));
    fireEvent.click(screen.getByRole("button", { name: "保存宏观政策研究员" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("macro_policy", {
      base_url: "https://research.example.com/v1",
      model: "deepseek-v4-pro",
      api_key: "test-secret",
      clear_api_key: false,
      enabled: false,
    }));
  });
});
