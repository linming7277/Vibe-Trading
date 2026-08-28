import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowRight, BellRing, CheckCircle2, Clock3, Loader2, RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";
import { api, type FocusSelectionItem, type FocusSelectionResponse, type StrategySignal, type ValueLeaderPool, type ValueResearchAutomation } from "@/lib/api";
import { cn } from "@/lib/utils";
import { companyResearchPath } from "@/lib/routeContext";

type SignalKind = "ENTRY" | "EXIT" | "REVIEW";

type PoolCompany = {
  stock_code: string;
  stock_name: string;
  best_score: number;
  industries: string[];
  research_status?: string;
  is_priority?: boolean;
};

function poolCompanies(pool: ValueLeaderPool | null): PoolCompany[] {
  if (!pool) return [];
  const states = new Map(pool.research_states.map((state) => [state.stock_code, state]));
  const companies = new Map<string, PoolCompany>();
  pool.members.filter((member) => member.lifecycle_status !== "OUT_OF_TOP2").forEach((member) => {
    const existing = companies.get(member.stock_code);
    const score = Number(member.leader_score) || 0;
    if (existing) {
      existing.best_score = Math.max(existing.best_score, score);
      if (!existing.industries.includes(member.level3_name)) existing.industries.push(member.level3_name);
      return;
    }
    const state = states.get(member.stock_code);
    companies.set(member.stock_code, {
      stock_code: member.stock_code,
      stock_name: member.stock_name,
      best_score: score,
      industries: [member.level3_name],
      research_status: state?.research_status,
      is_priority: Boolean(state?.is_priority),
    });
  });
  return [...companies.values()].sort((left, right) => right.best_score - left.best_score || left.stock_code.localeCompare(right.stock_code));
}

function signalKind(signal: StrategySignal): SignalKind {
  const direction = signal.direction.toUpperCase();
  if (/(BUY|LONG|ENTRY|OPEN)/.test(direction)) return "ENTRY";
  if (/(SELL|EXIT|CLOSE|SHORT)/.test(direction)) return "EXIT";
  return "REVIEW";
}

function activeSignals(signals: StrategySignal[]) {
  return signals.filter((signal) => !["EXPIRED", "CANCELLED", "REJECTED", "CLOSED"].includes(signal.status.toUpperCase()));
}

function automationStatus(automation: ValueResearchAutomation | null) {
  if (!automation) return { label: "状态未读取", tone: "bg-muted text-muted-foreground" };
  if (!automation.enabled) return { label: "已暂停", tone: "bg-amber-500/10 text-amber-700 dark:text-amber-300" };
  if (automation.last_status === "completed") return { label: "自动运行中", tone: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" };
  if (automation.last_status === "partial" || automation.last_status === "waiting_data") return { label: "等待数据或部分完成", tone: "bg-amber-500/10 text-amber-700 dark:text-amber-300" };
  if (automation.last_status === "stale") return { label: "数据已过期", tone: "bg-danger/10 text-danger" };
  return { label: "已启用，等待首次运行", tone: "bg-primary/10 text-primary" };
}

function dateLabel(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value.replace("T", " ").slice(0, 16) : parsed.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function researchStateLabel(status?: string) {
  if (status === "READY") return "资料已就绪";
  if (status === "PARTIAL") return "待补齐";
  return "待建档";
}

function SignalBadge({ signal }: { signal: StrategySignal }) {
  const kind = signalKind(signal);
  const copy = kind === "ENTRY" ? "入场候选" : kind === "EXIT" ? "风险 / 退出复核" : "需要复核";
  const tone = kind === "ENTRY" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : kind === "EXIT" ? "bg-danger/10 text-danger" : "bg-amber-500/10 text-amber-700 dark:text-amber-300";
  return <span className={cn("rounded-full px-2 py-1 text-[11px] font-medium", tone)}>{copy}</span>;
}

function SignalRow({ signal, companyName }: { signal: StrategySignal; companyName?: string }) {
  const range = signalKind(signal) === "ENTRY" && signal.entry_low != null && signal.entry_high != null
    ? `关注区间 ${signal.entry_low} – ${signal.entry_high}`
    : signal.target_low != null && signal.target_high != null ? `目标区间 ${signal.target_low} – ${signal.target_high}` : "请在公司研究中查看触发条件";
  const path = companyResearchPath(signal.symbol, { from: "/value/opportunities", fromLabel: "机会与风险" }, "overview");
  return <article className="flex flex-col gap-3 rounded-xl border bg-card p-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><SignalBadge signal={signal} /><span className="text-xs text-muted-foreground">数据日期 {signal.data_as_of}</span><span className="text-xs text-muted-foreground">覆盖率 {(signal.coverage * 100).toFixed(0)}%</span></div><h2 className="mt-2 font-semibold">{companyName || signal.symbol} <span className="font-mono text-xs font-normal text-muted-foreground">{signal.symbol}</span></h2><p className="mt-1 text-sm text-muted-foreground">{range}</p></div><Link to={path} className="inline-flex shrink-0 items-center justify-center gap-1 rounded-md border px-3 py-2 text-sm hover:border-primary/50 hover:text-primary">查看公司研究 <ArrowRight className="h-4 w-4" /></Link></article>;
}

function focusValuationLabel(status: string) {
  return status === "DEEPLY_UNDERVALUED" ? "深度低估" : status === "UNDERVALUED" ? "低估关注" : "资料不足";
}

function focusRiskLabel(status: string) {
  return status === "LOW" ? "暂未发现明显风险" : status === "MEDIUM" ? "需要继续观察" : status === "HIGH" ? "存在高等级风险" : "风险资料不足";
}

function focusTrapLabel(status: string) {
  return status === "LOW_TRAP_RISK" ? "低" : status === "MEDIUM_TRAP_RISK" ? "中等" : status === "HIGH_TRAP_RISK" ? "高" : "资料不足";
}

function focusThesisLabel(status: string) {
  const labels: Record<string, string> = { FORMING: "正在形成", STRENGTHENING: "逻辑正在增强", UNCHANGED: "逻辑基本稳定", WEAKENING: "逻辑正在减弱", FALSIFIED: "核心逻辑已失效", MISSING: "尚未建立" };
  return labels[status] || "资料不足";
}

function focusPrice(value: number | null) {
  return value == null ? "资料不足" : `${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`;
}

function FocusCompanyCard({ item }: { item: FocusSelectionItem }) {
  const path = companyResearchPath(item.stock_code, { from: "/value/opportunities", fromLabel: "机会与风险" }, "overview");
  const riskToneClass = item.risk_status === "HIGH" ? "border-danger/35 bg-danger/10 text-danger" : item.risk_status === "MEDIUM" ? "border-amber-500/35 bg-amber-500/10 text-amber-800 dark:text-amber-300" : item.risk_status === "LOW" ? "border-primary/30 bg-primary/10 text-primary" : "border-muted bg-muted/40 text-muted-foreground";
  return <article className="rounded-xl border bg-card p-4"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="text-xs text-primary">{item.industry_name} · 行业排名 Top{item.leader_rank}</div><h3 className="mt-1 truncate font-semibold">{item.company_name} <span className="font-mono text-xs font-normal text-muted-foreground">{item.stock_code}</span></h3></div><span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-1 text-xs font-medium text-amber-700 dark:text-amber-300">{focusValuationLabel(item.valuation_status)}</span></div><div className="mt-3 grid gap-2 sm:grid-cols-3"><div className="rounded-lg border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">当前价格</div><strong className="mt-1 block text-sm tabular-nums">{focusPrice(item.current_price)}</strong></div><div className="rounded-lg border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">合理价值中枢</div><strong className="mt-1 block text-sm tabular-nums">{focusPrice(item.fair_value_mid)}</strong></div><div className="rounded-lg border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">距离中枢</div><strong className="mt-1 block text-sm tabular-nums">{item.discount_to_mid == null ? "资料不足" : `${item.discount_to_mid >= 0 ? "低于" : "高于"} ${Math.abs(item.discount_to_mid * 100).toFixed(1)}%`}</strong></div></div><div className="mt-3 grid gap-2 text-xs sm:grid-cols-3"><div className={`rounded-lg border px-3 py-2 ${riskToneClass}`}><strong>风险：{focusRiskLabel(item.risk_status)}</strong></div><div className="rounded-lg border bg-muted/35 px-3 py-2 text-muted-foreground"><strong>低估陷阱：{focusTrapLabel(item.value_trap_risk)}</strong></div><div className="rounded-lg border bg-muted/35 px-3 py-2 text-muted-foreground"><strong>核心逻辑：{focusThesisLabel(item.thesis_status)}</strong></div></div><p className="mt-2 text-xs text-muted-foreground">资料：财务 {item.financial_status === "READY" ? "已就绪" : "待补齐"} · 主营业务 {item.business_profile_status === "READY" ? "已就绪" : item.business_profile_status === "PARTIAL" ? "部分完整" : "待补齐"}{item.thesis_authority === "AI_PROVISIONAL" ? " · AI 初步逻辑，待人工复核" : ""}</p>{item.focus_reasons.length ? <div className="mt-3"><div className="text-xs font-medium text-muted-foreground">值得研究的原因</div><p className="mt-1 text-sm leading-6">{item.focus_reasons.join("；")}</p></div> : null}{item.focus_cautions.length ? <div className="mt-3 rounded-lg bg-amber-500/[0.05] px-3 py-2"><div className="text-xs font-medium text-amber-800 dark:text-amber-300">需要注意</div><p className="mt-1 text-xs leading-5 text-muted-foreground">{item.focus_cautions.join("；")}</p></div> : null}{item.primary_demotion_reason ? <p className="mt-3 text-xs leading-5 text-muted-foreground">未排更高：{item.primary_demotion_reason}</p> : null}<div className="mt-4 flex justify-end border-t pt-3"><Link to={path} className="inline-flex items-center gap-1 text-sm text-primary hover:underline">查看公司研究 <ArrowRight className="h-4 w-4" /></Link></div></article>;
}

function EmptyState({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed bg-card p-8 text-center text-sm leading-6 text-muted-foreground">{children}</div>;
}

export function ValueCommandCenter() {
  const [pool, setPool] = useState<ValueLeaderPool | null>(null);
  const [signals, setSignals] = useState<StrategySignal[]>([]);
  const [automation, setAutomation] = useState<ValueResearchAutomation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [savingAutomation, setSavingAutomation] = useState(false);
  const load = async () => {
    setLoading(true);
    const [poolResult, signalsResult, automationResult] = await Promise.allSettled([api.getCurrentLeaderPool(), api.getValueSignals("CN"), api.getValueAutomation()]);
    if (poolResult.status === "fulfilled") setPool(poolResult.value);
    if (signalsResult.status === "fulfilled") setSignals(signalsResult.value);
    if (automationResult.status === "fulfilled") setAutomation(automationResult.value);
    const failed = [poolResult, signalsResult, automationResult].find((result) => result.status === "rejected");
    setError(failed && failed.status === "rejected" ? (failed.reason instanceof Error ? failed.reason.message : String(failed.reason)) : "");
    setLoading(false);
  };
  useEffect(() => { void load(); }, []);
  const companies = useMemo(() => poolCompanies(pool), [pool]);
  const currentSignals = useMemo(() => activeSignals(signals), [signals]);
  const entrySignals = currentSignals.filter((signal) => signalKind(signal) === "ENTRY");
  const exitSignals = currentSignals.filter((signal) => signalKind(signal) === "EXIT");
  const ready = companies.filter((company) => company.research_status === "READY").length;
  const status = automationStatus(automation);
  const companyNames = useMemo(() => new Map(companies.map((company) => [company.stock_code, company.stock_name])), [companies]);
  const toggleAutomation = async () => {
    if (!automation) return;
    setSavingAutomation(true);
    try {
      setAutomation(await api.updateValueAutomation(!automation.enabled));
      toast.success(!automation.enabled ? "已恢复交易日收盘后的自动研究" : "已暂停自动研究");
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : "更新自动研究状态失败"); }
    finally { setSavingAutomation(false); }
  };
  return <div className="mx-auto w-full max-w-[1500px] space-y-5 p-4 md:p-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">VALUE RESEARCH CONTROL</div><h1 className="mt-1 text-2xl font-semibold">投研总控台</h1><p className="mt-1 text-sm text-muted-foreground">从龙头候选、持续研究到机会提醒的工作状态；系统只提示，是否行动仍由人工决定。</p></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新状态</button></header>{error ? <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">部分状态读取失败：{error}</div> : null}{loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取价值研究工作状态…</div> : <><section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4"><article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">自动研究</span><BellRing className="h-4 w-4 text-primary" /></div><strong className="mt-3 block text-lg">{status.label}</strong><p className="mt-1 text-xs text-muted-foreground">交易日 {automation?.run_time || "16:45"} · {automation?.timezone || "Asia/Shanghai"}</p></article><article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">龙头候选池</span><CheckCircle2 className="h-4 w-4 text-primary" /></div><strong className="mt-3 block text-2xl tabular-nums">{companies.length}</strong><p className="mt-1 text-xs text-muted-foreground">去重公司 · 池日期 {pool?.as_of || "—"}</p></article><article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">研究资料已就绪</span><Clock3 className="h-4 w-4 text-primary" /></div><strong className="mt-3 block text-2xl tabular-nums">{ready}</strong><p className="mt-1 text-xs text-muted-foreground">其余公司会继续显示待建档或待补齐</p></article><article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">当前系统提醒</span><AlertTriangle className="h-4 w-4 text-primary" /></div><strong className="mt-3 block text-2xl tabular-nums">{currentSignals.length}</strong><p className="mt-1 text-xs text-muted-foreground">入场候选 {entrySignals.length} · 风险/退出复核 {exitSignals.length}</p></article></section><section className="grid gap-4 xl:grid-cols-[1.45fr_1fr]"><article className="rounded-xl border bg-card"><div className="flex flex-wrap items-center justify-between gap-3 border-b p-4"><div><h2 className="font-semibold">当前机会与风险</h2><p className="mt-1 text-sm text-muted-foreground">只展示已落库且尚未失效的价值线系统信号。</p></div><Link to="/value/opportunities" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">查看全部 <ArrowRight className="h-4 w-4" /></Link></div><div className="space-y-3 p-4">{currentSignals.slice(0, 4).map((signal) => <SignalRow key={signal.id} signal={signal} companyName={companyNames.get(signal.symbol)} />)}{!currentSignals.length ? <EmptyState>当前没有已生成且有效的系统提醒。<br />这不等于“没有机会”，只表示现有规则还没有产生可展示的提醒。</EmptyState> : null}</div></article><article className="rounded-xl border bg-card"><div className="border-b p-4"><h2 className="font-semibold">自动循环</h2><p className="mt-1 text-sm text-muted-foreground">收盘后更新龙头池并预建财务研究；已落库的系统信号会在“机会与风险”中展示。</p></div><div className="space-y-4 p-4"><div className="flex items-center justify-between gap-4"><div><span className={cn("rounded-full px-2 py-1 text-xs", status.tone)}>{status.label}</span><p className="mt-2 text-sm text-muted-foreground">下次运行：{dateLabel(automation?.next_run_at)}</p><p className="mt-1 text-xs text-muted-foreground">最近状态：{automation?.last_status || "尚无记录"}{automation?.last_error ? ` · ${automation.last_error}` : ""}</p></div><button type="button" onClick={() => void toggleAutomation()} disabled={!automation || savingAutomation} className="shrink-0 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60">{savingAutomation ? "保存中…" : automation?.enabled ? "暂停自动研究" : "恢复自动研究"}</button></div><div className="rounded-lg bg-muted/40 p-3 text-sm leading-6 text-muted-foreground">自动研究不会下单，也不会替代人工判断。进入“投资计划”的公司只是重点关注；出现提醒后仍需进入公司研究页复核。</div><div className="flex flex-wrap gap-2"><Link to="/value/leaders" className="rounded-md border px-3 py-2 text-sm hover:border-primary/50">查看龙头候选池</Link><Link to="/value/research" className="rounded-md border px-3 py-2 text-sm hover:border-primary/50">查看持续研究</Link><Link to="/value/plans" className="rounded-md border px-3 py-2 text-sm hover:border-primary/50">查看投资计划</Link></div></div></article></section></>}</div>;
}

export function ValueOpportunitiesCenter() {
  const [selection, setSelection] = useState<FocusSelectionResponse | null>(null);
  const [signals, setSignals] = useState<StrategySignal[]>([]);
  const [automation, setAutomation] = useState<ValueResearchAutomation | null>(null);
  const [showC, setShowC] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => {
    setLoading(true);
    const [selectionResult, signalsResult, automationResult] = await Promise.allSettled([api.getFocusSelection(), api.getValueSignals("CN"), api.getValueAutomation()]);
    if (selectionResult.status === "fulfilled") setSelection(selectionResult.value);
    if (signalsResult.status === "fulfilled") setSignals(signalsResult.value);
    if (automationResult.status === "fulfilled") setAutomation(automationResult.value);
    const failed = [selectionResult, signalsResult, automationResult].find((result) => result.status === "rejected");
    setError(failed && failed.status === "rejected" ? (failed.reason instanceof Error ? failed.reason.message : String(failed.reason)) : "");
    setLoading(false);
  };
  useEffect(() => { void load(); }, []);
  const current = useMemo(() => activeSignals(signals), [signals]);
  const entry = current.filter((signal) => signalKind(signal) === "ENTRY");
  const exit = current.filter((signal) => signalKind(signal) === "EXIT");
  const review = current.filter((signal) => signalKind(signal) === "REVIEW");
  return <div className="mx-auto w-full max-w-[1250px] space-y-5 p-4 md:p-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">LOW VALUE FOCUS</div><h1 className="mt-1 text-2xl font-semibold">机会与风险</h1><p className="mt-1 text-sm text-muted-foreground">从当前低估龙头中整理研究优先级；只用于决定先研究什么，不代表买入建议。</p></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新结果</button></header>{error ? <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">部分数据读取失败：{error}</div> : null}{loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取低估龙头研究优先级…</div> : <>{selection ? <><section className="grid gap-3 sm:grid-cols-4"><article className="rounded-xl border bg-card p-4"><div className="text-sm text-muted-foreground">当前低估龙头</div><strong className="mt-3 block text-2xl tabular-nums">{selection.total_low_value}</strong><p className="mt-1 text-xs text-muted-foreground">研究日期 {selection.research_as_of}</p></article><article className="rounded-xl border border-primary/30 bg-primary/[0.03] p-4"><div className="text-sm text-muted-foreground">重点研究</div><strong className="mt-3 block text-2xl tabular-nums text-primary">{selection.A_count}</strong><p className="mt-1 text-xs text-muted-foreground">优先投入研究时间</p></article><article className="rounded-xl border border-amber-500/30 bg-amber-500/[0.03] p-4"><div className="text-sm text-muted-foreground">继续观察</div><strong className="mt-3 block text-2xl tabular-nums text-amber-700 dark:text-amber-300">{selection.B_count}</strong><p className="mt-1 text-xs text-muted-foreground">需要观察风险或补资料</p></article><article className="rounded-xl border bg-card p-4"><div className="text-sm text-muted-foreground">暂缓优先研究</div><strong className="mt-3 block text-2xl tabular-nums">{selection.C_count}</strong><p className="mt-1 text-xs text-muted-foreground">仍保留在低估龙头池</p></article></section><section><div className="mb-3"><h2 className="font-semibold">重点研究</h2><p className="mt-1 text-sm text-muted-foreground">当前值得优先投入研究时间的低估龙头；每家公司仍需结合风险和资料边界复核。</p></div><div className="grid gap-3 lg:grid-cols-2">{selection.A.map((item) => <FocusCompanyCard key={item.stock_code} item={item} />)}</div>{!selection.A.length ? <EmptyState>当前没有满足重点研究条件的低估龙头。系统不会为了填满名单而降低资料或风险边界。</EmptyState> : null}</section><section><div className="mb-3"><h2 className="font-semibold">继续观察</h2><p className="mt-1 text-sm text-muted-foreground">有研究价值，但仍需观察风险、等待条件或补充资料。</p></div><div className="grid gap-3 lg:grid-cols-2">{selection.B.map((item) => <FocusCompanyCard key={item.stock_code} item={item} />)}</div>{!selection.B.length ? <EmptyState>当前没有进入继续观察档的公司。</EmptyState> : null}</section><section className="rounded-xl border bg-card"><button type="button" onClick={() => setShowC((value) => !value)} className="flex w-full items-center justify-between gap-3 p-4 text-left"><div><h2 className="font-semibold">暂缓优先研究</h2><p className="mt-1 text-sm text-muted-foreground">仍属于当前低估龙头，只是暂不优先投入研究时间。</p></div><span className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">{selection.C_count} 家 · {showC ? "收起" : "展开"}</span></button>{showC ? <div className="grid gap-3 border-t p-4 lg:grid-cols-2">{selection.C.map((item) => <FocusCompanyCard key={item.stock_code} item={item} />)}</div> : null}</section><p className="rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">{selection.selection_boundary}</p></> : <EmptyState>当前没有可读取的低估龙头重点研究结果。</EmptyState>}<section className="rounded-xl border bg-card"><div className="flex items-center justify-between gap-3 border-b p-4"><div><h2 className="font-semibold">系统提醒</h2><p className="mt-1 text-sm text-muted-foreground">保留原有入场、风险和退出复核提醒；它们与上方研究优先级不是同一套结论。</p></div><span className="text-xs text-muted-foreground">监控状态：{automationStatus(automation).label}</span></div><div className="grid gap-3 border-b p-4 sm:grid-cols-3"><div className="rounded-lg bg-muted/35 p-3 text-sm">入场候选 <strong className="float-right tabular-nums text-primary">{entry.length}</strong></div><div className="rounded-lg bg-muted/35 p-3 text-sm">风险 / 退出复核 <strong className="float-right tabular-nums text-danger">{exit.length}</strong></div><div className="rounded-lg bg-muted/35 p-3 text-sm">其他复核 <strong className="float-right tabular-nums">{review.length}</strong></div></div><div className="space-y-3 p-4">{current.slice(0, 12).map((signal) => <SignalRow key={signal.id} signal={signal} />)}{!current.length ? <EmptyState>当前没有已生成且有效的系统提醒。这不代表没有研究价值，只表示提醒规则未触发。</EmptyState> : null}</div></section></>}</div>;
}

export function ValueInvestmentPlans() {
  const [pool, setPool] = useState<ValueLeaderPool | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => { try { setPool(await api.getCurrentLeaderPool()); setError(""); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const priority = useMemo(() => poolCompanies(pool).filter((company) => company.is_priority), [pool]);
  return <div className="mx-auto w-full max-w-[1250px] space-y-5 p-4 md:p-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">INVESTMENT PLAN</div><h1 className="mt-1 text-2xl font-semibold">投资计划</h1><p className="mt-1 text-sm text-muted-foreground">这里仅管理人工标记的重点观察公司，不代表持仓、交易指令或自动下单。</p></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新清单</button></header>{error ? <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">读取重点观察清单失败：{error}</div> : null}{loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取当前重点观察公司…</div> : <><section className="rounded-xl border bg-card p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">重点观察清单</h2><p className="mt-1 text-sm text-muted-foreground">共 {priority.length} 家，来自当前三级行业龙头池。请结合“机会与风险”中的提醒决定是否进一步研究。</p></div><Link to="/value/research" className="inline-flex items-center gap-1 rounded-md border px-3 py-2 text-sm hover:border-primary/50">管理重点公司 <ArrowRight className="h-4 w-4" /></Link></div></section>{priority.length ? <section className="overflow-hidden rounded-xl border bg-card"><div className="hidden grid-cols-[52px_minmax(220px,1fr)_minmax(220px,1fr)_120px_110px] gap-3 border-b bg-muted/20 px-4 py-2 text-xs text-muted-foreground md:grid"><span>排序</span><span>公司</span><span>所属三级行业</span><span>研究状态</span><span className="text-right">综合评分</span></div><div className="divide-y">{priority.map((company, index) => <article key={company.stock_code} className="grid gap-3 px-4 py-3 md:grid-cols-[52px_minmax(220px,1fr)_minmax(220px,1fr)_120px_110px] md:items-center"><span className="text-xs text-muted-foreground">#{index + 1}</span><div><strong>{company.stock_name}</strong><span className="ml-2 font-mono text-xs text-muted-foreground">{company.stock_code}</span></div><p className="truncate text-sm text-muted-foreground">{company.industries.join("、")}</p><span className="text-sm">{researchStateLabel(company.research_status)}</span><div className="flex items-center justify-between gap-3 md:justify-end"><strong className="tabular-nums text-primary">{company.best_score.toFixed(1)}</strong><Link to={companyResearchPath(company.stock_code, { from: "/value/plans", fromLabel: "投资计划" }, "overview")} className="text-sm text-primary hover:underline">查看研究</Link></div></article>)}</div></section> : <EmptyState>还没有重点观察公司。<br /><Link to="/value/research" className="text-primary hover:underline">在持续研究中标记重点</Link>，再结合系统提醒和人工复核形成观察计划。</EmptyState>}<section className="rounded-xl border border-dashed p-4 text-sm leading-6 text-muted-foreground"><strong className="text-foreground">当前边界</strong><br />本页只复用已保存的“重点研究”标记。正式的仓位、买入价、卖出价、投委会决议和实盘连接不在本页自动生成，也不会被本页修改。</section></>}</div>;
}
