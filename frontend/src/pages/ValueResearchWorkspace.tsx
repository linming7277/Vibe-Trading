import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Outlet, useLocation, useNavigate, useOutletContext, useSearchParams } from "react-router";
import {
  Activity, AlertTriangle, ChevronRight, ClipboardCheck,
  Loader2, Plus, RefreshCw, Search, ShieldAlert,
} from "lucide-react";
import { toast } from "sonner";

import { PageHeader, WorkspacePage } from "@/components/workspace/WorkspaceUI";
import {
  api, type CalculationProfile, type CompanyResearchBatch, type CompanyResearchJob, type ValueCompanyArchive, type ValueUniverseCompany,
  type ResearchReport, type ValueEntryMonitor, type ValueLeaderScore, type ValueMonitorEvent, type ValueSectorScore, type ValueTrack,
  type Level3Leader, type ValueAnalysisDimension, type ValueCompanyAnalysis, type ValueIncrementalRun, type ValueResearchAutomation, type ValueResearchUniverse, type ValueSignalEvaluation, type ValueUniverseAnalysis, type ValueWorkbench,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { companyResearchPath } from "@/lib/routeContext";

type ValueWorkspaceContext = {
  profiles: CalculationProfile[];
  workbench: ValueWorkbench | null;
  tracks: ValueTrack[];
  leaders: ValueLeaderScore[];
  selectedTrack: ValueTrack | undefined;
  selectedTrackId: string;
  candidateTrackLimit: number;
  selectedSymbols: string[];
  batches: CompanyResearchBatch[];
  monitors: ValueEntryMonitor[];
  events: ValueMonitorEvent[];
  universes: ValueResearchUniverse[];
  activeUniverse?: ValueResearchUniverse;
  universeAnalysis: ValueUniverseAnalysis | null;
  automation: ValueResearchAutomation | null;
  signals: ValueSignalEvaluation[];
  loading: boolean;
  refreshing: boolean;
  selectProfile: (id: string) => void;
  selectTrack: (id: string) => void;
  setCandidateTrackLimit: (limit: number) => void;
  toggleSymbol: (symbol: string) => void;
  clearSelection: () => void;
  refreshScores: () => Promise<void>;
  reload: () => Promise<void>;
  reloadOperations: () => Promise<void>;
  startResearch: () => Promise<void>;
  freezeUniverse: () => Promise<void>;
  bootstrapUniverse: (id: string) => Promise<void>;
  activateUniverse: (id: string) => Promise<void>;
  runDailyIncrement: () => Promise<void>;
  setAutomationEnabled: (enabled: boolean) => Promise<void>;
};

const ALL_TRACK_ID = "__all_candidate_tracks__";
const CANDIDATE_TRACK_LIMIT_OPTIONS = [5, 10, 20, 50] as const;

const MODEL_LABELS: Record<string, string> = {
  policy_cycle: "政策与产业周期",
  economic_cycle: "经济周期",
  liquidity: "流动性",
  earnings_climate: "盈利景气",
};

const MACRO_LABELS: Record<string, string> = {
  growth: "增长", inflation: "通胀", liquidity: "流动性", credit: "信用", financial_conditions: "金融条件",
};

const SECTOR_COMPONENT_LABELS: Record<string, string> = {
  momentum: "相对动量", earnings_momentum: "盈利动量", valuation: "估值",
  capital_flow_proxy: "成交活跃度", macro_fit: "宏观匹配", policy_fit: "政策匹配", risk_quality: "风险质量",
};

const MACRO_FIELD_LABELS: Record<string, string> = {
  a_share_breadth_20d: "A股 20 日上涨宽度",
  csi_all_share_risk_appetite: "中证全指 20 日风险偏好",
  social_financing_increment: "社会融资规模增量",
  usd_cny: "美元兑人民币",
};

const SECTOR_EVIDENCE_LABELS: Record<string, string> = {
  momentum: "相对走势", earnings_momentum: "盈利趋势", valuation: "估值吸引力",
  capital_flow_proxy: "交易活跃度", macro_fit: "宏观环境", policy_fit: "政策支持", risk_quality: "风险质量",
};

const LEADER_STRENGTH_TEXT: Record<string, string> = {
  industry_position: "规模、营收和利润处于行业前列",
  profitability: "ROE及利润率处于行业前列",
  growth_stability: "中长期增长更稳定",
  cash_flow: "利润现金含量较好",
  valuation: "相对同行估值更有吸引力",
  governance_risk: "负债、股东和波动风险相对较低",
};

const LEADER_WEAKNESS_TEXT: Record<string, string> = {
  industry_position: "行业地位不是当前主要优势",
  profitability: "盈利能力仍需重点复核",
  growth_stability: "增长持续性仍需复核",
  cash_flow: "现金流质量仍需复核",
  valuation: "估值不是当前主要优势",
  governance_risk: "治理与风险仍需复核",
};

const statusStyles: Record<string, string> = {
  active: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  ready: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  completed: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  partial: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  running: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
  queued: "bg-muted text-muted-foreground",
  failed: "bg-destructive/10 text-destructive",
  cancelled: "bg-muted text-muted-foreground",
  paused: "bg-muted text-muted-foreground",
  draft: "bg-muted text-muted-foreground",
  bootstrapping: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
  archived: "bg-muted text-muted-foreground",
  entry_candidate: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  holding_review: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  exit_candidate: "bg-orange-500/10 text-orange-700 dark:text-orange-400",
  thesis_invalidated: "bg-destructive/10 text-destructive",
  data_insufficient: "bg-muted text-muted-foreground",
  stale: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
};

function score(value?: number | null) { return value == null ? "—" : value.toFixed(1); }
function percent(value?: number | null) { return value == null ? "—" : `${Math.round(value * 100)}%`; }
function numeric(value: unknown) { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function scoreLevel(value?: number | null) {
  if (value == null) return "暂不可判断";
  if (value >= 75) return "显著占优";
  if (value >= 60) return "较为有利";
  if (value >= 45) return "中性";
  if (value >= 30) return "较为不利";
  return "显著不利";
}
function sectorVerdict(sector?: ValueSectorScore) {
  if (!sector || sector.score == null || sector.coverage < .8) return "证据不足";
  if (sector.rank <= 15) return "优先看龙头";
  if (sector.rank <= 40) return "值得跟踪";
  if (sector.rank <= 80) return "中性观察";
  return "暂缓研究";
}
function formatMetric(value: unknown, suffix = "") { const number = numeric(value); return number == null ? null : `${number.toFixed(1)}${suffix}`; }
function sectorEvidence(sector: ValueSectorScore, component: string) {
  const raw = sector.raw_features || {};
  if (component === "momentum") return [formatMetric(raw.relative_20d, "%"), formatMetric(raw.up_breadth, "%")].filter(Boolean).length ? `20日相对收益 ${formatMetric(raw.relative_20d, "%") || "—"}，上涨宽度 ${formatMetric(raw.up_breadth, "%") || "—"}` : "相对收益和上涨宽度在行业横截面中占优";
  if (component === "earnings_momentum") return `营收同比中位数 ${formatMetric(raw.revenue_yoy_median, "%") || "—"}，利润同比中位数 ${formatMetric(raw.profit_yoy_median, "%") || "—"}`;
  if (component === "valuation") return `PE中位数 ${formatMetric(raw.pe_median) || "—"}，PB中位数 ${formatMetric(raw.pb_median) || "—"}，股息率 ${formatMetric(raw.dividend_yield_median, "%") || "—"}`;
  if (component === "capital_flow_proxy") return `成交额占比 ${formatMetric(raw.turnover_share, "%") || "—"}，活跃宽度 ${formatMetric(raw.active_breadth, "%") || "—"}`;
  if (component === "risk_quality") return `20日波动 ${formatMetric(raw.volatility_20d, "%") || "—"}，最大回撤 ${formatMetric(raw.max_drawdown, "%") || "—"}`;
  if (component === "macro_fit") {
    const driver = sector.macro_drivers?.[0];
    return driver ? `${driver.axis_name}是当前主要宏观影响，行业敏感度 ${Math.round(Math.abs(driver.sensitivity) * 100)}%` : "宏观传导数据暂不可用";
  }
  if (component === "policy_fit") return "官方政策事件尚未形成有效分类，本维度未计分";
  return "详细原始证据可在评分说明中查看";
}
function leaderMetrics(leader: ValueLeaderScore) {
  const raw = leader.raw_features || {};
  const candidates = [
    ["ROE", formatMetric(raw.roe, "%")], ["营收CAGR", formatMetric(raw.revenue_cagr, "%")],
    ["利润CAGR", formatMetric(raw.profit_cagr, "%")], ["PE", formatMetric(raw.pe)],
    ["PB", formatMetric(raw.pb)], ["股息率", formatMetric(raw.dividend_yield, "%")],
  ];
  return candidates.filter((item): item is [string, string] => Boolean(item[1])).slice(0, 3);
}
function labelStatus(value: string) {
  return ({ ready: "待激活", draft: "待建档", not_archived: "待建档", bootstrapping: "建档中", archived: "已归档", queued: "排队", running: "进行中", partial: "部分可用", completed: "已完成", failed: "失败", cancelled: "已取消", active: "活动中", paused: "已暂停", closed: "已关闭", watching: "持续观察", entry_candidate: "入场候选", holding_review: "风险复核", exit_candidate: "退出/减仓候选", thesis_invalidated: "逻辑失效", data_insufficient: "数据不足", stale: "数据过期", insufficient_data: "数据不足", macro_pending: "宏观待更新" } as Record<string, string>)[value] || value;
}

export function ValueResearchWorkspace() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [profiles, setProfiles] = useState<CalculationProfile[]>([]);
  const [workbench, setWorkbench] = useState<ValueWorkbench | null>(null);
  const [leaders, setLeaders] = useState<ValueLeaderScore[]>([]);
  const [batches, setBatches] = useState<CompanyResearchBatch[]>([]);
  const [monitors, setMonitors] = useState<ValueEntryMonitor[]>([]);
  const [events, setEvents] = useState<ValueMonitorEvent[]>([]);
  const [universes, setUniverses] = useState<ValueResearchUniverse[]>([]);
  const [universeAnalysis, setUniverseAnalysis] = useState<ValueUniverseAnalysis | null>(null);
  const [automation, setAutomation] = useState<ValueResearchAutomation | null>(null);
  const [signals, setSignals] = useState<ValueSignalEvaluation[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const profileId = params.get("profile") || "";
  const requestedTrackLimit = Number(params.get("candidate_limit"));
  const candidateTrackLimit = CANDIDATE_TRACK_LIMIT_OPTIONS.includes(requestedTrackLimit as 5 | 10 | 20 | 50)
    ? requestedTrackLimit
    : 20;
  const defaultTrackId = useMemo(() => {
    const candidateFirst = [...(workbench?.sector_scores || [])]
      .sort((left, right) => left.rank - right.rank)[0]?.sector_code;
    return candidateFirst || workbench?.tracks[0]?.track_id || "";
  }, [workbench?.sector_scores, workbench?.tracks]);
  const selectedTrackId = params.get("track") || defaultTrackId;
  const tracks = workbench?.tracks || [];
  const selectedTrack = tracks.find((item) => item.track_id === selectedTrackId);
  const activeUniverse = universes.find((item) => item.status === "active" && item.profile_id === workbench?.profile.id);

  const patchParams = useCallback((nextValues: Record<string, string | null>) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(nextValues).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
      return next;
    });
  }, [setParams]);

  const reloadOperations = useCallback(async () => {
    const [batchResult, monitorResult, eventResult, universeResult, automationResult, signalResult] = await Promise.all([
      api.getValueResearchBatches(), api.getValueMonitors(), api.getValueMonitorEvents(),
      api.getValueResearchUniverses(), api.getValueAutomation(), api.getValueMonitoringSignals({ limit: 200 }),
    ]);
    setBatches(batchResult.items);
    setMonitors(monitorResult.items);
    setEvents(eventResult.items);
    setUniverses(universeResult.items);
    // A fresh industry-leader archive must take precedence over an older
    // macro-universe.  Otherwise the company list is correct but every new
    // leader is incorrectly rendered as "待建档".
    const analysisUniverse = universeResult.items.find((item) => item.status === "bootstrapping")
      || universeResult.items.find((item) => ["ready", "partial"].includes(item.status))
      || universeResult.items.find((item) => item.status === "active");
    if (analysisUniverse) {
      setUniverseAnalysis(await api.getValueUniverseAnalysis(analysisUniverse.id));
    } else {
      setUniverseAnalysis(null);
    }
    setAutomation(automationResult);
    setSignals(signalResult.items);
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const profileResult = await api.getValueProfiles();
      const requestedProfile = profileResult.items.find((item) => item.id === profileId)
        || profileResult.items.find((item) => item.is_default) || profileResult.items[0];
      if (!requestedProfile) throw new Error("尚未配置价值计算方案");
      const nextWorkbench = await api.getValueWorkbench(requestedProfile.id);
      setProfiles(profileResult.items);
      setWorkbench(nextWorkbench);
      await reloadOperations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "价值研究工作台加载失败");
    } finally {
      setLoading(false);
    }
  }, [profileId, reloadOperations]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    if (!selectedTrackId) { setLeaders([]); return; }
    let cancelled = false;
    api.getValueLeaderScores(
      selectedTrackId === ALL_TRACK_ID ? undefined : selectedTrackId,
      workbench?.latest_run?.as_of || workbench?.macro?.as_of || undefined,
      selectedTrackId === ALL_TRACK_ID ? candidateTrackLimit : undefined,
    )
      .then((result) => { if (!cancelled) setLeaders(result.items); })
      .catch((error) => { if (!cancelled) { setLeaders([]); toast.error(error instanceof Error ? error.message : "龙头池加载失败"); } });
    return () => { cancelled = true; };
  }, [candidateTrackLimit, selectedTrackId, workbench?.latest_run?.as_of, workbench?.macro?.as_of]);

  useEffect(() => {
    if (!defaultTrackId || params.get("track")) return;
    patchParams({ track: defaultTrackId });
  }, [defaultTrackId, params, patchParams]);

  useEffect(() => setSelectedSymbols((current) => current.filter((symbol) => leaders.some((item) => item.symbol === symbol))), [leaders]);

  useEffect(() => {
    const universeRunning = universes.some((item) => ["bootstrapping"].includes(item.status) || ["queued", "running"].includes(item.latest_operation?.status || ""));
    if (!batches.some((batch) => ["queued", "running"].includes(batch.status)) && !universeRunning) return;
    const timer = window.setInterval(() => void reloadOperations(), 5000);
    return () => window.clearInterval(timer);
  }, [batches, reloadOperations, universes]);

  const refreshScores = useCallback(async () => {
    setRefreshing(true);
    try {
      const job = await api.startValueRefresh(["scores"]);
      let finished = false;
      for (let attempt = 0; attempt < 180; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        const current = await api.getValueRefreshJob(job.id);
        if (!["queued", "running"].includes(current.status)) {
          if (current.status === "failed") throw new Error(current.errors.map((item) => item.error).join("；") || "评分计算失败");
          if (current.status === "partial") toast.warning(current.errors.map((item) => `${item.module}: ${item.error}`).join("；") || "评分已完成，但部分数据不可用");
          finished = true;
          break;
        }
      }
      if (!finished) throw new Error("评分任务超过 3 分钟仍未完成，请到数据中心查看任务状态");
      await reload();
      toast.success("价值线 V2 评分已重新计算");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重新计算失败");
    } finally {
      setRefreshing(false);
    }
  }, [reload]);

  useEffect(() => {
    const handler = () => { void refreshScores(); };
    window.addEventListener("hengzhi:value-refresh", handler);
    return () => window.removeEventListener("hengzhi:value-refresh", handler);
  }, [refreshScores]);

  const startResearch = useCallback(async () => {
    if (!workbench?.latest_run?.id || !selectedTrackId || !selectedSymbols.length) return;
    try {
      const batch = await api.createValueResearchBatch({ run_id: workbench.latest_run.id, track_id: selectedTrackId, symbols: selectedSymbols, concurrency: 3 });
      toast.success(batch.created ? `已建立 ${batch.total} 家公司的研究批次` : "已打开相同条件的现有研究批次");
      setSelectedSymbols([]);
      await reloadOperations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "无法启动批量研究");
    }
  }, [reloadOperations, selectedSymbols, selectedTrackId, workbench?.latest_run?.id]);

  const freezeUniverse = useCallback(async () => {
    if (!workbench?.latest_run?.id) return;
    try {
      const universe = await api.createValueResearchUniverse({
        run_id: workbench.latest_run.id,
        candidate_limit: candidateTrackLimit as 5 | 10 | 20 | 50,
        leader_limit: 5,
      });
      toast.success(universe.created ? `已冻结 ${universe.track_count} 个赛道、${universe.company_count} 家公司` : "已打开相同条件的研究宇宙");
      await reloadOperations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "冻结研究宇宙失败");
    }
  }, [candidateTrackLimit, reloadOperations, workbench?.latest_run?.id]);

  const bootstrapUniverse = useCallback(async (id: string) => {
    try {
      const operation = await api.bootstrapValueResearchUniverse(id);
      toast.success(operation.created ? `已启动 ${operation.total} 家公司的首次建档` : "已打开相同条件的建档任务");
      await reloadOperations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "首次建档启动失败");
    }
  }, [reloadOperations]);

  const activateUniverse = useCallback(async (id: string) => {
    try {
      await api.activateValueResearchUniverse(id);
      toast.success("研究宇宙已激活，后续增量只更新该版本");
      await reloadOperations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "研究宇宙激活失败");
    }
  }, [reloadOperations]);

  const runDailyIncrement = useCallback(async () => {
    if (!activeUniverse) { toast.error("请先完成建档并激活一个研究宇宙"); return; }
    try {
      const operation = await api.startValueIncrementalRun(activeUniverse.id);
      toast.success(operation.created ? `已启动 ${operation.total} 家公司的今日增量` : "今日增量任务已经存在");
      await reloadOperations();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "今日增量启动失败");
    }
  }, [activeUniverse, reloadOperations]);

  const setAutomationEnabled = useCallback(async (enabled: boolean) => {
    try {
      const value = await api.updateValueAutomation(enabled);
      setAutomation(value);
      toast.success(enabled ? "已开启交易日 16:45 自动研究" : "已关闭自动研究");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "自动运行设置失败");
    }
  }, []);

  const context: ValueWorkspaceContext = {
    profiles, workbench, tracks, leaders, selectedTrack, selectedTrackId, candidateTrackLimit, selectedSymbols, batches, monitors, events, universes, activeUniverse, universeAnalysis, automation, signals, loading, refreshing,
    selectProfile: (id) => patchParams({ profile: id, track: null }),
    selectTrack: (id) => patchParams({ track: id }),
    setCandidateTrackLimit: (limit) => patchParams({ candidate_limit: String(limit), track: ALL_TRACK_ID }),
    toggleSymbol: (symbol) => setSelectedSymbols((current) => selectedTrackId === ALL_TRACK_ID ? current : current.includes(symbol) ? current.filter((item) => item !== symbol) : current.length >= 20 ? current : [...current, symbol]),
    clearSelection: () => setSelectedSymbols([]), refreshScores, reload, reloadOperations, startResearch,
    freezeUniverse, bootstrapUniverse, activateUniverse, runDailyIncrement, setAutomationEnabled,
  };

  return <WorkspacePage className={location.pathname === "/value" ? "!max-w-none !p-0" : "space-y-5"}>
    {location.pathname !== "/value" ? <ValueContextBar context={context} /> : null}
    <Outlet context={context} />
  </WorkspacePage>;
}

export function useValueWorkspace() { return useOutletContext<ValueWorkspaceContext>(); }

function ValueContextBar({ context }: { context: ValueWorkspaceContext }) {
  return <section className="sticky top-0 z-20 -mx-5 border-b bg-background/95 px-5 py-2 backdrop-blur md:-mx-8 md:px-8">
    <div className="mx-auto flex max-w-[1480px] flex-wrap items-center justify-end gap-2">
      <select value={context.workbench?.profile.id || ""} onChange={(event) => context.selectProfile(event.target.value)} className="h-9 max-w-[240px] rounded-lg border bg-card px-3 text-sm" aria-label="计算方案">
        {context.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · v{profile.version}</option>)}
      </select>
      <Link to="/value/profiles" className="inline-flex items-center rounded-lg border bg-card px-3 py-2 text-sm hover:bg-muted">计算方案</Link>
      <button onClick={() => void context.refreshScores()} disabled={context.refreshing} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", context.refreshing && "animate-spin")} />更新评分</button>
    </div>
  </section>;
}

export function ValueOverview() {
  const context = useValueWorkspace();
  const [params] = useSearchParams();
  const [query, setQuery] = useState("");
  const [rankView, setRankView] = useState<"macro" | "overall">("overall");
  const [activeGroup, setActiveGroup] = useState("全部");
  const candidateSectors = useMemo(
    () => (context.workbench?.sector_scores || []).filter((item) => item.rank <= context.candidateTrackLimit),
    [context.candidateTrackLimit, context.workbench?.sector_scores],
  );
  const groups = useMemo(() => {
    const grouped = new Map<string, ValueSectorScore[]>();
    for (const sector of candidateSectors) {
      const key = sector.macro_group_name || "未映射";
      grouped.set(key, [...(grouped.get(key) || []), sector]);
    }
    return [...grouped.entries()].map(([name, sectors]) => {
      const valid = sectors.filter((item) => item.macro_fit != null);
      const macroScore = valid.length ? valid.reduce((sum, item) => sum + Number(item.macro_fit), 0) / valid.length : null;
      return { name, sectors, macroScore, candidateRank: Math.min(...sectors.map((item) => item.rank)) };
    }).sort((left, right) => left.candidateRank - right.candidateRank);
  }, [candidateSectors]);
  useEffect(() => {
    if (activeGroup !== "全部" && !groups.some((group) => group.name === activeGroup)) setActiveGroup("全部");
  }, [activeGroup, groups]);
  const filteredSectors = useMemo(() => {
    const needle = query.toLowerCase();
    const items = candidateSectors.filter((item) => {
      const inGroup = activeGroup === "全部" || item.macro_group_name === activeGroup;
      return inGroup && (!query || `${item.sector_name} ${item.sector_code} ${item.macro_group_name || ""}`.toLowerCase().includes(needle));
    });
    return [...items].sort((left, right) => rankView === "macro"
      ? (left.macro_rank || 999) - (right.macro_rank || 999)
      : left.rank - right.rank);
  }, [activeGroup, candidateSectors, query, rankView]);
  const chooseFirst = useCallback((nextGroup: string, nextRank: "macro" | "overall") => {
    const candidates = candidateSectors.filter((item) => nextGroup === "全部" || item.macro_group_name === nextGroup);
    candidates.sort((left, right) => nextRank === "macro" ? (left.macro_rank || 999) - (right.macro_rank || 999) : left.rank - right.rank);
    if (candidates[0]) context.selectTrack(candidates[0].sector_code);
  }, [candidateSectors, context]);

  const changeGroup = (group: string) => {
    setActiveGroup(group);
    setQuery("");
    if (group === "全部") context.selectTrack(ALL_TRACK_ID);
    else chooseFirst(group, rankView);
  };
  const changeRank = (view: "macro" | "overall") => {
    setRankView(view);
    chooseFirst(activeGroup, view);
  };

  useEffect(() => {
    const focus = params.get("focus");
    if (!focus || context.loading) return;
    document.getElementById(`value-${focus}`)?.scrollIntoView({ block: "start" });
  }, [context.loading, params]);

  if (context.loading) return <Loading label="正在准备宏观、赛道和龙头快照" />;
  return <div className="space-y-4">
    <MacroStatusBar />
    <ResearchUniverseControl />
    <section className="grid overflow-hidden rounded-xl border bg-card shadow-sm xl:h-[calc(100vh-210px)] xl:min-h-[600px] xl:max-h-[860px] xl:grid-cols-[220px_360px_minmax(0,1fr)]">
      <IndustryGroupPane groups={groups} activeGroup={activeGroup} onSelect={changeGroup} />
      <TrackPane sectors={filteredSectors} query={query} setQuery={setQuery} rankView={rankView} setRankView={changeRank} />
      <div className="relative flex min-h-0 min-w-0 flex-col">
        <div className="pointer-events-none absolute right-4 top-2 z-10 hidden rounded-full border bg-background/90 px-2.5 py-1 text-xs text-muted-foreground shadow-sm xl:block">公司名称可点击进入详情</div>
        <DecisionPane />
      </div>
    </section>
    <ResearchSelectionBar />
    <PipelineSummary />
  </div>;
}

function ResearchUniverseControl() {
  const context = useValueWorkspace();
  const latest = context.universes.find((item) => item.profile_id === context.workbench?.profile.id);
  const operation = latest?.latest_operation;
  const jobCounts = (operation?.jobs || []).reduce((counts, job) => ({ ...counts, [job.status]: (counts[job.status] || 0) + 1 }), {} as Record<string, number>);
  const processed = (operation?.completed || 0) + (operation?.failed || 0);
  const progressPercent = operation?.total ? Math.round(processed / operation.total * 100) : 0;
  const busy = ["queued", "running"].includes(operation?.status || "") || latest?.status === "bootstrapping";
  const canFreeze = !latest || latest.status === "archived" || latest.engine_run_id !== context.workbench?.latest_run?.id || latest.candidate_limit !== context.candidateTrackLimit;
  const staleCount = context.universeAnalysis?.state_counts.stale || 0;
  const primaryAction = canFreeze
    ? <button onClick={() => void context.freezeUniverse()} className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">冻结候选池</button>
    : latest?.status === "draft"
      ? <button onClick={() => void context.bootstrapUniverse(latest.id)} className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">一键首次建档</button>
      : latest?.status === "ready"
        ? <button onClick={() => void context.activateUniverse(latest.id)} className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">激活长期研究</button>
        : latest?.status === "active"
          ? <button onClick={() => void context.runDailyIncrement()} disabled={busy} className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">更新数据</button>
          : null;
  return <section className="rounded-xl border bg-card px-4 py-3 shadow-sm">
    <div className="flex flex-wrap items-center gap-3"><div className="mr-auto min-w-[240px]"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">持续研究宇宙</span>{latest ? <StatusBadge status={latest.status} /> : null}{staleCount ? <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">数据过期 {staleCount}</span> : null}</div><div className="mt-1 text-xs text-muted-foreground">{latest ? `${latest.track_count} 个赛道 · ${latest.company_count} 家去重公司 · 数据 ${latest.data_as_of}` : "冻结候选池后，排名变化不会覆盖历史研究对象。"}</div></div>{primaryAction}<details className="relative"><summary className="cursor-pointer list-none rounded-lg border px-3 py-2 text-sm hover:bg-muted">任务与调度</summary><div className="absolute right-0 z-20 mt-2 w-[360px] rounded-xl border bg-card p-3 shadow-xl"><div className="flex items-center justify-between gap-3 text-sm"><span>交易日 16:45 自动运行</span>{context.activeUniverse ? <label className="flex items-center gap-2"><input type="checkbox" checked={Boolean(context.automation?.enabled)} onChange={(event) => void context.setAutomationEnabled(event.target.checked)} /><span className="text-xs text-muted-foreground">开启</span></label> : <span className="text-xs text-muted-foreground">激活后可用</span>}</div>{operation ? <div className="mt-3 border-t pt-3 text-xs text-muted-foreground"><div className="grid grid-cols-2 gap-2"><span>最近任务：{operation.run_kind === "bootstrap" ? "首次建档" : "日增量"}</span><span>状态：{labelStatus(operation.status)}</span><span>进度：{operation.completed}/{operation.total}</span><span>失败：{operation.failed}</span></div>{operation.message ? <div className="mt-2 text-amber-700 dark:text-amber-400">{operation.message}</div> : null}</div> : null}{context.automation?.enabled ? <div className="mt-3 border-t pt-3 text-xs text-muted-foreground">下次运行：{context.automation.next_run_at || "等待调度"}{context.automation.last_error ? ` · 最近问题：${context.automation.last_error}` : ""}</div> : null}</div></details></div>
    {busy && operation ? <div className="mt-3 rounded-lg border border-primary/30 bg-primary/[0.03] p-3 text-xs text-muted-foreground"><div className="flex flex-wrap justify-between gap-2"><span className="font-medium text-foreground">正在处理：{processed}/{operation.total}</span><span>运行中 {jobCounts.running || 0}</span><span>排队 {jobCounts.queued || 0}</span><span>部分可用 {jobCounts.partial || 0}</span><span>完整 {jobCounts.completed || 0}</span>{jobCounts.failed ? <span className="text-destructive">失败 {jobCounts.failed}</span> : null}</div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progressPercent}%` }} /></div></div> : null}
  </section>;
}

function CandidateTrackLimitControl() {
  const { candidateTrackLimit, setCandidateTrackLimit } = useValueWorkspace();
  return <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 px-2.5 py-2"><span className="text-xs text-muted-foreground">候选范围 · 龙头池 {candidateTrackLimit * 5} 家</span><div className="grid grid-cols-4 rounded-md bg-muted p-0.5 text-xs">{CANDIDATE_TRACK_LIMIT_OPTIONS.map((limit) => <button key={limit} onClick={() => setCandidateTrackLimit(limit)} className={cn("rounded px-2.5 py-1.5 transition", candidateTrackLimit === limit && "bg-background font-medium shadow-sm")}>前 {limit}</button>)}</div></div>;
}

function MacroStatusBar() {
  const { workbench } = useValueWorkspace();
  const macro = workbench?.macro;
  const missing = macro?.missing_series || [];
  return <details id="value-macro" className="group overflow-hidden rounded-xl border bg-card shadow-sm">
    <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
      <div className="flex items-baseline gap-2"><span className="text-xs font-semibold tracking-wide text-primary">宏观</span><span className="text-base font-semibold">{macro?.regime || "宏观待更新"}</span></div>
      <div className="justify-self-start"><StatusBadge status={macro?.status || "failed"} /></div>
      <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">{Object.entries(MACRO_LABELS).map(([key, label]) => <span key={key} className={cn("rounded-full px-2.5 py-1 text-xs", Number(macro?.axes?.[key] || 50) >= 60 ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground")}>{label} {macro?.states?.[key] || "—"} <b className="ml-1 font-mono text-foreground">{score(macro?.axes?.[key])}</b></span>)}</div>
      <div className="ml-auto text-right text-xs text-muted-foreground">{macro?.as_of || "—"} · {macro?.series_count ?? "—"}/{macro?.series_total ?? "—"}</div>
      <ChevronRight className="h-4 w-4 text-muted-foreground transition group-open:rotate-90" />
    </summary>
    <div className="grid gap-4 border-t bg-muted/20 p-4 text-sm md:grid-cols-[1fr_300px]">
      <div className="space-y-2 text-muted-foreground"><div className="font-medium text-foreground">数据质量</div>{missing.length ? <div className="flex gap-2"><AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" /><span>缺少：{missing.map((item) => MACRO_FIELD_LABELS[item] || item).join("、")}</span></div> : <div>关键宏观指标无缺失。</div>}{macro?.first_observed_count ? <div>{macro.first_observed_count} 项历史发布时间只能从首次抓取日确认。</div> : null}<Link to="/models/data" className="inline-block text-primary hover:underline">查看数据质量</Link></div>
      <div className="grid grid-cols-2 gap-y-2 rounded-lg border bg-background p-3 text-muted-foreground"><span>宏观轴覆盖</span><strong className="text-right font-mono text-foreground">{percent(macro?.axis_coverage ?? macro?.coverage)}</strong><span>指标覆盖</span><strong className="text-right font-mono text-foreground">{percent(macro?.series_coverage)}</strong><span>发布时间核验</span><strong className="text-right font-mono text-foreground">{percent(macro?.release_verified_coverage)}</strong><span>矩阵覆盖</span><strong className="text-right font-mono text-foreground">128/128</strong></div>
    </div>
  </details>;
}

function IndustryGroupPane({ groups, activeGroup, onSelect }: { groups: Array<{ name: string; sectors: ValueSectorScore[]; macroScore: number | null; candidateRank: number }>; activeGroup: string; onSelect: (group: string) => void }) {
  const total = groups.reduce((sum, group) => sum + group.sectors.length, 0);
  return <section className="border-b xl:flex xl:min-h-0 xl:flex-col xl:border-b-0 xl:border-r"><header className="shrink-0 border-b p-4"><div className="text-xs font-semibold tracking-wide text-primary">第一步</div><div className="mt-1 text-base font-semibold">选择产业方向</div><div className="mt-1 text-sm text-muted-foreground">按产业归类；宏观只作背景参考</div></header><div className="grid grid-cols-2 gap-1.5 p-2 sm:grid-cols-3 xl:min-h-0 xl:flex-1 xl:grid-cols-none xl:space-y-1 xl:overflow-y-auto">{[{ name: "全部", sectors: Array(total), macroScore: null, candidateRank: 1 }, ...groups].map((group) => <button key={group.name} onClick={() => onSelect(group.name)} className={cn("w-full rounded-lg px-3 py-3 text-left transition hover:bg-muted", activeGroup === group.name && "bg-primary text-primary-foreground hover:bg-primary")}><div className="flex items-center justify-between gap-2"><span className="truncate text-[15px] font-medium">{group.name}</span>{group.macroScore != null ? <span className="text-xs font-medium">宏观 {score(group.macroScore)}</span> : null}</div><div className={cn("mt-1 text-xs text-muted-foreground", activeGroup === group.name && "text-primary-foreground/70")}>{group.name === "全部" ? `${total} 个赛道` : `${group.sectors.length} 个 · 最佳候选 #${group.candidateRank}`}</div></button>)}</div></section>;
}

function TrackPane({ sectors, query, setQuery, rankView, setRankView }: { sectors: ValueSectorScore[]; query: string; setQuery: (value: string) => void; rankView: "macro" | "overall"; setRankView: (value: "macro" | "overall") => void }) {
  const { selectedTrackId, selectTrack, workbench } = useValueWorkspace();
  const detail = workbench?.sector_scores?.find((item) => item.sector_code === selectedTrackId);
  return <section id="value-tracks" className="min-w-0 border-b xl:flex xl:min-h-0 xl:flex-col xl:border-b-0 xl:border-r"><header className="shrink-0 border-b p-4"><div className="flex items-baseline justify-between"><div><div className="text-xs font-semibold tracking-wide text-primary">第二步</div><div className="mt-1 text-base font-semibold">候选赛道池</div></div><span className="text-sm text-muted-foreground">{sectors.length} 个</span></div><div className="mt-2 text-xs text-muted-foreground">排序用于展开龙头研究；宏观仅作背景观察。</div><CandidateTrackLimitControl /><button onClick={() => selectTrack(ALL_TRACK_ID)} className={cn("mt-3 w-full rounded-lg border px-3 py-2.5 text-left text-sm transition hover:bg-muted", selectedTrackId === ALL_TRACK_ID ? "border-primary bg-primary/10 text-primary" : "bg-background")}><b>全部候选赛道</b><span className="ml-2 text-xs text-muted-foreground">查看全量龙头研究优先队列</span></button><div className="mt-3 grid grid-cols-2 rounded-lg bg-muted p-1 text-sm"><button onClick={() => setRankView("overall")} className={cn("rounded-md px-2 py-2", rankView === "overall" && "bg-background font-medium shadow-sm")}>候选池排序</button><button onClick={() => setRankView("macro")} className={cn("rounded-md px-2 py-2", rankView === "macro" && "bg-background font-medium shadow-sm")}>宏观观察</button></div><label className="relative mt-2 block"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索细分赛道" className="h-10 w-full rounded-md border bg-background pl-9 pr-2 text-sm" /></label></header><div className="min-h-0 flex-1 divide-y overflow-y-auto">{sectors.map((sector) => { const activeScore = rankView === "macro" ? sector.macro_fit : sector.score; return <button key={sector.sector_code} onClick={() => selectTrack(sector.sector_code)} className={cn("grid w-full grid-cols-[32px_1fr_86px] items-center gap-3 px-4 py-3.5 text-left hover:bg-muted/60", sector.sector_code === selectedTrackId && "bg-primary/10")}><span className="font-mono text-sm text-muted-foreground">{rankView === "macro" ? sector.macro_rank : sector.rank}</span><span className="min-w-0"><span className="block truncate text-[15px] font-medium">{sector.sector_name}</span><span className="block truncate text-xs text-muted-foreground">候选 {sector.rank} · 宏观观察 {sector.macro_rank}</span></span><span className="text-right"><span className="block text-xs font-medium">{scoreLevel(activeScore)}</span><span className="block font-mono text-xs text-muted-foreground">指数 {score(activeScore)}</span></span></button>; })}{!sectors.length ? <Empty label="没有符合条件的赛道" /> : null}</div><SectorJudgement detail={detail} /></section>;
}

function SectorJudgement({ detail }: { detail?: ValueSectorScore }) {
  const components = detail?.components?.length ? detail.components : Object.entries(detail?.component_scores || {}).map(([name, normalized_value]) => ({ name, normalized_value, raw_value: null, weight: 0, contribution: null }));
  const rankedComponents = Object.entries(detail?.component_scores || {}).filter((entry): entry is [string, number] => entry[1] != null).sort((left, right) => right[1] - left[1]);
  const strengths = rankedComponents.slice(0, 2);
  const weakest = rankedComponents[rankedComponents.length - 1];
  if (!detail) return <div className="shrink-0 border-t bg-muted/20 px-3 py-2 text-xs text-muted-foreground">选择一个细分赛道后查看赛道判断</div>;
  return <div className="shrink-0 border-t bg-muted/20 p-3"><div className="flex items-center justify-between gap-2"><div className="text-sm font-semibold">赛道判断</div><span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", detail.rank <= 40 ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : detail.rank <= 80 ? "bg-amber-500/10 text-amber-700 dark:text-amber-400" : "bg-muted text-muted-foreground")}>{sectorVerdict(detail)}</span></div><div className="mt-2 grid grid-cols-3 divide-x rounded-md border bg-background/70 text-xs"><div className="px-2 py-2"><div className="text-muted-foreground">宏观适配</div><div className="mt-0.5 font-semibold">{score(detail.macro_fit)}</div></div><div className="px-2 py-2"><div className="text-muted-foreground">综合吸引力</div><div className="mt-0.5 font-semibold">{score(detail.score)}</div></div><div className="px-2 py-2"><div className="text-muted-foreground">数据完整度</div><div className="mt-0.5 font-semibold">{percent(detail.coverage)}</div></div></div><details className="mt-2 rounded-md border bg-background/50 text-xs"><summary className="cursor-pointer px-2.5 py-2 text-muted-foreground">展开研究解释</summary><div className="grid gap-2 border-t p-2.5 leading-4"><div className="rounded bg-emerald-500/5 p-2"><div className="font-medium text-emerald-700 dark:text-emerald-400">为什么值得看</div>{strengths.map(([name, value]) => <div key={name} className="mt-1"><b>{SECTOR_EVIDENCE_LABELS[name] || name}：{scoreLevel(value)}</b><span className="text-muted-foreground"> · {sectorEvidence(detail, name)}</span></div>)}</div><div className="rounded bg-amber-500/5 p-2"><div className="font-medium text-amber-700 dark:text-amber-400">需要注意</div>{weakest ? <div className="mt-1"><b>{SECTOR_EVIDENCE_LABELS[weakest[0]] || weakest[0]}：{scoreLevel(weakest[1])}</b><span className="text-muted-foreground"> · {sectorEvidence(detail, weakest[0])}</span></div> : null}{detail.missing_fields.map((name) => <div key={name} className="mt-1"><b>{SECTOR_EVIDENCE_LABELS[name] || name}：暂不可判断</b></div>)}</div><details className="rounded border"><summary className="cursor-pointer px-2 py-1.5 text-muted-foreground">评分构成与来源</summary><div className="grid gap-2 p-2 sm:grid-cols-2">{components.map((component) => <div key={component.name} className="rounded bg-muted/40 p-2"><div className="flex justify-between gap-2"><span>{SECTOR_COMPONENT_LABELS[component.name] || component.name}</span><b>{score(component.normalized_value)}</b></div><div className="mt-1 h-1 overflow-hidden rounded bg-muted"><div className="h-full bg-primary" style={{ width: `${Math.max(0, Math.min(100, component.normalized_value || 0))}%` }} /></div></div>)}<div className="text-muted-foreground sm:col-span-2">成分覆盖 {percent(detail.member_coverage)} · {detail.formula_version} · 缺失 {detail.missing_fields.map((item) => SECTOR_COMPONENT_LABELS[item] || item).join("、") || "无"}</div></div></details></div></details></div>;
}

function DecisionPane() {
  const { selectedTrackId, candidateTrackLimit, leaders, selectedSymbols, toggleSymbol } = useValueWorkspace();
  const isTotalPool = selectedTrackId === ALL_TRACK_ID;
  const [visibleCount, setVisibleCount] = useState(12);
  useEffect(() => setVisibleCount(12), [selectedTrackId]);
  const visibleLeaders = leaders.slice(0, isTotalPool ? visibleCount : 5);
  return <section className="min-w-0 xl:flex xl:min-h-0 xl:flex-col"><div id="value-leaders" className="xl:flex xl:min-h-0 xl:flex-1 xl:flex-col"><div className="flex shrink-0 items-center justify-between border-b bg-muted/20 px-4 py-3"><div><span className="text-base font-semibold">{isTotalPool ? "全部候选赛道龙头池" : "行业内优先研究公司"}</span><span className="ml-2 text-sm text-muted-foreground" title={isTotalPool ? `前 ${candidateTrackLimit} 个候选赛道的全部可评分公司统一竞争，只保留综合评分最高的 ${candidateTrackLimit * 5} 家` : "评分只用于同一行业内部比较"}>{isTotalPool ? "龙头综合评分排序" : "行业内比较"}</span></div><span className="text-sm text-muted-foreground">{isTotalPool ? `共 ${leaders.length} 家` : `前 ${Math.min(leaders.length, 5)} 家`}</span></div>{isTotalPool ? <div className="border-b bg-amber-500/5 px-4 py-2 text-xs text-amber-800 dark:text-amber-300">候选赛道数量只决定容量：前 {candidateTrackLimit} 个赛道 → 全池前 {candidateTrackLimit * 5} 家。全部可评分公司按龙头综合评分统一竞争，不给任何赛道固定 5 个名额。</div> : null}<div className="max-h-[560px] divide-y overflow-y-auto xl:min-h-0 xl:max-h-none xl:flex-1">{visibleLeaders.map((leader, index) => <LeaderRow key={`${leader.sector_code}:${leader.symbol}`} leader={leader} selected={selectedSymbols.includes(leader.symbol)} poolRank={isTotalPool ? index + 1 : undefined} onToggle={() => toggleSymbol(leader.symbol)} />)}{!leaders.length ? <Empty label={selectedTrackId ? "该赛道暂无可用龙头" : "请先选择赛道"} /> : null}{isTotalPool && visibleCount < leaders.length ? <button onClick={() => setVisibleCount((current) => Math.min(current + 24, leaders.length))} className="w-full border-t px-4 py-3 text-sm text-primary hover:bg-muted/50">显示更多龙头（已显示 {visibleCount}/{leaders.length}）</button> : null}</div></div></section>;
}

function LeaderRow({ leader, selected, poolRank, onToggle }: { leader: ValueLeaderScore; selected: boolean; poolRank?: number; onToggle: () => void }) {
  const location = useLocation();
  const dimensions = Object.entries(leader.component_scores).filter((entry): entry is [string, number] => entry[1] != null).sort((left, right) => right[1] - left[1]);
  const strongest = dimensions[0];
  const weakest = dimensions[dimensions.length - 1];
  const metrics = leaderMetrics(leader);
  const companyPath = companyResearchPath(leader.symbol, { from: `${location.pathname}${location.search}`, fromLabel: `${leader.sector_name}龙头`, sectorCode: leader.sector_code, sectorName: leader.sector_name });
  return <div className={cn("grid grid-cols-[30px_minmax(140px,180px)_minmax(220px,1fr)_100px] items-center gap-3 px-4 py-3.5 hover:bg-muted/40", selected && "bg-primary/5")}><input type="checkbox" checked={selected} onChange={onToggle} aria-label={`选择 ${leader.name}`} /><div className="min-w-0"><Link to={companyPath} className="block truncate text-[15px] font-medium text-primary hover:underline" title="查看公司详情">{leader.name}</Link><div className="font-mono text-xs text-muted-foreground">{leader.symbol}</div>{poolRank ? <div className="mt-0.5 truncate text-xs text-muted-foreground">{leader.sector_name} · 行业第 {leader.rank}</div> : null}</div><div className="min-w-0 text-[13px] leading-5"><div className="truncate text-emerald-700 dark:text-emerald-400" title={strongest ? LEADER_STRENGTH_TEXT[strongest[0]] : undefined}>优势：{strongest ? LEADER_STRENGTH_TEXT[strongest[0]] : "证据不足"}</div><div className="truncate text-amber-700 dark:text-amber-400" title={weakest ? LEADER_WEAKNESS_TEXT[weakest[0]] : undefined}>关注：{weakest ? LEADER_WEAKNESS_TEXT[weakest[0]] : "证据不足"}</div><div className="truncate text-muted-foreground">{metrics.length ? metrics.map(([label, value]) => `${label} ${value}`).join(" · ") : "暂无可读原始指标"}</div></div><div className="text-right"><div className="text-xs text-muted-foreground">{poolRank ? `全池第 ${poolRank}` : `行业第 ${leader.rank}`}</div><div className="text-sm font-semibold">{scoreLevel(leader.score)}</div><div className="font-mono text-xs text-muted-foreground">指数 {score(leader.score)} · {6 - leader.missing_fields.length}/6维</div></div></div>;
}

function ResearchSelectionBar() {
  const { selectedSymbols, leaders, clearSelection, startResearch, batches, selectedTrackId } = useValueWorkspace();
  const selected = leaders.filter((leader) => selectedSymbols.includes(leader.symbol));
  const newest = batches.find((batch) => batch.track_id === selectedTrackId);
  if (!selectedSymbols.length) return null;
  return <aside className="sticky bottom-3 z-10 rounded-xl border border-primary/30 bg-card/95 p-3.5 shadow-lg backdrop-blur"><div className="flex flex-wrap items-center gap-3"><div className="min-w-[150px]"><div className="text-sm font-semibold text-primary">下一步 · 深度研究</div><div className="mt-0.5 text-base font-semibold">已选 {selectedSymbols.length} 家公司</div></div><div className="flex min-w-0 flex-1 flex-wrap gap-1.5">{selected.slice(0, 6).map((leader) => <span key={leader.symbol} className="rounded-full bg-muted px-2.5 py-1 text-sm">{leader.name}</span>)}{selected.length > 6 ? <span className="rounded-full bg-muted px-2.5 py-1 text-sm">+{selected.length - 6}</span> : null}</div><button onClick={clearSelection} className="text-sm text-muted-foreground hover:text-foreground">清空</button><button onClick={() => void startResearch()} className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"><ClipboardCheck className="h-4 w-4" />研究所选公司</button>{newest ? <Link to="/value/research" className="text-sm text-primary hover:underline">最近批次 {newest.completed}/{newest.total}</Link> : null}</div></aside>;
}

function PipelineSummary() {
  const { monitors, events, activeUniverse, signals } = useValueWorkspace();
  const summaries = [
    ["活动研究宇宙", activeUniverse?.company_count || 0, activeUniverse ? `${activeUniverse.track_count} 个赛道已冻结` : "尚未冻结候选池", "/value/research"],
    ["首次建档", activeUniverse?.latest_operation?.completed || 0, activeUniverse?.latest_operation ? `共 ${activeUniverse.latest_operation.total} 家` : "等待启动", "/value/research"],
    ["人工监控", monitors.filter((monitor) => monitor.status === "active").length, `${signals.length} 条最近规则结论`, "/value/plans"],
    ["待复核事件", events.filter((event) => event.status === "open").length, "入场、风险、退出与数据问题", "/value/monitor"],
  ];
  return <section className="grid gap-3 md:grid-cols-4">{summaries.map(([title, value, help, to]) => <Link to={String(to)} key={String(title)} className="rounded-xl border bg-card p-4 shadow-sm transition hover:border-primary/40 hover:bg-muted/20"><div className="flex items-center justify-between text-sm text-muted-foreground"><span>{title}</span><ChevronRight className="h-4 w-4" /></div><div className="mt-1 font-mono text-3xl font-semibold">{value}</div><div className="mt-1 text-sm text-muted-foreground">{help}</div></Link>)}</section>;
}

export function ValueProfiles() {
  const { profiles, workbench, selectProfile, reload } = useValueWorkspace();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("新的组合方案");
  const [mode, setMode] = useState<"single" | "composite">("composite");
  const [weights, setWeights] = useState<Record<string, number>>({ policy_cycle: .25, economic_cycle: .25, liquidity: .25, earnings_climate: .25 });
  const save = async () => { try { const profile = await api.createValueProfile({ name, mode, model_weights: weights }); toast.success("计算方案已保存"); setCreating(false); await reload(); selectProfile(profile.id); } catch (error) { toast.error(error instanceof Error ? error.message : "方案保存失败"); } };
  return <div className="space-y-5"><PageHeader eyebrow="CALCULATION PROFILES" title="计算方案" description="方案保存权重和版本。组合方案只在数据覆盖达到要求时计入有效模型，缺失数据不会补 0。" actions={<button onClick={() => setCreating((value) => !value)} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><Plus className="h-4 w-4" />新建方案</button>} />
    {creating ? <ProfileEditor name={name} setName={setName} mode={mode} setMode={setMode} weights={weights} setWeights={setWeights} onCancel={() => setCreating(false)} onSave={() => void save()} /> : null}
    <div className="grid gap-4 lg:grid-cols-2">{profiles.map((profile) => <button key={profile.id} onClick={() => selectProfile(profile.id)} className={cn("rounded-xl border bg-card p-5 text-left shadow-sm transition hover:border-primary/50", workbench?.profile.id === profile.id && "ring-1 ring-primary")}><div className="flex justify-between gap-3"><div><div className="font-semibold">{profile.name}</div><div className="mt-1 text-xs text-muted-foreground">{profile.mode === "single" ? "单模型" : "加权组合"} · 版本 v{profile.version}</div></div>{profile.is_default ? <span className="h-fit rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">默认</span> : null}</div><div className="mt-4 grid grid-cols-2 gap-2">{Object.entries(profile.model_weights).map(([model, weight]) => <div key={model} className="rounded-md bg-muted/60 px-3 py-2"><div className="text-[11px] text-muted-foreground">{MODEL_LABELS[model]}</div><div className="mt-1 font-mono text-sm font-semibold">{percent(weight)}</div></div>)}</div></button>)}</div></div>;
}

function ProfileEditor({ name, setName, mode, setMode, weights, setWeights, onCancel, onSave }: { name: string; setName: (v: string) => void; mode: "single" | "composite"; setMode: (v: "single" | "composite") => void; weights: Record<string, number>; setWeights: (v: Record<string, number>) => void; onCancel: () => void; onSave: () => void }) {
  return <section className="rounded-xl border bg-card p-5 shadow-sm"><div className="grid gap-4 lg:grid-cols-[1fr_160px]"><label className="text-sm">方案名称<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 block h-9 w-full rounded-lg border bg-background px-3" /></label><label className="text-sm">计算方式<select value={mode} onChange={(event) => { const next = event.target.value as "single" | "composite"; setMode(next); if (next === "single") setWeights({ policy_cycle: 1 }); }} className="mt-1 block h-9 w-full rounded-lg border bg-background px-3"><option value="composite">加权组合</option><option value="single">单模型</option></select></label></div><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{Object.entries(MODEL_LABELS).map(([model, title]) => <label key={model} className={cn("rounded-lg border p-3", mode === "single" && weights[model] !== 1 && "opacity-50")}><div className="text-xs text-muted-foreground">{title}</div><input type="number" min="0" step="0.05" value={weights[model] || 0} onChange={(event) => { const value = Number(event.target.value); setWeights(mode === "single" ? { [model]: 1 } : { ...weights, [model]: value }); }} className="mt-2 h-8 w-full rounded border bg-background px-2 font-mono text-sm" /></label>)}</div><div className="mt-4 flex justify-end gap-2"><button onClick={onCancel} className="rounded-lg border px-3 py-2 text-sm">取消</button><button onClick={onSave} className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">保存方案</button></div></section>;
}

type ResearchLeaderCompany = {
  symbol: string;
  name: string;
  leaders: Level3Leader[];
  analysis: ValueCompanyAnalysis | null;
};

export function ValueResearchQueue() {
  const { batches, universes, universeAnalysis } = useValueWorkspace();
  const [params, setParams] = useSearchParams();
  const [archive, setArchive] = useState<ValueCompanyArchive | null>(null);
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [leaders, setLeaders] = useState<Level3Leader[]>([]);
  const [leadersAsOf, setLeadersAsOf] = useState<string | null>(null);
  const [leadersLoading, setLeadersLoading] = useState(true);
  const [leadersError, setLeadersError] = useState("");
  const selectedJobId = params.get("job");
  const selectedSymbol = params.get("symbol") || "";
  const selected = batches.flatMap((batch) => batch.jobs.map((job) => ({ batch, job }))).find((item) => item.job.id === selectedJobId);
  const archiveUniverse = universes.find((item) => item.id === universeAnalysis?.universe_id) || null;
  const leaderCompanies = useMemo<ResearchLeaderCompany[]>(() => {
    const analysisBySymbol = new Map((universeAnalysis?.items || []).map((item) => [item.symbol, item]));
    const bySymbol = new Map<string, ResearchLeaderCompany>();
    for (const leader of leaders) {
      const existing = bySymbol.get(leader.stock_code);
      if (existing) existing.leaders.push(leader);
      else bySymbol.set(leader.stock_code, { symbol: leader.stock_code, name: leader.stock_name, leaders: [leader], analysis: analysisBySymbol.get(leader.stock_code) || null });
    }
    return [...bySymbol.values()];
  }, [leaders, universeAnalysis?.items]);
  const selectedCompany = leaderCompanies.find((item) => item.symbol === selectedSymbol) || null;
  const selectedAnalysis = selectedCompany?.analysis || universeAnalysis?.items.find((item) => item.symbol === selectedSymbol) || null;
  const drawerAnalysis = selectedSymbol && archive?.analysis?.symbol === selectedSymbol
    ? archive.analysis
    : selectedAnalysis;
  const visibleCompanies = [...leaderCompanies.filter((item) => {
    const state = item.analysis?.current_state || "not_archived";
    const search = `${item.name} ${item.symbol} ${item.leaders.map((leader) => `${leader.level1_name} ${leader.level2_name} ${leader.level3_name}`).join(" ")}`.toLowerCase();
    return (stateFilter === "all" || state === stateFilter) && search.includes(query.trim().toLowerCase());
  })].sort((left, right) => {
    const leaderScore = (item: ResearchLeaderCompany) => Math.max(...item.leaders.map((leader) => Number(leader.leader_score) || 0), 0);
    const leaderCoverage = (item: ResearchLeaderCompany) => Math.max(...item.leaders.map((leader) => Number(leader.coverage) || 0), 0);
    return leaderScore(right) - leaderScore(left)
      || leaderCoverage(right) - leaderCoverage(left)
      || left.symbol.localeCompare(right.symbol);
  });
  const stateFilters = [
    ["all", "全部龙头", leaderCompanies.length],
    ["not_archived", "待建档", leaderCompanies.filter((item) => !item.analysis).length],
    ["stale", "待更新", leaderCompanies.filter((item) => item.analysis?.current_state === "stale").length],
    ["data_insufficient", "数据不足", leaderCompanies.filter((item) => item.analysis?.current_state === "data_insufficient").length],
    ["watching", "监控中", leaderCompanies.filter((item) => item.analysis?.current_state === "watching").length],
    ["entry_candidate", "入场候选", leaderCompanies.filter((item) => item.analysis?.current_state === "entry_candidate").length],
  ].filter(([, , count]) => Number(count) > 0 || stateFilter === "all") as Array<[string, string, number]>;
  useEffect(() => {
    let cancelled = false;
    setLeadersLoading(true); setLeadersError("");
    api.getAllLevel3Leaders(2).then((value) => {
      if (cancelled) return;
      setLeaders(Object.values(value.items).flat());
      setLeadersAsOf(value.as_of);
      setLeadersError(value.snapshot_status === "ready" ? "" : "三级行业龙头快照尚未生成。");
    }).catch((error) => {
      if (!cancelled) setLeadersError(error instanceof Error ? error.message : "读取三级行业龙头快照失败。");
    }).finally(() => { if (!cancelled) setLeadersLoading(false); });
    return () => { cancelled = true; };
  }, []);
  useEffect(() => { if (!selectedSymbol) { setArchive(null); return; } let cancelled = false; api.getValueCompanyArchive(selectedSymbol).then((value) => { if (!cancelled) setArchive(value); }).catch(() => { if (!cancelled) setArchive(null); }); return () => { cancelled = true; }; }, [selectedSymbol]);
  const selectCompany = (symbol: string) => setParams((current) => { const next = new URLSearchParams(current); next.set("symbol", symbol); next.delete("job"); return next; });
  const closeCompany = () => setParams((current) => { const next = new URLSearchParams(current); next.delete("symbol"); next.delete("job"); return next; });
  const closeJob = () => setParams((current) => { const next = new URLSearchParams(current); next.delete("job"); return next; });
  return <div className="space-y-4">
    <ResearchDesk archive={archive} selected={selected} selectedSymbol={selectedSymbol} selectedCompany={selectedCompany} drawerAnalysis={drawerAnalysis} visibleCompanies={visibleCompanies} sourceAsOf={leadersAsOf} operation={archiveUniverse?.latest_operation || null} loading={leadersLoading} sourceError={leadersError} query={query} stateFilter={stateFilter} stateFilters={stateFilters} onQueryChange={setQuery} onStateFilterChange={setStateFilter} onSelectCompany={selectCompany} onCloseCompany={closeCompany} onCloseJob={closeJob} />
  </div>;
}

function ResearchDesk({ archive, selected, selectedSymbol, selectedCompany, drawerAnalysis, visibleCompanies, sourceAsOf, operation, loading, sourceError, query, stateFilter, stateFilters, onQueryChange, onStateFilterChange, onSelectCompany, onCloseCompany, onCloseJob }: {
  archive: ValueCompanyArchive | null;
  selected?: { batch: CompanyResearchBatch; job: CompanyResearchJob };
  selectedSymbol: string;
  selectedCompany: ResearchLeaderCompany | null;
  drawerAnalysis: ValueCompanyAnalysis | null;
  visibleCompanies: ResearchLeaderCompany[];
  sourceAsOf: string | null;
  operation: ValueIncrementalRun | null;
  loading: boolean;
  sourceError: string;
  query: string;
  stateFilter: string;
  stateFilters: Array<[string, string, number]>;
  onQueryChange: (value: string) => void;
  onStateFilterChange: (value: string) => void;
  onSelectCompany: (symbol: string) => void;
  onCloseCompany: () => void;
  onCloseJob: () => void;
}) {
  const location = useLocation();
  const [drawerTab, setDrawerTab] = useState<"overview" | "analysis" | "records">("overview");
  useEffect(() => { setDrawerTab("overview"); }, [selectedSymbol]);
  return <section className="overflow-hidden rounded-xl border bg-card shadow-sm">
     <div className="border-b bg-muted/20 p-3"><div className="mb-3 flex flex-wrap items-end justify-between gap-2"><div><div className="font-semibold">行业龙头研究池</div><p className="mt-0.5 text-xs text-muted-foreground">与“行业龙头”页使用同一份三级行业龙头快照；研究档案和监控状态仅作为附加信息。</p></div><div className="text-xs text-muted-foreground">{loading ? "正在同步龙头快照…" : `快照日期：${sourceAsOf || "—"}`}</div></div>{operation ? <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-primary/20 bg-primary/[0.035] px-3 py-2 text-sm"><div><span className="font-medium">首次建档</span><span className="ml-2 text-muted-foreground">{operation.completed}/{operation.total} 家已完成 · 失败 {operation.failed}</span></div><StatusBadge status={operation.status} /></div> : null}{sourceError ? <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm text-amber-800 dark:text-amber-300">{sourceError}</div> : null}<label className="relative block"><Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索公司、代码或三级行业" className="h-9 w-full rounded-lg border bg-background py-2 pl-9 pr-3 text-sm" /></label><div className="mt-2 flex flex-wrap gap-1.5">{stateFilters.map(([key, label, count]) => <button key={key} onClick={() => onStateFilterChange(key)} className={cn("rounded-full border px-2.5 py-1 text-xs", stateFilter === key && "border-primary bg-primary/10 text-primary")}>{label} {count}</button>)}</div></div>
    <div className="space-y-3 p-3 sm:p-4">{visibleCompanies.map((company, index) => {
      const selectedCompany = company.symbol === selectedSymbol;
      const leaderScore = Math.max(...company.leaders.map((item) => Number(item.leader_score) || 0), 0);
      const state = company.analysis?.current_state || "not_archived";
      return <article key={company.symbol} className={cn("overflow-hidden rounded-xl border transition", selectedCompany ? "border-primary/50 bg-primary/[0.02] shadow-sm" : "hover:border-primary/30")}><button onClick={() => onSelectCompany(company.symbol)} className="grid w-full gap-3 p-4 text-left sm:grid-cols-[52px_minmax(0,1fr)_auto] sm:items-center"><div className="font-mono text-lg font-semibold text-muted-foreground">#{index + 1}</div><div className="min-w-0"><div className="flex flex-wrap items-center gap-x-2 gap-y-1"><span className="font-semibold">{company.name}</span><span className="font-mono text-xs text-muted-foreground">{company.symbol}</span><StatusBadge status={state} /></div><div className="mt-1 truncate text-xs text-muted-foreground">{company.leaders.map((item) => `${item.level3_name} · 行业第 ${item.leader_rank}`).join(" / ")}</div></div><div className="flex items-center justify-between gap-3 sm:justify-end"><div className="text-right"><div className="text-xs text-muted-foreground">行业内评分</div><div className="font-mono text-sm font-semibold">{leaderScore.toFixed(1)}</div></div><ChevronRight className="h-5 w-5 text-muted-foreground" /></div></button></article>;
    })}{loading ? <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取行业龙头池…</div> : !visibleCompanies.length ? <Empty label="没有符合条件的行业龙头公司。" /> : null}</div>
    {selectedSymbol ? <><button type="button" aria-label="关闭公司详情" className="fixed inset-0 z-40 cursor-default bg-black/30 backdrop-blur-[1px]" onClick={onCloseCompany} /><aside role="dialog" aria-modal="true" aria-label="公司研究详情" className="fixed inset-y-0 right-0 z-50 flex w-full max-w-3xl flex-col border-l border-border bg-background shadow-2xl"><header className="border-b border-border px-4 py-4 sm:px-6"><div className="flex items-center justify-between gap-4"><div className="min-w-0"><p className="text-xs text-muted-foreground">公司研究详情</p><h2 className="truncate text-lg font-semibold">{drawerAnalysis ? `${drawerAnalysis.name} · ${drawerAnalysis.symbol}` : selectedCompany ? `${selectedCompany.name} · ${selectedCompany.symbol}` : "正在加载公司档案"}</h2>{selectedCompany ? <p className="mt-1 truncate text-xs text-muted-foreground">{selectedCompany.leaders.map((leader) => `${leader.level1_name} / ${leader.level2_name} / ${leader.level3_name} · 行业第 ${leader.leader_rank}`).join("；")}</p> : null}</div><div className="flex shrink-0 items-center gap-2">{(drawerAnalysis || selectedCompany) ? <Link to={companyResearchPath((drawerAnalysis || selectedCompany)!.symbol, { from: `${location.pathname}${location.search}`, fromLabel: "公司研究" }, "research")} className="rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">进入公司详情</Link> : null}<button type="button" aria-label="关闭公司详情" onClick={onCloseCompany} className="rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">关闭</button></div></div>{drawerAnalysis ? <div role="tablist" aria-label="公司研究内容" className="mt-4 flex gap-1 overflow-x-auto"><button role="tab" aria-selected={drawerTab === "overview"} onClick={() => setDrawerTab("overview")} className={cn("shrink-0 rounded-md px-3 py-1.5 text-sm", drawerTab === "overview" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>研究结论</button><button role="tab" aria-selected={drawerTab === "analysis"} onClick={() => setDrawerTab("analysis")} className={cn("shrink-0 rounded-md px-3 py-1.5 text-sm", drawerTab === "analysis" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>七维分析</button><button role="tab" aria-selected={drawerTab === "records"} onClick={() => setDrawerTab("records")} className={cn("shrink-0 rounded-md px-3 py-1.5 text-sm", drawerTab === "records" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>资料与事件</button></div> : null}</header><div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">{drawerAnalysis ? <>{drawerTab === "overview" ? <CompanyResearchOverview analysis={drawerAnalysis} /> : null}{drawerTab === "analysis" ? <CompanyAnalysisDetail analysis={drawerAnalysis} /> : null}{drawerTab === "records" ? <div className="space-y-4">{archive?.analysis?.symbol === selectedSymbol ? <section className="rounded-xl border bg-card"><div className="border-b px-4 py-3 text-sm font-medium">资料来源、档案版本与历史变化</div><CompanyArchiveSources archive={archive} /></section> : <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">资料档案正在加载或尚未生成。</div>}{selected?.job.symbol === selectedSymbol ? <ResearchArchiveDetail batch={selected.batch} job={selected.job} onClose={onCloseJob} /> : <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">没有关联的批量研究任务记录。</div>}</div> : null}</> : <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">该公司已在行业龙头池中，尚未生成完整研究档案；可进入公司详情查看财务数据并发起分析。</div>}</div></aside></> : null}
  </section>;
}

function CompanyArchiveSources({ archive }: { archive: ValueCompanyArchive }) {
  const latest = archive.snapshots[0];
  return <div className="border-t p-4"><div className="grid gap-3 sm:grid-cols-4"><MetricCard label="档案版本" value={latest ? `v${latest.version}` : "待生成"} /><MetricCard label="完整度" value={latest ? percent(latest.completeness) : "—"} /><MetricCard label="数据截至" value={latest?.data_as_of || "—"} /><MetricCard label="资料状态" value={latest ? labelStatus(latest.status) : "待生成"} /></div><div className="mt-4 grid gap-3 lg:grid-cols-2"><article className="rounded-lg border p-4"><div className="font-semibold">来源与缺失</div><div className="mt-3 flex flex-wrap gap-2">{latest?.sources.map((source, index) => <span key={`${source}-${index}`} className="rounded-full bg-muted px-2.5 py-1 text-xs">{source}</span>)}</div><p className="mt-3 text-sm text-muted-foreground">缺失：{latest?.missing_fields.join("、") || "无"}</p></article><article className="rounded-lg border p-4"><div className="font-semibold">档案与事件</div><p className="mt-3 text-sm text-muted-foreground">证据 {archive.evidence.length} 条 · 监控事件 {archive.events.length} 条</p><p className="mt-2 text-xs text-muted-foreground">{latest && Object.keys(latest.diff).length ? `本次更新涉及 ${Object.keys(latest.diff).slice(0, 5).join("、")}` : "首次建档或本次输入没有变化。"}</p></article></div></div>;
}

function CompanyResearchOverview({ analysis }: { analysis: ValueCompanyAnalysis }) {
  const metrics = [
    ["最新价", analysis.metrics.price], ["PE(TTM)", analysis.metrics.pe_ttm], ["PB(MRQ)", analysis.metrics.pb_mrq], ["ROE", analysis.metrics.roe],
  ].filter((item) => item[1] !== null && item[1] !== undefined);
  return <section className="space-y-4"><div className="rounded-xl border border-primary/30 bg-card p-4 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-xl font-semibold">{analysis.name} · {analysis.symbol}</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{analysis.conclusion}</p></div><span className="rounded-full bg-primary/10 px-3 py-1.5 text-sm font-medium text-primary">{analysisStateLabel(analysis.current_state)}</span></div><div className="mt-4 grid gap-2 sm:grid-cols-3"><MetricCard label="数据状态" value={analysisStateLabel(analysis.data_status)} /><MetricCard label="研究状态" value={analysisStateLabel(analysis.research_state)} /><MetricCard label="决策状态" value={analysisStateLabel(analysis.decision_status)} /></div>{metrics.length ? <div className="mt-3 grid gap-2 grid-cols-2 sm:grid-cols-4">{metrics.map(([label, value]) => <div key={String(label)} className="rounded-lg bg-muted/50 p-2.5"><div className="text-[11px] text-muted-foreground">{String(label)}</div><div className="mt-1 font-mono text-sm font-semibold">{Number(value).toFixed(2)}</div></div>)}</div> : null}</div><div className="grid gap-4 lg:grid-cols-2"><AnalysisList title="可验证事实" items={analysis.supporting_facts} empty="暂无可展示事实" tone="positive" /><AnalysisList title="风险与数据缺口" items={analysis.risk_facts} empty="暂无规则风险" tone="risk" /></div><div className="rounded-xl border border-dashed p-4 text-sm"><span className="font-medium">下一步：</span><span className="text-muted-foreground">{analysis.next_action}</span></div></section>;
}

function CompanyAnalysisDetail({ analysis }: { analysis: ValueCompanyAnalysis }) {
  const metrics = [
    ["最新价", analysis.metrics.price], ["PE(TTM)", analysis.metrics.pe_ttm], ["PB(MRQ)", analysis.metrics.pb_mrq],
    ["股息率", analysis.metrics.dividend_yield], ["营收同比", analysis.metrics.revenue_yoy], ["净利润同比", analysis.metrics.net_profit_yoy], ["ROE", analysis.metrics.roe],
  ].filter((item) => item[1] !== null && item[1] !== undefined);
  return <section className="rounded-xl border border-primary/30 bg-card p-4 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-xl font-semibold">{analysis.name} · {analysis.symbol}</h2><p className="mt-1 max-w-4xl text-sm text-muted-foreground">{analysis.conclusion}</p></div><span className="rounded-full bg-primary/10 px-3 py-1.5 text-sm font-medium text-primary">{analysisStateLabel(analysis.current_state)}</span></div><div className="mt-3 grid gap-2 sm:grid-cols-3 xl:grid-cols-6"><MetricCard label="数据" value={analysisStateLabel(analysis.data_status)} /><MetricCard label="研究" value={analysisStateLabel(analysis.research_state)} /><MetricCard label="估值" value={analysisStateLabel(analysis.valuation_status)} /><MetricCard label="技术" value={analysisStateLabel(analysis.technical_status)} /><MetricCard label="监控" value={analysisStateLabel(analysis.monitor_status)} /><MetricCard label="决策" value={analysisStateLabel(analysis.decision_status)} /></div>{metrics.length ? <div className="mt-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-7">{metrics.map(([label, value]) => <div key={String(label)} className="rounded-lg bg-muted/50 p-2.5"><div className="text-[11px] text-muted-foreground">{String(label)}</div><div className="mt-1 font-mono text-sm font-semibold">{Number(value).toFixed(2)}</div></div>)}</div> : null}<div className="mt-4"><div className="mb-2"><h3 className="font-semibold">七维全景分析</h3></div><div className="grid gap-3 xl:grid-cols-2">{analysis.dimensions.map((dimension) => <AnalysisDimensionCard key={dimension.key} dimension={dimension} />)}</div></div><details className="mt-3 rounded-lg border"><summary className="cursor-pointer px-4 py-3 text-sm font-medium">查看汇总证据、风险与最近变化</summary><div className="grid gap-3 border-t p-4 lg:grid-cols-3"><AnalysisList title="可验证事实" items={analysis.supporting_facts} empty="暂无可展示事实" tone="positive" /><AnalysisList title="风险与数据缺口" items={analysis.risk_facts} empty="暂无规则风险" tone="risk" /><AnalysisList title="最近变化" items={analysis.changes} empty="暂无变化" tone="neutral" /></div></details><div className="mt-3 rounded-lg border border-dashed p-3 text-sm"><span className="font-medium">下一步：</span><span className="text-muted-foreground">{analysis.next_action}</span></div></section>;
}

function AnalysisDimensionCard({ dimension }: { dimension: ValueAnalysisDimension }) {
  const metrics = Object.entries(dimension.metrics).filter(([, value]) => value !== null && value !== undefined && value !== "");
  const statusClass = dimension.status === "ready" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : dimension.status === "stale" ? "bg-amber-500/10 text-amber-700 dark:text-amber-300" : "bg-muted text-muted-foreground";
  return <article className="rounded-xl border p-4"><div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><h4 className="font-semibold">{dimension.label}</h4><span className={cn("rounded-full px-2 py-0.5 text-[11px]", statusClass)}>{analysisStateLabel(dimension.status)}</span></div><p className="mt-2 text-sm leading-6 text-muted-foreground">{dimension.summary}</p></div><div className="shrink-0 text-right"><div className="font-mono text-sm font-semibold">{percent(dimension.coverage)}</div><div className="text-[10px] text-muted-foreground">覆盖率</div></div></div>{metrics.length ? <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">{metrics.slice(0, 12).map(([label, value]) => <div key={label} className="rounded-lg bg-muted/50 p-2.5"><dt className="text-[10px] text-muted-foreground">{label}</dt><dd className="mt-1 font-mono text-xs font-semibold">{typeof value === "number" ? value.toFixed(2) : String(value)}</dd></div>)}</dl> : null}{dimension.facts.length || dimension.risks.length ? <div className="mt-3 space-y-1.5 text-xs">{dimension.facts.slice(0, 3).map((fact) => <div key={fact} className="text-emerald-700 dark:text-emerald-400">• {fact}</div>)}{dimension.risks.slice(0, 3).map((risk) => <div key={risk} className="text-amber-700 dark:text-amber-300">• {risk}</div>)}</div> : null}<div className="mt-3 border-t pt-3 text-[11px] text-muted-foreground"><div>数据截至：{dimension.data_as_of || "—"}</div><div className="mt-1">来源：{dimension.sources.join("、") || "尚未接入"}</div>{dimension.missing_fields.length ? <div className="mt-1 text-amber-700 dark:text-amber-300">待补：{dimension.missing_fields.join("、")}</div> : null}</div></article>;
}

function AnalysisList({ title, items, empty, tone }: { title: string; items: string[]; empty: string; tone: "positive" | "risk" | "neutral" }) {
  const toneClass = tone === "risk" ? "text-amber-700 dark:text-amber-300" : tone === "positive" ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground";
  return <article className="rounded-lg border p-4"><div className="font-semibold">{title}</div><div className={cn("mt-3 space-y-2 text-sm", toneClass)}>{items.length ? items.map((item) => <div key={item}>• {item}</div>) : <div>{empty}</div>}</div></article>;
}

function analysisStateLabel(value: string) {
  return ({ not_archived: "待建档", missing: "缺少档案", ready_for_monitoring: "待设置监控", not_monitored: "未配置监控", research_watching: "研究监控", decision_watching: "决策监控", fresh: "数据新鲜", paused: "已暂停", automatic_screen: "自动初筛", manual_confirmed: "人工确认", not_configured: "待配置", pending: "待分析", data_insufficient: "数据不足", unavailable: "未接入", stale: "数据过期", watching: "持续观察", entry_candidate: "入场候选", holding_review: "风险复核", exit_candidate: "退出/减仓候选", thesis_invalidated: "逻辑失效", ready: "资料完整", partial: "部分可用", review_required: "待复核", completed: "已完成", failed: "失败" } as Record<string, string>)[value] || value;
}

function MetricCard({ label, value }: { label: string; value: string }) { return <div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 font-medium">{value}</div></div>; }

function ResearchArchiveDetail({ batch, job, onClose }: { batch: CompanyResearchBatch; job: CompanyResearchJob; onClose: () => void }) {
  const [report, setReport] = useState<ResearchReport | null>(null);
  useEffect(() => { if (!job.report_id) { setReport(null); return; } let cancelled = false; api.getResearchReport(job.report_id).then((value) => { if (!cancelled) setReport(value); }).catch(() => { if (!cancelled) setReport(null); }); return () => { cancelled = true; }; }, [job.report_id]);
  const dcf = job.valuation?.dcf as Record<string, unknown> | undefined;
  const comparable = job.valuation?.comparable as Record<string, unknown> | undefined;
  return <section className="rounded-xl border border-primary/30 bg-card p-5 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-semibold tracking-wide text-primary">RESEARCH ARCHIVE</div><h2 className="mt-1 text-xl font-semibold">{job.name} · {job.symbol}</h2><p className="mt-1 text-sm text-muted-foreground">{job.message || "研究任务档案"}</p></div><div className="flex items-center gap-2"><StatusBadge status={job.status} /><button onClick={onClose} className="rounded-lg border px-3 py-2 text-sm">收起</button></div></div><div className="mt-4 grid gap-3 text-sm md:grid-cols-4"><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">计算方案</div><div className="mt-1 font-mono text-xs">{batch.profile_id}</div></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">赛道快照</div><div className="mt-1 font-mono text-xs">{batch.track_id}</div></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">策略运行</div><div className="mt-1 font-mono text-xs">{batch.engine_run_id}</div></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">数据日期</div><div className="mt-1 font-medium">{report?.data_as_of || "待生成"}</div></div></div><div className="mt-4 grid gap-3 lg:grid-cols-2"><article className="rounded-lg border p-4"><div className="font-semibold">自动研究交付</div><dl className="mt-3 space-y-2 text-sm"><div className="flex justify-between gap-4"><dt>事实底稿</dt><dd>{job.dossier_id ? "已生成" : "未生成"}</dd></div><div className="flex justify-between gap-4"><dt>研究报告</dt><dd>{job.report_id ? "已生成" : "未生成"}</dd></div><div className="flex justify-between gap-4"><dt>PIT 财务与护城河</dt><dd>待人工复核补充</dd></div><div className="flex justify-between gap-4"><dt>来源状态</dt><dd>{report?.source_status || "待读取"}</dd></div></dl><div className="mt-4 flex flex-wrap gap-2"><Link to={`/company/CN/${job.symbol}`} className="rounded-lg border px-3 py-2 text-sm">查看公司数据</Link>{job.report_id ? <Link to={`/ai/reports/${job.report_id}`} className="rounded-lg border px-3 py-2 text-sm text-primary">打开研究报告</Link> : null}</div></article><article className="rounded-lg border p-4"><div className="font-semibold">估值与复核</div><dl className="mt-3 space-y-2 text-sm"><div className="flex justify-between gap-4"><dt>可比 PE</dt><dd>{String(comparable?.pe_ttm ?? "—")} / 行业中位 {String(comparable?.peer_median_pe ?? "—")}</dd></div><div className="flex justify-between gap-4"><dt>可比 PB</dt><dd>{String(comparable?.pb_mrq ?? "—")} / 行业中位 {String(comparable?.peer_median_pb ?? "—")}</dd></div><div className="flex justify-between gap-4"><dt>DCF</dt><dd>{dcf?.status === "unavailable" ? "输入不足，未运行" : String(dcf?.status || "待计算")}</dd></div></dl><p className="mt-4 rounded bg-amber-500/5 p-3 text-xs text-amber-800 dark:text-amber-300">{String(dcf?.reason || "自动研究结果必须经人工复核后才可进入投委与入场监控。")}</p></article></div></section>;
}

export function ValueValuationCenter() {
  const { activeUniverse, universeAnalysis, monitors, reload } = useValueWorkspace();
  const [queue, setQueue] = useState<"all" | "priority" | "entry" | "review_exit">("all");
  const [working, setWorking] = useState<string | null>(null);
  const all = universeAnalysis?.items || [];
  const items = all.filter((item) => queue === "all" || queue === "priority" ? (queue === "all" || item.is_priority) : queue === "entry" ? item.decision_status === "entry_candidate" : ["holding_review", "exit_candidate", "thesis_invalidated"].includes(item.decision_status));
  const decisionSymbols = new Set(monitors.filter((item) => item.universe_id === activeUniverse?.id).map((item) => item.symbol));
  const markPriority = async (item: ValueCompanyAnalysis) => {
    if (!item.research_monitor_id) return;
    try { setWorking(item.symbol); await api.updateValueResearchMonitor(item.research_monitor_id, { is_priority: !item.is_priority }); await reload(); }
    catch (error) { toast.error(error instanceof Error ? error.message : "更新重点状态失败"); }
    finally { setWorking(null); }
  };
  const upgrade = async (item: ValueCompanyAnalysis) => {
    if (!activeUniverse || !item.valuation) return;
    try {
      setWorking(item.symbol);
      if (item.valuation.review_status !== "manual_confirmed") await api.confirmValueValuation(item.valuation.id);
      await api.createValueMonitor({ universe_id: activeUniverse.id, symbol: item.symbol, position_state: "watching", risk_preset: "balanced", conditions: {}, channels: ["in_app"] });
      await reload(); toast.success(`${item.name} 已升级为决策监控`);
    } catch (error) { toast.error(error instanceof Error ? error.message : "升级决策监控失败"); }
    finally { setWorking(null); }
  };
  const tabs = [["all", "全部覆盖"], ["priority", "关注中"], ["entry", "入场候选"], ["review_exit", "持有复核 / 退出"]] as const;
  return <div className="space-y-5"><PageHeader eyebrow="VALUATION & TIMING" title="估值与买卖点" description="所有龙头自动进入研究监控；只有估值经人工确认并升级为决策监控后，四道确定性门槛才会产生入场、持有复核或退出候选。" />
    {!activeUniverse ? <Empty label="请先冻结并激活一个龙头研究宇宙。" /> : <>
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5"><MetricCard label="研究覆盖" value={`${all.length} 家`} /><MetricCard label="估值就绪" value={`${all.filter((item) => item.valuation_status === "ready").length} 家`} /><MetricCard label="决策监控" value={`${decisionSymbols.size} 家`} /><MetricCard label="入场候选" value={`${all.filter((item) => item.decision_status === "entry_candidate").length} 家`} /><MetricCard label="待风险复核" value={`${all.filter((item) => ["holding_review", "exit_candidate", "thesis_invalidated"].includes(item.decision_status)).length} 家`} /></section>
      <div className="flex flex-wrap gap-2">{tabs.map(([key, label]) => <button key={key} onClick={() => setQueue(key)} className={cn("rounded-lg border px-3 py-2 text-sm", queue === key && "border-primary bg-primary/10 text-primary")}>{label}</button>)}</div>
      <div className="grid gap-4 xl:grid-cols-2">{items.map((item) => <ValuationDecisionCard key={item.symbol} item={item} decisionWatching={decisionSymbols.has(item.symbol)} working={working === item.symbol} onPriority={() => void markPriority(item)} onUpgrade={() => void upgrade(item)} />)}{!items.length ? <Empty label="当前队列没有公司。" /> : null}</div>
    </>}</div>;
}

function ValuationDecisionCard({ item, decisionWatching, working, onPriority, onUpgrade }: { item: ValueCompanyAnalysis; decisionWatching: boolean; working: boolean; onPriority: () => void; onUpgrade: () => void }) {
  const value = item.valuation; const dcf = value?.dcf || {};
  const fmt = (number?: number | null, suffix = "") => number == null ? "—" : `${number.toFixed(2)}${suffix}`;
  return <article className="rounded-xl border bg-card p-5 shadow-sm"><div className="flex items-start justify-between gap-3"><div><Link to={`/value/research?symbol=${encodeURIComponent(item.symbol)}`} className="font-semibold hover:text-primary hover:underline">{item.name}</Link><div className="font-mono text-xs text-muted-foreground">{item.symbol} · {item.memberships.map((m) => m.track_name).join(" / ")}</div></div><StatusBadge status={item.decision_status} /></div>
    <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4"><MetricCard label="当前价格" value={fmt(value?.current_price)} /><MetricCard label="安全边际" value={fmt(value?.safety_margin, "%")} /><MetricCard label="PE / 同业" value={`${fmt(value?.pe_ttm)} / ${fmt(value?.peer_pe_median)}`} /><MetricCard label="PB / 同业" value={`${fmt(value?.pb_mrq)} / ${fmt(value?.peer_pb_median)}`} /></div>
    <div className="mt-3 grid gap-3 sm:grid-cols-3"><div className="rounded-lg border p-3 text-sm"><div className="text-xs text-muted-foreground">合理价值区间</div><div className="mt-1 font-mono">{fmt(value?.fair_value_low)} – {fmt(value?.fair_value_high)}</div></div><div className="rounded-lg border p-3 text-sm"><div className="text-xs text-muted-foreground">技术状态</div><div className="mt-1">{analysisStateLabel(item.technical_status)}</div></div><div className="rounded-lg border p-3 text-sm"><div className="text-xs text-muted-foreground">DCF</div><div className="mt-1">{dcf.status === "unavailable" ? "输入不足，不运行" : String(dcf.status || "待计算")}</div></div></div>
    <div className="mt-3 rounded-lg bg-muted/50 p-3 text-sm"><div className="font-medium">当前判断</div><p className="mt-1 text-muted-foreground">{item.conclusion}</p><div className="mt-2 text-xs text-muted-foreground">数据 {value?.data_as_of || item.data_as_of || "—"} · {value?.review_status === "manual_confirmed" ? "估值已人工确认" : "自动初筛，未确认"} · 规则 {value?.formula_version || "待生成"}</div></div>
    <div className="mt-4 flex flex-wrap gap-2"><button disabled={working || !item.research_monitor_id} onClick={onPriority} className="rounded-lg border px-3 py-2 text-sm">{item.is_priority ? "取消重点" : "标记重点研究"}</button>{decisionWatching ? <Link to="/value/plans" className="rounded-lg border border-primary px-3 py-2 text-sm text-primary">配置个人覆盖规则</Link> : <button disabled={working || !value || !["ready", "partial"].includes(value.status)} onClick={onUpgrade} className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">确认估值并加入决策监控</button>}<Link to={`/value/research?symbol=${encodeURIComponent(item.symbol)}`} className="rounded-lg border px-3 py-2 text-sm">完整研究档案</Link></div>
  </article>;
}

export function ValueMonitorCenter() {
  const { events, signals, reloadOperations } = useValueWorkspace();
  const [filter, setFilter] = useState<"all" | "entry" | "risk" | "exit" | "data">("all");
  const evaluate = async () => { try { await api.evaluateValueMonitors(); await reloadOperations(); toast.success("已完成一次确定性规则检查"); } catch (error) { toast.error(error instanceof Error ? error.message : "监控检查失败"); } };
  const handleEvent = async (event: ValueMonitorEvent, status: "acknowledged" | "closed") => { try { await api.acknowledgeValueMonitorEvent(event.id, status); await reloadOperations(); } catch (error) { toast.error(error instanceof Error ? error.message : "更新事件失败"); } };
  const category = (state: string) => state === "entry_candidate" ? "entry" : state === "holding_review" ? "risk" : ["exit_candidate", "thesis_invalidated"].includes(state) ? "exit" : ["data_insufficient", "stale"].includes(state) ? "data" : "all";
  const cards = [
    ["entry", "入场候选", signals.filter((item) => item.signal_state === "entry_candidate").length],
    ["risk", "风险复核", signals.filter((item) => item.signal_state === "holding_review").length],
    ["exit", "退出与逻辑失效", signals.filter((item) => ["exit_candidate", "thesis_invalidated"].includes(item.signal_state)).length],
    ["data", "数据问题", signals.filter((item) => ["data_insufficient", "stale"].includes(item.signal_state)).length],
  ] as const;
  const visibleSignals = signals.filter((item) => filter === "all" || category(item.signal_state) === filter);
  return <div className="space-y-5"><PageHeader eyebrow="MONITOR EVENTS" title="监控事件" description="这里只处理事件、证据与通知结果；监控配置统一放在“估值与买卖点”和“投委与计划”。所有结果仅用于人工复核。" actions={<button onClick={() => void evaluate()} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><Activity className="h-4 w-4" />运行规则检查</button>} />
    <section className="grid gap-3 md:grid-cols-4">{cards.map(([key, title, count]) => <button key={key} onClick={() => setFilter(filter === key ? "all" : key)} className={cn("rounded-xl border bg-card p-4 text-left shadow-sm", filter === key && "border-primary ring-1 ring-primary")}><div className="text-sm text-muted-foreground">{title}</div><div className="mt-1 font-mono text-3xl font-semibold">{count}</div></button>)}</section>
    <section className="grid gap-3 sm:grid-cols-3"><MetricCard label="估值更新事件" value={`${events.filter((item) => item.event_type === "valuation_update").length} 条`} /><MetricCard label="财务与基本面更新" value={`${events.filter((item) => item.event_type === "financial_update").length} 条`} /><MetricCard label="数据待更新" value={`${events.filter((item) => ["data_stale", "data_insufficient", "stale"].includes(item.event_type)).length} 条`} /></section>
    <section className="rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-4"><div className="font-semibold">今日规则结论</div><div className="text-xs text-muted-foreground">{visibleSignals.length} 条 · {signals[0]?.rule_version || "等待首次运行"}</div></div><div className="divide-y">{visibleSignals.map((signal) => <article key={signal.id} className="grid gap-3 p-4 lg:grid-cols-[180px_160px_1fr]"><div><div className="font-medium">{signal.name}</div><div className="font-mono text-xs text-muted-foreground">{signal.symbol}</div><div className="mt-1 text-xs text-muted-foreground">{signal.as_of}</div></div><StatusBadge status={signal.signal_state} /><div><div className="text-sm">{signal.reasons.join("；") || "没有触发需要复核的条件"}</div><details className="mt-2 text-xs text-muted-foreground"><summary className="cursor-pointer">查看规则输入与缺失项</summary><pre className="mt-2 max-h-48 overflow-auto rounded bg-muted/50 p-3 whitespace-pre-wrap">{JSON.stringify({ inputs: signal.inputs, missing_fields: signal.missing_fields, rule_version: signal.rule_version }, null, 2)}</pre></details></div></article>)}{!visibleSignals.length ? <Empty label="当前分类暂无规则结论。" /> : null}</div></section>
    <section className="rounded-xl border bg-card shadow-sm"><div className="border-b p-4 font-semibold">待复核事件与投递</div><div className="divide-y">{events.map((event) => <article key={event.id} className="p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><div className="font-medium">{event.title}</div><p className="mt-1 text-sm text-muted-foreground">{event.message}</p><div className="mt-2 text-xs text-muted-foreground">数据日期 {String(event.payload.data_as_of || "—")} · 规则 {String(event.payload.rule_version || "—")} · 人工状态 {String(event.payload.position_state || "—")}</div></div><div className="flex items-center gap-2"><StatusBadge status={event.status} />{event.status === "open" ? <><button onClick={() => void handleEvent(event, "acknowledged")} className="rounded border px-2.5 py-1 text-xs">已知悉</button><button onClick={() => void handleEvent(event, "closed")} className="rounded border px-2.5 py-1 text-xs">关闭</button></> : null}</div></div><div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">{event.deliveries.map((delivery) => <span key={delivery.id} className="rounded border px-2 py-1">{channelLabel(delivery.channel)}：{delivery.status}{delivery.error ? ` · ${delivery.error}` : ""}</span>)}</div></article>)}{!events.length ? <Empty label="暂无事件。自动任务默认关闭，开启后仅在交易日收盘数据就绪时运行。" /> : null}</div></section></div>;
}

export function ValuePlans() {
  const navigate = useNavigate();
  const { batches, monitors, reloadOperations } = useValueWorkspace();
  const [editing, setEditing] = useState<ValueEntryMonitor | null>(null);
  const reviewed = batches.flatMap((batch) => batch.jobs).filter((job) => ["partial", "completed"].includes(job.status));
  const startCommittee = async (job: CompanyResearchJob) => { try { const committee = await api.createCommittee({ market: "CN", symbol: job.symbol, company_name: job.name }); navigate(`/ai/committees/${committee.id}`); } catch (error) { toast.error(error instanceof Error ? error.message : "无法启动投委会"); } };
  const changeState = async (monitor: ValueEntryMonitor, position_state: "watching" | "holding") => { try { await api.updateValueMonitor(monitor.id, { position_state }); await reloadOperations(); } catch (error) { toast.error(error instanceof Error ? error.message : "更新人工状态失败"); } };
  return <div className="space-y-5"><PageHeader eyebrow="COMMITTEE & PLAN" title="投委与计划" description="这里只管理已人工确认估值并升级的决策监控。个人价格、PE、PB、股息率阈值是系统四道门之上的覆盖规则，不替代基本面、估值和技术门槛。" />
    {editing ? <DecisionMonitorEditor monitor={editing} onCancel={() => setEditing(null)} onSaved={async () => { setEditing(null); await reloadOperations(); }} /> : null}
    <section className="rounded-xl border bg-card shadow-sm"><div className="border-b p-4"><div className="font-semibold">决策监控公司</div><div className="mt-1 text-xs text-muted-foreground">未完成估值人工确认的公司不会出现在这里，也不会产生买卖候选。</div></div><div className="divide-y">{monitors.map((monitor) => <article key={monitor.id} className="grid gap-3 p-4 lg:grid-cols-[1fr_160px_160px_1.4fr_auto]"><div><Link to={`/value/research?symbol=${encodeURIComponent(monitor.symbol)}`} className="font-medium hover:text-primary">{monitor.name}</Link><div className="font-mono text-xs text-muted-foreground">{monitor.symbol}</div></div><div><div className="text-xs text-muted-foreground">人工状态</div><select value={monitor.position_state} onChange={(event) => void changeState(monitor, event.target.value as "watching" | "holding")} className="mt-1 h-9 rounded-lg border bg-background px-2 text-sm"><option value="watching">观察中</option><option value="holding">已持有</option></select></div><div><div className="text-xs text-muted-foreground">当前结论</div><div className="mt-1"><StatusBadge status={monitor.signal_state} /></div></div><div><div className="text-xs text-muted-foreground">个人覆盖规则</div><div className="mt-1 text-sm text-muted-foreground">{conditionSummary(monitor.conditions)}</div></div><button onClick={() => setEditing(monitor)} className="self-center rounded-lg border px-3 py-2 text-sm">配置规则与通知</button></article>)}{!monitors.length ? <Empty label="尚无决策监控。请在“估值与买卖点”确认估值后升级重点公司。" /> : null}</div></section>
    {reviewed.length ? <details className="rounded-xl border bg-card shadow-sm"><summary className="cursor-pointer p-4 font-semibold">Legacy：旧研究批次（{reviewed.length}）</summary><div className="grid gap-4 border-t p-4 lg:grid-cols-2">{reviewed.map((job) => <article key={job.id} className="rounded-lg border p-4"><div className="font-semibold">{job.name}</div><div className="font-mono text-xs text-muted-foreground">{job.symbol}</div><button onClick={() => void startCommittee(job)} className="mt-3 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">人工发起投委会</button></article>)}</div></details> : null}</div>;
}

function DecisionMonitorEditor({ monitor, onCancel, onSaved }: { monitor: ValueEntryMonitor; onCancel: () => void; onSaved: () => Promise<void> }) {
  const conditions = monitor.conditions || {};
  const [maxPe, setMaxPe] = useState(String(conditions.max_pe || "")); const [maxPb, setMaxPb] = useState(String(conditions.max_pb || "")); const [minDividend, setMinDividend] = useState(String(conditions.min_dividend_yield || ""));
  const [exitPrice, setExitPrice] = useState(String(conditions.exit_price || "")); const [exitPe, setExitPe] = useState(String(conditions.exit_pe || "")); const [channels, setChannels] = useState<Array<"in_app" | "feishu" | "weixin">>(monitor.channels as Array<"in_app" | "feishu" | "weixin">);
  const toggle = (channel: "in_app" | "feishu" | "weixin") => setChannels((current) => current.includes(channel) ? current.filter((item) => item !== channel) : [...current, channel]);
  const save = async () => { const number = (value: string) => value ? Number(value) : undefined; const next = { ...(number(maxPe) ? { max_pe: number(maxPe) } : {}), ...(number(maxPb) ? { max_pb: number(maxPb) } : {}), ...(number(minDividend) ? { min_dividend_yield: number(minDividend) } : {}), ...(number(exitPrice) ? { exit_price: number(exitPrice) } : {}), ...(number(exitPe) ? { exit_pe: number(exitPe) } : {}) }; try { await api.updateValueMonitor(monitor.id, { conditions: next, channels }); await onSaved(); toast.success("个人覆盖规则已更新"); } catch (error) { toast.error(error instanceof Error ? error.message : "保存失败"); } };
  return <section className="rounded-xl border border-primary/30 bg-card p-5 shadow-sm"><div className="flex justify-between"><div><div className="text-xs font-semibold text-primary">PERSONAL OVERLAYS</div><h2 className="mt-1 text-lg font-semibold">{monitor.name} · 个人覆盖规则</h2></div><button onClick={onCancel} className="rounded-lg border px-3 py-2 text-sm">取消</button></div><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5"><NumberField label="PE 上限" value={maxPe} setValue={setMaxPe} /><NumberField label="PB 上限" value={maxPb} setValue={setMaxPb} /><NumberField label="股息率下限 %" value={minDividend} setValue={setMinDividend} /><NumberField label="退出价格" value={exitPrice} setValue={setExitPrice} /><NumberField label="退出 PE" value={exitPe} setValue={setExitPe} /></div><div className="mt-4 flex flex-wrap gap-4 text-sm">{(["in_app", "feishu", "weixin"] as const).map((channel) => <label key={channel} className="flex items-center gap-2"><input type="checkbox" checked={channels.includes(channel)} onChange={() => toggle(channel)} />{channelLabel(channel)}</label>)}</div><div className="mt-4 flex justify-end"><button onClick={() => void save()} className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">保存覆盖规则</button></div></section>;
}

type MonitorTarget = { job: CompanyResearchJob; universeId?: never; company?: never } | { universeId: string; company: ValueUniverseCompany; job?: never };

export function MonitorConfigForm({ target, onCancel, onSaved }: { target: MonitorTarget; onCancel: () => void; onSaved: () => Promise<void> }) {
  const name = target.job?.name || target.company?.name || ""; const symbol = target.job?.symbol || target.company?.symbol || "";
  const [positionState, setPositionState] = useState<"watching" | "holding">("watching");
  const [entryLow, setEntryLow] = useState(""); const [entryHigh, setEntryHigh] = useState(""); const [maxPe, setMaxPe] = useState(""); const [maxPb, setMaxPb] = useState(""); const [minDividend, setMinDividend] = useState("");
  const [exitPrice, setExitPrice] = useState(""); const [exitPe, setExitPe] = useState(""); const [exitPb, setExitPb] = useState(""); const [channels, setChannels] = useState<Array<"in_app" | "feishu" | "weixin">>(["in_app"]);
  const positive = (raw: string) => raw === "" || (Number.isFinite(Number(raw)) && Number(raw) > 0);
  const save = async () => { const low = Number(entryLow); const high = Number(entryHigh); if ((entryLow && !entryHigh) || (!entryLow && entryHigh)) { toast.error("价格区间需要同时填写下限和上限"); return; } if (entryLow && (!Number.isFinite(low) || !Number.isFinite(high) || low > high)) { toast.error("价格区间无效"); return; } if (![maxPe, maxPb, minDividend, exitPrice, exitPe, exitPb].every(positive)) { toast.error("阈值必须为正数"); return; }
    const conditions = { ...(entryLow ? { entry_low: low, entry_high: high } : {}), ...(maxPe ? { max_pe: Number(maxPe) } : {}), ...(maxPb ? { max_pb: Number(maxPb) } : {}), ...(minDividend ? { min_dividend_yield: Number(minDividend) } : {}), ...(exitPrice ? { exit_price: Number(exitPrice) } : {}), ...(exitPe ? { exit_pe: Number(exitPe) } : {}), ...(exitPb ? { exit_pb: Number(exitPb) } : {}) };
    try { await api.createValueMonitor({ ...(target.job ? { research_job_id: target.job.id } : { universe_id: target.universeId, symbol }), position_state: positionState, risk_preset: "balanced", conditions, channels }); toast.success(`${name} 已加入持续监控`); await onSaved(); } catch (error) { toast.error(error instanceof Error ? error.message : "加入监控失败"); } };
  const toggle = (channel: "in_app" | "feishu" | "weixin") => setChannels((current) => current.includes(channel) ? current.filter((item) => item !== channel) : [...current, channel]);
  return <section className="rounded-xl border border-primary/30 bg-card p-5 shadow-sm"><div className="flex items-start justify-between gap-3"><div><div className="text-xs font-semibold tracking-wide text-primary">DETERMINISTIC MONITOR</div><h2 className="mt-1 text-lg font-semibold">设置 {name} · {symbol}</h2><p className="mt-1 text-sm text-muted-foreground">可不填入场/退出阈值，仅启用默认均衡风险复核。系统不会从组合或交易记录推断持仓。</p></div><button onClick={onCancel} className="rounded-lg border px-3 py-2 text-sm">取消</button></div>
    <div className="mt-4 flex gap-2">{(["watching", "holding"] as const).map((item) => <button key={item} onClick={() => setPositionState(item)} className={cn("rounded-lg border px-3 py-2 text-sm", positionState === item && "border-primary bg-primary/5 text-primary")}>{item === "watching" ? "观察中：只判断入场" : "已持有：判断风险与退出"}</button>)}</div>
    <div className="mt-4 grid gap-4 xl:grid-cols-2"><fieldset className="rounded-lg border p-4"><legend className="px-2 text-sm font-semibold">全部满足才提示入场</legend><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><NumberField label="价格下限" value={entryLow} setValue={setEntryLow} /><NumberField label="价格上限" value={entryHigh} setValue={setEntryHigh} /><NumberField label="PE(TTM) 上限" value={maxPe} setValue={setMaxPe} /><NumberField label="PB(MRQ) 上限" value={maxPb} setValue={setMaxPb} /><NumberField label="股息率下限 %" value={minDividend} setValue={setMinDividend} /></div></fieldset><fieldset className="rounded-lg border p-4"><legend className="px-2 text-sm font-semibold">任一满足即提示退出（仅已持有）</legend><div className="grid gap-3 sm:grid-cols-3"><NumberField label="目标价格" value={exitPrice} setValue={setExitPrice} /><NumberField label="PE(TTM) 下限" value={exitPe} setValue={setExitPe} /><NumberField label="PB(MRQ) 下限" value={exitPb} setValue={setExitPb} /></div></fieldset></div>
    <div className="mt-4 rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground">默认均衡风险复核：营收/利润同比 ≤ -20%、ROE 同比下降 ≥ 3 个百分点、盈利但经营现金流为负、负债率同比上升 ≥ 5 个百分点、ST 或退市。专业财务不足时显示数据不足，不补 0。</div><div className="mt-4 flex flex-wrap items-center gap-4 text-sm">{(["in_app", "feishu", "weixin"] as const).map((channel) => <label key={channel} className="flex items-center gap-2"><input type="checkbox" checked={channels.includes(channel)} onChange={() => toggle(channel)} />{channelLabel(channel)}</label>)}</div><div className="mt-4 flex justify-end"><button onClick={() => void save()} disabled={!channels.length} className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">确认加入监控</button></div></section>;
}

function NumberField({ label, value, setValue }: { label: string; value: string; setValue: (value: string) => void }) { return <label className="text-sm">{label}<input value={value} onChange={(event) => setValue(event.target.value)} inputMode="decimal" className="mt-1 block h-9 w-full rounded-lg border bg-background px-3" /></label>; }
function channelLabel(channel: string) { return channel === "in_app" ? "站内" : channel === "feishu" ? "飞书" : channel === "weixin" ? "微信" : channel; }
function conditionSummary(conditions: Record<string, unknown>) { const labels: Record<string, string> = { entry_low: "价格下限", entry_high: "价格上限", max_pe: "PE 上限", max_pb: "PB 上限", min_dividend_yield: "股息率下限", exit_price: "目标价", exit_pe: "退出 PE", exit_pb: "退出 PB" }; const items = Object.entries(conditions).map(([key, value]) => `${labels[key] || key} ${String(value)}`); return items.length ? items.join(" · ") : "默认均衡风险复核"; }

function StatusBadge({ status }: { status: string }) { return <span className={cn("inline-flex rounded-full px-3 py-1.5 text-sm", statusStyles[status] || "bg-muted text-muted-foreground")}>{labelStatus(status)}</span>; }
function Loading({ label }: { label: string }) { return <div className="flex min-h-[420px] items-center justify-center gap-2 rounded-xl border border-dashed text-base text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{label}</div>; }
function Empty({ label }: { label: string }) { return <div className="flex min-h-32 items-center justify-center px-6 text-center text-base text-muted-foreground"><ShieldAlert className="mr-2 h-4 w-4" />{label}</div>; }
