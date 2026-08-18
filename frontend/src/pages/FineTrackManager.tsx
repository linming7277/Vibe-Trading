import { useEffect, useMemo, useState } from "react";
import { ArrowRight, BarChart3, ChevronRight, Loader2, MessageCircle, RefreshCw, Search, SlidersHorizontal, X } from "lucide-react";
import { Link } from "react-router";
import { api, type FinancialAnalysisSnapshot, type FineTrackIndustry, type Level3Leader } from "@/lib/api";
import { FinancialAgentFloat } from "@/components/value/FinancialAgentFloat";
import { companyResearchPath } from "@/lib/routeContext";

type CatalogMeta = { level1: number; level2: number; level3: number };

type LeaderSnapshotCache = {
  industries: FineTrackIndustry[];
  leaderMap: Record<string, Level3Leader[]>;
  catalogMeta: CatalogMeta;
  snapshotAsOf: string | null;
  snapshotStatus: "ready" | "not_built";
  loadedAt: number;
};

const SNAPSHOT_CACHE_TTL_MS = 5 * 60 * 1000;
let leaderSnapshotCache: LeaderSnapshotCache | null = null;
let leaderSnapshotRequest: Promise<LeaderSnapshotCache> | null = null;
let leaderListScrollY = 0;
let leaderListViewState = { level1Filter: "", level2Filter: "", level3Query: "", query: "" };

const DIMENSIONS: Array<[string, string]> = [
  ["industry_position", "行业地位"], ["profitability", "盈利能力"],
  ["growth_stability", "成长稳定"], ["cash_flow", "现金流"],
  ["valuation", "估值"], ["governance_risk", "治理/稳健代理"],
];
const DIMENSION_WEIGHTS: Record<string, number> = {
  industry_position: 20, profitability: 20, growth_stability: 15,
  cash_flow: 15, valuation: 20, governance_risk: 10,
};

const DIMENSION_NOTES: Record<string, { strength: string; concern: string }> = {
  industry_position: { strength: "公司规模、营收或利润在该细分行业中处于前列", concern: "行业地位相对其他候选公司不够突出" },
  profitability: { strength: "ROE、毛利率和净利率在同行中表现较好", concern: "盈利能力是当前相对短板，需要核对利润质量" },
  growth_stability: { strength: "收入与利润增长更连续，历史波动相对较小", concern: "增长稳定性不足，需要确认是否存在周期性波动" },
  cash_flow: { strength: "经营现金流与利润匹配度相对较好", concern: "现金流是相对弱项，需要核对利润能否转化为现金" },
  valuation: { strength: "当前估值在同行中相对更有吸引力", concern: "估值优势不明显，仍需结合历史分位和安全边际复核" },
  governance_risk: { strength: "负债、股东稳定性和波动风险相对可控", concern: "稳健性指标相对偏弱，需要继续检查负债和波动风险" },
};

const FINANCIAL_STATUS_LABELS: Record<string, string> = {
  READY: "已完成", COMPLETED: "已生成", PARTIAL: "部分可用", LIMITED: "有限可用",
  INSUFFICIENT_DATA: "数据不足", CONFIGURATION_REQUIRED: "待配置财报研究员",
  NOT_RUN: "尚未生成解读", FAILED: "生成失败",
};
const CLAIM_LABELS: Record<string, string> = { FACT: "事实", INFERENCE: "推断", FORECAST: "情景", UNKNOWN: "待确认" };

function financialStatusLabel(status: string | undefined) {
  return status ? FINANCIAL_STATUS_LABELS[status] || status : "尚未生成";
}

function historyNumber(row: Record<string, number | string | null> | undefined, key: string) {
  const value = row?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function moneyYi(value: number | null) {
  return value == null ? "—" : `${(value / 100_000_000).toFixed(2)} 亿`;
}

function statusTone(status: string) {
  if (["READY", "COMPLETED", "IMPROVING"].includes(status)) return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  if (["FAILED", "WEAKENING"].includes(status)) return "bg-danger/10 text-danger";
  if (["PARTIAL", "LIMITED", "CONFIGURATION_REQUIRED", "VOLATILE"].includes(status)) return "bg-amber-500/10 text-amber-700 dark:text-amber-300";
  return "bg-muted text-muted-foreground";
}

function LeaderLink({ leader, onSelect }: { leader: Level3Leader; onSelect: () => void }) {
  return <button
    type="button"
    onClick={onSelect}
    aria-label={`查看 ${leader.stock_name} 研究摘要`}
    className="group flex min-w-0 items-center gap-2 rounded-md border border-border bg-background px-2.5 py-2 transition-colors hover:border-primary/50 hover:bg-primary/5"
  >
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-primary/10 text-[11px] font-bold text-primary">#{leader.leader_rank}</span>
    <span className="min-w-0 flex-1 truncate"><strong className="text-sm group-hover:text-primary">{leader.stock_name}</strong><span className="ml-1.5 font-mono text-[11px] text-muted-foreground">{leader.stock_code}</span></span>
    <span className="shrink-0 whitespace-nowrap text-right"><span className="text-sm font-semibold tabular-nums text-primary">{leader.leader_score.toFixed(1)}</span><span className="ml-1 text-[10px] text-muted-foreground">覆盖 {(leader.coverage * 100).toFixed(0)}%</span></span>
  </button>;
}

function IndustryLeaderRow({ industry, leaders, onSelect }: { industry: FineTrackIndustry; leaders: Level3Leader[]; onSelect: (leader: Level3Leader) => void }) {
  return <article className="rounded-lg border border-border bg-background p-3">
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      <h2 className="truncate font-semibold">{industry.level3_name}</h2>
      <span className="truncate text-xs text-muted-foreground">{industry.level1_name} <ChevronRight className="inline h-3 w-3" /> {industry.level2_name}</span>
      <span className="font-mono text-[10px] text-muted-foreground">{industry.level3_code} · {industry.member_count} 家</span>
    </div>
    {leaders.length > 0 ? <div className="mt-2 grid gap-1.5 sm:grid-cols-2">{leaders.map((leader) => <LeaderLink key={leader.stock_code} leader={leader} onSelect={() => onSelect(leader)} />)}</div> : <div className="mt-2 rounded-md border border-dashed border-border px-2.5 py-2 text-xs text-muted-foreground">该行业暂无符合研究条件的龙头公司</div>}
  </article>;
}

function CompanyResearchDrawer({ leader, financial, loading, error, onClose, onChat }: { leader: Level3Leader; financial: FinancialAnalysisSnapshot | null; loading: boolean; error: string; onClose: () => void; onChat: () => void }) {
  const parentLabel = `${leader.level3_name}龙头`;
  const companyPath = companyResearchPath(leader.stock_code, {
    from: "/value", fromLabel: parentLabel, sectorCode: leader.level3_code, sectorName: leader.level3_name,
  }, "overview");
  const trends = financial ? Object.entries(financial.feature.trends) : [];
  const scoreRows = DIMENSIONS.map(([key, label]) => ({ key, label, value: leader.component_scores[key], weight: DIMENSION_WEIGHTS[key] ?? 0 }))
    .filter((item) => item.value != null) as Array<{ key: string; label: string; value: number; weight: number }>;
  const strongest = [...scoreRows].sort((left, right) => right.value - left.value).slice(0, 2);
  const weakest = [...scoreRows].sort((left, right) => left.value - right.value).slice(0, 2);
  const analysis = financial?.analysis;
  const latestHistory = financial?.history?.slice().sort((left, right) => String(right.report_date || "").localeCompare(String(left.report_date || "")))[0];
  const keyMetrics = financial ? [
    ["最新报告期", String(latestHistory?.report_date || financial.historical_cutoff || "—")],
    ["营业收入", moneyYi(historyNumber(latestHistory, "revenue"))],
    ["净利润", moneyYi(historyNumber(latestHistory, "net_profit"))],
    ["经营现金流", moneyYi(historyNumber(latestHistory, "operating_cash_flow"))],
    ["ROE", historyNumber(latestHistory, "roe") == null ? "—" : `${historyNumber(latestHistory, "roe")!.toFixed(2)}%`],
    ["完整年度", `${financial.feature.data_quality.annual_period_count} 年`],
  ] : [];
  const scenarios = financial?.forecast?.scenarios ? Object.entries(financial.forecast.scenarios).filter(([, value]) => value) as Array<[string, NonNullable<FinancialAnalysisSnapshot["forecast"]["scenarios"]["BASE"]>]> : [];
  const trendLabels: Record<string, string> = { growth_trend: "经营增长", profitability_trend: "盈利能力", cash_flow_trend: "现金流", balance_sheet_trend: "资产负债" };
  const displayStatus: Record<string, string> = { IMPROVING: "改善", STABLE: "稳定", WEAKENING: "走弱", VOLATILE: "波动", INSUFFICIENT_DATA: "数据不足" };
  return <>
    <button type="button" aria-label="关闭公司研究摘要" className="fixed inset-0 z-40 cursor-default bg-black/35 backdrop-blur-[1px]" onClick={onClose} />
    <aside role="dialog" aria-modal="true" aria-label="公司研究摘要" className="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l border-border bg-background shadow-2xl">
      <header className="border-b border-border px-5 py-4">
        <div className="flex items-start justify-between gap-4"><div className="min-w-0"><p className="text-xs font-medium text-primary">{leader.level3_name} · 行业内龙头摘要</p><h2 className="mt-1 truncate text-xl font-semibold">{leader.stock_name} <span className="font-mono text-sm font-normal text-muted-foreground">{leader.stock_code}</span></h2><p className="mt-1 truncate text-xs text-muted-foreground">{leader.level1_name} <ChevronRight className="inline h-3 w-3" /> {leader.level2_name} <ChevronRight className="inline h-3 w-3" /> {leader.level3_name}</p></div><button type="button" aria-label="关闭公司研究摘要" onClick={onClose} className="rounded-md border border-border p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-4 w-4" /></button></div>
        <div className="mt-4 grid grid-cols-3 gap-2"><div className="rounded-md bg-primary/10 px-3 py-2"><div className="text-[11px] text-muted-foreground">细分行业位置</div><strong>第 {leader.leader_rank} 名</strong></div><div className="rounded-md bg-primary/10 px-3 py-2"><div className="text-[11px] text-muted-foreground">行业内相对评分</div><strong className="tabular-nums">{leader.leader_score.toFixed(1)} / 100</strong></div><div className="rounded-md bg-muted/60 px-3 py-2"><div className="text-[11px] text-muted-foreground">资料状态</div><strong className="text-sm">覆盖 {(leader.coverage * 100).toFixed(0)}%</strong></div></div>
        <p className="mt-3 text-[11px] leading-5 text-muted-foreground">评分只用于{leader.level3_name}行业内部排序，不代表预期收益，也不是买入建议。数据截至 {leader.as_of}。</p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <section className="rounded-xl border border-primary/20 bg-primary/[0.035] p-4"><h3 className="font-semibold">为什么入选</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">该公司在{leader.level3_name}行业内排名第 {leader.leader_rank}。{strongest.length ? `主要优势来自${strongest.map((item) => item.label).join("和")}。` : "当前评分证据仍需补充。"}{weakest.length ? `${weakest.map((item) => item.label).join("和")}是同行比较中的相对弱项，需要进一步核验。` : ""}</p></section>
        <section className="mt-4 grid gap-3 sm:grid-cols-2"><article className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3"><h3 className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">当前亮点</h3><div className="mt-2 space-y-2 text-xs leading-5">{strongest.length ? strongest.map((item) => <div key={item.key}><div className="font-medium">{item.label}</div><div className="text-muted-foreground">{DIMENSION_NOTES[item.key]?.strength || "该维度在同行中表现相对较好"}</div></div>) : <span className="text-muted-foreground">暂无有效评分</span>}</div></article><article className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3"><h3 className="text-sm font-semibold text-amber-700 dark:text-amber-300">重点复核</h3><div className="mt-2 space-y-2 text-xs leading-5">{weakest.length ? weakest.map((item) => <div key={item.key}><div className="font-medium">{item.label}</div><div className="text-muted-foreground">{DIMENSION_NOTES[item.key]?.concern || "该维度是同行比较中的相对弱项"}</div></div>) : <span className="text-muted-foreground">暂无有效评分</span>}</div></article></section>
        <details className="mt-3 rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">查看评分依据、权重与数据说明</summary><div className="space-y-4 border-t border-border p-3"><div className="grid grid-cols-2 gap-2">{DIMENSIONS.map(([key, label]) => { const value = leader.component_scores[key]; return <div key={key} className="rounded-lg bg-muted/40 px-3 py-2.5"><div className="flex justify-between gap-2 text-xs"><span className="text-muted-foreground">{label}<span className="ml-1 text-[10px]">权重 {DIMENSION_WEIGHTS[key]}%</span></span><strong className="tabular-nums">{value == null ? "—" : value.toFixed(1)}</strong></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} /></div></div>; })}</div><div className="grid gap-3 text-xs"><div><div className="font-medium">系统校验信息</div><div className="mt-1 text-muted-foreground">{leader.eligibility_reasons.length ? leader.eligibility_reasons.join("；") : "满足当前行业龙头筛选条件。"}</div></div>{leader.metric_applicability_notes.length ? <div><div className="font-medium">指标适用说明</div><div className="mt-1 text-muted-foreground">{leader.metric_applicability_notes.join("；")}</div></div> : null}<div className="text-muted-foreground">评分公式：{leader.leader_formula_version} · 数据覆盖 {(leader.coverage * 100).toFixed(0)}%</div></div></div></details>
        <section className="mt-6 border-t border-border pt-5"><div className="flex items-center justify-between"><div><h3 className="font-semibold">关键财务与研究进度</h3><p className="mt-1 text-xs text-muted-foreground">这里只读取现有快照；打开摘要不会自动调用财报研究员。</p></div>{loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}</div>
          {loading ? <div className="flex h-32 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取财务摘要…</div> : error ? <div className="mt-3 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error}</div> : financial ? <><div className="mt-3 grid grid-cols-3 gap-2"><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">历史财务</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(financial.feature_status)}`}>{financialStatusLabel(financial.feature_status)}</span></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">情景预测</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(financial.forecast_status)}`}>{financialStatusLabel(financial.forecast_status)}</span></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">财报研究员</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(financial.analysis_status)}`}>{financialStatusLabel(financial.analysis_status)}</span></div></div><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">{keyMetrics.map(([label, value]) => <div key={label} className="rounded-lg border border-border px-3 py-2"><div className="text-xs text-muted-foreground">{label}</div><strong className="mt-1 block text-sm tabular-nums">{value}</strong></div>)}</div>{trends.length ? <div className="mt-3 grid grid-cols-2 gap-2">{trends.map(([key, status]) => <div key={key} className="rounded-lg border border-border px-3 py-2"><div className="text-xs text-muted-foreground">{trendLabels[key] || key}</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(status)}`}>{displayStatus[status] || status}</span></div>)}</div> : null}{financial.data_gaps.length ? <div className="mt-3 rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">需要补充：{financial.data_gaps.join("、")}</div> : null}</> : <div className="mt-3 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">尚未生成财务分析快照。</div>}
        </section>
        {financial && !loading && !error ? <section className="mt-5 space-y-3 border-t border-border pt-5">
          {analysis ? <><article className="rounded-lg border border-primary/20 bg-primary/[0.04] p-3"><div className="flex items-start justify-between gap-3"><h3 className="font-semibold">财报研究员解读</h3><span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-[11px] text-primary">置信度：{analysis.confidence || "待评估"}</span></div><p className="mt-2 text-sm leading-6">{analysis.executive_summary}</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><div><div className="text-xs font-medium text-emerald-700 dark:text-emerald-300">财务优势</div><ul className="mt-1 space-y-1 text-xs text-muted-foreground">{analysis.financial_strengths.slice(0, 4).map((item) => <li key={item}>· {item}</li>)}</ul></div><div><div className="text-xs font-medium text-amber-700 dark:text-amber-300">财务风险</div><ul className="mt-1 space-y-1 text-xs text-muted-foreground">{analysis.financial_risks.slice(0, 4).map((item) => <li key={item}>· {item}</li>)}</ul></div></div></article><div className="grid gap-3 sm:grid-cols-2"><article className="rounded-lg border border-border p-3"><h3 className="text-sm font-semibold">持续跟踪指标</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{analysis.key_metrics_to_monitor?.slice(0, 6).join("、") || "暂无结构化跟踪指标"}</p></article><details className="rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">事实、推断与预测依据</summary><div className="space-y-2 border-t border-border p-3 text-xs">{analysis.claims?.slice(0, 6).map((claim, index) => <div key={`${claim.type}-${index}`} className="flex gap-2"><span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">{CLAIM_LABELS[claim.type] || claim.type}</span><span className="text-muted-foreground">{claim.statement}</span></div>) || <span className="text-muted-foreground">暂无可展示的结构化依据。</span>}</div></details></div></> : <div className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">财报研究员尚未生成结构化判断；打开公司研究后可查看历史数据并发起分析。</div>}
          <details className="rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">数据质量与预测情景</summary><div className="space-y-3 border-t border-border p-3 text-xs"><div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">财务覆盖</div><strong className="mt-1 block">{((financial.feature.data_quality.coverage ?? 0) * 100).toFixed(0)}%</strong></div><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">年度数据</div><strong className="mt-1 block">{financial.feature.data_quality.annual_period_count} 年</strong></div><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">特征版本</div><strong className="mt-1 block truncate" title={financial.financial_feature_version}>{financial.financial_feature_version}</strong></div><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">预测版本</div><strong className="mt-1 block truncate" title={financial.forecast_version}>{financial.forecast_version}</strong></div></div>{financial.feature.data_quality.missing_fields?.length ? <div className="text-amber-700 dark:text-amber-300">缺失：{financial.feature.data_quality.missing_fields.join("、")}</div> : null}{scenarios.length ? <div className="grid gap-2 sm:grid-cols-3">{scenarios.map(([key, scenario]) => <div key={key} className="rounded border border-border p-2"><div className="font-medium">{scenario.label || key}</div><div className="mt-1 text-muted-foreground">{scenario.assumption_notes?.slice(0, 2).join("；") || "暂无假设说明"}</div>{scenario.forecast?.slice(0, 2).map((point) => <div key={point.year} className="mt-2 border-t border-border pt-1 text-[11px] text-muted-foreground">{point.year} · 收入 {point.revenue == null ? "—" : point.revenue.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} · 净利 {point.net_profit == null ? "—" : point.net_profit.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}</div>)}</div>)}</div> : <div className="text-muted-foreground">暂无可用预测情景。</div>}</div></details>
        </section> : null}
      </div>
      <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-4"><button type="button" onClick={onClose} className="rounded-md border border-border px-3 py-2 text-sm">关闭</button><button type="button" onClick={onChat} className="inline-flex items-center gap-2 rounded-md border border-primary/40 px-3 py-2 text-sm text-primary hover:bg-primary/5"><MessageCircle className="h-4 w-4" />问财报研究员</button><Link to={companyPath} className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">打开公司研究 <ArrowRight className="h-4 w-4" /></Link></footer>
    </aside>
  </>;
}

export function FineTrackManager() {
  const [industries, setIndustries] = useState<FineTrackIndustry[]>(() => leaderSnapshotCache?.industries ?? []);
  const [leaderMap, setLeaderMap] = useState<Record<string, Level3Leader[]>>(() => leaderSnapshotCache?.leaderMap ?? {});
  const [catalogMeta, setCatalogMeta] = useState<CatalogMeta>(() => leaderSnapshotCache?.catalogMeta ?? { level1: 0, level2: 0, level3: 0 });
  const [snapshotAsOf, setSnapshotAsOf] = useState<string | null>(() => leaderSnapshotCache?.snapshotAsOf ?? null);
  const [snapshotStatus, setSnapshotStatus] = useState<"ready" | "not_built">(() => leaderSnapshotCache?.snapshotStatus ?? "not_built");
  const [selectedLeader, setSelectedLeader] = useState<Level3Leader | null>(null);
  const [chatTarget, setChatTarget] = useState<Level3Leader | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [financial, setFinancial] = useState<FinancialAnalysisSnapshot | null>(null);
  const [loadingFinancial, setLoadingFinancial] = useState(false);
  const [financialError, setFinancialError] = useState("");
  const [level1Filter, setLevel1Filter] = useState(() => leaderListViewState.level1Filter);
  const [level2Filter, setLevel2Filter] = useState(() => leaderListViewState.level2Filter);
  const [level3Query, setLevel3Query] = useState(() => leaderListViewState.level3Query);
  const [query, setQuery] = useState(() => leaderListViewState.query);
  const [loading, setLoading] = useState(() => !leaderSnapshotCache);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  function applySnapshot(cache: LeaderSnapshotCache) {
    setIndustries(cache.industries);
    setCatalogMeta(cache.catalogMeta);
    setLeaderMap(cache.leaderMap);
    setSnapshotAsOf(cache.snapshotAsOf);
    setSnapshotStatus(cache.snapshotStatus);
  }

  async function loadSnapshot({ force = false, background = false } = {}) {
    const cached = leaderSnapshotCache;
    if (!force && cached && Date.now() - cached.loadedAt < SNAPSHOT_CACHE_TTL_MS) {
      applySnapshot(cached);
      return;
    }
    if (background) setRefreshing(true); else setLoading(true);
    setError("");
    try {
      if (!leaderSnapshotRequest) {
        leaderSnapshotRequest = Promise.all([api.getFineTrackIndustries(), api.getAllLevel3Leaders(2)]).then(([catalog, snapshot]) => {
          // The catalogue already represents terminal industries.  Some TDX terminal
          // nodes originate at level 2, but are intentionally retained as a "三级/末级" row.
          const next: LeaderSnapshotCache = {
            industries: catalog.items,
            catalogMeta: { level1: catalog.level1_total, level2: catalog.level2_total, level3: catalog.level3_total },
            leaderMap: snapshot.items,
            snapshotAsOf: snapshot.as_of,
            snapshotStatus: snapshot.snapshot_status,
            loadedAt: Date.now(),
          };
          leaderSnapshotCache = next;
          return next;
        }).finally(() => { leaderSnapshotRequest = null; });
      }
      const next = await leaderSnapshotRequest;
      applySnapshot(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }

  useEffect(() => {
    const cached = leaderSnapshotCache;
    if (cached && Date.now() - cached.loadedAt < SNAPSHOT_CACHE_TTL_MS) {
      applySnapshot(cached);
      if (leaderListScrollY > 0) requestAnimationFrame(() => window.scrollTo({ top: leaderListScrollY }));
      return () => { leaderListScrollY = window.scrollY; };
    }
    void loadSnapshot({ background: Boolean(cached) });
    if (leaderListScrollY > 0) requestAnimationFrame(() => window.scrollTo({ top: leaderListScrollY }));
    return () => { leaderListScrollY = window.scrollY; };
  }, []);

  useEffect(() => () => {
    leaderListViewState = { level1Filter, level2Filter, level3Query, query };
  }, [level1Filter, level2Filter, level3Query, query]);

  useEffect(() => {
    if (!selectedLeader) { setFinancial(null); setFinancialError(""); return; }
    let cancelled = false;
    setLoadingFinancial(true); setFinancial(null); setFinancialError("");
    api.getCompanyFinancialAnalysis(selectedLeader.stock_code, selectedLeader.as_of)
      .then((value) => { if (!cancelled) setFinancial(value); })
      .catch((reason) => { if (!cancelled) setFinancialError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!cancelled) setLoadingFinancial(false); });
    return () => { cancelled = true; };
  }, [selectedLeader]);

  const level1Rows = useMemo(() => Array.from(new Map(industries.map((row) => [row.level1_code, { code: row.level1_code, name: row.level1_name }])).values()), [industries]);
  const level2Rows = useMemo(() => Array.from(new Map(industries.filter((row) => !level1Filter || row.level1_code === level1Filter).map((row) => [row.level2_code, { code: row.level2_code, name: row.level2_name }])).values()), [industries, level1Filter]);
  const level3Rows = useMemo(() => Array.from(new Map(industries
    .filter((row) => (!level1Filter || row.level1_code === level1Filter) && (!level2Filter || row.level2_code === level2Filter))
    .map((row) => [row.level3_code, { code: row.level3_code, name: row.level3_name, member_count: row.member_count }])).values()), [industries, level1Filter, level2Filter]);
  const filteredIndustries = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const level3Needle = level3Query.trim().toLowerCase();
    return industries.filter((row) => {
      if (level1Filter && row.level1_code !== level1Filter) return false;
      if (level2Filter && row.level2_code !== level2Filter) return false;
      if (level3Needle && ![row.level3_name, row.level3_code].some((value) => value.toLowerCase().includes(level3Needle))) return false;
      return !needle || [row.level1_name, row.level2_name, row.level3_name, row.level3_code].some((value) => value.toLowerCase().includes(needle));
    });
  }, [industries, level1Filter, level2Filter, level3Query, query]);
  const leaderCount = useMemo(() => Object.values(leaderMap).reduce((total, leaders) => total + leaders.length, 0), [leaderMap]);
  const industryWithLeaders = useMemo(() => Object.keys(leaderMap).length, [leaderMap]);

  function changeLevel1(code: string) {
    setLevel1Filter(code);
    setLevel2Filter("");
    setLevel3Query("");
  }

  function clearFilters() {
    setLevel1Filter("");
    setLevel2Filter("");
    setLevel3Query("");
    setQuery("");
  }

  const selectedLevel1Name = level1Rows.find((row) => row.code === level1Filter)?.name;
  const selectedLevel2Name = level2Rows.find((row) => row.code === level2Filter)?.name;
  const hasFilters = Boolean(level1Filter || level2Filter || level3Query || query);

  return <div className="w-full space-y-4 p-3 md:p-4">
    <header className="flex flex-col justify-between gap-4 rounded-xl border border-border bg-card px-5 py-4 lg:flex-row lg:items-center">
      <div>
        <div className="flex items-center gap-2"><BarChart3 className="h-5 w-5 text-primary" /><h1 className="text-xl font-semibold">三级行业龙头名单</h1></div>
        <p className="mt-1 text-sm text-muted-foreground">每个通达信三级/末级行业列出行业内前两家公司；点击公司先看入选原因，再打开统一公司研究。</p>
      </div>
      <div className="flex items-center gap-2"><div className="flex divide-x divide-border rounded-lg border border-border text-center text-sm"><div className="px-4 py-2"><strong className="block tabular-nums">{catalogMeta.level1 || "—"}</strong><span className="text-xs text-muted-foreground">一级行业</span></div><div className="px-4 py-2"><strong className="block tabular-nums">{catalogMeta.level2 || "—"}</strong><span className="text-xs text-muted-foreground">二级行业</span></div><div className="px-4 py-2"><strong className="block tabular-nums">{catalogMeta.level3 || "—"}</strong><span className="text-xs text-muted-foreground">三级/末级行业</span></div></div><button type="button" onClick={() => void loadSnapshot({ force: true, background: Boolean(leaderSnapshotCache) })} disabled={loading || refreshing} className="inline-flex h-10 items-center gap-2 rounded-md border border-border px-3 text-sm text-muted-foreground hover:bg-muted disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />{refreshing ? "更新中" : "刷新"}</button></div>
    </header>

    {error && <div role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>}

    <section className="rounded-xl border border-border bg-card">
      <div className="grid gap-2 border-b border-border p-3 lg:grid-cols-[auto_minmax(220px,1fr)_repeat(3,minmax(150px,1fr))] lg:items-center">
        <div className="flex shrink-0 items-center gap-2 px-1 text-sm font-semibold"><SlidersHorizontal className="h-4 w-4 text-primary" />龙头列表</div>
        <label className="flex min-w-0 items-center rounded-md border border-border bg-background px-2"><Search className="h-4 w-4 text-muted-foreground" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索行业或行业代码" className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none" /></label>
        <select aria-label="一级行业筛选" value={level1Filter} onChange={(event) => changeLevel1(event.target.value)} className="min-w-0 rounded-md border border-border bg-background px-2 py-2 text-sm"><option value="">全部一级行业</option>{level1Rows.map((row) => <option key={row.code} value={row.code}>{row.name}</option>)}</select>
        <select aria-label="二级行业筛选" value={level2Filter} onChange={(event) => { setLevel2Filter(event.target.value); setLevel3Query(""); }} className="min-w-0 rounded-md border border-border bg-background px-2 py-2 text-sm"><option value="">全部二级行业</option>{level2Rows.map((row) => <option key={row.code} value={row.code}>{row.name}</option>)}</select>
        <label className="flex min-w-0 items-center rounded-md border border-border bg-background px-2"><Search className="h-4 w-4 text-muted-foreground" /><input aria-label="三级行业快速筛选" list="level3-industry-options" value={level3Query} onChange={(event) => setLevel3Query(event.target.value)} placeholder="搜索三级/末级行业" className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none" /><datalist id="level3-industry-options">{level3Rows.map((row) => <option key={row.code} value={row.name}>{row.code} · {row.member_count} 家</option>)}</datalist></label>
      </div>

      {hasFilters ? <div className="flex flex-wrap items-center gap-2 border-b border-border bg-primary/[0.03] px-3 py-2 text-xs"><span className="text-muted-foreground">当前筛选：</span>{selectedLevel1Name ? <span className="rounded-full bg-primary/10 px-2 py-1 text-primary">{selectedLevel1Name}</span> : null}{selectedLevel2Name ? <span className="rounded-full bg-primary/10 px-2 py-1 text-primary">{selectedLevel2Name}</span> : null}{level3Query ? <span className="rounded-full bg-primary/10 px-2 py-1 text-primary">{level3Query}</span> : null}{query ? <span className="rounded-full bg-muted px-2 py-1 text-muted-foreground">关键词：{query}</span> : null}<button type="button" onClick={clearFilters} className="ml-auto text-primary hover:underline">清除筛选</button></div> : null}
      <div className="flex flex-wrap items-center justify-between gap-2 bg-muted/25 px-3 py-2 text-xs text-muted-foreground"><span>显示 {filteredIndustries.length} / {industries.length} 个三级/末级行业</span><span>{snapshotStatus === "ready" ? <>快照日期 {snapshotAsOf} · {industryWithLeaders} 个行业 · {leaderCount} 家具体龙头</> : "尚未生成龙头快照"}</span></div>

      {loading ? <div className="flex h-80 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />加载三级行业龙头名单…</div> : snapshotStatus === "not_built" ? <div className="p-12 text-center"><p className="text-sm text-muted-foreground">尚未生成三级行业 Leader 快照，请先运行全量构建脚本。</p></div> : filteredIndustries.length === 0 ? <div className="p-12 text-center text-sm text-muted-foreground">没有匹配的三级/末级行业</div> : <div className="grid gap-2 p-2 lg:grid-cols-2">{filteredIndustries.map((industry) => <IndustryLeaderRow key={industry.level3_code} industry={industry} leaders={leaderMap[industry.level3_code] ?? []} onSelect={setSelectedLeader} />)}</div>}
    </section>

    <p className="px-1 text-xs text-muted-foreground">行业内龙头评分只用于同一三级行业内部排序，不用于跨行业比较，也不代表预期收益或买入建议。</p>
    <button type="button" aria-label="打开财报研究员" onClick={() => { setChatTarget(null); setChatOpen(true); }} className="fixed bottom-5 right-5 z-30 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-3 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/25 transition-transform hover:-translate-y-0.5"><MessageCircle className="h-4 w-4" />财报研究员</button>
    {selectedLeader ? <CompanyResearchDrawer leader={selectedLeader} financial={financial} loading={loadingFinancial} error={financialError} onClose={() => setSelectedLeader(null)} onChat={() => { setChatTarget(selectedLeader); setChatOpen(true); }} /> : null}
    <FinancialAgentFloat open={chatOpen} target={chatTarget} candidates={Object.values(leaderMap).flat()} onClose={() => setChatOpen(false)} />
  </div>;
}
