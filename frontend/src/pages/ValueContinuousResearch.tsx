import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock3, Loader2, RefreshCw, Star } from "lucide-react";
import { Link } from "react-router";
import { api, type ValueCompanyResearchState, type ValueLeaderPool, type ValueLeaderPoolMember, type ValueResearchAutomation } from "@/lib/api";
import { cn } from "@/lib/utils";
import { companyResearchPath } from "@/lib/routeContext";

type ResearchCompany = {
  stock_code: string;
  stock_name: string;
  best_score: number;
  industries: string[];
  state?: ValueCompanyResearchState;
};

function companiesFromPool(pool: ValueLeaderPool | null): ResearchCompany[] {
  if (!pool) return [];
  const states = new Map(pool.research_states.map((state) => [state.stock_code, state]));
  const grouped = new Map<string, ResearchCompany>();
  pool.members.filter((member) => member.lifecycle_status !== "OUT_OF_TOP2").forEach((member: ValueLeaderPoolMember) => {
    const current = grouped.get(member.stock_code);
    const score = Number(member.leader_score) || 0;
    if (current) {
      current.best_score = Math.max(current.best_score, score);
      if (!current.industries.includes(member.level3_name)) current.industries.push(member.level3_name);
      return;
    }
    grouped.set(member.stock_code, { stock_code: member.stock_code, stock_name: member.stock_name, best_score: score, industries: [member.level3_name], state: states.get(member.stock_code) });
  });
  return [...grouped.values()].sort((left, right) => right.best_score - left.best_score || left.stock_code.localeCompare(right.stock_code));
}

function scheduleLabel(automation: ValueResearchAutomation | null) {
  if (!automation) return { label: "状态未读取", tone: "bg-muted text-muted-foreground" };
  if (!automation.enabled) return { label: "已暂停", tone: "bg-amber-500/10 text-amber-700 dark:text-amber-300" };
  if (automation.last_status === "completed") return { label: "自动运行中", tone: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" };
  if (["partial", "waiting_data"].includes(automation.last_status || "")) return { label: "等待数据或部分完成", tone: "bg-amber-500/10 text-amber-700 dark:text-amber-300" };
  if (automation.last_status === "stale") return { label: "数据已过期", tone: "bg-danger/10 text-danger" };
  return { label: "已启用，等待运行", tone: "bg-primary/10 text-primary" };
}

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.replace("T", " ").slice(0, 16) : date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function StatusBadge({ state }: { state?: ValueCompanyResearchState }) {
  const ready = state?.research_status === "READY";
  const partial = state?.research_status === "PARTIAL";
  return <span className={cn("rounded-full px-2 py-1 text-[11px]", ready ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : partial ? "bg-amber-500/10 text-amber-700 dark:text-amber-300" : "bg-muted text-muted-foreground")}>{ready ? "资料已就绪" : partial ? "资料待补齐" : "等待建档"}</span>;
}

function ResearchActionRow({ company, reason }: { company: ResearchCompany; reason: string }) {
  return <article className="flex flex-col gap-3 rounded-xl border bg-card p-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><StatusBadge state={company.state} /><span className="text-xs text-muted-foreground">{reason}</span></div><h2 className="mt-2 font-semibold">{company.stock_name} <span className="font-mono text-xs font-normal text-muted-foreground">{company.stock_code}</span></h2><p className="mt-1 truncate text-sm text-muted-foreground">{company.industries.join("、")} · 综合评分 {company.best_score.toFixed(1)}</p></div><Link to={companyResearchPath(company.stock_code, { from: "/value/research", fromLabel: "持续研究" }, "overview")} className="inline-flex shrink-0 items-center justify-center gap-1 rounded-md border px-3 py-2 text-sm hover:border-primary/50 hover:text-primary">查看并处理 <ArrowRight className="h-4 w-4" /></Link></article>;
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed bg-card p-7 text-center text-sm leading-6 text-muted-foreground">{children}</div>;
}

export function ValueContinuousResearch() {
  const [pool, setPool] = useState<ValueLeaderPool | null>(null);
  const [automation, setAutomation] = useState<ValueResearchAutomation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => {
    setLoading(true);
    const [poolResult, automationResult] = await Promise.allSettled([api.getCurrentLeaderPool(), api.getValueAutomation()]);
    if (poolResult.status === "fulfilled") setPool(poolResult.value);
    if (automationResult.status === "fulfilled") setAutomation(automationResult.value);
    const failed = [poolResult, automationResult].find((result) => result.status === "rejected");
    setError(failed && failed.status === "rejected" ? (failed.reason instanceof Error ? failed.reason.message : String(failed.reason)) : "");
    setLoading(false);
  };
  useEffect(() => { void load(); }, []);
  const companies = useMemo(() => companiesFromPool(pool), [pool]);
  const pending = companies.filter((company) => !company.state || !["READY", "PARTIAL"].includes(company.state.research_status));
  const partial = companies.filter((company) => company.state?.research_status === "PARTIAL");
  const priority = companies.filter((company) => Boolean(company.state?.is_priority));
  const ready = companies.filter((company) => company.state?.research_status === "READY");
  const schedule = scheduleLabel(automation);
  const needsAttention = [...pending, ...partial];
  return <div className="mx-auto w-full max-w-[1250px] space-y-5 p-4 md:p-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">CONTINUOUS RESEARCH</div><h1 className="mt-1 text-2xl font-semibold">持续研究</h1><p className="mt-1 text-sm text-muted-foreground">只显示需要处理的研究例外和自动运行状态；完整公司名单在“龙头候选池”。</p></div><button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-primary/50 disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新状态</button></header>{error ? <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">部分研究状态读取失败：{error}</div> : null}{loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取自动研究状态…</div> : <><section className="grid gap-3 md:grid-cols-3"><article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">自动研究</span><Clock3 className="h-4 w-4 text-primary" /></div><span className={cn("mt-3 inline-block rounded-full px-2 py-1 text-xs", schedule.tone)}>{schedule.label}</span><p className="mt-2 text-xs text-muted-foreground">下次运行：{formatTime(automation?.next_run_at)}</p></article><article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">需要处理</span><AlertTriangle className="h-4 w-4 text-primary" /></div><strong className="mt-3 block text-2xl tabular-nums">{needsAttention.length}</strong><p className="mt-1 text-xs text-muted-foreground">待建档 {pending.length} · 待补齐 {partial.length}</p></article><article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><span className="text-sm text-muted-foreground">研究池已就绪</span><CheckCircle2 className="h-4 w-4 text-primary" /></div><strong className="mt-3 block text-2xl tabular-nums">{ready.length}</strong><p className="mt-1 text-xs text-muted-foreground">当前池共 {companies.length} 家 · 池日期 {pool?.as_of || "—"}</p></article></section><section className="space-y-3"><div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="font-semibold">需要处理</h2><p className="mt-1 text-sm text-muted-foreground">只有无法完成自动研究的公司才会出现在这里。</p></div><Link to="/value/leaders" className="text-sm text-primary hover:underline">查看完整龙头候选池</Link></div>{needsAttention.length ? <div className="space-y-3">{pending.map((company) => <ResearchActionRow key={company.stock_code} company={company} reason="新进入研究池，尚未建立研究档案" />)}{partial.map((company) => <ResearchActionRow key={company.stock_code} company={company} reason="已有研究资料，但仍有数据或分析缺口" />)}</div> : <Empty>当前没有待建档或待补齐的公司。<br />已就绪公司不在这里重复列出，系统会在后续收盘后继续更新。</Empty>}</section><section className="space-y-3"><div className="flex items-end justify-between gap-3"><div><h2 className="flex items-center gap-2 font-semibold"><Star className="h-4 w-4 text-primary" />重点观察</h2><p className="mt-1 text-sm text-muted-foreground">人工标记的重点公司；它们不是交易指令。</p></div><Link to="/value/plans" className="text-sm text-primary hover:underline">进入投资计划</Link></div>{priority.length ? <div className="space-y-3">{priority.slice(0, 12).map((company) => <ResearchActionRow key={company.stock_code} company={company} reason="人工标记为重点观察" />)}</div> : <Empty>尚未标记重点观察公司。可在龙头候选池或公司研究中标记重点。</Empty>}</section><section className="rounded-xl border bg-card p-4"><h2 className="font-semibold">最近一次自动运行</h2><div className="mt-3 grid gap-3 text-sm md:grid-cols-3"><div><span className="text-muted-foreground">最近状态</span><p className="mt-1 font-medium">{automation?.last_status || "尚无记录"}</p></div><div><span className="text-muted-foreground">最近运行标识</span><p className="mt-1 break-all font-mono text-xs">{automation?.last_run_id || "—"}</p></div><div><span className="text-muted-foreground">异常说明</span><p className="mt-1 text-muted-foreground">{automation?.last_error || "无"}</p></div></div><p className="mt-4 rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">目前只展示已有的最近一次自动运行状态；项目尚未提供可读取的逐日运行历史，因此本页不会虚构“历史任务列表”。</p></section></>}</div>;
}
