import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Database, KeyRound, Loader2, MessageSquareMore, Play, RefreshCw, RotateCcw, Save, Server, SlidersHorizontal, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ModelPicker } from "@/components/settings/ModelPicker";
import { QVerisSettings } from "@/components/settings/QVerisSettings"; // QVERIS-INTEGRATION
import { api, isAuthRequiredError, type ChannelRuntimeStatus, type DataSourceSettings, type FeishuChannelConfig, type LLMProviderOption, type LLMSettings } from "@/lib/api";
import { getApiAuthKey, setApiAuthKey } from "@/lib/apiAuth";

interface LLMFormState {
  provider: string;
  model_name: string;
  base_url: string;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort: string;
}

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60";
const labelClass = "text-sm font-medium";
const hintClass = "text-xs text-muted-foreground";

function toForm(settings: LLMSettings): LLMFormState {
  return {
    provider: settings.provider,
    model_name: settings.model_name,
    base_url: settings.base_url,
    temperature: settings.temperature,
    timeout_seconds: settings.timeout_seconds,
    max_retries: settings.max_retries,
    reasoning_effort: settings.reasoning_effort || "",
  };
}

export function Settings() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [dataSettings, setDataSettings] = useState<DataSourceSettings | null>(null);
  const [channelStatus, setChannelStatus] = useState<ChannelRuntimeStatus | null>(null);
  const [feishuConfig, setFeishuConfig] = useState<FeishuChannelConfig | null>(null);
  const [feishuSecret, setFeishuSecret] = useState("");
  const [clearFeishuSecret, setClearFeishuSecret] = useState(false);
  const [feishuPairingCode, setFeishuPairingCode] = useState("");
  const [feishuPairingResult, setFeishuPairingResult] = useState("");
  const [form, setForm] = useState<LLMFormState | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelListHint, setModelListHint] = useState<string | null>(null);
  const [localApiKey, setLocalApiKeyState] = useState(() => getApiAuthKey());
  const [clearApiKey, setClearApiKey] = useState(false);
  const [tushareToken, setTushareToken] = useState("");
  const [clearTushareToken, setClearTushareToken] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dataSaving, setDataSaving] = useState(false);
  const [channelRefreshing, setChannelRefreshing] = useState(false);
  const [channelAction, setChannelAction] = useState<"start" | "stop" | null>(null);
  const [feishuSaving, setFeishuSaving] = useState(false);
  const [settingsLoadError, setSettingsLoadError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    Promise.allSettled([
      api.getLLMSettings(),
      api.getDataSourceSettings(),
      api.getChannelStatus(),
      api.getFeishuChannelConfig(),
    ])
      .then(([llmResult, dataSourceResult, channelResult, feishuResult]) => {
        if (!alive) return;

        if (llmResult.status === "fulfilled") {
          setSettings(llmResult.value);
          setForm(toForm(llmResult.value));
          setModelOptions(Array.from(new Set([
            llmResult.value.model_name,
            llmResult.value.providers.find((provider) => provider.name === llmResult.value.provider)?.default_model ?? "",
          ].filter(Boolean))));
        } else {
          const message = llmResult.reason instanceof Error
            ? llmResult.reason.message
            : t("settings.unknownError", { defaultValue: "Unknown error" });
          setSettingsLoadError(message);
          if (isAuthRequiredError(llmResult.reason)) {
            toast.error(message);
          } else {
            toast.error(t("settings.loadLlmSettingsFailed", { message }));
          }
        }

        if (dataSourceResult.status === "fulfilled") {
          setDataSettings(dataSourceResult.value);
        } else {
          const message = dataSourceResult.reason instanceof Error
            ? dataSourceResult.reason.message
            : t("settings.unknownError", { defaultValue: "Unknown error" });
          setSettingsLoadError(message);
          if (isAuthRequiredError(dataSourceResult.reason)) {
            toast.error(message);
          } else {
            toast.error(t("settings.loadDataSourceSettingsFailed", { message }));
          }
        }

        if (channelResult.status === "fulfilled") {
          setChannelStatus(channelResult.value);
        } else {
          const message = channelResult.reason instanceof Error
            ? channelResult.reason.message
            : t("settings.unknownError", { defaultValue: "Unknown error" });
          toast.error(`${t("settings.channels.refreshFailed")}: ${message}`);
          setChannelStatus(null);
        }

        if (feishuResult.status === "fulfilled") {
          setFeishuConfig(feishuResult.value);
        } else {
          const message = feishuResult.reason instanceof Error
            ? feishuResult.reason.message
            : t("settings.unknownError", { defaultValue: "Unknown error" });
          toast.error(`${t("settings.channels.feishuLoadFailed")}: ${message}`);
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
    };
  }, [t]);

  const refreshChannelStatus = async () => {
    setChannelRefreshing(true);
    try {
      setChannelStatus(await api.getChannelStatus());
    } catch (error) {
      toast.error(`${t("settings.channels.refreshFailed")}: ${error instanceof Error ? error.message : t("settings.unknownError", { defaultValue: "Unknown error" })}`);
    } finally {
      setChannelRefreshing(false);
    }
  };

  const setChannelsRunning = async (action: "start" | "stop") => {
    setChannelAction(action);
    try {
      const updated = action === "start" ? await api.startChannels() : await api.stopChannels();
      setChannelStatus(updated);
      toast.success(action === "start" ? t("settings.channels.started") : t("settings.channels.stoppedToast"));
    } catch (error) {
      toast.error(`${action === "start" ? t("settings.channels.startFailed") : t("settings.channels.stopFailed")}: ${error instanceof Error ? error.message : t("settings.unknownError", { defaultValue: "Unknown error" })}`);
    } finally {
      setChannelAction(null);
    }
  };

  const saveFeishuConfig = async () => {
    if (!feishuConfig) return;
    setFeishuSaving(true);
    try {
      const result = await api.updateFeishuChannelConfig({
        auto_start: feishuConfig.auto_start,
        enabled: feishuConfig.enabled,
        app_id: feishuConfig.app_id.trim(),
        app_secret: feishuSecret.trim() || undefined,
        clear_app_secret: clearFeishuSecret,
        domain: feishuConfig.domain,
        group_policy: feishuConfig.group_policy,
        reply_to_message: feishuConfig.reply_to_message,
        streaming: feishuConfig.streaming,
        topic_isolation: feishuConfig.topic_isolation,
        default_agent: feishuConfig.default_agent,
      });
      setFeishuConfig(result.config);
      setChannelStatus(result.runtime);
      setFeishuSecret("");
      setClearFeishuSecret(false);
      toast.success(result.bot?.app_name
        ? t("settings.channels.feishuConnectedAs", { name: result.bot.app_name })
        : t("settings.channels.feishuSaved"));
    } catch (error) {
      toast.error(`${t("settings.channels.feishuSaveFailed")}: ${error instanceof Error ? error.message : t("settings.unknownError", { defaultValue: "Unknown error" })}`);
    } finally {
      setFeishuSaving(false);
    }
  };

  const runFeishuPairing = async (command: "list" | "approve") => {
    const code = feishuPairingCode.trim();
    if (command === "approve" && !code) return;
    try {
      const result = await api.runChannelPairingCommand({
        channel: "feishu",
        command: command === "list" ? "list" : `approve ${code}`,
      });
      setFeishuPairingResult(result.reply);
      if (command === "approve" && result.reply.startsWith("Approved")) {
        setFeishuPairingCode("");
        toast.success(t("settings.channels.pairingApproved"));
      }
    } catch (error) {
      toast.error(`${t("settings.channels.pairingFailed")}: ${error instanceof Error ? error.message : t("settings.unknownError", { defaultValue: "Unknown error" })}`);
    }
  };

  const providers = settings?.providers ?? [];
  const selectedProvider = useMemo<LLMProviderOption | undefined>(
    () => providers.find((provider) => provider.name === form?.provider),
    [form?.provider, providers],
  );

  const applyProviderDefaults = (provider = selectedProvider) => {
    if (!provider || !form) return;
    setForm({
      ...form,
      model_name: provider.default_model,
      base_url: provider.default_base_url,
    });
    setModelOptions([provider.default_model]);
    setModelListHint(null);
  };

  const onProviderChange = (name: string) => {
    const provider = providers.find((item) => item.name === name);
    if (!provider || !form) return;
    setForm({
      ...form,
      provider: provider.name,
      model_name: provider.default_model,
      base_url: provider.default_base_url,
    });
    setApiKey("");
    setClearApiKey(false);
    setModelOptions([provider.default_model]);
    setModelListHint(null);
  };

  const refreshModels = async () => {
    if (!form || !selectedProvider) return;
    setModelsLoading(true);
    setModelListHint(null);
    try {
      const result = await api.listLLMModels({
        provider: form.provider,
        base_url: form.base_url,
        api_key: apiKey.trim() || undefined,
      });
      setModelOptions(Array.from(new Set([
        form.model_name,
        selectedProvider.default_model,
        ...result.models,
      ].filter(Boolean))));
      const warningMessages = {
        oauth_discovery_unsupported: t("settings.modelDiscoveryOauthUnsupported"),
        api_key_required: t("settings.modelDiscoveryApiKeyRequired"),
        model_list_unavailable: t("settings.modelDiscoveryUnavailable"),
      };
      setModelListHint(
        result.warning_code
          ? warningMessages[result.warning_code]
          : t("settings.modelsLoaded", { count: result.models.length }),
      );
    } catch (error) {
      setModelListHint(error instanceof Error ? error.message : t("settings.modelsLoadFailed"));
    } finally {
      setModelsLoading(false);
    }
  };

  const submitLocalApiKey = (event: FormEvent) => {
    event.preventDefault();
    setApiAuthKey(localApiKey);
    toast.success(t("settings.localApiKeySaved"));
    window.location.reload();
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    setSaving(true);
    try {
      const updated = await api.updateLLMSettings({
        ...form,
        api_key: apiKey.trim() || undefined,
        clear_api_key: clearApiKey,
      });
      setSettings(updated);
      setForm(toForm(updated));
      setApiKey("");
      setClearApiKey(false);
      toast.success(t("settings.llmSettingsSaved"));
    } catch (error) {
      toast.error(t("settings.saveLlmSettingsFailed", {
        message: error instanceof Error
          ? error.message
          : t("settings.unknownError", { defaultValue: "Unknown error" }),
      }));
    } finally {
      setSaving(false);
    }
  };

  const submitDataSources = async (event: FormEvent) => {
    event.preventDefault();
    setDataSaving(true);
    try {
      const updated = await api.updateDataSourceSettings({
        tushare_token: tushareToken.trim() || undefined,
        clear_tushare_token: clearTushareToken,
      });
      setDataSettings(updated);
      setTushareToken("");
      setClearTushareToken(false);
      toast.success(t("settings.dataSourceSettingsSaved"));
    } catch (error) {
      toast.error(t("settings.saveDataSourceSettingsFailed", {
        message: error instanceof Error
          ? error.message
          : t("settings.unknownError", { defaultValue: "Unknown error" }),
      }));
    } finally {
      setDataSaving(false);
    }
  };

  const localApiAccessSection = (
    <form onSubmit={submitLocalApiKey} className="rounded-lg border bg-card p-5 shadow-sm">
      <div className="mb-4 space-y-1">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-primary" />
          <h2 className="text-base font-semibold">{t("settings.localApiAccess")}</h2>
        </div>
        <p className="text-sm text-muted-foreground">{t("settings.localApiAccessDesc")}</p>
      </div>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
        <label className="grid gap-2">
          <span className={labelClass}>{t("settings.serverApiKey")}</span>
          <input
            type="password"
            value={localApiKey}
            onChange={(event) => setLocalApiKeyState(event.target.value)}
            className={fieldClass}
            placeholder={t("settings.storedInBrowser")}
            autoComplete="current-password"
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center justify-center gap-2 self-end rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
        >
          <Save className="h-4 w-4" />
          {t("settings.save")}
        </button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{t("settings.storedInBrowser")}</p>
    </form>
  );

  if (loading || !form || !settings || !dataSettings) {
    return (
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{t("settings.title")}</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">{t("settings.subtitle")}</p>
        </div>
        {localApiAccessSection}
        {/* QVERIS-INTEGRATION */}
        <QVerisSettings />
        <div className="flex min-h-32 items-center justify-center rounded-lg border bg-card p-5 text-sm text-muted-foreground">
          {settingsLoadError ? (
            <div className="text-center">
              <div className="font-medium text-foreground">{t("settings.unavailable")}</div>
              <div className="mt-1">{settingsLoadError}</div>
            </div>
          ) : (
            <>
              <Loader2 className="me-2 h-4 w-4 animate-spin" />
              {t("settings.loading")}
            </>
          )}
        </div>
      </div>
    );
  }

  const keyStatus = settings.api_key_configured
    ? t("settings.configured")
    : settings.api_key_required
      ? t("settings.keepCurrentKey")
      : selectedProvider?.auth_type === "oauth" && selectedProvider.login_command
        ? t("settings.providerUsesOauth", { command: selectedProvider.login_command })
        : t("settings.noApiKeyRequired");
  const apiKeyDisabled = !selectedProvider?.api_key_required || clearApiKey;
  const tushareStatus = dataSettings.tushare_token_configured
    ? t("settings.configured")
    : t("settings.keepCurrentToken");
  const channelRows = channelStatus
    ? Object.entries(channelStatus.channels ?? {}).sort(([a], [b]) => a.localeCompare(b))
    : [];
  const channelEnabledCount = channelRows.filter(([, item]) => item.enabled).length;
  const channelLoadedCount = channelRows.filter(([, item]) => item.loaded).length;
  const channelUnavailableCount = channelRows.filter(([, item]) => item.available === false).length;
  const channelBusy = channelRefreshing || channelAction !== null;

  const channelsSection = (
    <section className="rounded-lg border bg-card p-5 shadow-sm">
      <div className="mb-5 flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <MessageSquareMore className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{t("settings.channels.title")}</h2>
          </div>
          <p className="max-w-3xl text-sm text-muted-foreground">{t("settings.channels.description")}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={refreshChannelStatus}
            disabled={channelBusy}
            className="inline-flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
          >
            {channelRefreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {t("settings.channels.refresh")}
          </button>
          <button
            type="button"
            onClick={() => setChannelsRunning("start")}
            disabled={channelBusy || !channelStatus}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {channelAction === "start" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {t("settings.channels.start")}
          </button>
          <button
            type="button"
            onClick={() => setChannelsRunning("stop")}
            disabled={channelBusy || !channelStatus}
            className="inline-flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
          >
            {channelAction === "stop" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
            {t("settings.channels.stop")}
          </button>
        </div>
      </div>

      {feishuConfig ? (
        <div className="mb-5 border-y bg-muted/10 px-4 py-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold">{t("settings.channels.feishuTitle")}</h3>
              <p className="mt-1 text-xs text-muted-foreground">{t("settings.channels.feishuDescription")}</p>
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
              <input
                aria-label={t("settings.channels.feishuEnabled")}
                type="checkbox"
                checked={feishuConfig.enabled}
                onChange={(event) => setFeishuConfig({ ...feishuConfig, enabled: event.target.checked })}
              />
              {t("settings.channels.feishuEnabled")}
            </label>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="grid gap-2">
              <span className={labelClass}>App ID</span>
              <input
                aria-label="飞书 App ID"
                className={fieldClass}
                value={feishuConfig.app_id}
                onChange={(event) => setFeishuConfig({ ...feishuConfig, app_id: event.target.value })}
                placeholder="cli_xxxxxxxxxxxxxxxx"
              />
            </label>
            <label className="grid gap-2">
              <span className={labelClass}>App Secret</span>
              <input
                aria-label="飞书 App Secret"
                type="password"
                autoComplete="new-password"
                className={fieldClass}
                value={feishuSecret}
                disabled={clearFeishuSecret}
                onChange={(event) => setFeishuSecret(event.target.value)}
                placeholder={feishuConfig.app_secret_configured ? t("settings.channels.secretConfigured") : t("settings.channels.secretRequired")}
              />
              {feishuConfig.app_secret_configured ? (
                <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <input
                    aria-label={t("settings.channels.clearFeishuSecret")}
                    type="checkbox"
                    checked={clearFeishuSecret}
                    onChange={(event) => { setClearFeishuSecret(event.target.checked); setFeishuSecret(""); }}
                  />
                  {t("settings.channels.clearFeishuSecret")}
                </label>
              ) : null}
            </label>
            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.channels.defaultAgent")}</span>
              <select
                aria-label={t("settings.channels.defaultAgent")}
                className={fieldClass}
                value={feishuConfig.default_agent}
                onChange={(event) => setFeishuConfig({ ...feishuConfig, default_agent: event.target.value as FeishuChannelConfig["default_agent"] })}
              >
                <option value="financial_analyst">{t("settings.channels.financialAnalyst")}</option>
                <option value="general">{t("settings.channels.generalAgent")}</option>
              </select>
            </label>
            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.channels.groupPolicy")}</span>
              <select
                aria-label={t("settings.channels.groupPolicy")}
                className={fieldClass}
                value={feishuConfig.group_policy}
                onChange={(event) => setFeishuConfig({ ...feishuConfig, group_policy: event.target.value as FeishuChannelConfig["group_policy"] })}
              >
                <option value="mention">{t("settings.channels.mentionOnly")}</option>
                <option value="open">{t("settings.channels.allGroupMessages")}</option>
              </select>
            </label>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
            <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
              <label className="inline-flex items-center gap-2"><input type="checkbox" checked={feishuConfig.auto_start} onChange={(event) => setFeishuConfig({ ...feishuConfig, auto_start: event.target.checked })} />{t("settings.channels.autoStart")}</label>
              <label className="inline-flex items-center gap-2"><input type="checkbox" checked={feishuConfig.reply_to_message} onChange={(event) => setFeishuConfig({ ...feishuConfig, reply_to_message: event.target.checked })} />{t("settings.channels.replyToMessage")}</label>
              <label className="inline-flex items-center gap-2"><input type="checkbox" checked={feishuConfig.streaming} onChange={(event) => setFeishuConfig({ ...feishuConfig, streaming: event.target.checked })} />{t("settings.channels.streaming")}</label>
              <label className="inline-flex items-center gap-2"><input type="checkbox" checked={feishuConfig.topic_isolation} onChange={(event) => setFeishuConfig({ ...feishuConfig, topic_isolation: event.target.checked })} />{t("settings.channels.topicIsolation")}</label>
            </div>
            <button
              type="button"
              onClick={() => void saveFeishuConfig()}
              disabled={feishuSaving || (feishuConfig.enabled && (!feishuConfig.app_id.trim() || (!feishuConfig.app_secret_configured && !feishuSecret.trim())))}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
            >
              {feishuSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {feishuConfig.enabled ? t("settings.channels.saveAndConnect") : t("settings.channels.saveFeishu")}
            </button>
          </div>
          <div className="mt-4 border-t pt-4">
            <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
              <div>
                <h4 className="text-sm font-medium">{t("settings.channels.pairingTitle")}</h4>
                <p className="mt-1 text-xs text-muted-foreground">{t("settings.channels.pairingDescription")}</p>
              </div>
              <button type="button" className="rounded-md border px-3 py-1.5 text-xs hover:bg-muted" onClick={() => void runFeishuPairing("list")}>{t("settings.channels.listPairing")}</button>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                aria-label={t("settings.channels.pairingCode")}
                className={fieldClass}
                value={feishuPairingCode}
                onChange={(event) => setFeishuPairingCode(event.target.value.toUpperCase())}
                placeholder={t("settings.channels.pairingCodePlaceholder")}
              />
              <button type="button" disabled={!feishuPairingCode.trim()} className="shrink-0 rounded-md border px-4 py-2 text-sm font-medium disabled:opacity-50" onClick={() => void runFeishuPairing("approve")}>{t("settings.channels.approvePairing")}</button>
            </div>
            {feishuPairingResult ? <pre className="mt-3 whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-xs">{feishuPairingResult}</pre> : null}
          </div>
        </div>
      ) : null}

      {channelStatus ? (
        <>
          <div className="mb-4 grid gap-3 md:grid-cols-4">
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">{t("settings.channels.runtime")}</div>
              <div className="text-sm font-medium">{channelStatus.running ? t("settings.channels.running") : t("settings.channels.stopped")}</div>
            </div>
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">{t("settings.channels.enabled")}</div>
              <div className="text-sm font-medium">{channelEnabledCount}</div>
            </div>
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">{t("settings.channels.loaded")}</div>
              <div className="text-sm font-medium">{channelLoadedCount}</div>
            </div>
            <div className="rounded-md border bg-muted/20 px-3 py-2">
              <div className="text-xs text-muted-foreground">{t("settings.channels.unavailable")}</div>
              <div className="text-sm font-medium">{channelUnavailableCount}</div>
            </div>
          </div>

          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-start font-medium">{t("settings.channels.channel")}</th>
                  <th className="px-3 py-2 text-start font-medium">{t("settings.channels.state")}</th>
                  <th className="px-3 py-2 text-start font-medium">{t("settings.channels.recovery")}</th>
                </tr>
              </thead>
              <tbody>
                {channelRows.map(([name, item]) => (
                  <tr key={name} className="border-t">
                    <td className="px-3 py-2 align-top">
                      <div className="font-medium">{item.display_name || name}</div>
                      <div className="text-xs text-muted-foreground">{name}</div>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <div className="flex flex-wrap gap-1.5">
                        <span className={`rounded-full px-2 py-0.5 text-xs ${item.enabled ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}`}>
                          {item.enabled ? t("settings.channels.enabled") : t("settings.channels.disabled")}
                        </span>
                        <span className={`rounded-full px-2 py-0.5 text-xs ${item.loaded ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}>
                          {item.loaded ? t("settings.channels.loaded") : t("settings.channels.notLoaded")}
                        </span>
                        <span className={`rounded-full px-2 py-0.5 text-xs ${item.running ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}>
                          {item.running ? t("settings.channels.running") : t("settings.channels.stopped")}
                        </span>
                      </div>
                    </td>
                    <td className="max-w-md px-3 py-2 align-top text-xs text-muted-foreground">
                      {item.install_hint || item.error || t("settings.channels.noRecovery")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="rounded-md border bg-muted/20 px-4 py-6 text-center text-sm text-muted-foreground">
          {t("settings.channels.refreshFailed")}
        </div>
      )}
    </section>
  );
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{t("settings.title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("settings.subtitle")}</p>
      </div>

      {localApiAccessSection}

      {/* QVERIS-INTEGRATION */}
      <QVerisSettings />

      {channelsSection}

      <div className="space-y-2">
        <h2 className="text-lg font-semibold tracking-tight">{t("settings.llmSettings")}</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("settings.llmSettingsDesc")}</p>
      </div>

      {/* Column ratio matches the QVeris and data-source sections so the
          card seams align down the page. */}
      <form onSubmit={submit} className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.9fr)]">
        <section className="rounded-lg border bg-card p-5 shadow-sm">
          <div className="mb-5 flex items-center gap-2">
            <Server className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{t("settings.connection")}</h2>
          </div>

          <div className="grid gap-4">
            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.provider")}</span>
              <select
                value={form.provider}
                onChange={(event) => onProviderChange(event.target.value)}
                className={fieldClass}
              >
                {providers.map((provider) => (
                  <option key={provider.name} value={provider.name}>{provider.label}</option>
                ))}
              </select>
              <span className={hintClass}>
                {t("settings.providerChangeHint", {
                  defaultValue: "Changing providers updates the recommended model and endpoint.",
                })}
              </span>
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.model")}</span>
              <div className="flex gap-2">
                <ModelPicker
                  value={form.model_name}
                  options={modelOptions}
                  onChange={(modelName) => setForm({ ...form, model_name: modelName })}
                  ariaLabel={t("settings.model")}
                  optionsAriaLabel={t("settings.modelOptions")}
                />
                <button
                  type="button"
                  onClick={() => void refreshModels()}
                  disabled={modelsLoading}
                  className="inline-flex shrink-0 items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
                  title={t("settings.loadModels")}
                >
                  {modelsLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  <span className="hidden sm:inline">{t("settings.loadModels")}</span>
                </button>
                <button
                  type="button"
                  onClick={() => applyProviderDefaults()}
                  className="inline-flex shrink-0 items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground"
                  title={t("settings.useProviderDefaults")}
                >
                  <RotateCcw className="h-4 w-4" />
                  <span className="hidden sm:inline">{t("settings.useProviderDefaults")}</span>
                </button>
              </div>
              <span className={hintClass}>
                {modelListHint || t("settings.modelPickerHint")}
              </span>
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.baseUrl")}</span>
              <input
                value={form.base_url}
                onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                className={fieldClass}
                placeholder={selectedProvider?.default_base_url}
                list={selectedProvider?.base_url_options?.length ? "llm-base-url-options" : undefined}
                disabled={selectedProvider?.auth_type === "oauth"}
              />
              {selectedProvider?.base_url_options?.length ? (
                <datalist id="llm-base-url-options">
                  {selectedProvider.base_url_options.map((baseUrl) => (
                    <option key={baseUrl} value={baseUrl} />
                  ))}
                </datalist>
              ) : null}
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>
                {selectedProvider?.auth_type === "oauth"
                  ? t("settings.oauth", { defaultValue: "OAuth" })
                  : t("settings.apiKey", { defaultValue: "API key" })}
              </span>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute start-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  className={`${fieldClass} ps-9`}
                  placeholder={keyStatus}
                  autoComplete="current-password"
                  disabled={apiKeyDisabled}
                />
              </div>
              <div className="flex items-start justify-between gap-3">
                <span className={hintClass}>{keyStatus}</span>
                {selectedProvider?.api_key_required ? (
                  <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={clearApiKey}
                      onChange={(event) => {
                        setClearApiKey(event.target.checked);
                        if (event.target.checked) setApiKey("");
                      }}
                      className="h-3.5 w-3.5 accent-primary"
                    />
                    {t("settings.clearApiKey")}
                  </label>
                ) : null}
              </div>
            </label>
          </div>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm">
          <div className="mb-5 flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{t("settings.generation")}</h2>
          </div>

          <div className="grid gap-4">
            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.temperature")}</span>
              <input
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={form.temperature}
                onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })}
                className={fieldClass}
              />
              <span className={hintClass}>{t("settings.temperatureDesc")}</span>
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.timeoutSeconds")}</span>
              <input
                type="number"
                min={1}
                max={3600}
                step={1}
                value={form.timeout_seconds}
                onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) })}
                className={fieldClass}
              />
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.maxRetries")}</span>
              <input
                type="number"
                min={0}
                max={20}
                step={1}
                value={form.max_retries}
                onChange={(event) => setForm({ ...form, max_retries: Number(event.target.value) })}
                className={fieldClass}
              />
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.reasoningEffort")}</span>
              <select
                value={form.reasoning_effort}
                onChange={(event) => setForm({ ...form, reasoning_effort: event.target.value })}
                className={fieldClass}
              >
                <option value="">{t("settings.providerDefault")}</option>
                <option value="none">{t("settings.reasoningEffortNone")}</option>
                <option value="low">{t("settings.reasoningEffortLow")}</option>
                <option value="medium">{t("settings.reasoningEffortMedium")}</option>
                <option value="high">{t("settings.reasoningEffortHigh")}</option>
                <option value="max">{t("settings.reasoningEffortMax")}</option>
              </select>
              <span className={hintClass}>{t("settings.reasoningEffortDesc")}</span>
            </label>

            <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{t("settings.saved")}: </span>
              <span className="break-all font-mono">{settings.env_path}</span>
            </div>

            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {saving ? t("settings.saving") : t("settings.save")}
            </button>
          </div>
        </section>
      </form>

      <form onSubmit={submitDataSources} className="rounded-lg border bg-card p-5 shadow-sm">
        <div className="mb-5 space-y-1">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{t("settings.dataSourceSettings")}</h2>
          </div>
          <p className="text-sm text-muted-foreground">{t("settings.dataSourceSettingsDesc")}</p>
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.9fr)]">
          <div className="grid gap-4">
            <label className="grid gap-2">
              <span className={labelClass}>{t("settings.tushareToken")}</span>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute start-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="password"
                  value={tushareToken}
                  onChange={(event) => setTushareToken(event.target.value)}
                  className={`${fieldClass} ps-9`}
                  placeholder={tushareStatus}
                  autoComplete="current-password"
                  disabled={clearTushareToken}
                />
              </div>
              <div className="flex items-start justify-between gap-3">
                <span className={hintClass}>
                  {t("settings.tushareTokenDesc", {
                    defaultValue: "Used for China A-share, futures, fund, and macro data. If unset, the project falls back to AKShare where available.",
                  })}
                </span>
                <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={clearTushareToken}
                    onChange={(event) => {
                      setClearTushareToken(event.target.checked);
                      if (event.target.checked) setTushareToken("");
                    }}
                    className="h-3.5 w-3.5 accent-primary"
                  />
                  {t("settings.clearTushareToken")}
                </label>
              </div>
            </label>

            <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{t("settings.saved")}: </span>
              <span className="break-all font-mono">{dataSettings.env_path}</span>
            </div>

            <button
              type="submit"
              disabled={dataSaving}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {dataSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {dataSaving ? t("settings.saving") : t("settings.saveDataSourceSettings")}
            </button>
          </div>

          <div className="rounded-md border bg-muted/20 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <span className="text-sm font-medium">{t("settings.baostock")}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs ${dataSettings.baostock_supported ? "bg-success/10 text-success" : "bg-warning/10 text-warning"}`}>
                {dataSettings.baostock_supported ? t("settings.loaderAvailable") : t("settings.noProjectLoader")}
              </span>
            </div>
            <div className="space-y-2 text-sm text-muted-foreground">
              <p>{dataSettings.baostock_message}</p>
              <p>
                {dataSettings.baostock_installed
                  ? t("settings.pythonPackageInstalled")
                  : t("settings.pythonPackageNotInstalled")}
              </p>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
}
