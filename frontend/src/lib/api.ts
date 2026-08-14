import i18n from "@/i18n";
import { authHeaders, withAuthTicket } from "@/lib/apiAuth";

const BASE = "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const AUTH_REQUIRED_MESSAGE_KEY = "agent.authRequired";

function getAuthRequiredMessage(): string {
  return i18n.t(AUTH_REQUIRED_MESSAGE_KEY as never);
}

// Keep the existing string export compatible with consumers while updating its
// live ES-module binding whenever the active locale changes.
export let AUTH_REQUIRED_MESSAGE = getAuthRequiredMessage();
i18n.on("languageChanged", () => {
  AUTH_REQUIRED_MESSAGE = getAuthRequiredMessage();
});

export function isAuthRequiredError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export interface CorrelationResponse {
  labels: string[];
  matrix: number[][];
}

export interface RegimeEpisode {
  start: string;
  end: string | null;
}

export interface CorrelationRegimeResponse {
  labels: string[];
  dates: string[];
  density: (number | null)[];
  smoothed: (number | null)[];
  fused: number[];
  episodes: RegimeEpisode[];
  params: {
    days: number;
    corr_window: number;
    edge_threshold: number;
    smooth_window: number;
    enter_threshold: number;
    exit_threshold: number;
  };
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body.detail || body.message || detail;
  } catch { /* ignore */ }
  if (res.status === 401 || res.status === 403) {
    detail = getAuthRequiredMessage();
  }
  return new ApiError(detail, res.status);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { headers, ...rest } = options ?? {};
  const mergedHeaders: Record<string, string> = { "Content-Type": "application/json", ...authHeaders() };
  if (headers) {
    new Headers(headers).forEach((value, key) => {
      mergedHeaders[key] = value;
    });
  }
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: mergedHeaders,
  });
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  const text = await res.text();
  if (!text) return {} as T;

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const preview = text.slice(0, 80).replace(/\s+/g, " ");
    throw new ApiError(
      `Expected JSON from ${path}, got ${contentType || "unknown content type"}: ${preview}`,
      res.status,
    );
  }

  return JSON.parse(text) as T;
}

export interface UploadResult {
  status: string;
  file_path: string;
  filename: string;
}

async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/upload`, { method: "POST", headers: authHeaders(), body: form });
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  return res.json();
}

function appendQueryParam(url: string, key: string, value: string): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
}

export const api = {
  uploadFile,
  getCorrelation: (codes: string, days: number, method: "pearson" | "spearman") =>
    request<CorrelationResponse>(
      `/correlation?codes=${encodeURIComponent(codes)}&days=${encodeURIComponent(String(days))}&method=${encodeURIComponent(method)}`,
    ),
  getCorrelationRegime: (codes: string, days: number) =>
    request<CorrelationRegimeResponse>(
      `/correlation/regime?codes=${encodeURIComponent(codes)}&days=${encodeURIComponent(String(days))}`,
    ),
  listRuns: (limit?: number) => request<RunListItem[]>(`/runs${limit ? `?limit=${encodeURIComponent(String(limit))}` : ""}`),
  getRun: (id: string, params: RunDetailParams = {}) => {
    const q = new URLSearchParams();
    if (params.chart_payload) q.set("chart_payload", params.chart_payload);
    if (params.chart_symbol) q.set("chart_symbol", params.chart_symbol);
    const qs = q.toString();
    return request<RunData>(`/runs/${id}${qs ? `?${qs}` : ""}`);
  },
  getRunCode: (id: string) => request<Record<string, string>>(`/runs/${id}/code`),
  getRunPine: (id: string) => request<PineScriptResult>(`/runs/${id}/pine`),
  listSessions: () => request<SessionItem[]>("/sessions"),
  createSession: (title?: string) => request<SessionItem>("/sessions", { method: "POST", body: JSON.stringify({ title: title || "" }) }),
  deleteSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "DELETE" }),
  renameSession: (sid: string, title: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  // Codex-style LLM summary title from the first exchange; backend refuses to
  // overwrite a manual rename, so this is safe to fire-and-forget.
  autoTitleSession: (sid: string) => request<{ status: string; title: string }>(`/sessions/${sid}/title/auto`, { method: "POST" }),
  // Scheduled research: cadence + timezone are stored as authored (local
  // wall-clock cron + IANA key), so list rows render without any UTC math.
  listScheduledRuns: (signal?: AbortSignal) => request<ScheduledRun[]>("/scheduled-runs", { signal }),
  createScheduledRun: (body: CreateScheduledRunRequest) =>
    request<ScheduledRun>("/scheduled-runs", { method: "POST", body: JSON.stringify(body) }),
  deleteScheduledRun: (id: string) =>
    request<void>(`/scheduled-runs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  sendMessage: (sid: string, content: string) => request<{ message_id: string; attempt_id: string }>(`/sessions/${sid}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  cancelSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}/cancel`, { method: "POST" }),
  getSessionMessages: (sid: string) => request<MessageItem[]>(`/sessions/${sid}/messages`),
  createGoal: (sid: string, body: CreateGoalRequest) =>
    request<GoalSnapshot>(`/sessions/${sid}/goal`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getGoal: (sid: string) => request<GoalSnapshot>(`/sessions/${sid}/goal`),
  updateGoal: (sid: string, body: UpdateGoalRequest) =>
    request<UpdateGoalResponse>(`/sessions/${sid}/goal`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  addGoalEvidence: (sid: string, body: AddGoalEvidenceRequest) =>
    request<AddGoalEvidenceResponse>(`/sessions/${sid}/goal/evidence`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateGoalStatus: (sid: string, body: UpdateGoalStatusRequest) =>
    request<UpdateGoalStatusResponse>(`/sessions/${sid}/goal/status`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  // Returns the bare stream URL (no auth in the query string). The SSE ticket
  // is minted per connect/reconnect inside useSSE (tickets are single-use, so
  // baking one into a cached URL would break reconnection).
  sseUrl: (sid: string, options?: { replay?: "active" }) => {
    let url = `${BASE}/sessions/${sid}/events`;
    if (options?.replay) url = appendQueryParam(url, "replay", options.replay);
    return url;
  },

  // Swarm API
  listSwarmPresets: () => request<SwarmPreset[]>("/swarm/presets"),
  createSwarmRun: (preset_name: string, user_vars: Record<string, string>) =>
    request<{ id: string; status: string }>("/swarm/runs", {
      method: "POST",
      body: JSON.stringify({ preset_name, user_vars }),
    }),
  listSwarmRuns: () => request<SwarmRunSummary[]>("/swarm/runs"),
  getSwarmRun: (id: string) => request<Record<string, unknown>>(`/swarm/runs/${id}`),
  swarmSseUrl: (id: string) => withAuthTicket(`${BASE}/swarm/runs/${id}/events`),
  cancelSwarmRun: (id: string) =>
    request<{ status: string }>(`/swarm/runs/${id}/cancel`, { method: "POST" }),
  retrySwarmRun: (id: string) =>
    request<{ id: string; status: string; preset_name: string }>(`/swarm/runs/${id}/retry`, { method: "POST" }),
  getLLMSettings: () => request<LLMSettings>("/settings/llm"),
  updateLLMSettings: (settings: UpdateLLMSettingsRequest) =>
    request<LLMSettings>("/settings/llm", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  listLLMModels: (settings: ListLLMModelsRequest) =>
    request<LLMModelsResponse>("/settings/llm/models", {
      method: "POST",
      body: JSON.stringify(settings),
    }),
  getDataSourceSettings: () => request<DataSourceSettings>("/settings/data-sources"),
  updateDataSourceSettings: (settings: UpdateDataSourceSettingsRequest) =>
    request<DataSourceSettings>("/settings/data-sources", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getChannelStatus: () => request<ChannelRuntimeStatus>("/channels/status"),
  startChannels: () => request<ChannelRuntimeActionResponse>("/channels/start", { method: "POST" }),
  stopChannels: () => request<ChannelRuntimeActionResponse>("/channels/stop", { method: "POST" }),
  runChannelPairingCommand: (body: ChannelPairingCommandRequest) =>
    request<ChannelPairingCommandResponse>("/channels/pairing/command", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // Alpha Zoo API
  listAlphas: (params: AlphaListParams = {}) => {
    const q = new URLSearchParams();
    if (params.zoo) q.set("zoo", params.zoo);
    if (params.theme) q.set("theme", params.theme);
    if (params.universe) q.set("universe", params.universe);
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<AlphaListResponse>(`/alpha/list${qs ? `?${qs}` : ""}`);
  },
  getAlpha: (alphaId: string) =>
    request<AlphaDetailResponse>(`/alpha/${encodeURIComponent(alphaId)}`),
  createAlphaBench: (body: AlphaBenchRequest) =>
    request<{ status: string; job_id: string }>("/alpha/bench", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaBenchStreamUrl: (jobId: string) =>
    withAuthTicket(`${BASE}/alpha/bench/${encodeURIComponent(jobId)}/stream`),
  createAlphaCompare: (body: AlphaCompareRequest) =>
    request<{ status: string; job_id: string }>("/alpha/compare", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaCompareStreamUrl: (jobId: string) =>
    withAuthTicket(`${BASE}/alpha/compare/${encodeURIComponent(jobId)}/stream`),

  // 恒值投资 structured research workspace
  getDashboard: () => request<DashboardToday>("/dashboard/today"),
  getTdxStatus: () => request<TdxStatus>("/tdx/status"),
  getTdxMarketOverview: () => request<TdxMarketOverview>("/tdx/market/overview"),
  getTdxMarketRanks: (options?: { category?: string; query?: string; sector?: string; sort?: string; direction?: string; limit?: number; offset?: number }) =>
    request<TdxRankResult>(`/tdx/market/ranks?${queryString(options)}`),
  getTdxSectors: (options?: { category?: string; query?: string; limit?: number; offset?: number }) =>
    request<TdxSectorResult>(`/tdx/sectors?${queryString(options)}`),
  getTdxSector: (code: string) => request<TdxSectorDetail>(`/tdx/sectors/${encodeURIComponent(code)}`),
  screenTdxSecurities: (options?: TdxScreenerFilters) =>
    request<TdxScreenerResult>(`/tdx/screener?${queryString(options)}`),
  getTdxFunds: (options?: { category?: string; query?: string; limit?: number; offset?: number }) =>
    request<TdxFundResult>(`/tdx/funds?${queryString(options)}`),
  searchTdxSecurities: (query: string, limit = 20) =>
    request<{ items: TdxSecuritySearchItem[] }>(`/tdx/securities/search?query=${encodeURIComponent(query)}&limit=${limit}`),
  getTdxSecurityOverview: (symbol: string) =>
    request<TdxSecurityOverview>(`/tdx/securities/${encodeURIComponent(symbol)}/overview`),
  getTdxFinancialHistory: (symbol: string, options?: { as_of?: string; period_type?: string }) =>
    request<TdxFinancialHistory>(`/tdx/financial-history/${encodeURIComponent(symbol)}?${queryString(options)}`),
  startTdxFormulaScan: (body: TdxFormulaScanRequest) =>
    request<TdxFormulaScan>("/tdx/formula-scans", { method: "POST", body: JSON.stringify(body) }),
  getTdxFormulaScan: (id: string) => request<TdxFormulaScan>(`/tdx/formula-scans/${encodeURIComponent(id)}`),
  startTdxUpdate: (module = "all") =>
    request<TdxJob>("/tdx/update", { method: "POST", body: JSON.stringify({ module }) }),
  getTdxJob: (id: string) => request<TdxJob>(`/tdx/jobs/${encodeURIComponent(id)}`),
  getTdxData: (dataset: string, options?: { category?: string; query?: string; limit?: number; offset?: number }) => {
    const params = new URLSearchParams();
    if (options?.category) params.set("category", options.category);
    if (options?.query) params.set("query", options.query);
    if (options?.limit) params.set("limit", String(options.limit));
    if (options?.offset) params.set("offset", String(options.offset));
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<TdxDataset>(`/tdx/data/${encodeURIComponent(dataset)}${suffix}`);
  },
  refreshTdxSecurity: (symbol: string) =>
    request<Record<string, unknown>>("/tdx/securities/refresh", { method: "POST", body: JSON.stringify({ symbol }) }),
  getTdxSecurity: (symbol: string) => request<Record<string, unknown>>(`/tdx/securities/${encodeURIComponent(symbol)}`),
  getTdxKline: (body: { symbol: string; period?: string; count?: number; dividend_type?: string }) =>
    request<Record<string, unknown>>("/tdx/klines", { method: "POST", body: JSON.stringify(body) }),
  refreshDashboard: (body: { module?: string; market?: MarketCode }) =>
    request<ResearchRun>("/dashboard/refresh", { method: "POST", body: JSON.stringify(body) }),
  getResearchRun: (id: string) => request<ResearchRun>(`/research-runs/${encodeURIComponent(id)}`),
  getMacroBrief: (market: MarketCode) => request<MacroBrief>(`/macro/briefs/latest?market=${market}`),
  getSectorRankings: (market: MarketCode) => request<{ market: MarketCode; items: SectorScore[] }>(`/sectors/rankings?market=${market}`),
  screenSecurities: (market: MarketCode, query = "") => request<{ market: MarketCode; items: SecurityCandidate[] }>(`/securities/screener?market=${market}&query=${encodeURIComponent(query)}`),
  getCompanyDossier: (market: MarketCode, symbol: string) => request<CompanyDossier>(`/securities/${market}/${encodeURIComponent(symbol)}/dossier`),
  researchCompany: (market: MarketCode, symbol: string) => request<CompanyResearchResult>(`/securities/${market}/${encodeURIComponent(symbol)}/research`, { method: "POST", body: JSON.stringify({ create_report: true }) }),
  listResearchReports: (reportType?: string) => request<ResearchReport[]>(`/reports${reportType ? `?report_type=${encodeURIComponent(reportType)}` : ""}`),
  getResearchReport: (id: string) => request<ResearchReport>(`/reports/${encodeURIComponent(id)}`),
  listCommittees: () => request<Committee[]>("/committees"),
  getCommittee: (id: string) => request<Committee>(`/committees/${encodeURIComponent(id)}`),
  createCommittee: (body: { market: MarketCode; symbol: string; company_name?: string }) => request<Committee>("/committees", { method: "POST", body: JSON.stringify(body) }),
  cancelCommittee: (id: string) => request<Committee>(`/committees/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  committeeSseUrl: (id: string) => withAuthTicket(`${BASE}/committees/${encodeURIComponent(id)}/events`),
  listTradePlans: (status?: TradePlanStatus) => request<TradePlan[]>(`/trade-plans${status ? `?status=${status}` : ""}`),
  createTradePlan: (body: Partial<TradePlan>) => request<TradePlan>("/trade-plans", { method: "POST", body: JSON.stringify(body) }),
  updateTradePlan: (id: string, body: Partial<TradePlan>) => request<TradePlan>(`/trade-plans/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  listPortfolios: () => request<Portfolio[]>("/portfolios"),
  getPortfolio: (id: string) => request<Portfolio>(`/portfolios/${encodeURIComponent(id)}`),
  createPortfolio: (body: CreatePortfolioRequest) => request<Portfolio>("/portfolios", { method: "POST", body: JSON.stringify(body) }),
  updatePortfolio: (id: string, body: Partial<Portfolio>) => request<Portfolio>(`/portfolios/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deletePortfolio: (id: string) => request<void>(`/portfolios/${encodeURIComponent(id)}`, { method: "DELETE" }),
  addPortfolioTransaction: (id: string, body: PortfolioTransactionRequest) => request<PortfolioTransaction>(`/portfolios/${encodeURIComponent(id)}/transactions`, { method: "POST", body: JSON.stringify(body) }),
  getPortfolioPositions: (id: string) => request<PortfolioPosition[]>(`/portfolios/${encodeURIComponent(id)}/positions`),
  getPortfolioAnalytics: (id: string) => request<PortfolioAnalytics>(`/portfolios/${encodeURIComponent(id)}/analytics`),
  importPortfolioCsv: async (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/portfolios/${encodeURIComponent(id)}/import`, { method: "POST", headers: authHeaders(), body: form });
    if (!res.ok) throw await errorFromResponse(res);
    return res.json() as Promise<{ imported: number; errors: Array<{ line: number; error: string }> }>;
  },

  // Deterministic value/emotion strategy engines
  createStrategyRun: (body: CreateStrategyRunRequest) =>
    request<EngineRun>("/strategy-runs", { method: "POST", body: JSON.stringify(body) }),
  getStrategyRun: (id: string) => request<EngineRun>(`/strategy-runs/${encodeURIComponent(id)}`),
  getStrategyFormulas: () => request<StrategyFormulaResponse>("/strategy/formulas"),
  getStrategyDataStatus: () => request<StrategyDataStatus>("/strategy/data/status"),
  getValueDashboard: (market: "CN" | "HK") => request<StrategyDashboard>(`/strategy/value/dashboard?market=${market}`),
  getValueDataStatus: () => request<ValueDataStatus>("/strategy/value/data/status"),
  startValueRefresh: (modules: ValueRefreshModule[] = ["all"], asOf?: string) =>
    request<ValueRefreshJob>("/strategy/value/refresh", { method: "POST", body: JSON.stringify({ modules, as_of: asOf }) }),
  getValueRefreshJob: (id: string) => request<ValueRefreshJob>(`/strategy/value/refresh/${encodeURIComponent(id)}`),
  getValueMacro: (asOf?: string) => request<ValueMacroSnapshot>(`/strategy/value/macro${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`),
  getValuePolicies: (options?: { status?: string; limit?: number }) =>
    request<{ items: ValuePolicyEvent[] }>(`/strategy/value/policies?${queryString(options)}`),
  getValueSectorScores: (options?: { as_of?: string; status?: string; query?: string }) =>
    request<ValueSectorScoreList>(`/strategy/value/sectors?market=CN&${queryString(options)}`),
  getValueLeaderScores: (sectorCode?: string, asOf?: string, candidateTrackLimit?: number) =>
    request<ValueLeaderScoreList>(`/strategy/value/leaders?market=CN${sectorCode ? `&sector_code=${encodeURIComponent(sectorCode)}` : ""}${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}${candidateTrackLimit ? `&candidate_track_limit=${candidateTrackLimit}` : ""}`),
  getValueSectors: (market: "CN" | "HK") => request<StrategyScoreList>(`/strategy/value/sectors?market=${market}`),
  getValueLeaders: (market: "CN" | "HK") => request<StrategyScoreList>(`/strategy/value/leaders?market=${market}`),
  getValueSignals: (market: "CN" | "HK") => request<StrategySignal[]>(`/strategy/value/signals?market=${market}`),
  getValueProfiles: () => request<{ items: CalculationProfile[] }>("/strategy/value/profiles"),
  createValueProfile: (body: Pick<CalculationProfile, "name" | "mode" | "model_weights">) =>
    request<CalculationProfile>("/strategy/value/profiles", { method: "POST", body: JSON.stringify(body) }),
  updateValueProfile: (id: string, body: Pick<CalculationProfile, "name" | "mode" | "model_weights">) =>
    request<CalculationProfile>(`/strategy/value/profiles/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteValueProfile: (id: string) => request<{ status: string }>(`/strategy/value/profiles/${encodeURIComponent(id)}`, { method: "DELETE" }),
  getValueWorkbench: (profileId?: string) => request<ValueWorkbench>(`/strategy/value/workbench${profileId ? `?profile_id=${encodeURIComponent(profileId)}` : ""}`),
  getValueTracks: (runId: string) => request<{ run_id: string; items: ValueTrack[] }>(`/strategy/value/tracks?run_id=${encodeURIComponent(runId)}`),
  getValueTrackLeaders: (runId: string, trackId: string) => request<{ run_id: string; track_id: string; items: ValueTrackLeader[] }>(`/strategy/value/tracks/${encodeURIComponent(trackId)}/leaders?run_id=${encodeURIComponent(runId)}`),
  getValueResearchUniverses: (profileId?: string) => request<{ items: ValueResearchUniverse[] }>(`/strategy/value/research-universes${profileId ? `?profile_id=${encodeURIComponent(profileId)}` : ""}`),
  createValueResearchUniverse: (body: { run_id: string; candidate_limit: 5 | 10 | 20 | 50; leader_limit?: 5 }) =>
    request<ValueResearchUniverse & { created: boolean }>("/strategy/value/research-universes", { method: "POST", body: JSON.stringify(body) }),
  getValueResearchUniverse: (id: string) => request<ValueResearchUniverse>(`/strategy/value/research-universes/${encodeURIComponent(id)}`),
  getValueUniverseAnalysis: (id: string) => request<ValueUniverseAnalysis>(`/strategy/value/research-universes/${encodeURIComponent(id)}/analysis`),
  bootstrapValueResearchUniverse: (id: string, asOf?: string) => request<ValueIncrementalRun & { created: boolean }>(`/strategy/value/research-universes/${encodeURIComponent(id)}/bootstrap`, { method: "POST", body: JSON.stringify({ as_of: asOf }) }),
  activateValueResearchUniverse: (id: string) => request<ValueResearchUniverse>(`/strategy/value/research-universes/${encodeURIComponent(id)}/activate`, { method: "POST" }),
  getValueCompanyArchive: (symbol: string) => request<ValueCompanyArchive>(`/strategy/value/company-archives/${encodeURIComponent(symbol)}`),
  startValueIncrementalRun: (universeId: string, asOf?: string) => request<ValueIncrementalRun & { created: boolean }>("/strategy/value/incremental-runs", { method: "POST", body: JSON.stringify({ universe_id: universeId, as_of: asOf }) }),
  getValueIncrementalRun: (id: string) => request<ValueIncrementalRun>(`/strategy/value/incremental-runs/${encodeURIComponent(id)}`),
  cancelValueIncrementalRun: (id: string) => request<ValueIncrementalRun>(`/strategy/value/incremental-runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  retryValueIncrementalRun: (id: string) => request<ValueIncrementalRun>(`/strategy/value/incremental-runs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  getValueMonitoringSignals: (options?: { status?: string; symbol?: string; limit?: number }) => request<{ items: ValueSignalEvaluation[] }>(`/strategy/value/signals?scope=monitoring&${queryString(options)}`),
  createValueResearchBatch: (body: { run_id: string; track_id: string; symbols: string[]; concurrency?: number }) =>
    request<CompanyResearchBatch>("/strategy/value/research-batches", { method: "POST", body: JSON.stringify(body) }),
  getValueResearchBatches: () => request<{ items: CompanyResearchBatch[] }>("/strategy/value/research-batches"),
  getValueResearchBatch: (id: string) => request<CompanyResearchBatch>(`/strategy/value/research-batches/${encodeURIComponent(id)}`),
  cancelValueResearchBatch: (id: string) => request<CompanyResearchBatch>(`/strategy/value/research-batches/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  retryValueResearchBatch: (id: string) => request<CompanyResearchBatch>(`/strategy/value/research-batches/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  getValueMonitors: () => request<{ items: ValueEntryMonitor[] }>("/strategy/value/monitors"),
  createValueMonitor: (body: { research_job_id?: string; universe_id?: string; symbol?: string; position_state?: "watching" | "holding"; risk_preset?: "balanced"; conditions: Record<string, unknown>; channels: string[] }) =>
    request<ValueEntryMonitor>("/strategy/value/monitors", { method: "POST", body: JSON.stringify(body) }),
  updateValueMonitor: (id: string, body: Partial<Pick<ValueEntryMonitor, "status" | "conditions" | "channels" | "position_state" | "risk_preset" | "thesis_invalidated">>) =>
    request<ValueEntryMonitor>(`/strategy/value/monitors/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  evaluateValueMonitors: () => request<{ items: ValueMonitorEvent[] }>("/strategy/value/monitors/evaluate", { method: "POST" }),
  getValueMonitorEvents: () => request<{ items: ValueMonitorEvent[] }>("/strategy/value/monitor-events"),
  acknowledgeValueMonitorEvent: (id: string, status: "acknowledged" | "closed", note = "") => request<ValueMonitorEvent>(`/strategy/value/monitor-events/${encodeURIComponent(id)}/acknowledge`, { method: "POST", body: JSON.stringify({ status, note }) }),
  getValueAutomation: () => request<ValueResearchAutomation>("/strategy/value/automation"),
  updateValueAutomation: (enabled: boolean) => request<ValueResearchAutomation>("/strategy/value/automation", { method: "PATCH", body: JSON.stringify({ enabled }) }),
  getEmotionDashboard: (market: "CN" | "HK") => request<StrategyDashboard>(`/strategy/emotion/dashboard?market=${market}`),
  getEmotionSectors: (market: "CN" | "HK") => request<StrategyScoreList>(`/strategy/emotion/sectors?market=${market}`),
  getEmotionCandidates: (market: "CN" | "HK", horizon: "short" | "swing") =>
    request<StrategyScoreList>(`/strategy/emotion/candidates?market=${market}&horizon=${horizon}`),
  getEmotionSignals: (market: "CN" | "HK", horizon?: "short" | "swing") =>
    request<StrategySignal[]>(`/strategy/emotion/signals?market=${market}${horizon ? `&horizon=${horizon}` : ""}`),
  getDecisionChain: (runId: string) => request<DecisionChain>(`/decision-chains/${encodeURIComponent(runId)}`),
  getStrategySignal: (signalId: string) => request<StrategySignal>(`/signals/${encodeURIComponent(signalId)}`),
  createStrategyCommittee: (body: StrategyCommitteeRequest) =>
    request<Committee>("/committees", { method: "POST", body: JSON.stringify(body) }),
  publishCommitteeDecision: (committeeId: string, body: CommitteeDecisionRequest) =>
    request<StructuredCommitteeDecision>(`/committees/${encodeURIComponent(committeeId)}/decision`, { method: "POST", body: JSON.stringify(body) }),
  listPaperAccounts: () => request<PaperAccount[]>("/paper/accounts"),
  getPaperAccount: (id: string) => request<PaperAccount>(`/paper/accounts/${encodeURIComponent(id)}`),
  getPaperOrders: (id: string) => request<PaperOrder[]>(`/paper/accounts/${encodeURIComponent(id)}/orders`),
  getPaperPositions: (id: string) => request<PaperPosition[]>(`/paper/accounts/${encodeURIComponent(id)}/positions`),
  getPaperNav: (id: string) => request<PaperNav>(`/paper/accounts/${encodeURIComponent(id)}/nav`),
  submitPaperOrder: (id: string, body: PaperOrderRequest) =>
    request<PaperOrder>(`/paper/accounts/${encodeURIComponent(id)}/orders`, { method: "POST", body: JSON.stringify(body) }),
};

export type MarketCode = "CN" | "HK" | "US";
export type SourceStatus = "live" | "agent" | "sample" | "stale" | string;

export interface CreateStrategyRunRequest { strategy_line: "value" | "emotion"; market: "CN" | "HK"; as_of?: string; symbols?: string[]; force_refresh?: boolean; profile_id?: string; }
export type ValueRefreshModule = "financial_history" | "market_history" | "macro" | "policy" | "scores" | "all";
export interface ValueModuleState { code: Exclude<ValueRefreshModule, "all">; label: string; status: string; progress: number; total: number; item_count: number; message: string; error: string; metadata: Record<string, unknown>; started_at?: string | null; updated_at?: string | null; last_success_at?: string | null; }
export interface ValueRefreshJob { id: string; modules: ValueRefreshModule[]; as_of: string; status: string; current_module: string; progress: number; total: number; results: Record<string, unknown>; errors: Array<{ module: string; error: string }>; created_at: string; started_at?: string | null; completed_at?: string | null; }
export interface ValueDataStatus { professional_finance: { status: string; file_count: number; first_period?: string | null; last_period?: string | null; raw_version?: string | null }; modules: ValueModuleState[]; recent_jobs: ValueRefreshJob[]; latest_score_as_of?: string | null; schedule_template?: { name: string; cron: string; timezone: string; modules: ValueRefreshModule[]; enabled: boolean }; }
export interface ValueScoreComponent { name: string; raw_value: unknown; normalized_value: number | null; weight: number; contribution: number | null; }
export interface ValueMacroSnapshot { id?: string; as_of?: string | null; formula_version?: string; regime?: string; score?: number | null; coverage?: number; axis_coverage?: number; series_coverage?: number; series_count?: number; series_total?: number; release_verified_coverage?: number; first_observed_count?: number; confidence?: "LOW" | "MEDIUM" | "HIGH"; status: string; axes: Record<string, number | null>; states?: Record<string, string>; missing_fields?: string[]; missing_series?: string[]; sources?: string[]; provenance_key?: string; }
export interface ValueSectorMacroDriver { axis: string; axis_name: string; axis_score: number; sensitivity: number; adjusted_score: number; contribution: number | null; }
export interface ValueSectorScore { sector_code: string; sector_name: string; rank: number; score: number | null; base_score: number | null; coverage: number; confidence: "LOW" | "MEDIUM" | "HIGH"; status: string; member_coverage: number; raw_features?: Record<string, number | null>; normalized_features?: Record<string, number | null>; component_scores: Record<string, number | null>; components: ValueScoreComponent[]; missing_fields: string[]; formula_version: string; matrix_version?: string; ranking_basis?: string[]; context_fields?: { macro_fit?: number | null; policy_fit?: number | null; available: string[]; missing: string[] }; data_as_of: string; sources: string[]; provenance_key: string; macro_fit?: number | null; policy_fit?: number | null; macro_rank?: number; macro_group?: string; macro_group_name?: string; macro_exposure?: Record<string, number>; macro_stance?: "beneficiary" | "neutral" | "headwind" | "unavailable"; macro_drivers?: ValueSectorMacroDriver[]; macro_matrix_explicit?: boolean; }
export interface ValueLeaderScore { sector_code: string; sector_name: string; symbol: string; name: string; rank: number; candidate_sector_rank?: number; score: number | null; base_score: number | null; coverage: number; confidence: "LOW" | "MEDIUM" | "HIGH"; status: string; raw_features?: Record<string, number | null>; normalized_features?: Record<string, number | null>; component_scores: Record<string, number | null>; components: ValueScoreComponent[]; missing_fields: string[]; growth_status?: Record<string, string>; formula_version: string; data_as_of: string; sources: string[]; provenance_key: string; }
export interface ValueSectorScoreList { market: "CN"; as_of?: string | null; items: ValueSectorScore[]; total: number; formula_version?: string; run?: EngineRun | null; }
export interface ValueLeaderScoreList { market: "CN"; as_of?: string | null; sector_code: string; items: ValueLeaderScore[]; total: number; formula_version?: string; pool_rule?: { per_track_limit: number; candidate_track_limit: number | null; scored_only: boolean; deduplicated_by_symbol: boolean } | null; run?: EngineRun | null; }
export interface ValuePolicyEvent { id: string; document_number: string; title: string; normalized_url: string; source: string; published_at?: string | null; fetched_at: string; status: string; classifications: Array<{ industry_code: string; industry_name: string; direction: number; strength: number; sensitivity: number; horizon_days: number; evidence: string; confidence: number; status: string }>; }
export interface TdxFinancialHistory { symbol: string; as_of?: string | null; period_type?: string | null; total: number; items: Array<Record<string, number | string | null>>; package: Record<string, unknown>; }
export interface EngineRun { id: string; strategy_line: "value" | "emotion"; market: "CN" | "HK"; as_of: string; formula_version: string; profile_id?: string | null; profile_version?: number | null; status: string; source_status: string; message: string; started_at: string; completed_at?: string | null; created?: boolean; }
export interface CalculationProfile { id: string; name: string; mode: "single" | "composite"; model_weights: Record<string, number>; version: number; is_default: boolean; is_builtin: boolean; created_at: string; updated_at: string; }
export interface ValueTrack { id: string; engine_run_id: string; profile_id: string; track_id: string; track_name: string; category: string; base_score: number | null; coverage: number; rank: number; component_scores: Record<string, number | null>; quality_flags: string[]; source_status: string; data_as_of: string; }
export interface ValueTrackLeader { id: string; engine_run_id: string; track_id: string; symbol: string; name: string; leader_type: string; base_score: number | null; coverage: number; rank: number; component_scores: Record<string, number | null>; quality_flags: string[]; research_status: string; }
export interface ValueUniverseMember { id: string; universe_id: string; track_id: string; track_name: string; track_rank: number; symbol: string; name: string; leader_rank: number; leader_type: string; leader_score: number | null; leader_coverage: number; inclusion_reason: string; created_at: string; }
export interface ValueUniverseCompany { symbol: string; name: string; memberships: ValueUniverseMember[]; }
export interface ValueIncrementalJob { id: string; run_id: string; symbol: string; name: string; primary_track_id: string; status: string; stage: string; attempts: number; message: string; snapshot_id?: string | null; created_at: string; updated_at: string; }
export interface ValueIncrementalRun { id: string; universe_id: string; run_kind: "bootstrap" | "incremental"; trigger_kind: string; as_of: string; status: string; total: number; completed: number; failed: number; coverage: number; cancel_requested: boolean; message: string; created_at: string; started_at?: string | null; completed_at?: string | null; jobs: ValueIncrementalJob[]; }
export interface ValueResearchUniverse { id: string; engine_run_id: string; profile_id: string; candidate_limit: 5 | 10 | 20 | 50; leader_limit: 5; status: "draft" | "bootstrapping" | "partial" | "ready" | "active" | "archived"; data_as_of: string; formula_version: string; track_count: number; membership_count: number; company_count: number; created_at: string; activated_at?: string | null; archived_at?: string | null; members: ValueUniverseMember[]; companies: ValueUniverseCompany[]; latest_operation?: ValueIncrementalRun | null; }
export interface ValueResearchSnapshot { id: string; universe_id: string; symbol: string; version: number; data_as_of: string; status: string; completeness: number; source_hash: string; payload: Record<string, unknown>; diff: Record<string, unknown>; missing_fields: string[]; sources: string[]; evidence_ids: string[]; dossier_id?: string | null; report_id?: string | null; created_at: string; }
export interface ValueResearchEvidence { id: string; symbol: string; evidence_type: string; source: string; source_id: string; data_as_of: string; published_at?: string | null; fetched_at: string; content_hash: string; payload: Record<string, unknown>; status: string; }
export interface ValueCompanyAnalysis { symbol: string; name: string; memberships: ValueUniverseMember[]; current_state: string; research_state: string; signal_state: string; model_state: "not_configured" | "pending" | "completed" | "failed"; conclusion: string; next_action: string; data_as_of?: string | null; snapshot_version?: number | null; completeness: number; missing_fields: string[]; metrics: { price?: number | null; pe_ttm?: number | null; pb_mrq?: number | null; dividend_yield?: number | null; revenue_yoy?: number | null; net_profit_yoy?: number | null; roe?: number | null }; supporting_facts: string[]; risk_facts: string[]; changes: string[]; monitor_id?: string | null; position_state?: "watching" | "holding" | null; }
export interface ValueUniverseAnalysis { universe_id: string; universe_status: string; data_as_of: string; total: number; state_counts: Record<string, number>; monitored: number; model_state: ValueCompanyAnalysis["model_state"]; items: ValueCompanyAnalysis[]; }
export interface ValueCompanyArchive { symbol: string; memberships: Array<ValueUniverseMember & { profile_id: string; engine_run_id: string; universe_as_of: string; universe_status: string }>; snapshots: ValueResearchSnapshot[]; evidence: ValueResearchEvidence[]; monitors: ValueEntryMonitor[]; events: ValueMonitorEvent[]; analysis?: ValueCompanyAnalysis | null; }
export interface ValueSignalEvaluation { id: string; monitor_id: string; snapshot_id?: string | null; symbol: string; name: string; position_state: "watching" | "holding"; as_of: string; signal_state: "watching" | "entry_candidate" | "holding_review" | "exit_candidate" | "thesis_invalidated" | "data_insufficient" | "stale"; rule_version: string; input_hash: string; rules: Record<string, unknown>; inputs: Record<string, unknown>; reasons: string[]; missing_fields: string[]; created_at: string; }
export interface ValueResearchAutomation { id: "default"; enabled: boolean; timezone: "Asia/Shanghai"; run_time: "16:45"; max_retries: number; retry_minutes: number; next_run_at?: string | null; last_run_id?: string | null; last_status?: string | null; last_error: string; updated_at: string; }
export interface CompanyResearchJob { id: string; batch_id: string; symbol: string; name: string; status: "queued" | "running" | "partial" | "completed" | "failed"; stage: string; message: string; dossier_id?: string | null; report_id?: string | null; valuation_status: string; valuation: Record<string, unknown>; attempts: number; created_at?: string; updated_at?: string; }
export interface CompanyResearchBatch { id: string; engine_run_id: string; profile_id: string; track_id: string; status: string; total: number; completed: number; failed: number; cancel_requested: boolean; concurrency: number; created_at: string; completed_at?: string | null; jobs: CompanyResearchJob[]; created?: boolean; }
export interface ValueEntryMonitor { id: string; symbol: string; name: string; engine_run_id: string; track_id: string; research_job_id: string; universe_id?: string | null; status: "active" | "paused" | "closed"; position_state: "watching" | "holding"; signal_state: ValueSignalEvaluation["signal_state"]; risk_preset: "balanced"; thesis_invalidated: boolean; conditions: Record<string, unknown>; channels: string[]; confirmed_at: string; last_checked_at?: string | null; }
export interface NotificationDelivery { id: string; event_id: string; channel: "in_app" | "feishu" | "weixin"; status: string; error: string; attempted_at: string; }
export interface ValueMonitorEvent { id: string; monitor_id: string; event_type: string; severity: string; title: string; message: string; payload: Record<string, unknown>; triggered_at: string; evaluation_id?: string | null; status: "open" | "acknowledged" | "closed"; acknowledgement_note?: string; acknowledged_at?: string | null; resolved_at?: string | null; deliveries: NotificationDelivery[]; }
export interface ValueWorkbench { profile: CalculationProfile; latest_run?: EngineRun | null; macro?: ValueMacroSnapshot | null; tracks: ValueTrack[]; sector_scores: ValueSectorScore[]; research_batches: CompanyResearchBatch[]; monitor_summary: { active: number; events: number }; }
export interface StrategyFormula { id: string; strategy_line: "value" | "emotion"; name: string; version: string; weights: Record<string, number>; engine_path: string; universe: string; minimum_coverage: number | null; }
export interface StrategyFormulaResponse { items: StrategyFormula[]; strategy_store_ids: string[]; }
export interface StrategyProviderStatus { market: "CN" | "HK"; status: string; provider?: string | null; fallback_chain: string[]; error?: string; }
export interface StrategyDataCatalogItem { id: string; market: string; dataset: string; partition_path: string; provider: string; data_as_of: string; available_at: string; row_count: number; coverage: number; status: string; metadata: Record<string, unknown>; }
export interface StrategyDataStatus { providers: StrategyProviderStatus[]; partitions: number; catalog: StrategyDataCatalogItem[]; }
export interface EngineScore { id: string; engine_run_id: string; engine: string; formula_version: string; strategy_line: "value" | "emotion"; market: "CN" | "HK"; subject_type: string; subject_id: string; data_as_of: string; available_at: string; raw_features: Record<string, number | null>; normalized_features: Record<string, number | null>; component_scores: Record<string, number | null>; base_score: number | null; coverage: number; status: string; quality_flags: string[]; evidence_ids: string[]; }
export interface EngineRegime { id: string; strategy_line: "value" | "emotion"; market: "CN" | "HK"; regime: string; previous_regime?: string | null; score?: number | null; confidence: number; coverage: number; triggers: string[]; data_as_of: string; formula_version: string; }
export interface StrategySignal { id: string; engine_run_id: string; strategy_line: "value" | "emotion"; horizon: "long" | "short" | "swing"; market: "CN" | "HK"; symbol: string; data_as_of: string; valid_from: string; valid_until: string; direction: string; base_score: number; entry_low?: number | null; entry_high?: number | null; stop_price?: number | null; target_low?: number | null; target_high?: number | null; position_cap: number; coverage: number; formula_versions: string[]; evidence_ids: string[]; status: string; invalidation_rules: string[]; }
export interface StrategyDashboard { strategy_line: "value" | "emotion"; market: "CN" | "HK"; latest_run?: EngineRun | null; regime?: EngineRegime | null; scores: EngineScore[]; signals: StrategySignal[]; }
export interface StrategyScoreList { market: "CN" | "HK"; items: EngineScore[]; run?: EngineRun | null; horizon?: string; }
export interface DecisionChain { id: string; engine_run_id: string; strategy_line: string; market: string; macro_snapshot_id?: string | null; sector_score_id?: string | null; candidate_score_id?: string | null; committee_id?: string | null; timing_signal_id?: string | null; formula_versions: string[]; status: string; }
export interface StrategyCommitteeRequest { market: "CN" | "HK"; symbol: string; company_name?: string; strategy_line: "value" | "emotion"; horizon: "long" | "short" | "swing"; signal_id: string; }
export interface CommitteeDecisionRequest { signal_id: string; strategy_line: "value" | "emotion"; status: "approve" | "reject" | "wait"; direction: "buy" | "sell" | "wait"; position_cap: number; entry_low?: number | null; entry_high?: number | null; stop_price?: number | null; target_low?: number | null; target_high?: number | null; holding_period: string; confidence: number; summary: string; review_triggers: string[]; evidence_ids: string[]; engine_run_ids: string[]; }
export interface StructuredCommitteeDecision extends CommitteeDecisionRequest { id: string; committee_id: string; decision_status: "approve" | "reject" | "wait"; created_at: string; }
export interface PaperAccount { id: string; name: string; strategy_line: "value" | "emotion"; horizon: "long" | "short" | "swing"; market: "CN" | "HK"; currency: "CNY" | "HKD"; initial_cash: number; cash: number; status: string; updated_at: string; }
export interface PaperOrder { id: string; account_id: string; signal_id: string; symbol: string; side: string; order_type: string; quantity: number; limit_price?: number | null; status: string; submitted_at: string; expires_at?: string | null; rejection_reason: string; }
export interface PaperPosition { account_id: string; symbol: string; quantity: number; average_cost: number; realized_pnl: number; updated_at: string; }
export interface PaperNav { account_id: string; currency: string; cash: number; market_value: number; nav: number; positions: PaperPosition[]; }
export interface PaperOrderRequest { signal_id: string; committee_id: string; decision_id: string; quantity: number; limit_price?: number | null; board_lot?: number | null; }

export interface ResearchRun { id: string; kind: string; market?: MarketCode | null; symbol?: string | null; status: string; message: string; started_at: string; completed_at?: string | null; }
export interface MarketSnapshot { id: string; market: MarketCode; as_of: string; status: string; summary: string; metrics: Record<string, string | number>; risks: string[]; source_status: SourceStatus; }
export interface MacroBrief { id: string; market: MarketCode; as_of: string; headline: string; stance: string; summary: string; themes: string[]; risks: string[]; source_status: SourceStatus; }
export interface SectorScore { id: string; market: MarketCode; taxonomy: string; sector_code: string; sector_name: string; as_of: string; momentum: number; earnings: number; fund_flow: number; breadth: number; valuation: number; risk: number; base_score: number; agent_adjustment: number; agent_reason: string; final_score: number; rank: number; source_status: SourceStatus; }
export interface SecurityCandidate { id: string; market: MarketCode; symbol: string; name: string; currency: string; exchange: string; taxonomy: string; sector_name: string; as_of: string; industry_position: number; growth: number; quality: number; valuation: number; momentum: number; liquidity: number; base_score: number; agent_adjustment: number; agent_reason: string; final_score: number; rank: number; excluded: boolean; exclusion_reason: string; source_status: SourceStatus; }
export interface CompanyDossier { id: string; market: MarketCode; symbol: string; name: string; currency: string; exchange: string; taxonomy: string; sector_name: string; overview: string; bull_thesis: string; bear_thesis: string; metrics: Record<string, string | number>; catalysts: string[]; risks: string[]; data_as_of: string; source_status: SourceStatus; updated_at: string; }
export interface ResearchReport { id: string; report_type: string; title: string; summary: string; content_md: string; market?: MarketCode | null; symbol?: string | null; data_as_of: string; source_kind: string; source_id?: string | null; source_status: SourceStatus; created_at: string; }
export interface CompanyResearchResult { run: ResearchRun; dossier: CompanyDossier; report?: ResearchReport | null; }
export interface DashboardMarket { market: MarketCode; meta: { currency: string; taxonomy: string; label: string }; snapshot?: MarketSnapshot | null; macro?: MacroBrief | null; sectors: SectorScore[]; candidates: SecurityCandidate[]; }
export interface DashboardToday { markets: DashboardMarket[]; reports: ResearchReport[]; research_runs: ResearchRun[]; generated_at: string; }

export type TdxModuleStatus = "never" | "running" | "ready" | "failed" | string;
export interface TdxModule {
  code: string;
  label: string;
  description: string;
  status: TdxModuleStatus;
  progress: number;
  total: number;
  item_count: number;
  message: string;
  error: string;
  metadata: Record<string, unknown>;
  started_at?: string | null;
  updated_at?: string | null;
}

function queryString(values?: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  return params.toString();
}
export interface TdxJob {
  id: string;
  module: string;
  status: string;
  progress: number;
  total: number;
  message: string;
  error: string;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}
export interface TdxStatus {
  available: boolean;
  tdx_home: string;
  client_process_running: boolean;
  active_job?: TdxJob | null;
  modules: TdxModule[];
  recent_jobs: TdxJob[];
}
export interface TdxRecord {
  dataset: string;
  key: string;
  category: string;
  name: string;
  payload: Record<string, unknown>;
  updated_at: string;
}
export interface TdxDataset { dataset: string; total: number; items: TdxRecord[]; }

export interface TdxQuote {
  code: string; name: string; price?: number | null; last_close?: number | null;
  change_pct?: number | null; volume_lots?: number | null; amount_10k?: number | null;
  data_as_of?: string; rank?: number; sectors?: Array<{ code: string; name: string }>;
  pe_ttm?: number | null; pe_dynamic?: number | null; pb_mrq?: number | null;
  dividend_yield?: number | null; turnover_rate?: number | null; market_cap_100m?: number | null;
  revenue_10k?: number | null; net_profit_10k?: number | null; eps?: number | null;
  [key: string]: unknown;
}
export interface TdxMarketOverview {
  source: string; as_of?: string | null; client_running: boolean; indices: TdxQuote[];
  breadth: { valid: number; up: number; down: number; flat: number; up_down_ratio?: number | null; median_change_pct?: number | null };
  distribution: Array<{ label: string; count: number }>;
  activity: TdxQuote[]; index_etfs: Array<Record<string, unknown>>; limit_up_preview: TdxQuote[];
  special_counts?: Record<string, number>;
}
export interface TdxRankResult {
  category: string; categories: string[]; total: number; limit: number; offset: number;
  items: TdxQuote[]; as_of?: string | null;
  coverage: { quotes: number; fundamentals: number; fundamental_pct: number };
}
export interface TdxSector extends TdxQuote {
  category: string; member_count: number; up: number; down: number; breadth_pct?: number | null; leader?: TdxQuote | null;
}
export interface TdxSectorResult { categories: string[]; total: number; items: TdxSector[]; as_of?: string | null; }
export interface TdxSectorDetail {
  sector: TdxSector; members: TdxQuote[]; member_count: number; up: number; down: number;
  leaders: TdxQuote[]; laggards: TdxQuote[]; etfs: Array<Record<string, unknown>>; as_of?: string | null;
}
export interface TdxScreenerFilters {
  query?: string; sector?: string; min_price?: number; max_price?: number; min_change?: number; max_change?: number;
  min_turnover?: number; max_pe?: number; max_pb?: number; min_dividend_yield?: number;
  min_market_cap?: number; min_revenue?: number; min_net_profit?: number; min_eps?: number;
  include_st?: boolean; include_quit?: boolean; include_bj?: boolean; is_hs300?: boolean; is_margin?: boolean; is_connect?: boolean;
  sort?: string; direction?: string; limit?: number; offset?: number;
}
export interface TdxScreenerResult { total: number; items: TdxQuote[]; limit: number; offset: number; coverage: { quotes: number; fundamentals: number }; as_of?: string | null; }
export interface TdxFundResult { category: string; categories: string[]; total: number; items: Array<Record<string, unknown>>; as_of?: string | null; }
export interface TdxSecuritySearchItem { code: string; name: string; quote?: TdxQuote | null; updated_at: string; }
export interface TdxSecurityOverview {
  code: string; name: string; quote?: TdxQuote | null; fundamental?: Record<string, unknown> | null;
  detail?: Record<string, unknown> | null; sectors: Array<Record<string, unknown>>;
  klines: Array<Record<string, unknown>>; professional_finance_available: boolean; source: string; as_of: string;
  cache?: { quote_updated_at?: string | null; fundamental_updated_at?: string | null; detail_updated_at?: string | null; stale: boolean };
}
export interface TdxFormulaScanRequest { formula_type: number; formula_code: string; formula_args?: string; universe?: string; period?: string; }
export interface TdxFormulaScan {
  id: string; formula_type: number; formula_code: string; formula_args: string; universe: string; period: string;
  status: string; progress: number; total: number; results: Array<TdxQuote & { signals?: Array<{ line: string; value: unknown; date?: string }> }>;
  message: string; error: string; created_at: string; completed_at?: string | null;
}

export interface CommitteeDecision { committee_id: string; direction: string; position_cap?: number | null; target_low?: number | null; target_high?: number | null; stop_price?: number | null; holding_period: string; confidence: number; review_triggers: string[]; evidence_date: string; summary: string; }
export interface CommitteeTask { id?: string; task_id?: string; agent_id?: string; status?: string; output?: string; result?: string; error?: string; }
export interface Committee { id: string; market: MarketCode; symbol: string; company_name: string; status: string; swarm_run_id?: string | null; created_at: string; completed_at?: string | null; decision?: CommitteeDecision | null; tasks?: CommitteeTask[]; final_report?: string | null; warning?: string; }
export type TradePlanStatus = "draft" | "active" | "triggered" | "closed" | "cancelled";
export interface TradePlan { id: string; committee_id?: string | null; market: MarketCode; symbol: string; name: string; status: TradePlanStatus; direction: string; position_cap?: number | null; entry_low?: number | null; entry_high?: number | null; target_low?: number | null; target_high?: number | null; stop_price?: number | null; triggers: string[]; notes: string; created_at: string; updated_at: string; }

export interface Portfolio { id: string; name: string; base_currency: string; benchmark: string; initial_cash: number; cash: Record<string, number>; position_count?: number; created_at: string; updated_at: string; }
export interface CreatePortfolioRequest { name: string; base_currency: string; benchmark?: string; initial_cash?: number; cash?: Record<string, number>; }
export interface PortfolioTransactionRequest { market: MarketCode; symbol: string; name: string; side: "buy" | "sell"; trade_date: string; quantity: number; price: number; fee?: number; currency: string; notes?: string; }
export interface PortfolioTransaction extends PortfolioTransactionRequest { id: string; portfolio_id: string; created_at: string; }
export interface PortfolioPosition { market: MarketCode; symbol: string; name: string; currency: string; quantity: number; cost: number; average_cost: number; realized_pnl: number; latest_price?: number | null; market_value?: number | null; unrealized_pnl?: number | null; }
export interface PortfolioAnalytics {
  portfolio: Portfolio;
  positions: PortfolioPosition[];
  subtotals: Record<string, number>;
  as_of: string;
  base_currency_total?: number | null;
  aggregate_available: boolean;
  aggregate_warning?: string | null;
  fx_evidence?: Array<Record<string, unknown>>;
  concentration: Array<{ symbol: string; currency: string; weight: number }>;
  risk_alerts: string[];
  sector_exposure?: Array<{ market: MarketCode; sector: string; currency: string; market_value: number }>;
  performance?: { total_return?: number | null; max_drawdown?: number | null };
  correlation?: { status: string; reason?: string };
  pending_trade_plans?: TradePlan[];
}

// --- Scheduled research types ---

export interface ScheduledRun {
  id: string;
  prompt: string;
  schedule: string;
  next_run_at: number;
  status: string;
  created_at: number;
  last_run_at: number | null;
  consecutive_failures: number;
  last_error: string | null;
  failure_kind: string | null;
  config: Record<string, unknown>;
  timezone: string | null;
}

export interface CreateScheduledRunRequest {
  id?: string;
  prompt: string;
  schedule: string;
  timezone?: string | null;
  config?: Record<string, unknown>;
}

// --- Swarm types ---

export interface SwarmPreset {
  name: string;
  title: string;
  description: string;
  agent_count: number;
  variables: { name: string; description: string; required: boolean }[];
}

export interface SwarmRunSummary {
  id: string;
  preset_name: string;
  status: string;
  created_at: string;
  task_count: number;
  completed_count: number;
}

export interface LLMProviderOption {
  name: string;
  label: string;
  api_key_env?: string | null;
  base_url_env: string;
  default_model: string;
  default_base_url: string;
  base_url_options?: string[];
  api_key_required: boolean;
  auth_type?: string;
  login_command?: string | null;
}

export interface LLMSettings {
  provider: string;
  model_name: string;
  base_url: string;
  api_key_env?: string | null;
  api_key_configured: boolean;
  api_key_hint?: string | null;
  api_key_required: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort: string;
  sse_timeout_seconds: number;
  env_path: string;
  providers: LLMProviderOption[];
}

export interface UpdateLLMSettingsRequest {
  provider: string;
  model_name: string;
  base_url: string;
  api_key?: string;
  clear_api_key?: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort?: string;
}

export interface ListLLMModelsRequest {
  provider: string;
  base_url?: string;
  api_key?: string;
}

export interface LLMModelsResponse {
  provider: string;
  models: string[];
  source: "provider" | "default";
  warning_code?:
    | "oauth_discovery_unsupported"
    | "api_key_required"
    | "model_list_unavailable"
    | null;
}

export interface DataSourceSettings {
  tushare_token_configured: boolean;
  tushare_token_hint?: string | null;
  baostock_supported: boolean;
  baostock_installed: boolean;
  baostock_message: string;
  env_path: string;
}

export interface UpdateDataSourceSettingsRequest {
  tushare_token?: string;
  clear_tushare_token?: boolean;
}

export interface ChannelAdapterStatus {
  name: string;
  display_name: string;
  configured: boolean;
  enabled: boolean;
  available: boolean;
  loaded: boolean;
  running: boolean;
  error?: string;
  install_hint?: string;
}

export interface ChannelRuntimeStatus {
  running: boolean;
  inbound_queue: number;
  outbound_queue: number;
  session_count: number;
  channels: Record<string, ChannelAdapterStatus>;
}

export interface ChannelRuntimeActionResponse extends ChannelRuntimeStatus {
  status: string;
}

export interface ChannelPairingCommandRequest {
  channel: string;
  command: string;
}

export interface ChannelPairingCommandResponse {
  channel: string;
  reply: string;
}

// --- Types matching backend API contracts ---

export interface RunListItem {
  run_id: string;
  status: string;
  created_at: string;
  prompt?: string;
  total_return?: number;
  sharpe?: number;
  codes?: string[];
  start_date?: string;
  end_date?: string;
}

export interface RunDetailParams {
  chart_payload?: "summary";
  chart_symbol?: string;
}

export interface PriceBar {
  time: string;
  timestamp?: string;
  code?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TradeMarker {
  time: string;
  timestamp?: string;
  code?: string;
  side: "BUY" | "SELL";
  price: number;
  qty?: number;
  reason?: string;
  text?: string;
}

export interface EquityPoint {
  time: string;
  equity: string | number;
  drawdown: string | number;
}

/** Monte Carlo fan-chart payload: percentile envelope + sampled paths over trade order. */
export interface MonteCarloEquityPaths {
  steps: number[];
  initial_capital: number;
  actual: number[];
  band_p5: number[];
  band_p25: number[];
  band_p50: number[];
  band_p75: number[];
  band_p95: number[];
  samples: number[][];
}

export interface ValidationData {
  monte_carlo?: {
    actual_sharpe: number;
    actual_max_dd: number;
    p_value_sharpe: number;
    p_value_max_dd: number;
    simulated_sharpe_mean: number;
    simulated_sharpe_std: number;
    simulated_sharpe_p5: number;
    simulated_sharpe_p95: number;
    n_simulations: number;
    n_trades: number;
    sharpe_samples?: number[];
    equity_paths?: MonteCarloEquityPaths;
    error?: string;
  };
  bootstrap?: {
    observed_sharpe: number;
    ci_lower: number;
    ci_upper: number;
    median_sharpe: number;
    prob_positive: number;
    confidence: number;
    n_bootstrap: number;
    sharpe_samples?: number[];
    error?: string;
  };
  walk_forward?: {
    n_windows: number;
    windows: Array<{
      window: number;
      start: string;
      end: string;
      return: number;
      sharpe: number;
      max_dd: number;
      trades: number;
      win_rate: number;
    }>;
    profitable_windows: number;
    consistency_rate: number;
    return_mean: number;
    return_std: number;
    sharpe_mean: number;
    sharpe_std: number;
    error?: string;
  };
}

export interface RiskXRayPayload {
  inputs?: {
    symbols?: string[];
    weights?: Record<string, number>;
    aligned_days?: number;
    return_observations?: number;
    first_date?: string;
    last_date?: string;
  };
  concentration?: { hhi?: number; effective_n?: number; top_weight?: number };
  volatility?: { annualized_vol?: number };
  drawdown?: { max_drawdown?: number };
  tail_risk?: Record<string, unknown>;
  diversification?: Record<string, unknown>;
  correlation?: Record<string, unknown>;
  skipped?: string[];
  warnings?: string[];
}

export interface RebalanceNotesPayload {
  rebalances?: Array<{
    date: string;
    turnover: number;
    entries?: Array<{ code: string; to: number }>;
    exits?: Array<{ code: string; from: number }>;
    top_moves?: Array<{ code: string; from: number; to: number; delta: number }>;
  }>;
  summary?: {
    rebalance_count: number;
    turnover_total: number;
    turnover_mean: number;
    turnover_max: number;
    largest_rebalance_date?: string | null;
  };
}

export interface RunData {
  status: string;
  run_id: string;
  prompt?: string;
  elapsed_seconds?: number;
  run_directory?: string;
  run_stage?: string;
  run_context?: Record<string, unknown>;

  metrics?: BacktestMetrics;
  artifacts?: ArtifactInfo[];
  run_card?: RunCard;
  risk_xray?: RiskXRayPayload;
  rebalance_notes?: RebalanceNotesPayload;
  validation?: ValidationData;

  chart_symbols?: string[];
  price_series?: Record<string, PriceBar[]>;
  indicator_series?: Record<string, Record<string, IndicatorPoint[]>>;
  trade_markers?: TradeMarker[];
  equity_curve?: EquityPoint[];
  trade_log?: Array<Record<string, string>>;
  run_logs?: Array<{ source?: string; line_number?: number; message?: string }>;
}

export interface RunCard {
  schema_version?: string;
  generated_at?: string;
  run_dir?: string;
  backtest?: Record<string, unknown>;
  reproducibility?: Record<string, unknown>;
  data_sources?: string[];
  metrics?: Record<string, unknown>;
  validation?: unknown;
  warnings?: string[];
  artifacts?: RunCardArtifact[];
  [key: string]: unknown;
}

export interface RunCardArtifact {
  path: string;
  size_bytes: number;
  sha256: string;
}

export interface BacktestMetrics {
  final_value: number;
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  win_rate: number;
  trade_count: number;
  [key: string]: number;
}


export interface IndicatorPoint {
  time: string;
  value: number;
}

export interface ArtifactInfo {
  name: string;
  path: string;
  type: string;
  size: number;
  exists: boolean;
}

export interface PineScriptResult {
  exists: boolean;
  content: string | null;
}

export interface SessionItem {
  session_id: string;
  title?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  last_attempt_id?: string;
}

// --- Goal types ---

export type GoalStatus =
  | "active"
  | "paused"
  | "waiting_user"
  | "needs_refresh"
  | "insufficient_evidence"
  | "compliance_blocked"
  | "blocked"
  | "budget_limited"
  | "usage_limited"
  | "complete"
  | "cancelled"
  | "superseded";

export type GoalRiskTier =
  | "research_general"
  | "market_specific_short_term"
  | "personalized_advice_or_position_sizing";

export interface GoalRecord {
  goal_id: string;
  session_id: string;
  status: GoalStatus;
  objective: string;
  ui_summary: string;
  source: string;
  protocol: string;
  risk_tier: GoalRiskTier;
  token_budget?: number | null;
  tokens_used: number;
  turn_budget?: number | null;
  turns_used: number;
  time_budget_seconds?: number | null;
  time_used_seconds: number;
  budget_wrapup_sent: boolean;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  recap?: string | null;
}

export interface GoalClaim {
  claim_id: string;
  goal_id: string;
  session_id: string;
  claim_type: string;
  text: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface GoalCriterion {
  criterion_id: string;
  goal_id: string;
  session_id: string;
  text: string;
  required: boolean;
  status: string;
  freshness_requirement?: string | null;
  protocol_step?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GoalEvidence {
  evidence_id: string;
  goal_id: string;
  session_id: string;
  text: string;
  criterion_id?: string | null;
  claim_id?: string | null;
  evidence_type: string;
  tool_call_id?: string | null;
  run_id?: string | null;
  source_provider?: string | null;
  source_type?: string | null;
  source_uri?: string | null;
  symbol_universe: string[];
  benchmark: string[];
  timeframe?: string | null;
  method?: string | null;
  assumptions: Record<string, unknown>;
  artifact_path?: string | null;
  artifact_hash?: string | null;
  retrieved_at: string;
  data_as_of?: string | null;
  freshness_status: string;
  verification_status: string;
  confidence?: string | null;
  caveat?: string | null;
  contradicts_claim_ids: string[];
  created_at: string;
}

export interface GoalSnapshot {
  goal: GoalRecord;
  claims: GoalClaim[];
  criteria: GoalCriterion[];
  evidence: GoalEvidence[];
  evidence_count: number;
}

export interface CreateGoalRequest {
  objective: string;
  criteria?: string[];
  ui_summary?: string;
  protocol?: string;
  risk_tier?: GoalRiskTier;
  token_budget?: number;
  turn_budget?: number;
  time_budget_seconds?: number;
}

export interface AddGoalEvidenceRequest {
  goal_id: string;
  expected_goal_id: string;
  text: string;
  criterion_id?: string | null;
  claim_id?: string | null;
  evidence_type?: string;
  tool_call_id?: string | null;
  run_id?: string | null;
  source_provider?: string | null;
  source_type?: string | null;
  source_uri?: string | null;
  symbol_universe?: string[];
  benchmark?: string[];
  timeframe?: string | null;
  method?: string | null;
  assumptions?: Record<string, unknown>;
  artifact_path?: string | null;
  artifact_hash?: string | null;
  data_as_of?: string | null;
  confidence?: string | null;
  caveat?: string | null;
  contradicts_claim_ids?: string[];
}

export interface UpdateGoalRequest {
  goal_id: string;
  expected_goal_id: string;
  objective?: string;
  ui_summary?: string;
}

export interface UpdateGoalResponse {
  goal: GoalRecord;
  snapshot: GoalSnapshot;
}

export interface AddGoalEvidenceResponse {
  evidence: GoalEvidence;
  snapshot: GoalSnapshot;
}

export interface GoalAuditRowRequest {
  criterion_id: string;
  result: string;
  evidence_ids?: string[];
  notes?: string;
}

export interface UpdateGoalStatusRequest {
  goal_id: string;
  expected_goal_id: string;
  status: GoalStatus;
  audit?: GoalAuditRowRequest[];
  recap?: string | null;
}

export interface UpdateGoalStatusResponse {
  goal: GoalRecord;
  snapshot: GoalSnapshot;
}

// --- Alpha Zoo types ---

export interface AlphaListParams {
  zoo?: string;
  theme?: string;
  universe?: string;
  limit?: number;
}

export interface AlphaSummary {
  id: string;
  zoo: string;
  theme: string[];
  universe: string[];
  nickname?: string;
  decay_horizon?: number | null;
  min_warmup_bars?: number | null;
  requires_sector?: boolean;
}

export interface AlphaListResponse {
  status: string;
  alphas: AlphaSummary[];
  total: number;
  returned: number;
  truncated: boolean;
}

export interface AlphaDetail {
  id: string;
  zoo: string;
  module_path?: string;
  meta: Record<string, unknown>;
}

export interface AlphaDetailResponse {
  status: string;
  alpha: AlphaDetail;
  source_code: string;
}

export interface AlphaBenchRequest {
  zoo: string;
  universe: string;
  period: string;
  top?: number;
}

export interface AlphaBenchTopRow {
  id: string;
  ic_mean: number;
  ir: number;
  theme: string[];
  formula_latex: string;
  category: "alive" | "reversed" | "dead";
}

export interface AlphaBenchResult {
  alive: number;
  reversed: number;
  dead: number;
  skipped?: number;
  top5_by_ir: AlphaBenchTopRow[];
  dead_examples: AlphaBenchTopRow[];
  by_theme: Record<string, { alive: number; reversed: number; dead: number }>;
}

export interface AlphaCompareRequest {
  alpha_ids: string[];
  universe: string;
  period: string;
  /** One of: ir | ic_mean | ic_positive_ratio | ic_count (default ir). */
  sort?: string;
}

export interface AlphaCompareRow {
  rank: number;
  id: string;
  zoo: string;
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_positive_ratio: number;
  ic_count: number;
  /** `delta_<sort>_vs_best` — gap to the top-ranked alpha on the active metric. */
  [deltaKey: string]: number | string;
}

export interface AlphaCompareSkip {
  id: string;
  reason: string;
}

export interface AlphaCompareResult {
  universe: string;
  period: string;
  sort: string;
  n_compared: number;
  n_skipped: number;
  winner: string;
  ranking: AlphaCompareRow[];
  skipped: AlphaCompareSkip[];
}

export interface MessageItem {
  message_id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
  linked_attempt_id?: string;
  metadata?: Record<string, unknown>;
  tool_trail?: ToolTrailItem[];
}

export interface ToolTrailItem {
  tool: string;
  status: "running" | "ok" | "error";
  arguments?: Record<string, string>;
  elapsed_ms?: number;
  preview?: string;
  call_id?: string;
  timestamp?: number;
}
