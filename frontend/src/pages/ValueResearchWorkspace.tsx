import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Loader2, RefreshCw, Search, Star, X } from "lucide-react";
import { Link, Outlet, useSearchParams } from "react-router";
import { toast } from "sonner";
import { api, type FinancialDossier, type ValueCompanyResearchState, type ValueLeaderPool, type ValueLeaderPoolMember } from "@/lib/api";
import { cn } from "@/lib/utils";
import { companyResearchPath } from "@/lib/routeContext";
import { ValueContinuousResearch } from "@/pages/ValueContinuousResearch";

type PoolCompany = { stock_code: string; stock_name: string; leaders: ValueLeaderPoolMember[]; best_score: number; research?: ValueCompanyResearchState };

export function ValueResearchWorkspace() { return <Outlet />; }

function companiesFromPool(pool: ValueLeaderPool | null): PoolCompany[] {
  if (!pool) return [];
  const states = new Map(pool.research_states.map((item) => [item.stock_code, item]));
  const grouped = new Map<string, PoolCompany>();
  pool.members.filter((item) => item.lifecycle_status !== "OUT_OF_TOP2").forEach((member) => {
    const current = grouped.get(member.stock_code);
    if (current) { current.leaders.push(member); current.best_score = Math.max(current.best_score, Number(member.leader_score) || 0); }
    else grouped.set(member.stock_code, { stock_code: member.stock_code, stock_name: member.stock_name, leaders: [member], best_score: Number(member.leader_score) || 0, research: states.get(member.stock_code) });
  });
  return [...grouped.values()].sort((a, b) => b.best_score - a.best_score || a.stock_code.localeCompare(b.stock_code));
}

function StateBadge({ state }: { state?: ValueCompanyResearchState }) {
  const ready = state?.research_status === "READY";
  const partial = state?.research_status === "PARTIAL";
  return <span className={cn("rounded-full px-2 py-1 text-[11px]", ready ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : partial ? "bg-amber-500/10 text-amber-700 dark:text-amber-300" : "bg-muted text-muted-foreground")}>{ready ? "资料已就绪" : partial ? "资料待补齐" : "等待建档"}</span>;
}

function CompanyDrawer({ company, pool, onClose, onReload }: { company: PoolCompany; pool: ValueLeaderPool; onClose: () => void; onReload: () => Promise<void> }) {
  const [dossier, setDossier] = useState<FinancialDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.getCompanyFinancialDossier(company.stock_code, pool.as_of).then((value) => { if (!cancelled) setDossier(value); }).catch(() => { if (!cancelled) setDossier(null); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [company.stock_code, pool.as_of]);
  const snapshot = dossier?.snapshot;
  const primary = company.leaders.slice().sort((a, b) => a.leader_rank - b.leader_rank)[0];
  const researchPath = companyResearchPath(company.stock_code, { from: "/value/research", fromLabel: "公司研究", sectorCode: primary.level3_code, sectorName: primary.level3_name }, "overview");
  const refresh = async () => {
    setRefreshing(true);
    try { await api.refreshValueCompanyResearch(company.stock_code); setDossier(await api.getCompanyFinancialDossier(company.stock_code, pool.as_of)); await onReload(); toast.success("公司确定性财务快照已更新"); }
    catch (error) { toast.error(error instanceof Error ? error.message : "研究刷新失败"); }
    finally { setRefreshing(false); }
  };
  return <>
    <button aria-label="关闭公司详情" className="fixed inset-0 z-40 bg-black/35" onClick={onClose} />
    <aside role="dialog" aria-modal="true" aria-label={company.stock_name + "公司详情"} className="fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l bg-background shadow-2xl">
      <header className="border-b p-5"><div className="flex items-start justify-between gap-4"><div><div className="text-xs text-primary">当前三级行业 Top2 龙头池</div><h2 className="mt-1 text-xl font-semibold">{company.stock_name} <span className="font-mono text-sm font-normal text-muted-foreground">{company.stock_code}</span></h2><div className="mt-2 flex flex-wrap gap-1.5">{company.leaders.map((item) => <span key={item.level3_code} className="rounded-full bg-muted px-2 py-1 text-xs">{item.level3_name} · #{item.leader_rank}</span>)}</div></div><button aria-label="关闭公司详情" onClick={onClose} className="rounded-md border p-2 text-muted-foreground hover:bg-muted"><X className="h-4 w-4" /></button></div><div className="mt-4 grid grid-cols-3 gap-2 text-sm"><div className="rounded-lg bg-primary/10 p-3"><div className="text-xs text-muted-foreground">最高行业内评分</div><strong>{company.best_score.toFixed(1)}</strong></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">覆盖行业</div><strong>{company.leaders.length} 个</strong></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">研究状态</div><div className="mt-1"><StateBadge state={company.research} /></div></div></div></header>
      <div className="min-h-0 flex-1 overflow-y-auto p-5"><section><h3 className="font-semibold">行业内入选依据</h3><div className="mt-3 space-y-2">{company.leaders.map((leader) => <article key={leader.level3_code} className="rounded-lg border p-3"><div className="flex justify-between gap-3"><div><strong>{leader.level3_name}</strong><div className="mt-1 text-xs text-muted-foreground">{leader.level1_name} / {leader.level2_name}</div></div><div className="text-right"><div className="font-semibold text-primary">第 {leader.leader_rank} 名</div><div className="text-xs text-muted-foreground">评分 {leader.leader_score.toFixed(1)} · 覆盖 {(leader.coverage * 100).toFixed(0)}%</div></div></div></article>)}</div></section><section className="mt-6 border-t pt-5"><div className="flex items-center justify-between gap-3"><div><h3 className="font-semibold">财务与预测快照</h3><p className="mt-1 text-xs text-muted-foreground">读取本地历史财务、三年情景预测和财报研究员存档。</p></div><button onClick={() => void refresh()} disabled={refreshing} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm disabled:opacity-50"><RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />刷新研究</button></div>{loading ? <div className="flex h-40 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取本地研究档案…</div> : snapshot ? <div className="mt-4 space-y-3"><div className="grid grid-cols-3 gap-2 text-sm"><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">历史财务</div><strong>{snapshot.feature_status}</strong></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">三年预测</div><strong>{snapshot.forecast_status}</strong></div><div className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">Agent 解读</div><strong>{snapshot.analysis_status}</strong></div></div>{snapshot.analysis?.executive_summary ? <article className="rounded-lg border border-primary/20 bg-primary/[0.035] p-4"><h4 className="font-medium">研究摘要</h4><p className="mt-2 text-sm leading-6 text-muted-foreground">{snapshot.analysis.executive_summary}</p></article> : <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">确定性财务与预测已保存；大模型解读尚未生成。</div>}</div> : <div className="mt-4 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">本地尚无该公司的财务研究快照，可点击“刷新研究”预建。</div>}</section></div>
      <footer className="flex justify-end gap-2 border-t p-4"><button onClick={onClose} className="rounded-md border px-3 py-2 text-sm">关闭</button><Link to={researchPath} className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">进入统一公司研究 <ArrowRight className="h-4 w-4" /></Link></footer>
    </aside>
  </>;
}

function ResearchPoolPage({ valuationOnly = false }: { valuationOnly?: boolean }) {
  const [params, setParams] = useSearchParams();
  const [pool, setPool] = useState<ValueLeaderPool | null>(null);
  const [query, setQuery] = useState("");
  const [onlyPriority, setOnlyPriority] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => { try { setPool(await api.getCurrentLeaderPool()); setError(""); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const companies = useMemo(() => companiesFromPool(pool), [pool]);
  const visible = useMemo(() => { const needle = query.trim().toLowerCase(); return companies.filter((company) => (!onlyPriority || Boolean(company.research?.is_priority)) && (!needle || [company.stock_code, company.stock_name, ...company.leaders.map((item) => item.level3_name)].some((value) => value.toLowerCase().includes(needle)))); }, [companies, query, onlyPriority]);
  const selected = companies.find((item) => item.stock_code === params.get("symbol")) || null;
  const togglePriority = async (company: PoolCompany) => { try { await api.updateValueResearchState(company.stock_code, !Boolean(company.research?.is_priority)); await load(); } catch (error) { toast.error(error instanceof Error ? error.message : "更新失败"); } };
  return <div className="mx-auto w-full max-w-[1500px] space-y-4 p-4 md:p-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">{valuationOnly ? "VALUATION RESEARCH" : "COMPANY RESEARCH"}</div><h1 className="mt-1 text-2xl font-semibold">{valuationOnly ? "估值研究" : "公司研究"}</h1><p className="mt-1 text-sm text-muted-foreground">直接跟随当前三级行业 Top2 龙头池；公司按最高行业内综合评分排序。</p></div>{pool ? <div className="text-right text-sm text-muted-foreground"><div>池日期 {pool.as_of}</div><div>{pool.company_count} 家公司 · {pool.current_membership_count} 条行业龙头关系</div></div> : null}</header><section className="rounded-xl border bg-card"><div className="flex flex-wrap gap-2 border-b p-3"><label className="flex min-w-[260px] flex-1 items-center rounded-md border bg-background px-3"><Search className="h-4 w-4 text-muted-foreground" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索公司、代码或三级行业" className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none" /></label><button onClick={() => setOnlyPriority((value) => !value)} className={cn("inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm", onlyPriority && "border-primary bg-primary/10 text-primary")}><Star className="h-4 w-4" />重点研究</button></div>{error ? <div className="m-4 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error}</div> : null}{loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取当前龙头池…</div> : <div className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3">{visible.map((company, index) => <article key={company.stock_code} className="rounded-xl border bg-background p-4 transition-colors hover:border-primary/40"><button onClick={() => setParams({ symbol: company.stock_code })} className="block w-full text-left"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="text-xs text-muted-foreground">综合排序 #{index + 1}</div><h2 className="mt-1 truncate font-semibold">{company.stock_name} <span className="font-mono text-xs font-normal text-muted-foreground">{company.stock_code}</span></h2></div><strong className="text-lg tabular-nums text-primary">{company.best_score.toFixed(1)}</strong></div><div className="mt-3 flex flex-wrap gap-1">{company.leaders.slice(0, 3).map((item) => <span key={item.level3_code} className="rounded bg-muted px-2 py-1 text-[11px] font-semibold">{item.level1_name} / {item.level2_name} / {item.level3_name} #{item.leader_rank}</span>)}</div><div className="mt-3 flex items-center justify-between"><StateBadge state={company.research} /><span className="text-xs text-primary">查看详情</span></div></button><button onClick={() => void togglePriority(company)} className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"><Star className={cn("h-3.5 w-3.5", company.research?.is_priority && "fill-current text-primary")} />{company.research?.is_priority ? "取消重点" : "标记重点"}</button></article>)}</div>}{!loading && !visible.length ? <div className="p-12 text-center text-sm text-muted-foreground">没有匹配的公司</div> : null}</section>{selected && pool ? <CompanyDrawer company={selected} pool={pool} onClose={() => setParams({})} onReload={load} /> : null}</div>;
}

type ResearchFilter = "ALL" | "PRIORITY" | "READY" | "PARTIAL" | "PENDING";

function researchFilterMatches(company: PoolCompany, filter: ResearchFilter) {
  if (filter === "ALL") return true;
  if (filter === "PRIORITY") return Boolean(company.research?.is_priority);
  if (filter === "READY") return company.research?.research_status === "READY";
  if (filter === "PARTIAL") return company.research?.research_status === "PARTIAL";
  return !company.research || !["READY", "PARTIAL"].includes(company.research.research_status);
}

function researchNextStep(company: PoolCompany) {
  if (company.research?.research_status === "READY") return "可查看研究结论";
  if (company.research?.research_status === "PARTIAL") return "需要补齐研究资料";
  return "等待建立研究档案";
}

export function ResearchQueuePage() {
  const [params, setParams] = useSearchParams();
  const [pool, setPool] = useState<ValueLeaderPool | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ResearchFilter>("ALL");
  const [industry, setIndustry] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = async () => { try { setPool(await api.getCurrentLeaderPool()); setError(""); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const companies = useMemo(() => companiesFromPool(pool), [pool]);
  const industries = useMemo(() => [...new Set(companies.flatMap((company) => company.leaders.map((item) => item.level3_name)))].sort((left, right) => left.localeCompare(right, "zh-CN")), [companies]);
  const counts = useMemo(() => ({ ALL: companies.length, PRIORITY: companies.filter((item) => researchFilterMatches(item, "PRIORITY")).length, READY: companies.filter((item) => researchFilterMatches(item, "READY")).length, PARTIAL: companies.filter((item) => researchFilterMatches(item, "PARTIAL")).length, PENDING: companies.filter((item) => researchFilterMatches(item, "PENDING")).length }), [companies]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return companies.filter((company) => researchFilterMatches(company, filter) && (industry === "ALL" || company.leaders.some((item) => item.level3_name === industry)) && (!needle || [company.stock_code, company.stock_name, ...company.leaders.flatMap((item) => [item.level1_name, item.level2_name, item.level3_name])].some((value) => value.toLowerCase().includes(needle))));
  }, [companies, filter, industry, query]);
  const selected = companies.find((item) => item.stock_code === params.get("symbol")) || null;
  const togglePriority = async (company: PoolCompany) => { try { await api.updateValueResearchState(company.stock_code, !Boolean(company.research?.is_priority)); await load(); } catch (reason) { toast.error(reason instanceof Error ? reason.message : "更新重点研究失败"); } };
  const filters: Array<[ResearchFilter, string]> = [["ALL", "全部"], ["PRIORITY", "重点"], ["PENDING", "待建档"], ["PARTIAL", "待补齐"], ["READY", "资料已就绪"]];
  return <div className="mx-auto w-full max-w-[1500px] space-y-4 p-4 md:p-6"><header className="flex flex-wrap items-end justify-between gap-4"><div><div className="text-xs font-semibold text-primary">CONTINUOUS RESEARCH</div><h1 className="mt-1 text-2xl font-semibold">持续研究</h1><p className="mt-1 text-sm text-muted-foreground">当前三级行业龙头的自动研究队列。综合评分用于排序，重点看资料状态和下一步复核。</p></div>{pool ? <div className="flex flex-wrap justify-end gap-2 text-xs text-muted-foreground"><span className="rounded-full bg-muted px-2.5 py-1.5">池日期 {pool.as_of}</span><span className="rounded-full bg-muted px-2.5 py-1.5">{companies.length} 家去重公司</span><span className="rounded-full bg-muted px-2.5 py-1.5">{pool.current_membership_count} 条龙头关系</span></div> : null}</header><section className="rounded-xl border bg-card"><div className="border-b p-3"><div className="flex flex-wrap gap-2"><label className="flex min-w-[260px] flex-1 items-center rounded-md border bg-background px-3"><Search className="h-4 w-4 text-muted-foreground" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索公司、代码、一级至三级行业" className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none" /></label><select aria-label="筛选三级行业" value={industry} onChange={(event) => setIndustry(event.target.value)} className="rounded-md border bg-background px-3 py-2 text-sm"><option value="ALL">全部三级行业</option>{industries.map((item) => <option key={item} value={item}>{item}</option>)}</select></div><div role="tablist" aria-label="研究状态筛选" className="mt-3 flex flex-wrap gap-2">{filters.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={filter === key} onClick={() => setFilter(key)} className={cn("rounded-full border px-3 py-1.5 text-xs", filter === key ? "border-primary bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:border-primary/50 hover:text-foreground")}>{label} <span className="ml-1 tabular-nums">{counts[key]}</span></button>)}</div></div>{error ? <div className="m-4 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error}</div> : null}{loading ? <div className="flex h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取当前研究队列…</div> : <><div className="flex items-center justify-between border-b bg-muted/20 px-4 py-2 text-xs text-muted-foreground"><span>显示 {visible.length} 家公司</span><span>按行业内综合评分从高到低排序</span></div><div className="hidden grid-cols-[52px_minmax(170px,1fr)_minmax(250px,1.55fr)_150px_88px] gap-3 border-b px-4 py-2 text-xs text-muted-foreground lg:grid"><span>排名</span><span>公司</span><span>所属三级行业</span><span>研究状态 / 下一步</span><span className="text-right">评分</span></div><div className="divide-y">{visible.map((company) => { const rank = companies.findIndex((item) => item.stock_code === company.stock_code) + 1; const primary = company.leaders.slice().sort((left, right) => left.leader_rank - right.leader_rank)[0]; return <article key={company.stock_code} className="flex items-stretch gap-2 px-3 py-2 transition-colors hover:bg-muted/25"><button type="button" onClick={() => setParams({ symbol: company.stock_code })} className="grid min-w-0 flex-1 grid-cols-[42px_minmax(0,1fr)_auto] items-center gap-3 py-1 text-left lg:grid-cols-[52px_minmax(170px,1fr)_minmax(250px,1.55fr)_150px_88px]"><div className="text-xs text-muted-foreground">#{rank}</div><div className="min-w-0"><strong className="truncate text-sm">{company.stock_name}</strong><span className="ml-2 font-mono text-xs text-muted-foreground">{company.stock_code}</span><p className="mt-1 text-xs text-muted-foreground lg:hidden">{primary.level1_name} / {primary.level2_name} / {primary.level3_name} · 行业第 {primary.leader_rank} 名</p></div><div className="hidden min-w-0 lg:block"><p className="truncate text-sm">{primary.level1_name} / {primary.level2_name} / {primary.level3_name}</p><p className="mt-1 text-xs text-muted-foreground">行业第 {primary.leader_rank} 名{company.leaders.length > 1 ? ` · 覆盖 ${company.leaders.length} 个三级行业` : ""}</p></div><div className="hidden lg:block"><StateBadge state={company.research} /><p className="mt-1 text-xs text-muted-foreground">{researchNextStep(company)}</p></div><div className="text-right"><strong className="text-base tabular-nums text-primary">{company.best_score.toFixed(1)}</strong><span className="mt-1 block text-xs text-primary lg:hidden">查看</span></div></button><button type="button" aria-label={`${company.research?.is_priority ? "取消" : "标记"}${company.stock_name}为重点研究`} onClick={() => void togglePriority(company)} className={cn("self-center rounded-md border p-2 text-muted-foreground hover:border-primary/50 hover:text-primary", company.research?.is_priority && "border-primary/40 bg-primary/10 text-primary")}><Star className={cn("h-4 w-4", company.research?.is_priority && "fill-current")} /></button></article>; })}</div></>}{!loading && !visible.length ? <div className="p-12 text-center text-sm text-muted-foreground">没有匹配的公司，请调整研究状态、行业或搜索条件。</div> : null}</section>{selected && pool ? <CompanyDrawer company={selected} pool={pool} onClose={() => setParams({})} onReload={load} /> : null}</div>;
}

export function ValueResearchQueue() { return <ValueContinuousResearch />; }
export function ValueValuationCenter() { return <ResearchPoolPage valuationOnly />; }
