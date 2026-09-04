import { useEffect, useState } from "react";
import { ArrowRight, Bot, Crown, FileText, Loader2, RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { LeaderCompanyQuickView } from "@/components/value/LeaderCompanyQuickView";
import { MacroEnvironmentCard } from "@/components/value/MacroEnvironmentCard";
import { api, type InvestmentResearchDailyBrief, type Level3Leader, type LowValueLeaderEvent, type LowValueLeaderEventsResponse, type LowValueLeaderPoolItem, type LowValueLeaderPoolResponse } from "@/lib/api";
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

function DailyBriefCard({ brief, error }: { brief: InvestmentResearchDailyBrief | null; error: boolean }) {
  if (!brief) return <section className="rounded-xl border border-dashed bg-card p-4"><div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold">今日投研简报</h2><p className="mt-1 text-sm text-muted-foreground">{error ? "简报读取失败，不影响低估龙头池；可稍后刷新页面或向投研主管确认。" : "暂无已完成简报；页面不会为此重新计算研究结果。"}</p></div><Link to="/ai/agent" className="inline-flex shrink-0 items-center gap-1 rounded-md border px-3 py-2 text-sm text-primary hover:bg-muted"><Bot className="h-4 w-4" />问投研主管</Link></div></section>;
  const situations = brief.brief_payload?.executive_situations || [];
  const priorities = brief.priority_companies.length ? brief.priority_companies : (brief.brief_payload?.executive_watchlist || []);
  return <section className="rounded-xl border border-primary/25 bg-card p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-primary" /><h2 className="font-semibold">今日投研简报</h2></div><p className="mt-1 text-xs text-muted-foreground">研究日期 {brief.research_as_of} · 读取已保存简报，不会触发重算</p></div><div className="flex gap-2"><Link to="/value/research" className="inline-flex items-center gap-1 rounded-md border px-3 py-2 text-sm text-primary hover:bg-muted">查看完整简报 <ArrowRight className="h-4 w-4" /></Link><Link to="/ai/agent" className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><Bot className="h-4 w-4" />问投研主管</Link></div></div><div className="mt-4 grid gap-3 md:grid-cols-3"><article className="rounded-lg border bg-background p-3"><div className="text-xs text-muted-foreground">当前低估龙头</div><strong className="mt-1 block text-lg tabular-nums">{brief.low_value_active_count} 家</strong></article><article className="rounded-lg border bg-background p-3"><div className="text-xs text-muted-foreground">今日新增 / 退出</div><strong className="mt-1 block text-lg tabular-nums">{brief.enter_count} / {brief.exit_count}</strong></article><article className="rounded-lg border bg-background p-3"><div className="text-xs text-muted-foreground">重点风险变化</div><strong className="mt-1 block text-sm">{situations.length ? `${situations.length} 项待复核` : "暂无已确认变化"}</strong></article></div>{situations.length ? <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/[0.04] p-3 text-sm"><strong>需要关注</strong><p className="mt-1 text-muted-foreground">{situations.slice(0, 2).map((item) => `${item.company_name || "公司"}：${item.basis || item.impact || "研究状态变化"}`).join("；")}</p></div> : null}{priorities.length ? <div className="mt-3"><div className="text-xs font-medium text-muted-foreground">优先研究公司</div><div className="mt-2 flex flex-wrap gap-2">{priorities.slice(0, 5).map((item, index) => <span key={`${item.stock_code || item.company_name || "company"}-${index}`} className="rounded-full border bg-muted/30 px-2.5 py-1 text-xs">{item.company_name || item.stock_code || "待确认公司"}{item.industry_name ? ` · ${item.industry_name}` : ""}</span>)}</div></div> : null}</section>;
}

export function ValueFocusPage() {
  const [result, setResult] = useState<LowValueLeaderPoolResponse | null>(() => lowValuePageCache?.result || null);
  const [events, setEvents] = useState<LowValueLeaderEventsResponse | null>(() => lowValuePageCache?.events || null);
  const [brief, setBrief] = useState<InvestmentResearchDailyBrief | null>(null);
  const [selected, setSelected] = useState<Level3Leader | null>(null);
  const [briefError, setBriefError] = useState(false);
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
  useEffect(() => {
    const loadBrief = api.getInvestmentResearchDailyBrief;
    if (typeof loadBrief === "function") loadBrief().then((value) => { setBrief(value); setBriefError(false); }).catch(() => { setBrief(null); setBriefError(true); });
  }, []);

  const companies = sortLowValueLeaders(result?.items || []);
  const enteredEvents = events?.items.filter((event) => event.event_type === "ENTER_LOW_VALUE") || [];
  const exitedEvents = events?.items.filter((event) => event.event_type === "EXIT_LOW_VALUE") || [];
  const selectEvent = (event: LowValueLeaderEvent) => setSelected(quickViewEvent(event, result?.items || []));
  return <div className="mx-auto w-full max-w-[1250px] space-y-5 p-4 md:p-6">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">VALUE DAILY VIEW</div><div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1"><h1 className="text-2xl font-semibold">低估龙头池</h1><span className="text-sm text-muted-foreground">当前 {result?.total ?? 0} 家 · 数据日期 {dataDate(result)}</span></div><p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">每日先看当前低估龙头、状态变化和已完成的投研简报；仅用于研究筛选，不代表买入建议。</p></div><div className="flex flex-wrap gap-2"><Link to="/ai/agent" className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><Bot className="h-4 w-4" />问投研主管</Link><button type="button" onClick={() => void load({ force: true })} disabled={loading} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60"><RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />刷新结果</button></div></header>
    <DailyBriefCard brief={brief} error={briefError} />
    <MacroEnvironmentCard />
    <section aria-label="低估龙头池状态变化" className="rounded-xl border bg-card"><div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3"><div><h2 className="font-semibold">今日变化</h2><p className="mt-1 text-xs text-muted-foreground">数据日期 {events?.event_date || "暂无状态变化"} · 只记录进入或退出低估区域，不代表买卖建议。</p></div><div className="flex gap-2 text-sm"><span className="rounded-full bg-amber-500/15 px-3 py-1 text-amber-700 dark:text-amber-300">新增 {events?.entered ?? 0}</span><span className="rounded-full bg-primary/10 px-3 py-1 text-primary">退出 {events?.exited ?? 0}</span></div></div>{events ? <div className="grid gap-3 p-3 md:grid-cols-2"><div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.03] p-2"><div className="flex items-center justify-between px-1 py-1"><span className="text-xs font-semibold text-amber-700 dark:text-amber-300">🟠 新进入</span><span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">{enteredEvents.length}</span></div>{enteredEvents.length ? <div className="grid gap-2">{enteredEvents.map((event) => <ChangeEventCard key={event.id} event={event} onSelect={selectEvent} />)}</div> : <p className="rounded-lg border border-dashed px-3 py-2.5 text-xs text-muted-foreground">今日没有新进入低估区域的行业龙头。</p>}</div><div className="rounded-lg border border-primary/20 bg-primary/[0.03] p-2"><div className="flex items-center justify-between px-1 py-1"><span className="text-xs font-semibold text-primary">🔵 退出</span><span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">{exitedEvents.length}</span></div>{exitedEvents.length ? <div className="grid gap-2">{exitedEvents.map((event) => <ChangeEventCard key={event.id} event={event} onSelect={selectEvent} />)}</div> : <p className="rounded-lg border border-dashed px-3 py-2.5 text-xs text-muted-foreground">今日没有退出低估区域的行业龙头。</p>}</div></div> : <p className="px-4 py-4 text-sm text-muted-foreground">状态变化数据暂不可读，不影响低估龙头池列表。</p>}</section>
    {error ? <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">读取低估龙头池失败：{error}</div> : null}
    {loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取低估龙头池…</div> : companies.length ? <section className="grid gap-3 md:grid-cols-2">{companies.map((company) => <article key={company.id} onClick={(e) => { if ((e.target as HTMLElement).closest("a")) return; setSelected(quickViewLeader(company)); }} className="cursor-pointer rounded-xl border bg-card p-4 transition-colors hover:border-primary/50 hover:shadow-sm">
      <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex items-center gap-2 text-xs text-primary"><Crown className="h-3.5 w-3.5" />行业龙头 · 行业排名 Top{company.leader_rank}</div><h2 className="mt-2 truncate font-semibold">{company.company_name} <span className="font-mono text-xs font-normal text-muted-foreground">{company.stock_code}</span></h2><p className="mt-1 truncate text-sm text-muted-foreground">{company.industry_name}</p></div><span className="inline-flex shrink-0 rounded-full bg-amber-500/15 px-2 py-1 text-xs font-medium text-amber-700 dark:text-amber-300">{lowValueLabel(company.valuation_status)}</span></div>
       <div className="mt-4 grid gap-2 sm:grid-cols-3"><div className="rounded-lg border border-border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">当前价格</div><strong className="mt-1 block text-sm tabular-nums">{price(company.current_price)}</strong></div><div className="rounded-lg border border-border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">合理价值中枢</div><strong className="mt-1 block text-sm tabular-nums">{price(company.fair_value_mid)}</strong></div><div className="rounded-lg border border-border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">距离中枢</div><strong className="mt-1 block text-sm tabular-nums">{distanceToMid(company.current_price, company.fair_value_mid)}</strong></div></div>
       <div className={`mt-3 rounded-lg border px-3 py-2.5 text-sm ${riskTone(company.risk_overall)}`}><strong>风险复核：{riskLabel(company.risk_overall)}</strong><p className="mt-1 line-clamp-1 text-xs leading-5 opacity-90">{company.risk_summary || "当前没有可展示的风险摘要。"}</p></div>
      <p className="mt-3 text-xs text-muted-foreground">资料日期：{company.source_as_of || company.risk_as_of || "资料不足"}</p>
      <div className="mt-4 flex flex-wrap items-center justify-end gap-3 border-t pt-3"><Link to={companyResearchPath(company.stock_code, { from: "/value/focus", fromLabel: "低估龙头池" }, "overview")} className="inline-flex items-center gap-1 text-sm text-primary hover:underline">打开公司研究 <ArrowRight className="h-4 w-4" /></Link></div>
    </article>)}</section> : <section className="rounded-xl border border-dashed bg-card p-10 text-center"><Crown className="mx-auto h-5 w-5 text-primary" /><h2 className="mt-3 font-semibold">当前没有进入低估区域的行业龙头</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">这不代表没有优质公司，只表示当前已有价格区间结果中没有满足低估条件的有效行业龙头。</p><Link to="/value/leaders" className="mt-4 inline-flex items-center gap-1 rounded-md border px-3 py-2 text-sm hover:border-primary/50">查看行业龙头 <ArrowRight className="h-4 w-4" /></Link></section>}
    <p className="rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">低估龙头池不自动生成买入、卖出、仓位或交易指令；历史估值、支撑和入场研究仅用于辅助理解，不影响公司是否进入本池。</p>
    {selected ? <LeaderCompanyQuickView leader={selected} onClose={() => setSelected(null)} onChat={() => { window.location.assign("/ai/agent"); }} /> : null}
  </div>;
}
