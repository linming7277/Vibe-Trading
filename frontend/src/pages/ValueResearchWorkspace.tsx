import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Outlet, useNavigate, useOutletContext, useSearchParams } from "react-router";
import {
  Activity, AlertTriangle, BarChart3, ChevronRight, CircleDollarSign, ClipboardCheck,
  Loader2, Pause, Play, Plus, RefreshCw, Search, ShieldAlert, Target,
} from "lucide-react";
import { toast } from "sonner";

import { PageHeader, WorkspacePage } from "@/components/workspace/WorkspaceUI";
import {
  api, type CalculationProfile, type CompanyResearchBatch, type CompanyResearchJob,
  type ValueEntryMonitor, type ValueLeaderScore, type ValueMonitorEvent, type ValueSectorScore, type ValueTrack,
  type ValueWorkbench,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type ValueWorkspaceContext = {
  profiles: CalculationProfile[];
  workbench: ValueWorkbench | null;
  tracks: ValueTrack[];
  leaders: ValueLeaderScore[];
  selectedTrack: ValueTrack | undefined;
  selectedTrackId: string;
  selectedSymbols: string[];
  batches: CompanyResearchBatch[];
  monitors: ValueEntryMonitor[];
  events: ValueMonitorEvent[];
  loading: boolean;
  refreshing: boolean;
  selectProfile: (id: string) => void;
  selectTrack: (id: string) => void;
  toggleSymbol: (symbol: string) => void;
  clearSelection: () => void;
  refreshScores: () => Promise<void>;
  reload: () => Promise<void>;
  reloadOperations: () => Promise<void>;
  startResearch: () => Promise<void>;
};

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
  paused: "bg-muted text-muted-foreground",
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
  return ({ ready: "可用", queued: "排队", running: "进行中", partial: "部分可用", completed: "已完成", failed: "失败", active: "监控中", paused: "已暂停", closed: "已关闭", insufficient_data: "数据不足", macro_pending: "宏观待更新" } as Record<string, string>)[value] || value;
}

export function ValueResearchWorkspace() {
  const [params, setParams] = useSearchParams();
  const [profiles, setProfiles] = useState<CalculationProfile[]>([]);
  const [workbench, setWorkbench] = useState<ValueWorkbench | null>(null);
  const [leaders, setLeaders] = useState<ValueLeaderScore[]>([]);
  const [batches, setBatches] = useState<CompanyResearchBatch[]>([]);
  const [monitors, setMonitors] = useState<ValueEntryMonitor[]>([]);
  const [events, setEvents] = useState<ValueMonitorEvent[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const profileId = params.get("profile") || "";
  const defaultTrackId = useMemo(() => {
    const candidateFirst = [...(workbench?.sector_scores || [])]
      .sort((left, right) => left.rank - right.rank)[0]?.sector_code;
    return candidateFirst || workbench?.tracks[0]?.track_id || "";
  }, [workbench?.sector_scores, workbench?.tracks]);
  const selectedTrackId = params.get("track") || defaultTrackId;
  const tracks = workbench?.tracks || [];
  const selectedTrack = tracks.find((item) => item.track_id === selectedTrackId);

  const patchParams = useCallback((nextValues: Record<string, string | null>) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(nextValues).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
      return next;
    });
  }, [setParams]);

  const reloadOperations = useCallback(async () => {
    const [batchResult, monitorResult, eventResult] = await Promise.all([
      api.getValueResearchBatches(), api.getValueMonitors(), api.getValueMonitorEvents(),
    ]);
    setBatches(batchResult.items);
    setMonitors(monitorResult.items);
    setEvents(eventResult.items);
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
    api.getValueLeaderScores(selectedTrackId, workbench?.latest_run?.as_of || workbench?.macro?.as_of || undefined)
      .then((result) => { if (!cancelled) setLeaders(result.items.slice(0, 20)); })
      .catch((error) => { if (!cancelled) { setLeaders([]); toast.error(error instanceof Error ? error.message : "龙头池加载失败"); } });
    return () => { cancelled = true; };
  }, [selectedTrackId, workbench?.latest_run?.as_of, workbench?.macro?.as_of]);

  useEffect(() => {
    if (!defaultTrackId || params.get("track")) return;
    patchParams({ track: defaultTrackId });
  }, [defaultTrackId, params, patchParams]);

  useEffect(() => setSelectedSymbols((current) => current.filter((symbol) => leaders.some((item) => item.symbol === symbol))), [leaders]);

  useEffect(() => {
    if (!batches.some((batch) => ["queued", "running"].includes(batch.status))) return;
    const timer = window.setInterval(() => void reloadOperations(), 5000);
    return () => window.clearInterval(timer);
  }, [batches, reloadOperations]);

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

  const context: ValueWorkspaceContext = {
    profiles, workbench, tracks, leaders, selectedTrack, selectedTrackId, selectedSymbols, batches, monitors, events, loading, refreshing,
    selectProfile: (id) => patchParams({ profile: id, track: null }),
    selectTrack: (id) => patchParams({ track: id }),
    toggleSymbol: (symbol) => setSelectedSymbols((current) => current.includes(symbol) ? current.filter((item) => item !== symbol) : current.length >= 20 ? current : [...current, symbol]),
    clearSelection: () => setSelectedSymbols([]), refreshScores, reload, reloadOperations, startResearch,
  };

  return <WorkspacePage className="space-y-5">
    <ValueContextBar context={context} />
    <Outlet context={context} />
  </WorkspacePage>;
}

export function useValueWorkspace() { return useOutletContext<ValueWorkspaceContext>(); }

function ValueContextBar({ context }: { context: ValueWorkspaceContext }) {
  const macro = context.workbench?.macro;
  return <section className="sticky top-0 z-20 -mx-5 border-b bg-background/95 px-5 py-3 backdrop-blur md:-mx-8 md:px-8">
    <div className="mx-auto flex max-w-[1480px] flex-wrap items-center gap-3">
      <div className="mr-2"><div className="text-base font-semibold">价值龙头</div></div>
      <select value={context.workbench?.profile.id || ""} onChange={(event) => context.selectProfile(event.target.value)} className="h-10 max-w-[240px] rounded-lg border bg-card px-3 text-sm" aria-label="计算方案">
        {context.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · v{profile.version}</option>)}
      </select>
      <span className="rounded-full bg-primary/10 px-3 py-1.5 text-sm font-medium text-primary">{macro?.regime || "宏观数据待更新"}</span>
      <span className="text-sm text-muted-foreground">数据 {macro?.as_of || context.workbench?.latest_run?.as_of || "—"}</span>
      <span className="text-sm text-muted-foreground">公式 {context.workbench?.latest_run?.formula_version?.split(":")[0] || "—"}</span>
      <Link to="/value/profiles" className="ml-auto text-sm text-muted-foreground hover:text-foreground">计算方案</Link>
      <button onClick={() => void context.refreshScores()} disabled={context.refreshing} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", context.refreshing && "animate-spin")} />更新评分</button>
    </div>
  </section>;
}

export function ValueOverview() {
  const context = useValueWorkspace();
  const [params] = useSearchParams();
  const [query, setQuery] = useState("");
  const [rankView, setRankView] = useState<"macro" | "overall">("overall");
  const [activeGroup, setActiveGroup] = useState("全部");
  const groups = useMemo(() => {
    const grouped = new Map<string, ValueSectorScore[]>();
    for (const sector of context.workbench?.sector_scores || []) {
      const key = sector.macro_group_name || "未映射";
      grouped.set(key, [...(grouped.get(key) || []), sector]);
    }
    return [...grouped.entries()].map(([name, sectors]) => {
      const valid = sectors.filter((item) => item.macro_fit != null);
      const macroScore = valid.length ? valid.reduce((sum, item) => sum + Number(item.macro_fit), 0) / valid.length : null;
      return { name, sectors, macroScore, candidateRank: Math.min(...sectors.map((item) => item.rank)) };
    }).sort((left, right) => left.candidateRank - right.candidateRank);
  }, [context.workbench?.sector_scores]);
  const filteredSectors = useMemo(() => {
    const needle = query.toLowerCase();
    const items = (context.workbench?.sector_scores || []).filter((item) => {
      const inGroup = activeGroup === "全部" || item.macro_group_name === activeGroup;
      return inGroup && (!query || `${item.sector_name} ${item.sector_code} ${item.macro_group_name || ""}`.toLowerCase().includes(needle));
    });
    return [...items].sort((left, right) => rankView === "macro"
      ? (left.macro_rank || 999) - (right.macro_rank || 999)
      : left.rank - right.rank);
  }, [activeGroup, context.workbench?.sector_scores, query, rankView]);
  const jobs = context.batches.flatMap((batch) => batch.jobs);

  const chooseFirst = useCallback((nextGroup: string, nextRank: "macro" | "overall") => {
    const candidates = (context.workbench?.sector_scores || []).filter((item) => nextGroup === "全部" || item.macro_group_name === nextGroup);
    candidates.sort((left, right) => nextRank === "macro" ? (left.macro_rank || 999) - (right.macro_rank || 999) : left.rank - right.rank);
    if (candidates[0]) context.selectTrack(candidates[0].sector_code);
  }, [context]);

  const changeGroup = (group: string) => {
    setActiveGroup(group);
    setQuery("");
    chooseFirst(group, rankView);
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
    <section className="grid overflow-hidden rounded-xl border bg-card shadow-sm xl:h-[calc(100vh-260px)] xl:min-h-[600px] xl:max-h-[860px] xl:grid-cols-[220px_360px_minmax(0,1fr)]">
      <IndustryGroupPane groups={groups} activeGroup={activeGroup} onSelect={changeGroup} />
      <TrackPane sectors={filteredSectors} query={query} setQuery={setQuery} rankView={rankView} setRankView={changeRank} />
      <DecisionPane />
    </section>
    <ResearchSelectionBar />
    <PipelineSummary jobs={jobs} />
  </div>;
}

function MacroStatusBar() {
  const { workbench } = useValueWorkspace();
  const macro = workbench?.macro;
  const missing = macro?.missing_series || [];
  return <details id="value-macro" className="group overflow-hidden rounded-xl border bg-card shadow-sm">
    <summary className="flex cursor-pointer list-none flex-wrap items-center gap-3 px-4 py-3">
      <div className="mr-2"><div className="text-xs font-semibold tracking-wide text-primary">当前宏观环境</div><div className="text-xl font-semibold">{macro?.regime || "宏观待更新"}</div></div>
      <StatusBadge status={macro?.status || "failed"} />
      <div className="flex min-w-0 flex-1 flex-wrap gap-2">{Object.entries(MACRO_LABELS).map(([key, label]) => <span key={key} className={cn("rounded-full px-3 py-1.5 text-sm", Number(macro?.axes?.[key] || 50) >= 60 ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground")}>{label} {macro?.states?.[key] || "—"} <b className="ml-1 font-mono text-foreground">{score(macro?.axes?.[key])}</b></span>)}</div>
      <div className="text-right text-sm text-muted-foreground"><div>{macro?.as_of || "—"} · 置信度 {macro?.confidence || "LOW"}</div><div>指标 {macro?.series_count ?? "—"}/{macro?.series_total ?? "—"}</div></div>
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
  return <section id="value-tracks" className="min-w-0 border-b xl:flex xl:min-h-0 xl:flex-col xl:border-b-0 xl:border-r"><header className="shrink-0 border-b p-4"><div className="flex items-baseline justify-between"><div><div className="text-xs font-semibold tracking-wide text-primary">第二步</div><div className="mt-1 text-base font-semibold">候选赛道池</div></div><span className="text-sm text-muted-foreground">{sectors.length} 个</span></div><div className="mt-2 text-xs text-muted-foreground">排序用于展开龙头研究；宏观仅作背景观察。</div><div className="mt-3 grid grid-cols-2 rounded-lg bg-muted p-1 text-sm"><button onClick={() => setRankView("overall")} className={cn("rounded-md px-2 py-2", rankView === "overall" && "bg-background font-medium shadow-sm")}>候选池排序</button><button onClick={() => setRankView("macro")} className={cn("rounded-md px-2 py-2", rankView === "macro" && "bg-background font-medium shadow-sm")}>宏观观察</button></div><label className="relative mt-2 block"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索细分赛道" className="h-10 w-full rounded-md border bg-background pl-9 pr-2 text-sm" /></label></header><div className="min-h-0 flex-1 divide-y overflow-y-auto">{sectors.map((sector) => { const activeScore = rankView === "macro" ? sector.macro_fit : sector.score; return <button key={sector.sector_code} onClick={() => selectTrack(sector.sector_code)} className={cn("grid w-full grid-cols-[32px_1fr_86px] items-center gap-3 px-4 py-3.5 text-left hover:bg-muted/60", sector.sector_code === selectedTrackId && "bg-primary/10")}><span className="font-mono text-sm text-muted-foreground">{rankView === "macro" ? sector.macro_rank : sector.rank}</span><span className="min-w-0"><span className="block truncate text-[15px] font-medium">{sector.sector_name}</span><span className="block truncate text-xs text-muted-foreground">候选 {sector.rank} · 宏观观察 {sector.macro_rank}</span></span><span className="text-right"><span className="block text-xs font-medium">{scoreLevel(activeScore)}</span><span className="block font-mono text-xs text-muted-foreground">指数 {score(activeScore)}</span></span></button>; })}{!sectors.length ? <Empty label="没有符合条件的赛道" /> : null}</div><SectorJudgement detail={detail} /></section>;
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
  const { selectedTrackId, leaders, selectedSymbols, toggleSymbol } = useValueWorkspace();
  return <section className="min-w-0 xl:flex xl:min-h-0 xl:flex-col"><div id="value-leaders" className="xl:flex xl:min-h-0 xl:flex-1 xl:flex-col"><div className="flex shrink-0 items-center justify-between border-b bg-muted/20 px-4 py-3"><div><span className="text-base font-semibold">行业内优先研究公司</span><span className="ml-2 text-sm text-muted-foreground" title="评分只用于同一行业内部比较">行业内比较</span></div><span className="text-sm text-muted-foreground">前 {Math.min(leaders.length, 12)} 家</span></div><div className="max-h-[560px] divide-y overflow-y-auto xl:min-h-0 xl:max-h-none xl:flex-1">{leaders.slice(0, 12).map((leader) => <LeaderRow key={leader.symbol} leader={leader} selected={selectedSymbols.includes(leader.symbol)} onToggle={() => toggleSymbol(leader.symbol)} />)}{!leaders.length ? <Empty label={selectedTrackId ? "该赛道暂无可用龙头" : "请先选择赛道"} /> : null}</div></div></section>;
}

function LeaderRow({ leader, selected, onToggle }: { leader: ValueLeaderScore; selected: boolean; onToggle: () => void }) {
  const dimensions = Object.entries(leader.component_scores).filter((entry): entry is [string, number] => entry[1] != null).sort((left, right) => right[1] - left[1]);
  const strongest = dimensions[0];
  const weakest = dimensions[dimensions.length - 1];
  const metrics = leaderMetrics(leader);
  return <div className={cn("grid grid-cols-[30px_minmax(140px,180px)_minmax(220px,1fr)_100px] items-center gap-3 px-4 py-3.5 hover:bg-muted/40", selected && "bg-primary/5")}><input type="checkbox" checked={selected} onChange={onToggle} aria-label={`选择 ${leader.name}`} /><div className="min-w-0"><Link to={`/company/CN/${encodeURIComponent(leader.symbol)}?from=value`} className="block truncate text-[15px] font-medium hover:text-primary hover:underline">{leader.name}</Link><div className="font-mono text-xs text-muted-foreground">{leader.symbol}</div></div><div className="min-w-0 text-[13px] leading-5"><div className="truncate text-emerald-700 dark:text-emerald-400" title={strongest ? LEADER_STRENGTH_TEXT[strongest[0]] : undefined}>优势：{strongest ? LEADER_STRENGTH_TEXT[strongest[0]] : "证据不足"}</div><div className="truncate text-amber-700 dark:text-amber-400" title={weakest ? LEADER_WEAKNESS_TEXT[weakest[0]] : undefined}>关注：{weakest ? LEADER_WEAKNESS_TEXT[weakest[0]] : "证据不足"}</div><div className="truncate text-muted-foreground">{metrics.length ? metrics.map(([label, value]) => `${label} ${value}`).join(" · ") : "暂无可读原始指标"}</div></div><div className="text-right"><div className="text-xs text-muted-foreground">行业第 {leader.rank}</div><div className="text-sm font-semibold">{scoreLevel(leader.score)}</div><div className="font-mono text-xs text-muted-foreground">指数 {score(leader.score)} · {6 - leader.missing_fields.length}/6维</div></div></div>;
}

function ResearchSelectionBar() {
  const { selectedSymbols, leaders, clearSelection, startResearch, batches, selectedTrackId } = useValueWorkspace();
  const selected = leaders.filter((leader) => selectedSymbols.includes(leader.symbol));
  const newest = batches.find((batch) => batch.track_id === selectedTrackId);
  if (!selectedSymbols.length) return null;
  return <aside className="sticky bottom-3 z-10 rounded-xl border border-primary/30 bg-card/95 p-3.5 shadow-lg backdrop-blur"><div className="flex flex-wrap items-center gap-3"><div className="min-w-[150px]"><div className="text-sm font-semibold text-primary">下一步 · 深度研究</div><div className="mt-0.5 text-base font-semibold">已选 {selectedSymbols.length} 家公司</div></div><div className="flex min-w-0 flex-1 flex-wrap gap-1.5">{selected.slice(0, 6).map((leader) => <span key={leader.symbol} className="rounded-full bg-muted px-2.5 py-1 text-sm">{leader.name}</span>)}{selected.length > 6 ? <span className="rounded-full bg-muted px-2.5 py-1 text-sm">+{selected.length - 6}</span> : null}</div><button onClick={clearSelection} className="text-sm text-muted-foreground hover:text-foreground">清空</button><button onClick={() => void startResearch()} className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground"><ClipboardCheck className="h-4 w-4" />研究所选公司</button>{newest ? <Link to="/value/research" className="text-sm text-primary hover:underline">最近批次 {newest.completed}/{newest.total}</Link> : null}</div></aside>;
}

function PipelineSummary({ jobs }: { jobs: CompanyResearchJob[] }) {
  const { monitors, events } = useValueWorkspace();
  const summaries = [
    ["研究队列", jobs.filter((job) => ["queued", "running"].includes(job.status)).length, "正在进行的公司研究", "/value/research"],
    ["估值待复核", jobs.filter((job) => job.status === "partial" || job.status === "completed").length, "进入估值与买点人工复核", "/value/valuation"],
    ["投委与计划", jobs.filter((job) => ["partial", "completed"].includes(job.status)).length, "形成可执行或等待结论", "/value/plans"],
    ["持续监控", monitors.filter((monitor) => monitor.status === "active").length + events.filter((event) => !event.acknowledged_at).length, "重点公司与触发提醒", "/value/monitor"],
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

export function ValueResearchQueue() {
  const { batches, reloadOperations } = useValueWorkspace();
  const [expanded, setExpanded] = useState<string | null>(batches[0]?.id || null);
  const cancel = async (id: string) => { try { await api.cancelValueResearchBatch(id); await reloadOperations(); toast.success("研究批次已请求取消"); } catch (error) { toast.error(error instanceof Error ? error.message : "取消失败"); } };
  return <div className="space-y-5"><PageHeader eyebrow="COMPANY RESEARCH" title="公司研究队列" description="每家公司依次形成事实底稿、PIT 财务分析、护城河与风险，以及独立的 DCF/可比估值结果。不可验证的 DCF 输入会被明确阻断。" />
    <div className="space-y-3">{batches.map((batch) => <section key={batch.id} className="rounded-xl border bg-card shadow-sm"><button onClick={() => setExpanded(expanded === batch.id ? null : batch.id)} className="flex w-full flex-wrap items-center gap-3 p-4 text-left"><div><div className="font-semibold">{batch.track_id} · {batch.total} 家公司</div><div className="mt-1 font-mono text-xs text-muted-foreground">{batch.id}</div></div><StatusBadge status={batch.status} /><span className="text-xs text-muted-foreground">完成 {batch.completed}/{batch.total} · 失败 {batch.failed}</span><ChevronRight className={cn("ml-auto h-4 w-4 transition", expanded === batch.id && "rotate-90")} /></button>{expanded === batch.id ? <div className="border-t p-4"><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{batch.jobs.map((job) => <JobCard key={job.id} job={job} />)}</div>{["queued", "running"].includes(batch.status) ? <button onClick={() => void cancel(batch.id)} className="mt-4 rounded-lg border px-3 py-2 text-sm hover:bg-muted">取消批次</button> : null}</div> : null}</section>)}{!batches.length ? <Empty label="尚无公司研究批次；从总览的龙头池勾选公司开始。" /> : null}</div></div>;
}

function JobCard({ job }: { job: CompanyResearchJob }) { return <article className="rounded-lg border p-4"><div className="flex justify-between gap-2"><div><Link to={`/company/CN/${job.symbol}`} className="font-semibold hover:text-primary hover:underline">{job.name}</Link><div className="font-mono text-xs text-muted-foreground">{job.symbol}</div></div><StatusBadge status={job.status} /></div><div className="mt-4 grid grid-cols-2 gap-2 text-xs"><div className="rounded bg-muted/60 p-2"><div className="text-muted-foreground">当前阶段</div><div className="mt-1 font-medium">{job.stage}</div></div><div className="rounded bg-muted/60 p-2"><div className="text-muted-foreground">估值</div><div className="mt-1 font-medium">{labelStatus(job.valuation_status)}</div></div></div><p className="mt-3 text-xs text-muted-foreground">{job.message || "等待研究任务启动"}</p></article>; }

export function ValueValuationCenter() {
  const { batches } = useValueWorkspace();
  const jobs = batches.flatMap((batch) => batch.jobs).filter((job) => job.status === "partial" || job.status === "completed");
  return <div className="space-y-5"><PageHeader eyebrow="VALUATION CENTER" title="估值中心" description="DCF 和可比估值是两条独立链路。DCF 输入不足时会显示不可运行，不会补造假设值。" />
    <div className="grid gap-4 lg:grid-cols-2">{jobs.map((job) => <ValuationCard key={job.id} job={job} />)}{!jobs.length ? <Empty label="研究完成或部分完成后，估值结果会出现在这里。" /> : null}</div></div>;
}

function ValuationCard({ job }: { job: CompanyResearchJob }) { const valuation = job.valuation || {}; const comparable = valuation.comparable as Record<string, unknown> | undefined; const dcf = valuation.dcf as Record<string, unknown> | undefined; return <article className="rounded-xl border bg-card p-5 shadow-sm"><div className="flex justify-between"><div><Link to={`/company/CN/${job.symbol}`} className="font-semibold hover:text-primary hover:underline">{job.name}</Link><div className="font-mono text-xs text-muted-foreground">{job.symbol}</div></div><StatusBadge status={job.valuation_status} /></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="rounded-lg border p-3"><div className="flex items-center gap-2 text-xs font-semibold"><BarChart3 className="h-3.5 w-3.5" />可比估值</div><dl className="mt-3 space-y-1 text-xs text-muted-foreground"><div className="flex justify-between"><dt>PE TTM</dt><dd className="font-mono text-foreground">{String(comparable?.pe_ttm ?? "—")}</dd></div><div className="flex justify-between"><dt>同业 PE 中位数</dt><dd className="font-mono text-foreground">{String(comparable?.peer_median_pe ?? "—")}</dd></div><div className="flex justify-between"><dt>PB MRQ</dt><dd className="font-mono text-foreground">{String(comparable?.pb_mrq ?? "—")}</dd></div></dl></div><div className="rounded-lg border p-3"><div className="flex items-center gap-2 text-xs font-semibold"><CircleDollarSign className="h-3.5 w-3.5" />DCF</div><div className="mt-3 text-sm font-medium">{String(dcf?.status === "unavailable" ? "不可运行" : dcf?.status || "等待计算")}</div><p className="mt-1 text-xs text-muted-foreground">{String(dcf?.reason || "需要经过验证的 PIT 输入")}</p></div></div></article>; }

export function ValueMonitorCenter() {
  const { monitors, events, reloadOperations } = useValueWorkspace();
  const evaluate = async () => { try { await api.evaluateValueMonitors(); await reloadOperations(); toast.success("已完成一次入场监控检查"); } catch (error) { toast.error(error instanceof Error ? error.message : "监控检查失败"); } };
  const toggle = async (monitor: ValueEntryMonitor) => { try { await api.updateValueMonitor(monitor.id, { status: monitor.status === "active" ? "paused" : "active" }); await reloadOperations(); } catch (error) { toast.error(error instanceof Error ? error.message : "更新监控失败"); } };
  return <div className="space-y-5"><PageHeader eyebrow="ENTRY MONITORS" title="入场监控" description="人工确认的重点公司才会进入监控池。监控只产生站内、飞书和微信提醒与复核任务，不会连接实盘下单。" actions={<button onClick={() => void evaluate()} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><Activity className="h-4 w-4" />立即检查</button>} />
    <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="border-b p-4 font-semibold">已确认监控</div><div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="p-3">公司</th><th>状态</th><th>条件</th><th>渠道</th><th>最近检查</th><th></th></tr></thead><tbody className="divide-y">{monitors.map((monitor) => <tr key={monitor.id}><td className="p-3"><div className="font-medium">{monitor.name}</div><div className="font-mono text-xs text-muted-foreground">{monitor.symbol}</div></td><td><StatusBadge status={monitor.status} /></td><td className="max-w-[250px] truncate text-xs text-muted-foreground">{Object.keys(monitor.conditions).length ? Object.keys(monitor.conditions).join(" · ") : "仅数据过期与逻辑失效复核"}</td><td className="text-xs">{monitor.channels.join(" · ")}</td><td className="text-xs text-muted-foreground">{monitor.last_checked_at || "尚未检查"}</td><td><button onClick={() => void toggle(monitor)} className="rounded-md border p-2 hover:bg-muted" aria-label={monitor.status === "active" ? "暂停监控" : "恢复监控"}>{monitor.status === "active" ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}</button></td></tr>)}{!monitors.length ? <tr><td colSpan={6}><Empty label="暂无监控；在公司研究页完成人工确认后加入。" /></td></tr> : null}</tbody></table></div></section>
    <section className="rounded-xl border bg-card shadow-sm"><div className="border-b p-4 font-semibold">监控事件与投递记录</div><div className="divide-y">{events.map((event) => <article key={event.id} className="p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><div className="font-medium">{event.title}</div><p className="mt-1 text-sm text-muted-foreground">{event.message}</p></div><span className="rounded-full bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-400">{event.severity}</span></div><div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">{event.deliveries.map((delivery) => <span key={delivery.id} className="rounded border px-2 py-1">{delivery.channel}: {delivery.status}{delivery.error ? ` · ${delivery.error}` : ""}</span>)}</div></article>)}{!events.length ? <Empty label="暂无触发事件。交易日收盘后会自动运行监控。" /> : null}</div></section></div>;
}

export function ValuePlans() {
  const navigate = useNavigate();
  const { batches, monitors, reloadOperations } = useValueWorkspace();
  const reviewed = batches.flatMap((batch) => batch.jobs).filter((job) => ["partial", "completed"].includes(job.status));
  const monitoredJobIds = new Set(monitors.map((monitor) => monitor.research_job_id));
  const addMonitor = async (job: CompanyResearchJob) => { try { await api.createValueMonitor({ research_job_id: job.id, conditions: {}, channels: ["in_app"] }); await reloadOperations(); toast.success(`${job.name} 已加入入场监控`); } catch (error) { toast.error(error instanceof Error ? error.message : "加入监控失败"); } };
  const startCommittee = async (job: CompanyResearchJob) => { try { const committee = await api.createCommittee({ market: "CN", symbol: job.symbol, company_name: job.name }); navigate(`/ai/committees/${committee.id}`); } catch (error) { toast.error(error instanceof Error ? error.message : "无法启动投委会"); } };
  return <div className="space-y-5"><PageHeader eyebrow="VALUE PLAN" title="人工投委会与价值计划" description="自动研究只生成证据和反证。只有研究完成后，由你手动发起价值投委会；确认重点公司后才允许进入入场监控。" />
    <div className="grid gap-4 lg:grid-cols-2">{reviewed.map((job) => <article key={job.id} className="rounded-xl border bg-card p-5 shadow-sm"><div className="flex items-start justify-between"><div><div className="font-semibold">{job.name}</div><div className="font-mono text-xs text-muted-foreground">{job.symbol}</div></div><StatusBadge status={job.status} /></div><p className="mt-3 text-sm text-muted-foreground">{job.message || "研究已完成，等待人工确认。"}</p><div className="mt-4 flex flex-wrap gap-2"><button onClick={() => void startCommittee(job)} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><ClipboardCheck className="h-4 w-4" />人工发起投委会</button><button disabled={monitoredJobIds.has(job.id)} onClick={() => void addMonitor(job)} className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"><Target className="h-4 w-4" />{monitoredJobIds.has(job.id) ? "已加入监控" : "确认加入监控"}</button></div></article>)}{!reviewed.length ? <Empty label="暂无可进入投委会的公司；请先完成批量研究。" /> : null}</div></div>;
}

function StatusBadge({ status }: { status: string }) { return <span className={cn("inline-flex rounded-full px-3 py-1.5 text-sm", statusStyles[status] || "bg-muted text-muted-foreground")}>{labelStatus(status)}</span>; }
function Loading({ label }: { label: string }) { return <div className="flex min-h-[420px] items-center justify-center gap-2 rounded-xl border border-dashed text-base text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{label}</div>; }
function Empty({ label }: { label: string }) { return <div className="flex min-h-32 items-center justify-center px-6 text-center text-base text-muted-foreground"><ShieldAlert className="mr-2 h-4 w-4" />{label}</div>; }
