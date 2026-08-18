import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { Settings } from "../Settings";

const tr = (key: string) => i18n.t(key);

const apiMock = vi.hoisted(() => ({
  getLLMSettings: vi.fn(),
  getDataSourceSettings: vi.fn(),
  getChannelStatus: vi.fn(),
  getFeishuChannelConfig: vi.fn(),
  updateFeishuChannelConfig: vi.fn(),
  runChannelPairingCommand: vi.fn(),
  listLLMModels: vi.fn(),
  startChannels: vi.fn(),
  stopChannels: vi.fn(),
  updateLLMSettings: vi.fn(),
  updateDataSourceSettings: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: apiMock,
    isAuthRequiredError: vi.fn(() => false),
  };
});

vi.mock("@/lib/apiAuth", () => ({
  getApiAuthKey: vi.fn(() => ""),
  setApiAuthKey: vi.fn(),
}));

function llmSettings() {
  return {
    provider: "openrouter",
    model_name: "deepseek/deepseek-v3.2",
    base_url: "https://openrouter.ai/api/v1",
    api_key_env: "OPENROUTER_API_KEY",
    api_key_configured: false,
    api_key_required: true,
    temperature: 0.1,
    timeout_seconds: 120,
    max_retries: 2,
    reasoning_effort: "",
    sse_timeout_seconds: 300,
    env_path: "agent/.env",
    providers: [
      {
        name: "openrouter",
        label: "OpenRouter",
        api_key_env: "OPENROUTER_API_KEY",
        base_url_env: "OPENROUTER_BASE_URL",
        default_model: "deepseek/deepseek-v3.2",
        default_base_url: "https://openrouter.ai/api/v1",
        api_key_required: true,
        auth_type: "api_key",
      },
    ],
  };
}

function dataSourceSettings() {
  return {
    tushare_token_configured: false,
    baostock_supported: true,
    baostock_installed: true,
    baostock_message: "BaoStock available",
    env_path: "agent/.env",
  };
}

function channelStatus(overrides = {}) {
  return {
    running: false,
    inbound_queue: 0,
    outbound_queue: 0,
    session_count: 0,
    channels: {
      feishu: {
        name: "feishu",
        display_name: "飞书",
        configured: true,
        enabled: true,
        available: true,
        loaded: true,
        running: false,
        error: "",
        install_hint: "",
      },
      weixin: {
        name: "weixin",
        display_name: "微信",
        configured: true,
        enabled: false,
        available: false,
        loaded: false,
        running: false,
        error: "未配置",
        install_hint: "请配置微信通道",
      },
    },
    ...overrides,
  };
}

function feishuConfig() {
  return {
    auto_start: true,
    enabled: true,
    app_id: "cli_financial",
    app_secret_configured: true,
    domain: "feishu" as const,
    group_policy: "mention" as const,
    reply_to_message: true,
    streaming: true,
    topic_isolation: true,
    default_agent: "financial_analyst" as const,
    allow_from_count: 1,
    config_path: "C:/Users/test/.vibe-trading/agent.json",
  };
}

describe("Settings IM channels panel", () => {
  beforeEach(() => {
    apiMock.getLLMSettings.mockResolvedValue(llmSettings());
    apiMock.getDataSourceSettings.mockResolvedValue(dataSourceSettings());
    apiMock.getChannelStatus.mockResolvedValue(channelStatus());
    apiMock.getFeishuChannelConfig.mockResolvedValue(feishuConfig());
    apiMock.listLLMModels.mockResolvedValue({
      provider: "openrouter",
      models: ["deepseek/deepseek-v3.2"],
      source: "default",
      warning_code: "api_key_required",
    });
    apiMock.startChannels.mockResolvedValue(channelStatus({ running: true }));
    apiMock.stopChannels.mockResolvedValue(channelStatus());
    apiMock.updateFeishuChannelConfig.mockResolvedValue({
      config: feishuConfig(),
      bot: { app_name: "财报研究员", open_id: "ou_bot" },
      runtime: channelStatus({ running: true }),
    });
    apiMock.runChannelPairingCommand.mockResolvedValue({
      channel: "feishu",
      reply: "Approved pairing code `ABC123` — user can now access feishu",
    });
  });

  it("renders channel runtime status and refreshes it", async () => {
    render(<Settings />);

    expect(await screen.findByText(tr("settings.channels.title"))).toBeInTheDocument();
    expect(screen.getByText("feishu")).toBeInTheDocument();
    expect(screen.getByText("weixin")).toBeInTheDocument();
    expect(screen.getByText("请配置微信通道")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: tr("settings.channels.refresh") }));

    await waitFor(() => expect(apiMock.getChannelStatus).toHaveBeenCalledTimes(2));
  });

  it("starts channels from the settings control surface", async () => {
    render(<Settings />);
    await screen.findByText(tr("settings.channels.title"));

    fireEvent.click(screen.getByRole("button", { name: tr("settings.channels.start") }));

    await waitFor(() => expect(apiMock.startChannels).toHaveBeenCalledTimes(1));
  });

  it("saves the dedicated Feishu Financial Analyst configuration", async () => {
    render(<Settings />);
    await screen.findByText(tr("settings.channels.feishuTitle"));

    fireEvent.click(screen.getByRole("button", { name: tr("settings.channels.saveAndConnect") }));

    await waitFor(() => expect(apiMock.updateFeishuChannelConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        app_id: "cli_financial",
        default_agent: "financial_analyst",
        group_policy: "mention",
      }),
    ));
  });

  it("approves a Feishu user pairing code", async () => {
    render(<Settings />);
    await screen.findByText(tr("settings.channels.pairingTitle"));

    fireEvent.change(screen.getByLabelText(tr("settings.channels.pairingCode")), { target: { value: "abc123" } });
    fireEvent.click(screen.getByRole("button", { name: tr("settings.channels.approvePairing") }));

    await waitFor(() => expect(apiMock.runChannelPairingCommand).toHaveBeenCalledWith({
      channel: "feishu",
      command: "approve ABC123",
    }));
  });

  it("still renders LLM and data source settings when channel status fails", async () => {
    apiMock.getChannelStatus.mockRejectedValue(
      new Error('Expected JSON from /channels/status, got text/html: <!doctype html>'),
    );

    render(<Settings />);

    expect(await screen.findByText(tr("settings.llmSettings"))).toBeInTheDocument();
    expect(screen.getByText(tr("settings.dataSourceSettings"))).toBeInTheDocument();
    expect(screen.getByText(tr("settings.channels.title"))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: tr("settings.channels.refresh") })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: tr("settings.channels.start") })).toBeDisabled();
  });

  it("translates stable model-discovery warning codes in the frontend", async () => {
    render(<Settings />);
    await screen.findByText(tr("settings.llmSettings"));

    fireEvent.click(screen.getByRole("button", { name: tr("settings.loadModels") }));

    expect(await screen.findByText(tr("settings.modelDiscoveryApiKeyRequired"))).toBeInTheDocument();
  });
});
