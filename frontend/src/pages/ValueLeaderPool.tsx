import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowRight, BarChart3, ChevronRight, HelpCircle, Info, Loader2, MessageCircle, RefreshCw, Search, SlidersHorizontal, X } from "lucide-react";
import { Link } from "react-router";
import { api, type FinancialAnalysisSnapshot, type LeaderFormulaContract, type LeaderIndustrySummary, type Level3Leader, type ValueIndustry } from "@/lib/api";
import { CompanyResearchModalDetails } from "@/components/value/CompanyResearchModalDetails";
import { LeaderCompanyQuickView } from "@/components/value/LeaderCompanyQuickView";
import { leaderValuationStatusLabel, LeaderValuationStatusBadge, type LeaderValuationStatus } from "@/components/value/LeaderValuationStatus";

type CatalogMeta = { level1: number; level2: number; level3: number };
type LeaderValuationSnapshot = {
  status: "READY" | "MISSING" | string;
  total: number;
  expected: number;
  data_as_of: string | null;
  is_complete?: boolean;
  items: Record<string, { presentation_status: LeaderValuationStatus }>;
};

type LeaderSnapshotCache = {
  industries: ValueIndustry[];
  leaderMap: Record<string, Level3Leader[]>;
  catalogMeta: CatalogMeta;
  snapshotAsOf: string | null;
  snapshotStatus: "ready" | "not_built";
  formula?: LeaderFormulaContract;
  industrySummaries: Record<string, LeaderIndustrySummary>;
  valuationSnapshot: LeaderValuationSnapshot;
  loadedAt: number;
};

let leaderSnapshotCache: LeaderSnapshotCache | null = null;
let leaderSnapshotRequest: Promise<LeaderSnapshotCache> | null = null;
let leaderListScrollY = 0;
let leaderListViewState = { level1Filter: "", level2Filter: "", level3Query: "", query: "", valuationStatusFilter: "" as LeaderValuationStatus | "" };

const DIMENSIONS: Array<[string, string]> = [
  ["industry_position", "行业地位"], ["profitability", "盈利能力"],
  ["growth_stability", "成长稳定"], ["cash_flow", "现金流"],
  ["valuation", "估值"], ["governance_risk", "治理/稳健代理"],
];
const DIMENSION_WEIGHTS: Record<string, number> = {
  industry_position: 25, profitability: 20, growth_stability: 15,
  cash_flow: 15, valuation: 15, governance_risk: 10,
};

const DIMENSION_NOTES: Record<string, { strength: string; concern: string }> = {
  industry_position: { strength: "公司规模、营收或利润在该细分行业中处于前列", concern: "规模与经营地位相对其他候选公司不够突出" },
  profitability: { strength: "ROE、毛利率和净利率在同行中表现较好", concern: "盈利能力是当前相对短板，需要核对利润质量" },
  growth_stability: { strength: "收入与利润增长更连续，历史波动相对较小", concern: "增长稳定性不足，需要确认是否存在周期性波动" },
  cash_flow: { strength: "经营现金流与利润匹配度相对较好", concern: "现金流是相对弱项，需要核对利润能否转化为现金" },
  valuation: { strength: "当前估值在同行中相对更有吸引力", concern: "估值优势不明显，仍需结合历史分位和安全边际复核" },
  governance_risk: { strength: "负债、股东人数变化和市场波动相对可控", concern: "财务稳健与波动指标相对偏弱，需要继续检查负债和Beta" },
};

function formulaDimension(formula: LeaderFormulaContract | undefined, key: string) {
  return formula?.dimensions.find((item) => item.key === key);
}

function formatRawMetric(key: string, value: number | null, unit: string) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (unit === "元") return moneyYi(value);
  if (key === "debt_safety") return `${Math.abs(value).toFixed(2)}% 负债率`;
  if (key === "shareholder_stability") return `${Math.abs(value).toFixed(2)}% 变化`;
  if (key === "low_beta") return `${Math.abs(value).toFixed(2)} Beta绝对值`;
  if (key === "growth_low_volatility") return `${Math.abs(value).toFixed(2)} 波动值`;
  if (unit === "%") return `${value.toFixed(2)}%`;
  if (unit === "亿元") return `${value.toFixed(2)} 亿`;
  if (unit === "倍") return `${value.toFixed(2)} 倍`;
  return value.toFixed(2);
}

const FINANCIAL_STATUS_LABELS: Record<string, string> = {
  READY: "已完成", COMPLETED: "已生成", PARTIAL: "部分可用", LIMITED: "有限可用",
  INSUFFICIENT_DATA: "数据不足", CONFIGURATION_REQUIRED: "待配置财报研究员",
  NOT_RUN: "尚未生成解读", FAILED: "生成失败",
};
const CLAIM_LABELS: Record<string, string> = { FACT: "事实依据", INFERENCE: "分析判断", FORECAST: "情景预测", UNKNOWN: "暂无足够资料" };

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

function LeaderLink({ leader, onSelect, valuationStatus }: { leader: Level3Leader; onSelect: () => void; valuationStatus: LeaderValuationStatus }) {
  const strongest = leader.explanation?.strongest?.map((item) => item.label).join("、");
  const weakest = leader.explanation?.weakest?.map((item) => item.label).join("、");
  return <button
    type="button"
    onClick={onSelect}
    aria-label={`查看 ${leader.stock_name} 研究摘要`}
    className="group min-w-0 rounded-md border border-border bg-background px-3 py-2.5 text-left transition-colors hover:border-primary/50 hover:bg-primary/5"
  >
    <span className="flex min-w-0 items-center gap-2"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-primary/10 text-[11px] font-bold text-primary">#{leader.leader_rank}</span><span className="min-w-0 flex-1 truncate"><strong className="text-sm group-hover:text-primary">{leader.stock_name}</strong><span className="ml-1.5 font-mono text-[11px] text-muted-foreground">{leader.stock_code}</span></span><span className="shrink-0 text-sm font-semibold tabular-nums text-primary">{leader.leader_score.toFixed(1)}</span></span>
    <span className="mt-2 flex items-center gap-1.5"><span className="text-[10px] text-muted-foreground">历史估值位置</span><LeaderValuationStatusBadge status={valuationStatus} /></span>
    <span className="mt-2 block text-[11px] leading-5 text-muted-foreground">{strongest ? <>靠前：{strongest}</> : "评分依据待补充"}{weakest ? <> · 复核：{weakest}</> : null}</span>
  </button>;
}

function IndustryLeaderRow({ industry, leaders, summary, onSelect, valuationStatuses }: { industry: ValueIndustry; leaders: Level3Leader[]; summary?: LeaderIndustrySummary; onSelect: (leader: Level3Leader) => void; valuationStatuses: Record<string, LeaderValuationStatus> }) {
  const [expanded, setExpanded] = useState(false);
  const [ranking, setRanking] = useState<Awaited<ReturnType<typeof api.getLevel3IndustryLeaders>> | null>(null);
  const [loadingRanking, setLoadingRanking] = useState(false);
  const [rankingError, setRankingError] = useState("");
  async function toggleRanking() {
    const next = !expanded;
    setExpanded(next);
    if (!next || ranking) return;
    setLoadingRanking(true); setRankingError("");
    try { setRanking(await api.getLevel3IndustryLeaders(industry.level3_code, 100)); }
    catch (reason) { setRankingError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoadingRanking(false); }
  }
  const eligibleCount = summary?.eligible_count ?? leaders[0]?.explanation?.eligible_count;
  return <article className="rounded-lg border border-border bg-background p-3">
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      <h2 className="truncate font-semibold">{industry.level3_name}</h2>
      <span className="truncate text-xs text-muted-foreground">{industry.level1_name} <ChevronRight className="inline h-3 w-3" /> {industry.level2_name}</span>
      <span className="font-mono text-[10px] text-muted-foreground">{industry.level3_code}</span>
    </div>
    <div className="mt-1 text-xs text-muted-foreground">{summary?.member_count ?? industry.member_count} 家成分 · {eligibleCount ?? "—"} 家可评分 · 前2进入量化候选</div>
    {summary?.sample_warning ? <div className="mt-2 flex items-start gap-1.5 rounded bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{summary.sample_warning}</div> : null}
    {leaders.length > 0 ? <div className="mt-2 grid gap-1.5 sm:grid-cols-2">{leaders.map((leader) => <LeaderLink key={leader.stock_code} leader={leader} onSelect={() => onSelect(leader)} valuationStatus={valuationStatuses[leader.stock_code] || "INSUFFICIENT_DATA"} />)}</div> : <div className="mt-2 rounded-md border border-dashed border-border px-2.5 py-2 text-xs text-muted-foreground">该行业暂无符合当前筛选条件的量化候选</div>}
    <button type="button" onClick={() => void toggleRanking()} className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">{expanded ? "收起行业比较" : "查看全行业排名与排除原因"}<ChevronRight className={`h-3 w-3 transition-transform ${expanded ? "rotate-90" : ""}`} /></button>
    {expanded ? <div className="mt-3 border-t border-border pt-3">{loadingRanking ? <div className="flex items-center py-3 text-xs text-muted-foreground"><Loader2 className="mr-2 h-3 w-3 animate-spin" />读取完整行业排名…</div> : rankingError ? <div className="text-xs text-danger">{rankingError}</div> : ranking ? <div className="space-y-3"><div className="max-h-64 overflow-y-auto rounded border border-border"><table className="w-full text-xs"><thead className="sticky top-0 bg-muted text-muted-foreground"><tr><th className="px-2 py-2 text-left">排名</th><th className="px-2 py-2 text-left">公司</th><th className="px-2 py-2 text-right">同行相对分</th><th className="px-2 py-2 text-right">指标覆盖</th></tr></thead><tbody>{ranking.items.map((item) => <tr key={item.stock_code} className="border-t border-border/70"><td className="px-2 py-2">#{item.leader_rank}{item.leader_rank <= 2 ? <span className="ml-1 text-primary">候选</span> : null}</td><td className="px-2 py-2"><button type="button" onClick={() => onSelect(item)} className="font-medium hover:text-primary">{item.stock_name}</button><span className="ml-1 font-mono text-[10px] text-muted-foreground">{item.stock_code}</span></td><td className="px-2 py-2 text-right tabular-nums">{item.leader_score.toFixed(1)}</td><td className="px-2 py-2 text-right">{item.raw_metric_available ?? "—"}/{item.raw_metric_total ?? "—"}</td></tr>)}</tbody></table></div>{ranking.excluded_items?.length ? <details className="rounded border border-border"><summary className="cursor-pointer px-3 py-2 text-xs font-medium">未参与评分 {ranking.excluded_items.length} 家</summary><div className="space-y-2 border-t border-border p-3">{ranking.excluded_items.map((item) => <div key={item.stock_code} className="flex justify-between gap-3 text-xs"><span>{item.stock_name} <span className="font-mono text-[10px] text-muted-foreground">{item.stock_code}</span></span><span className="text-right text-muted-foreground">{item.eligibility_reason_labels?.join("；") || item.eligibility_reasons.join("；")}</span></div>)}</div></details> : null}</div> : null}</div> : null}
  </article>;
}

/** @deprecated Retained for consumers during the transition to LeaderCompanyQuickView. */
export function CompanyResearchDrawer({ leader, formula, financial, loading, error, onClose, onChat }: { leader: Level3Leader; formula?: LeaderFormulaContract; financial: FinancialAnalysisSnapshot | null; loading: boolean; error: string; onClose: () => void; onChat: () => void }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  const trends = financial ? Object.entries(financial.feature.trends) : [];
  const scoreRows = (leader.components?.map((item) => ({
    key: item.key, label: item.label, value: item.score, weight: item.weight * 100,
    contribution: item.contribution, coverage: item.coverage, reweighted: item.reweighted, metrics: item.metrics,
  })) ?? DIMENSIONS.map(([key, label]) => ({
    key, label: formulaDimension(formula, key)?.label || label, value: leader.component_scores[key],
    weight: (formulaDimension(formula, key)?.weight ?? (DIMENSION_WEIGHTS[key] / 100)) * 100,
    contribution: null, coverage: 0, reweighted: false, metrics: [],
  }))).filter((item) => item.value != null) as Array<{ key: string; label: string; value: number; weight: number; contribution: number | null; coverage: number; reweighted: boolean; metrics: NonNullable<Level3Leader["components"]>[number]["metrics"] }>;
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
    <section role="dialog" aria-modal="true" aria-label="公司研究弹窗" className="fixed inset-3 z-50 flex flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl md:inset-y-6 md:left-1/2 md:right-auto md:w-[min(1180px,calc(100vw-3rem))] md:-translate-x-1/2">
      <header className="border-b border-border px-5 py-4">
        <div className="flex items-start justify-between gap-4"><div className="min-w-0"><p className="text-xs font-medium text-primary">{leader.level3_name} · 量化龙头候选解释</p><h2 className="mt-1 truncate text-xl font-semibold">{leader.stock_name} <span className="font-mono text-sm font-normal text-muted-foreground">{leader.stock_code}</span></h2><p className="mt-1 truncate text-xs text-muted-foreground">{leader.level1_name} <ChevronRight className="inline h-3 w-3" /> {leader.level2_name} <ChevronRight className="inline h-3 w-3" /> {leader.level3_name}</p></div><button type="button" aria-label="关闭公司研究摘要" onClick={onClose} className="rounded-md border border-border p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-4 w-4" /></button></div>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4"><div className="rounded-md bg-primary/10 px-3 py-2"><div className="text-[11px] text-muted-foreground">同行位置</div><strong>第 {leader.leader_rank} / {leader.explanation?.eligible_count ?? "—"} 名</strong></div><div className="rounded-md bg-primary/10 px-3 py-2"><div className="text-[11px] text-muted-foreground">同行相对分</div><strong className="tabular-nums">{leader.leader_score.toFixed(1)}</strong></div><div className="rounded-md bg-muted/60 px-3 py-2"><div className="text-[11px] text-muted-foreground">原始指标</div><strong className="text-sm">{leader.raw_metric_available ?? "—"}/{leader.raw_metric_total ?? "—"}</strong></div><div className="rounded-md bg-muted/60 px-3 py-2"><div className="text-[11px] text-muted-foreground">排名证据日期</div><strong className="text-sm">{leader.as_of}</strong></div></div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <section className="rounded-xl border border-primary/20 bg-primary/[0.035] p-4"><h3 className="font-semibold">结论先行</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{leader.explanation?.summary || `该公司在${leader.level3_name}行业内排名第 ${leader.leader_rank}。`}</p><p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground"><Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />{leader.explanation?.score_interpretation || formula?.comparison_scope || "评分只用于同行内部排序。"}</p>{leader.explanation?.sample_warning ? <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{leader.explanation.sample_warning}</p> : null}{leader.explanation?.overall_reweighted ? <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />缺少{leader.explanation.missing_dimensions?.map((item) => item.label).join("、") || "部分维度"}，最终分数已按剩余维度重新分配权重。</p> : null}</section>
        <section className="mt-4 grid gap-3 sm:grid-cols-2"><article className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3"><h3 className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">当前亮点</h3><div className="mt-2 space-y-2 text-xs leading-5">{strongest.length ? strongest.map((item) => <div key={item.key}><div className="font-medium">{item.label}</div><div className="text-muted-foreground">{DIMENSION_NOTES[item.key]?.strength || "该维度在同行中表现相对较好"}</div></div>) : <span className="text-muted-foreground">暂无有效评分</span>}</div></article><article className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3"><h3 className="text-sm font-semibold text-amber-700 dark:text-amber-300">重点复核</h3><div className="mt-2 space-y-2 text-xs leading-5">{weakest.length ? weakest.map((item) => <div key={item.key}><div className="font-medium">{item.label}</div><div className="text-muted-foreground">{DIMENSION_NOTES[item.key]?.concern || "该维度是同行比较中的相对弱项"}</div></div>) : <span className="text-muted-foreground">暂无有效评分</span>}</div></article></section>
        <CompanyResearchModalDetails stockCode={leader.stock_code} />
        <details className="mt-3 rounded-lg border border-border" open><summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">评分贡献与原始依据</summary><div className="space-y-3 border-t border-border p-3">{scoreRows.map((component) => <article key={component.key} className="rounded-lg border border-border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><strong className="text-sm">{component.label}</strong><span className="ml-2 text-[11px] text-muted-foreground">权重 {component.weight.toFixed(0)}%</span>{component.reweighted ? <span className="ml-2 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-700 dark:text-amber-300">缺失重加权</span> : null}</div><div className="text-right"><strong className="tabular-nums">{component.value.toFixed(1)}</strong><span className="ml-2 text-[11px] text-muted-foreground">贡献 {component.contribution == null ? "—" : component.contribution.toFixed(1)}</span></div></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(0, Math.min(100, component.value))}%` }} /></div>{component.metrics.length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[430px] text-xs"><thead className="text-muted-foreground"><tr><th className="pb-1 text-left">指标及含义</th><th className="pb-1 text-right">原始值</th><th className="pb-1 text-right">同行分位</th><th className="pb-1 text-right">内部权重</th></tr></thead><tbody>{component.metrics.map((metric) => <tr key={metric.key} className="border-t border-border/60"><td className="py-1.5 pr-3"><div className="font-medium">{metric.label}</div><div className="mt-0.5 max-w-[230px] text-[10px] leading-4 text-muted-foreground">{metric.description}</div></td><td className="py-1.5 text-right tabular-nums">{formatRawMetric(metric.key, metric.raw_value, metric.unit)}</td><td className="py-1.5 text-right tabular-nums">{metric.percentile == null ? "缺失" : metric.percentile.toFixed(1)}</td><td className="py-1.5 text-right">{(metric.weight * 100).toFixed(0)}%</td></tr>)}</tbody></table></div> : <p className="mt-2 text-xs text-muted-foreground">当前旧快照未保存原始指标解释，重新生成排名后可展示。</p>}</article>)}</div></details>
        <details className="mt-3 rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">数据质量、公式与局限</summary><div className="space-y-3 border-t border-border p-3 text-xs text-muted-foreground"><div className="grid grid-cols-2 gap-2"><div>维度覆盖：{(leader.coverage * 100).toFixed(0)}%</div><div>原始指标：{leader.raw_metric_available ?? "—"}/{leader.raw_metric_total ?? "—"}</div><div>成分公司：{leader.explanation?.member_count ?? "—"} 家</div><div>可评分公司：{leader.explanation?.eligible_count ?? "—"} 家</div></div>{leader.explanation?.missing_dimensions?.length ? <p className="text-amber-700 dark:text-amber-300">缺失维度：{leader.explanation.missing_dimensions.map((item) => item.label).join("、")}。最终分数使用剩余维度重加权。</p> : null}<p>{formula?.normalization || "行业内缩尾后计算相对分位。"}</p><p>公式：{leader.leader_formula_version}。维度覆盖表示参与最终加权的维度权重，不代表数据准确率或投资胜率。</p>{leader.metric_applicability_notes.length ? <p className="text-amber-700 dark:text-amber-300">{leader.metric_applicability_notes.join("；")}</p> : null}</div></details>
        <section className="mt-6 border-t border-border pt-5"><div className="flex items-center justify-between"><div><h3 className="font-semibold">关键财务与研究进度</h3></div>{loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}</div>
          {loading ? <div className="flex h-32 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取财务摘要…</div> : error ? <div className="mt-3 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error}</div> : financial ? <><div className="mt-3 grid grid-cols-3 gap-2"><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">历史财务</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(financial.feature_status)}`}>{financialStatusLabel(financial.feature_status)}</span></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">情景预测</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(financial.forecast_status)}`}>{financialStatusLabel(financial.forecast_status)}</span></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">财报研究员</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(financial.analysis_status)}`}>{financialStatusLabel(financial.analysis_status)}</span></div></div><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">{keyMetrics.map(([label, value]) => <div key={label} className="rounded-lg border border-border px-3 py-2"><div className="text-xs text-muted-foreground">{label}</div><strong className="mt-1 block text-sm tabular-nums">{value}</strong></div>)}</div>{trends.length ? <div className="mt-3 grid grid-cols-2 gap-2">{trends.map(([key, status]) => <div key={key} className="rounded-lg border border-border px-3 py-2"><div className="text-xs text-muted-foreground">{trendLabels[key] || key}</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${statusTone(status)}`}>{displayStatus[status] || status}</span></div>)}</div> : null}{financial.data_gaps.length ? <div className="mt-3 rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">需要补充：{financial.data_gaps.join("、")}</div> : null}</> : <div className="mt-3 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">尚未生成财务分析快照。</div>}
        </section>
        {financial && !loading && !error ? <section className="mt-5 space-y-3 border-t border-border pt-5">
          {analysis ? <><article className="rounded-lg border border-primary/20 bg-primary/[0.04] p-3"><div className="flex items-start justify-between gap-3"><h3 className="font-semibold">财报研究员解读</h3><span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-[11px] text-primary">置信度：{analysis.confidence || "待评估"}</span></div><p className="mt-2 text-sm leading-6">{analysis.executive_summary}</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><div><div className="text-xs font-medium text-emerald-700 dark:text-emerald-300">财务优势</div><ul className="mt-1 space-y-1 text-xs text-muted-foreground">{analysis.financial_strengths.slice(0, 4).map((item) => <li key={item}>· {item}</li>)}</ul></div><div><div className="text-xs font-medium text-amber-700 dark:text-amber-300">财务风险</div><ul className="mt-1 space-y-1 text-xs text-muted-foreground">{analysis.financial_risks.slice(0, 4).map((item) => <li key={item}>· {item}</li>)}</ul></div></div></article><div className="grid gap-3 sm:grid-cols-2"><article className="rounded-lg border border-border p-3"><h3 className="text-sm font-semibold">持续跟踪指标</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{analysis.key_metrics_to_monitor?.slice(0, 6).join("、") || "暂无结构化跟踪指标"}</p></article><details className="rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">研究观点与依据</summary><div className="space-y-2 border-t border-border p-3 text-xs">{analysis.claims?.slice(0, 6).map((claim, index) => <div key={`${claim.type}-${index}`} className="flex gap-2"><span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">{CLAIM_LABELS[claim.type] || "研究观点"}</span><span className="text-muted-foreground">{claim.statement}</span></div>) || <span className="text-muted-foreground">暂无可展示的研究依据。</span>}</div></details></div></> : null}
          <details className="rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2.5 text-sm font-medium">数据质量与预测情景</summary><div className="space-y-3 border-t border-border p-3 text-xs"><div className="grid grid-cols-2 gap-2 sm:grid-cols-4"><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">财务覆盖</div><strong className="mt-1 block">{((financial.feature.data_quality.coverage ?? 0) * 100).toFixed(0)}%</strong></div><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">年度数据</div><strong className="mt-1 block">{financial.feature.data_quality.annual_period_count} 年</strong></div><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">特征版本</div><strong className="mt-1 block truncate" title={financial.financial_feature_version}>{financial.financial_feature_version}</strong></div><div className="rounded bg-muted/50 p-2"><div className="text-muted-foreground">预测版本</div><strong className="mt-1 block truncate" title={financial.forecast_version}>{financial.forecast_version}</strong></div></div>{financial.feature.data_quality.missing_fields?.length ? <div className="text-amber-700 dark:text-amber-300">缺失：{financial.feature.data_quality.missing_fields.join("、")}</div> : null}{scenarios.length ? <div className="grid gap-2 sm:grid-cols-3">{scenarios.map(([key, scenario]) => <div key={key} className="rounded border border-border p-2"><div className="font-medium">{scenario.label || key}</div><div className="mt-1 text-muted-foreground">{scenario.assumption_notes?.slice(0, 2).join("；") || "暂无假设说明"}</div>{scenario.forecast?.slice(0, 2).map((point) => <div key={point.year} className="mt-2 border-t border-border pt-1 text-[11px] text-muted-foreground">{point.year} · 收入 {point.revenue == null ? "—" : point.revenue.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} · 净利 {point.net_profit == null ? "—" : point.net_profit.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}</div>)}</div>)}</div> : <div className="text-muted-foreground">暂无可用预测情景。</div>}</div></details>
        </section> : null}
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-4"><p className="text-xs text-muted-foreground">关闭后将保留当前行业筛选和浏览位置。</p><div className="flex gap-2"><button type="button" onClick={onClose} className="rounded-md border border-border px-3 py-2 text-sm">关闭</button><button type="button" onClick={onChat} className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><MessageCircle className="h-4 w-4" />问财报研究员</button></div></footer>
    </section>
  </>;
}

export function ValueLeaderPoolPage() {
  const [industries, setIndustries] = useState<ValueIndustry[]>(() => leaderSnapshotCache?.industries ?? []);
  const [leaderMap, setLeaderMap] = useState<Record<string, Level3Leader[]>>(() => leaderSnapshotCache?.leaderMap ?? {});
  const [catalogMeta, setCatalogMeta] = useState<CatalogMeta>(() => leaderSnapshotCache?.catalogMeta ?? { level1: 0, level2: 0, level3: 0 });
  const [snapshotAsOf, setSnapshotAsOf] = useState<string | null>(() => leaderSnapshotCache?.snapshotAsOf ?? null);
  const [snapshotStatus, setSnapshotStatus] = useState<"ready" | "not_built">(() => leaderSnapshotCache?.snapshotStatus ?? "not_built");
  const [formula, setFormula] = useState<LeaderFormulaContract | undefined>(() => leaderSnapshotCache?.formula);
  const [industrySummaries, setIndustrySummaries] = useState<Record<string, LeaderIndustrySummary>>(() => leaderSnapshotCache?.industrySummaries ?? {});
  const [selectedLeader, setSelectedLeader] = useState<Level3Leader | null>(null);
  const [level1Filter, setLevel1Filter] = useState(() => leaderListViewState.level1Filter);
  const [level2Filter, setLevel2Filter] = useState(() => leaderListViewState.level2Filter);
  const [level3Query, setLevel3Query] = useState(() => leaderListViewState.level3Query);
  const [query, setQuery] = useState(() => leaderListViewState.query);
  const [valuationStatusFilter, setValuationStatusFilter] = useState<LeaderValuationStatus | "">(() => leaderListViewState.valuationStatusFilter);
  const [valuationSnapshot, setValuationSnapshot] = useState<LeaderValuationSnapshot>(() => leaderSnapshotCache?.valuationSnapshot ?? { status: "MISSING", total: 0, expected: 0, data_as_of: null, items: {} });
  const [loading, setLoading] = useState(() => !leaderSnapshotCache);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  function applySnapshot(cache: LeaderSnapshotCache) {
    setIndustries(cache.industries);
    setCatalogMeta(cache.catalogMeta);
    setLeaderMap(cache.leaderMap);
    setSnapshotAsOf(cache.snapshotAsOf);
    setSnapshotStatus(cache.snapshotStatus);
    setFormula(cache.formula);
    setIndustrySummaries(cache.industrySummaries);
    setValuationSnapshot(cache.valuationSnapshot);
  }

  async function loadSnapshot({ force = false, background = false } = {}) {
    const cached = leaderSnapshotCache;
    if (!force && cached) {
      applySnapshot(cached);
      return;
    }
    if (background) setRefreshing(true); else setLoading(true);
    setError("");
    try {
      if (!leaderSnapshotRequest) {
        leaderSnapshotRequest = Promise.all([api.getValueIndustries(), api.getCurrentLeaderPool()]).then(([catalog, pool]) => {
          // The catalogue already represents terminal industries.  Some TDX terminal
          // nodes originate at level 2, but are intentionally retained as a "三级/末级" row.
          const next: LeaderSnapshotCache = {
            industries: catalog.items,
            catalogMeta: { level1: catalog.level1_total, level2: catalog.level2_total, level3: catalog.level3_total },
            leaderMap: pool.members
              .filter((item) => item.lifecycle_status !== "OUT_OF_TOP2")
              .reduce<Record<string, Level3Leader[]>>((result, item) => {
                (result[item.level3_code] ||= []).push(item);
                return result;
              }, {}),
            snapshotAsOf: pool.as_of,
            snapshotStatus: "ready",
            formula: pool.formula,
            industrySummaries: pool.industry_summaries ?? {},
            valuationSnapshot: pool.valuation_snapshot ?? { status: "MISSING", total: 0, expected: 0, data_as_of: null, items: {} },
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
    if (cached) {
      applySnapshot(cached);
      if (leaderListScrollY > 0) requestAnimationFrame(() => window.scrollTo({ top: leaderListScrollY }));
      return () => { leaderListScrollY = window.scrollY; };
    }
    void loadSnapshot({ background: Boolean(cached) });
    if (leaderListScrollY > 0) requestAnimationFrame(() => window.scrollTo({ top: leaderListScrollY }));
    return () => { leaderListScrollY = window.scrollY; };
  }, []);

  useEffect(() => () => {
    leaderListViewState = { level1Filter, level2Filter, level3Query, query, valuationStatusFilter };
  }, [level1Filter, level2Filter, level3Query, query, valuationStatusFilter]);


  const level1Rows = useMemo(() => Array.from(new Map(industries.map((row) => [row.level1_code, { code: row.level1_code, name: row.level1_name }])).values()), [industries]);
  const level2Rows = useMemo(() => Array.from(new Map(industries.filter((row) => !level1Filter || row.level1_code === level1Filter).map((row) => [row.level2_code, { code: row.level2_code, name: row.level2_name }])).values()), [industries, level1Filter]);
  const level3Rows = useMemo(() => Array.from(new Map(industries
    .filter((row) => (!level1Filter || row.level1_code === level1Filter) && (!level2Filter || row.level2_code === level2Filter))
    .map((row) => [row.level3_code, { code: row.level3_code, name: row.level3_name, member_count: row.member_count }])).values()), [industries, level1Filter, level2Filter]);
  const valuationStatuses = useMemo<Record<string, LeaderValuationStatus>>(
    () => Object.fromEntries(Object.entries(valuationSnapshot.items).map(([stockCode, item]) => [stockCode, item.presentation_status])),
    [valuationSnapshot],
  );
  const valuationSnapshotReady = valuationSnapshot.status === "READY" && valuationSnapshot.total === valuationSnapshot.expected;
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
  const visibleIndustries = useMemo(() => !valuationStatusFilter || !valuationSnapshotReady ? filteredIndustries : filteredIndustries.filter((industry) => (leaderMap[industry.level3_code] || []).some((leader) => valuationStatuses[leader.stock_code] === valuationStatusFilter)), [filteredIndustries, leaderMap, valuationSnapshotReady, valuationStatuses, valuationStatusFilter]);
  const displayedLeaderCount = useMemo(() => visibleIndustries.reduce((total, industry) => total + (leaderMap[industry.level3_code] || []).filter((leader) => !valuationStatusFilter || !valuationSnapshotReady || valuationStatuses[leader.stock_code] === valuationStatusFilter).length, 0), [leaderMap, valuationSnapshotReady, valuationStatusFilter, valuationStatuses, visibleIndustries]);
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
    setValuationStatusFilter("");
  }

  const selectedLevel1Name = level1Rows.find((row) => row.code === level1Filter)?.name;
  const selectedLevel2Name = level2Rows.find((row) => row.code === level2Filter)?.name;
  const hasFilters = Boolean(level1Filter || level2Filter || level3Query || query || valuationStatusFilter);

  return <div className="w-full space-y-4 p-3 md:p-4">
    <header className="flex flex-col justify-between gap-4 rounded-xl border border-border bg-card px-5 py-4 lg:flex-row lg:items-center">
      <div>
        <div className="flex items-center gap-2"><BarChart3 className="h-5 w-5 text-primary" /><h1 className="text-xl font-semibold">三级行业量化龙头候选</h1></div>
      </div>
      <div className="flex items-center gap-2"><div className="flex divide-x divide-border rounded-lg border border-border text-center text-sm"><div className="px-4 py-2"><strong className="block tabular-nums">{catalogMeta.level1 || "—"}</strong><span className="text-xs text-muted-foreground">一级行业</span></div><div className="px-4 py-2"><strong className="block tabular-nums">{catalogMeta.level2 || "—"}</strong><span className="text-xs text-muted-foreground">二级行业</span></div><div className="px-4 py-2"><strong className="block tabular-nums">{catalogMeta.level3 || "—"}</strong><span className="text-xs text-muted-foreground">三级/末级行业</span></div></div><button type="button" onClick={() => void loadSnapshot({ force: true, background: Boolean(leaderSnapshotCache) })} disabled={loading || refreshing} className="inline-flex h-10 items-center gap-2 rounded-md border border-border px-3 text-sm text-muted-foreground hover:bg-muted disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />{refreshing ? "更新中" : "刷新"}</button></div>
    </header>

    {error && <div role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>}

    <section className="rounded-xl border border-primary/20 bg-primary/[0.025] px-4 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div className="flex min-w-0 items-start gap-2"><HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><div className="min-w-0"><h2 className="text-sm font-semibold">这页怎样选出候选公司</h2><div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs"><span className="rounded bg-background px-2 py-1">1 基础资格校验</span><ChevronRight className="h-3 w-3 text-muted-foreground" /><span className="rounded bg-background px-2 py-1">2 同行业20项指标比较</span><ChevronRight className="h-3 w-3 text-muted-foreground" /><span className="rounded bg-background px-2 py-1">3 六维加权排序</span><ChevronRight className="h-3 w-3 text-muted-foreground" /><span className="rounded bg-background px-2 py-1">4 每行业前2进入研究池</span></div></div></div><Link to="/value/methodology" className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-primary/30 bg-background px-3 py-2 text-xs font-medium text-primary hover:bg-primary/5">查看详细筛选说明 <ArrowRight className="h-3.5 w-3.5" /></Link></div>
    </section>

    <section className="rounded-xl border border-border bg-card">
      <div className="grid gap-2 border-b border-border p-3 lg:grid-cols-[auto_minmax(220px,1fr)_repeat(4,minmax(150px,1fr))] lg:items-center">
        <div className="flex shrink-0 items-center gap-2 px-1 text-sm font-semibold"><SlidersHorizontal className="h-4 w-4 text-primary" />行业候选列表</div>
        <label className="flex min-w-0 items-center rounded-md border border-border bg-background px-2"><Search className="h-4 w-4 text-muted-foreground" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索行业或行业代码" className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none" /></label>
        <select aria-label="一级行业筛选" value={level1Filter} onChange={(event) => changeLevel1(event.target.value)} className="min-w-0 rounded-md border border-border bg-background px-2 py-2 text-sm"><option value="">全部一级行业</option>{level1Rows.map((row) => <option key={row.code} value={row.code}>{row.name}</option>)}</select>
        <select aria-label="二级行业筛选" value={level2Filter} onChange={(event) => { setLevel2Filter(event.target.value); setLevel3Query(""); }} className="min-w-0 rounded-md border border-border bg-background px-2 py-2 text-sm"><option value="">全部二级行业</option>{level2Rows.map((row) => <option key={row.code} value={row.code}>{row.name}</option>)}</select>
        <label className="flex min-w-0 items-center rounded-md border border-border bg-background px-2"><Search className="h-4 w-4 text-muted-foreground" /><input aria-label="三级行业快速筛选" list="level3-industry-options" value={level3Query} onChange={(event) => setLevel3Query(event.target.value)} placeholder="搜索三级/末级行业" className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none" /><datalist id="level3-industry-options">{level3Rows.map((row) => <option key={row.code} value={row.name}>{row.code} · {row.member_count} 家</option>)}</datalist></label>
        <select aria-label="历史估值位置筛选" value={valuationStatusFilter} onChange={(event) => setValuationStatusFilter(event.target.value as LeaderValuationStatus | "")} disabled={!valuationSnapshotReady} title={valuationSnapshotReady ? "按公司自身历史 PE、PB、股息率位置筛选" : "历史估值快照尚未完成，不能显示不完整筛选结果"} className="min-w-0 rounded-md border border-border bg-background px-2 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-55"><option value="">全部历史估值位置</option><option value="DEEPLY_UNDERVALUED">历史深度低估</option><option value="UNDERVALUED">历史低估关注</option><option value="FAIR">历史合理观察</option><option value="OVERVALUED">历史估值偏高</option><option value="DEEPLY_OVERVALUED">历史明显偏高</option><option value="INSUFFICIENT_DATA">历史数据不足</option></select>
      </div>

      {hasFilters ? <div className="flex flex-wrap items-center gap-2 border-b border-border bg-primary/[0.03] px-3 py-2 text-xs"><span className="text-muted-foreground">当前筛选：</span>{selectedLevel1Name ? <span className="rounded-full bg-primary/10 px-2 py-1 text-primary">{selectedLevel1Name}</span> : null}{selectedLevel2Name ? <span className="rounded-full bg-primary/10 px-2 py-1 text-primary">{selectedLevel2Name}</span> : null}{level3Query ? <span className="rounded-full bg-primary/10 px-2 py-1 text-primary">{level3Query}</span> : null}{query ? <span className="rounded-full bg-muted px-2 py-1 text-muted-foreground">关键词：{query}</span> : null}{valuationStatusFilter ? <span className="rounded-full bg-primary/10 px-2 py-1 text-primary">价值状态：{leaderValuationStatusLabel(valuationStatusFilter)}</span> : null}<button type="button" onClick={clearFilters} className="ml-auto text-primary hover:underline">清除筛选</button></div> : null}
      <div className="flex flex-wrap items-center justify-between gap-2 bg-muted/25 px-3 py-2 text-xs text-muted-foreground"><span>显示 {visibleIndustries.length} / {industries.length} 个三级/末级行业 · {displayedLeaderCount} 家龙头候选</span><span>{snapshotStatus === "ready" ? <>快照日期 {snapshotAsOf} · {industryWithLeaders} 个行业 · {leaderCount} 个量化候选席位</> : "尚未生成龙头快照"}</span><span className={valuationSnapshotReady ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}>历史估值快照：{valuationSnapshotReady ? `完整 ${valuationSnapshot.total}/${valuationSnapshot.expected}` : `未就绪 ${valuationSnapshot.total}/${valuationSnapshot.expected}`}{valuationSnapshot.data_as_of ? ` · 数据截至 ${valuationSnapshot.data_as_of}` : ""}</span></div>

      {loading ? <div className="flex h-80 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />加载三级行业量化候选…</div> : snapshotStatus === "not_built" ? <div className="p-12 text-center"><p className="text-sm text-muted-foreground">尚未生成三级行业 Leader 快照，请先运行全量构建脚本。</p></div> : visibleIndustries.length === 0 ? <div className="p-12 text-center text-sm text-muted-foreground">{valuationStatusFilter && !valuationSnapshotReady ? "历史估值快照尚未完成，暂不显示不完整筛选结果。" : "没有匹配的三级/末级行业"}</div> : <div className="grid gap-2 p-2 lg:grid-cols-2">{visibleIndustries.map((industry) => <IndustryLeaderRow key={industry.level3_code} industry={industry} leaders={(leaderMap[industry.level3_code] ?? []).filter((leader) => !valuationStatusFilter || !valuationSnapshotReady || valuationStatuses[leader.stock_code] === valuationStatusFilter)} summary={industrySummaries[industry.level3_code]} onSelect={setSelectedLeader} valuationStatuses={valuationStatuses} />)}</div>}
    </section>

    <details className="rounded-xl border border-border bg-card"><summary className="cursor-pointer px-4 py-3 text-sm font-semibold">指标词典与计算口径</summary><div className="space-y-4 border-t border-border p-4"><p className="text-xs leading-5 text-muted-foreground">{formula?.normalization || "所有指标先在同一行业内处理极端值，再转换成0—100相对分位。分数越高只表示相对同行更有利。"}</p><div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">{(formula?.dimensions || []).map((dimension) => <section key={dimension.key} className="rounded-lg border border-border p-3"><div className="flex justify-between gap-2"><strong className="text-sm">{dimension.label}</strong><span className="text-xs text-primary">总权重 {(dimension.weight * 100).toFixed(0)}%</span></div><div className="mt-2 space-y-2">{dimension.metrics.map((metric) => <div key={metric.key} className="text-xs"><div className="flex justify-between gap-2"><span className="font-medium">{metric.label}</span><span className="text-muted-foreground">{(metric.weight * 100).toFixed(0)}%</span></div><p className="mt-0.5 leading-5 text-muted-foreground">{metric.description}</p></div>)}</div></section>)}</div><div className="rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground"><strong className="text-foreground">覆盖率不是准确率：</strong>维度覆盖表示有多少评分权重获得了有效数据；缺失时可能对剩余指标重新加权。页面同时显示原始指标数量、报告日期和小样本警告。</div></div></details>
    <p className="px-1 text-xs text-muted-foreground">量化候选只用于同一三级行业内部排序，不用于跨行业比较，也不代表预期收益或买入建议。</p>
    <Link to="/ai/agent" className="fixed bottom-5 right-5 z-30 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-3 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/25 transition-transform hover:-translate-y-0.5"><MessageCircle className="h-4 w-4" />问投研主管</Link>
    {selectedLeader ? <LeaderCompanyQuickView leader={selectedLeader} onClose={() => setSelectedLeader(null)} onChat={() => { window.location.assign("/ai/agent"); }} /> : null}
  </div>;
}
