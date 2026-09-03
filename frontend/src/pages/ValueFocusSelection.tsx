import { useEffect, useState } from "react";
import { ChevronDown, Loader2, RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { api, type FocusSelectionItem, type FocusSelectionResponse, type ValueResearchAutomation, type ValueStrategyEventBatch } from "@/lib/api";
import { LeaderCompanyQuickView } from "@/components/value/LeaderCompanyQuickView";
import { cn } from "@/lib/utils";
import { companyResearchPath } from "@/lib/routeContext";

type Tier = "A" | "B" | "C";

function valuationLabel(status: string) {
  return status === "DEEPLY_UNDERVALUED" ? "深度低估" : status === "UNDERVALUED" ? "低估关注" : "资料不足";
}

function riskLabel(status: string) {
  return status === "LOW" ? "暂未发现明显风险" : status === "MEDIUM" ? "需要继续观察" : status === "HIGH" ? "存在高等级风险" : "风险资料不足";
}

function trapLabel(status: string) {
  return status === "LOW_TRAP_RISK" ? "低" : status === "MEDIUM_TRAP_RISK" ? "中等" : status === "HIGH_TRAP_RISK" ? "高" : "资料不足";
}

function thesisLabel(status: string) {
  return ({ FORMING: "正在形成", STRENGTHENING: "逻辑正在增强", UNCHANGED: "逻辑基本稳定", WEAKENING: "逻辑正在减弱", FALSIFIED: "核心逻辑已失效", MISSING: "尚未建立" } as Record<string, string>)[status] || "资料不足";
}

function price(value: number | null) {
  return value == null ? "资料不足" : `${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`;
}

function FocusCompanyCard({ item, onOpen }: { item: FocusSelectionItem; onOpen: () => void }) {
  const riskTone = item.risk_status === "HIGH" ? "border-danger/35 bg-danger/10 text-danger" : item.risk_status === "MEDIUM" ? "border-amber-500/35 bg-amber-500/10 text-amber-800 dark:text-amber-300" : item.risk_status === "LOW" ? "border-primary/30 bg-primary/10 text-primary" : "border-muted bg-muted/40 text-muted-foreground";
  return <article
    role="button"
    tabIndex={0}
    aria-label={`查看 ${item.company_name} 研究数据`}
    onClick={onOpen}
    onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); } }}
    className="cursor-pointer rounded-xl border bg-card p-4 transition-colors hover:border-primary/50 hover:bg-primary/[0.03]"
  ><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="text-xs text-primary">{item.industry_name} · 行业排名 Top{item.leader_rank}</div><h3 className="mt-1 truncate font-semibold">{item.company_name} <span className="font-mono text-xs font-normal text-muted-foreground">{item.stock_code}</span></h3></div><span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-1 text-xs font-medium text-amber-700 dark:text-amber-300">{valuationLabel(item.valuation_status)}</span></div><div className="mt-3 grid gap-2 sm:grid-cols-3"><Metric label="当前价格" value={price(item.current_price)} /><Metric label="合理价值中枢" value={price(item.fair_value_mid)} /><Metric label="距离中枢" value={item.discount_to_mid == null ? "资料不足" : `${item.discount_to_mid >= 0 ? "低于" : "高于"} ${Math.abs(item.discount_to_mid * 100).toFixed(1)}%`} /></div><div className="mt-3 grid gap-2 text-xs sm:grid-cols-3"><div className={cn("rounded-lg border px-3 py-2", riskTone)}><strong>风险：{riskLabel(item.risk_status)}</strong></div><div className="rounded-lg border bg-muted/35 px-3 py-2 text-muted-foreground"><strong>低估陷阱：{trapLabel(item.value_trap_risk)}</strong></div><div className="rounded-lg border bg-muted/35 px-3 py-2 text-muted-foreground"><strong>核心逻辑：{thesisLabel(item.thesis_status)}</strong></div></div><p className="mt-2 text-xs text-muted-foreground">资料：财务 {item.financial_status === "READY" ? "已就绪" : "待补齐"} · 主营业务 {item.business_profile_status === "READY" ? "已就绪" : item.business_profile_status === "PARTIAL" ? "部分完整" : "待补齐"}{item.thesis_authority === "AI_PROVISIONAL" ? " · AI 初步逻辑，待人工复核" : ""}</p>{item.focus_reasons.length ? <div className="mt-3"><div className="text-xs font-medium text-muted-foreground">值得研究的原因</div><p className="mt-1 text-sm leading-6">{item.focus_reasons.join("；")}</p></div> : null}{item.focus_cautions.length ? <div className="mt-3 rounded-lg bg-amber-500/[0.05] px-3 py-2"><div className="text-xs font-medium text-amber-800 dark:text-amber-300">需要注意</div><p className="mt-1 text-xs leading-5 text-muted-foreground">{item.focus_cautions.join("；")}</p></div> : null}{item.primary_demotion_reason ? <p className="mt-3 text-xs leading-5 text-muted-foreground">未排更高：{item.primary_demotion_reason}</p> : null}<div className="mt-4 flex justify-end border-t pt-3"><Link to={companyResearchPath(item.stock_code, { from: "/value/opportunities", fromLabel: "机会与风险" }, "overview")} onClick={(event) => event.stopPropagation()} className="text-sm text-primary hover:underline">查看公司研究</Link></div></article>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">{label}</div><strong className="mt-1 block text-sm tabular-nums">{value}</strong></div>;
}

function TierSection({ title, subtitle, items, open, onToggle, onOpenItem }: { title: string; subtitle?: string; items: FocusSelectionItem[]; open: boolean; onToggle: () => void; onOpenItem: (item: FocusSelectionItem) => void }) {
  return <section className="overflow-hidden rounded-xl border bg-card"><button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-center justify-between gap-3 p-4 text-left hover:bg-muted/20"><div><h2 className="font-semibold">{title}</h2>{subtitle ? <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p> : null}</div><span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">{items.length} 家 <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} /></span></button>{open ? <div className="border-t p-4"><div className="grid gap-3 lg:grid-cols-2">{items.map((item) => <FocusCompanyCard key={item.stock_code} item={item} onOpen={() => onOpenItem(item)} />)}</div>{!items.length ? <p className="py-4 text-center text-sm text-muted-foreground">当前没有公司进入这一档。</p> : null}</div> : null}</section>;
}

export function ValueFocusSelectionPage() {
  const [selection, setSelection] = useState<FocusSelectionResponse | null>(null);
  const [automation, setAutomation] = useState<ValueResearchAutomation | null>(null);
  const [open, setOpen] = useState<Record<Tier, boolean>>({ A: true, B: false, C: false });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [quickViewItem, setQuickViewItem] = useState<FocusSelectionItem | null>(null);
  const [recentChanges, setRecentChanges] = useState<ValueStrategyEventBatch[]>([]);
  const load = async () => {
    setLoading(true);
    const [focus, auto] = await Promise.allSettled([api.getFocusSelection(), api.getValueAutomation()]);
    if (focus.status === "fulfilled") setSelection(focus.value);
    if (auto.status === "fulfilled") setAutomation(auto.value);
    const failure = [focus, auto].find((result) => result.status === "rejected");
    setError(failure && failure.status === "rejected" ? (failure.reason instanceof Error ? failure.reason.message : String(failure.reason)) : "");
    setLoading(false);
    api.getValueStrategyEventBatches(undefined, 10).then((value) => setRecentChanges(value.items)).catch(() => setRecentChanges([]));
  };
  useEffect(() => { void load(); }, []);
  const toggle = (tier: Tier) => setOpen((current) => ({ ...current, [tier]: !current[tier] }));
  return <div className="mx-auto w-full max-w-[1250px] space-y-5 p-4 md:p-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">LOW VALUE FOCUS</div><h1 className="mt-1 text-2xl font-semibold">机会与风险</h1></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新结果</button></header>{error ? <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">部分数据读取失败：{error}</div> : null}{loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取低估龙头研究优先级…</div> : <>{selection ? <><section className="grid gap-3 sm:grid-cols-4"><Metric label="当前低估龙头" value={`${selection.total_low_value} 家`} /><Metric label="重点研究" value={`${selection.A_count} 家`} /><Metric label="继续观察" value={`${selection.B_count} 家`} /><Metric label="暂缓优先研究" value={`${selection.C_count} 家`} /></section><section className="rounded-xl border bg-card p-4"><h2 className="font-semibold">最近研究变化</h2>{recentChanges.length ? <div className="mt-3 space-y-2">{recentChanges.map((item) => <div key={item.transition_batch_id} className="rounded-lg border bg-muted/20 px-3 py-2 text-sm"><strong>{item.title}</strong><p className="mt-1 text-xs text-muted-foreground">{item.summary}</p></div>)}</div> : <p className="mt-2 text-sm text-muted-foreground">当前没有需要主动展示的研究状态变化。</p>}</section><TierSection title="重点研究" items={selection.A} open={open.A} onToggle={() => toggle("A")} onOpenItem={setQuickViewItem} /><TierSection title="继续观察" subtitle="有研究价值，但仍需观察风险、等待条件或补充资料；默认收起。" items={selection.B} open={open.B} onToggle={() => toggle("B")} onOpenItem={setQuickViewItem} /><TierSection title="暂缓优先研究" subtitle="仍属于当前低估龙头，只是暂不优先投入研究时间；默认收起。" items={selection.C} open={open.C} onToggle={() => toggle("C")} onOpenItem={setQuickViewItem} /><p className="rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">{selection.selection_boundary}</p></> : <div className="rounded-xl border border-dashed bg-card p-8 text-center text-sm text-muted-foreground">当前没有可读取的低估龙头重点研究结果。</div>}<section className="rounded-xl border bg-card p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">价值线日终任务状态</h2><p className="mt-1 text-sm text-muted-foreground">这里仅显示研究数据任务是否启用，不代表触发任何研究结论。</p></div><span className="rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">{automation?.enabled ? "已启用" : "未启用"}</span></div></section></>}{quickViewItem ? <LeaderCompanyQuickView leader={{ stock_code: quickViewItem.stock_code, stock_name: quickViewItem.company_name, leader_rank: quickViewItem.leader_rank, level3_name: quickViewItem.industry_name }} onClose={() => setQuickViewItem(null)} onChat={() => { window.location.assign("/ai/agent"); }} /> : null}</div>;
}
