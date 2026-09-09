import { useEffect, useState } from "react";
import { ArrowRight, Bot, ChevronDown, Crown, Loader2, RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { LeaderCompanyQuickView } from "@/components/value/LeaderCompanyQuickView";
import { MacroEnvironmentCard } from "@/components/value/MacroEnvironmentCard";
import { api, type Level3Leader, type LowValueLeaderEvent, type LowValueLeaderEventsResponse, type LowValueLeaderPoolItem, type LowValueLeaderPoolResponse } from "@/lib/api";
import { companyResearchPath } from "@/lib/routeContext";

type LowValuePageCache = { result: LowValueLeaderPoolResponse; events: LowValueLeaderEventsResponse | null };

let lowValuePageCache: LowValuePageCache | null = null;
let lowValuePageRequest: Promise<LowValuePageCache> | null = null;

async function loadLowValuePageData(force = false) {
  if (!force && lowValuePageCache) return lowValuePageCache;
  if (!lowValuePageRequest) {
    lowValuePageRequest = Promise.allSettled([api.getLowValueLeaders(), api.getLowValueLeaderEvents()])
      .then(([pool, changes]) => {
        if (pool.status !== "fulfilled") throw pool.reason;
        const value = { result: pool.value, events: changes.status === "fulfilled" ? changes.value : null };
        lowValuePageCache = value;
        return value;
      })
      .finally(() => { lowValuePageRequest = null; });
  }
  return lowValuePageRequest;
}

function number(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function price(value: number | null | undefined) {
  const text = number(value);
  return text === "—" ? "资料不足" : `${text} 元`;
}

function distanceToMid(priceValue: number | null | undefined, midpoint: number | null | undefined) {
  if (priceValue == null || midpoint == null || midpoint === 0) return "资料不足";
  const distance = (midpoint - priceValue) / midpoint;
  if (distance > 0) return `低于中枢 ${(distance * 100).toFixed(1)}%`;
  if (distance < 0) return `高于中枢 ${Math.abs(distance * 100).toFixed(1)}%`;
  return "接近价值中枢";
}

function lowValueLabel(status: string) { return status === "DEEPLY_UNDERVALUED" ? "深度低估" : "低估关注"; }

function statusLabel(status: string | null | undefined) {
  const labels: Record<string, string> = {
    DEEPLY_UNDERVALUED: "深度低估", UNDERVALUED: "低估关注", FAIR: "合理观察",
    OVERVALUED: "估值偏高", DEEPLY_OVERVALUED: "明显偏高", NO_LONGER_LEADER: "已移出研究范围：当前不再属于三级行业Top1/Top2",
  };
  return status ? labels[status] || status : "无历史记录";
}

function riskLabel(status: string) {
  const labels: Record<string, string> = { HIGH: "有明显风险需要核验", MEDIUM: "有事项需要继续观察", LOW: "暂未发现明显风险", UNKNOWN: "资料不足" };
  return labels[status] || "资料不足";
}
function riskTone(status: string) {
  return status === "HIGH" ? "border-red-500/35 bg-red-500/10 text-red-800" : status === "MEDIUM" ? "border-amber-500/35 bg-amber-500/10 text-amber-800" : status === "LOW" ? "border-primary/30 bg-primary/10 text-primary" : "border-muted bg-muted/40 text-muted-foreground";
}

function discountToFairValueMid(item: LowValueLeaderPoolItem) {
  if (item.current_price == null || item.fair_value_mid == null || item.fair_value_mid === 0) return Number.NEGATIVE_INFINITY;
  return (item.fair_value_mid - item.current_price) / item.fair_value_mid;
}

function valuationRank(status: string) { return status === "DEEPLY_UNDERVALUED" ? 0 : status === "UNDERVALUED" ? 1 : 2; }

function historicalRank(status: string | null) {
  const ranks: Record<string, number> = { VERY_CHEAP: 0, CHEAP: 1, NORMAL: 2, EXPENSIVE: 3, VERY_EXPENSIVE: 4 };
  return ranks[String(status || "").toUpperCase()] ?? 5;
}

function supportRank(status: string | null) { return status === "AVAILABLE" ? 0 : 1; }

export function sortLowValueLeaders(items: LowValueLeaderPoolItem[]) {
  return [...items].sort((left, right) => (
    valuationRank(left.valuation_status) - valuationRank(right.valuation_status)
    || right.leader_score - left.leader_score
    || discountToFairValueMid(right) - discountToFairValueMid(left)
    || historicalRank(left.historical_valuation_status) - historicalRank(right.historical_valuation_status)
    || supportRank(left.support_status) - supportRank(right.support_status)
    || left.stock_code.localeCompare(right.stock_code)
  ));
}

function quickViewLeader(item: LowValueLeaderPoolItem): Level3Leader {
  return {
    level1_code: "", level1_name: "", level2_code: "", level2_name: "", level3_code: item.industry_code, level3_name: item.industry_name,
    stock_code: item.stock_code, stock_name: item.company_name, leader_rank: item.leader_rank, leader_score: item.leader_score,
    leader_formula_version: "low-value-leader-pool", component_scores: {}, coverage: 0,
    eligibility_status: "eligible", eligibility_reasons: [], metric_applicability_notes: [], as_of: item.source_as_of,
  };
}

function quickViewEvent(event: LowValueLeaderEvent, pool: LowValueLeaderPoolItem[]): Level3Leader {
  const matched = pool.find((item) => item.stock_code === event.stock_code);
  if (matched) return quickViewLeader(matched);
  return {
    level1_code: "", level1_name: "", level2_code: "", level2_name: "",
    level3_code: event.industry_code || "", level3_name: event.industry_name || "",
    stock_code: event.stock_code, stock_name: event.company_name,
    leader_rank: 0, leader_score: 0,
    leader_formula_version: "low-value-leader-event", component_scores: {}, coverage: 0,
    eligibility_status: "eligible", eligibility_reasons: [], metric_applicability_notes: [], as_of: event.source_as_of,
  };
}

function dataDate(result: LowValueLeaderPoolResponse | null) {
  if (result?.data_as_of) return result.data_as_of;
  if (!result?.items.length) return result?.last_evaluated_at?.slice(0, 10) || "—";
  const dates = result.items.map((item) => item.source_as_of).sort();
  return dates[dates.length - 1] || result.last_evaluated_at?.slice(0, 10) || "—";
}

function ChangeEventCard({ event, onSelect }: { event: LowValueLeaderEvent; onSelect: (event: LowValueLeaderEvent) => void }) {
  return <article role="button" tabIndex={0} onClick={() => onSelect(event)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(event); } }} className="cursor-pointer rounded-lg border border-border bg-background px-3 py-2.5 transition-colors hover:border-primary/50 hover:bg-muted/40"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="text-xs font-medium text-primary">{event.event_type === "ENTER_LOW_VALUE" ? "🟠 新进入低估区域" : "🔵 退出低估区域"}</div><div className="mt-1 truncate text-sm font-semibold">{event.company_name} <span className="font-mono text-[11px] font-normal text-muted-foreground">{event.stock_code}</span></div><p className="mt-1 truncate text-xs text-muted-foreground">{event.industry_name || "行业资料不足"} · {statusLabel(event.before_status)} → {statusLabel(event.after_status)}</p></div><div className="shrink-0 text-right text-xs text-muted-foreground"><div>当前 {price(event.current_price)}</div><div className="mt-1">中枢 {price(event.fair_value_mid)}</div></div></div></article>;
}

export function ValueFocusPage() {
  const [result, setResult] = useState<LowValueLeaderPoolResponse | null>(() => lowValuePageCache?.result || null);
  const [events, setEvents] = useState<LowValueLeaderEventsResponse | null>(() => lowValuePageCache?.events || null);
  const [selected, setSelected] = useState<Level3Leader | null>(null);
  const [changesOpen, setChangesOpen] = useState(false);
  const [loading, setLoading] = useState(() => !lowValuePageCache);
  const [error, setError] = useState("");

  const load = async ({ force = false } = {}) => {
    if (!force && lowValuePageCache) {
      setResult(lowValuePageCache.result);
      setEvents(lowValuePageCache.events);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const value = await loadLowValuePageData(force);
      setResult(value.result);
      setEvents(value.events);
      setError("");
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (!lowValuePageCache) void load(); }, []);

  const companies = sortLowValueLeaders(result?.items || []);
  const enteredEvents = events?.items.filter((event) => event.event_type === "ENTER_LOW_VALUE") || [];
  const exitedEvents = events?.items.filter((event) => event.event_type === "EXIT_LOW_VALUE") || [];
  const selectEvent = (event: LowValueLeaderEvent) => setSelected(quickViewEvent(event, result?.items || []));
  return <div className="mx-auto w-full max-w-[1250px] space-y-5 p-4 md:p-6">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">VALUE DAILY VIEW</div><div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1"><h1 className="text-2xl font-semibold">低估龙头池</h1><span className="text-sm text-muted-foreground">当前 {result?.total ?? 0} 家 · 数据日期 {dataDate(result)}</span></div><p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">每日先看当前低估龙头、状态变化和已完成的投研简报；仅用于研究筛选，不代表买入建议。</p></div><div className="flex flex-wrap gap-2"><Link to="/ai/agent" className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><Bot className="h-4 w-4" />问投研主管</Link><button type="button" onClick={() => void load({ force: true })} disabled={loading} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60"><RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />刷新结果</button></div></header>
    <MacroEnvironmentCard />
    <section aria-label="低估龙头池状态变化" className="rounded-xl border bg-card"><button type="button" onClick={() => setChangesOpen((value) => !value)} aria-expanded={changesOpen} className="flex w-full flex-wrap items-center justify-between gap-3 border-b px-4 py-3 text-left hover:bg-muted/20"><div><h2 className="font-semibold">今日变化</h2><p className="mt-1 text-xs text-muted-foreground">数据日期 {events?.event_date || "暂无状态变化"} · 只记录进入或退出低估区域，不代表买卖建议。</p></div><span className="inline-flex shrink-0 items-center gap-2 text-sm"><span className="rounded-full bg-amber-500/15 px-3 py-1 text-amber-700 dark:text-amber-300">新增 {events?.entered ?? 0}</span><span className="rounded-full bg-primary/10 px-3 py-1 text-primary">退出 {events?.exited ?? 0}</span><ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${changesOpen ? "rotate-180" : ""}`} /></span></button>{changesOpen ? (events ? <div className="grid gap-3 p-3 md:grid-cols-2"><div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.03] p-2"><div className="flex items-center justify-between px-1 py-1"><span className="text-xs font-semibold text-amber-700 dark:text-amber-300">🟠 新进入</span><span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">{enteredEvents.length}</span></div>{enteredEvents.length ? <div className="grid gap-2">{enteredEvents.map((event) => <ChangeEventCard key={event.id} event={event} onSelect={selectEvent} />)}</div> : <p className="rounded-lg border border-dashed px-3 py-2.5 text-xs text-muted-foreground">今日没有新进入低估区域的行业龙头。</p>}</div><div className="rounded-lg border border-primary/20 bg-primary/[0.03] p-2"><div className="flex items-center justify-between px-1 py-1"><span className="text-xs font-semibold text-primary">🔵 退出</span><span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">{exitedEvents.length}</span></div>{exitedEvents.length ? <div className="grid gap-2">{exitedEvents.map((event) => <ChangeEventCard key={event.id} event={event} onSelect={selectEvent} />)}</div> : <p className="rounded-lg border border-dashed px-3 py-2.5 text-xs text-muted-foreground">今日没有退出低估区域的行业龙头。</p>}</div></div> : <p className="px-4 py-4 text-sm text-muted-foreground">状态变化数据暂不可读，不影响低估龙头池列表。</p>) : null}</section>
    {error ? <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">读取低估龙头池失败：{error}</div> : null}
    {loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取低估龙头池…</div> : companies.length ? <section className="grid gap-2.5 md:grid-cols-2">{companies.map((company) => <article key={company.id} onClick={(e) => { if ((e.target as HTMLElement).closest("a")) return; setSelected(quickViewLeader(company)); }} className="cursor-pointer rounded-xl border bg-card p-3 transition-colors hover:border-primary/50 hover:bg-muted/30">
      <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex items-center gap-1.5 text-xs text-primary"><Crown className="h-3.5 w-3.5 shrink-0" /><span className="truncate">行业龙头 · Top{company.leader_rank} · {company.industry_name}</span></div><h2 className="mt-1 truncate text-sm font-semibold">{company.company_name} <span className="font-mono text-xs font-normal text-muted-foreground">{company.stock_code}</span></h2></div><span className="inline-flex shrink-0 rounded-full bg-amber-500/15 px-2 py-1 text-xs font-medium text-amber-700 dark:text-amber-300">{lowValueLabel(company.valuation_status)}</span></div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"><span>现价 <strong className="text-sm tabular-nums text-foreground">{price(company.current_price)}</strong></span><span>中枢 <strong className="text-sm tabular-nums text-foreground">{price(company.fair_value_mid)}</strong></span><span className="font-medium tabular-nums text-primary">{distanceToMid(company.current_price, company.fair_value_mid)}</span><span>资料 {company.source_as_of || company.risk_as_of || "—"}</span><Link to={companyResearchPath(company.stock_code, { from: "/value/focus", fromLabel: "低估龙头池" }, "overview")} className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">打开公司研究 <ArrowRight className="h-3.5 w-3.5" /></Link></div>
      <div className={`mt-2 flex items-baseline gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${riskTone(company.risk_overall)}`}><strong className="shrink-0">风险复核：{riskLabel(company.risk_overall)}</strong><span className="truncate opacity-90">{company.risk_summary || "当前没有可展示的风险摘要。"}</span></div>
    </article>)}</section> : <section className="rounded-xl border border-dashed bg-card p-10 text-center"><Crown className="mx-auto h-5 w-5 text-primary" /><h2 className="mt-3 font-semibold">当前没有进入低估区域的行业龙头</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">这不代表没有优质公司，只表示当前已有价格区间结果中没有满足低估条件的有效行业龙头。</p><Link to="/value/leaders" className="mt-4 inline-flex items-center gap-1 rounded-md border px-3 py-2 text-sm hover:border-primary/50">查看行业龙头 <ArrowRight className="h-4 w-4" /></Link></section>}
    <p className="rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">低估龙头池不自动生成买入、卖出、仓位或交易指令；历史估值、支撑和入场研究仅用于辅助理解，不影响公司是否进入本池。</p>
    {selected ? <LeaderCompanyQuickView leader={selected} onClose={() => setSelected(null)} onChat={() => { window.location.assign("/ai/agent"); }} /> : null}
  </div>;
}
