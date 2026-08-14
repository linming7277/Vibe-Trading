import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ArrowDown, ArrowRight, ArrowUp, Search } from "lucide-react";
import { api, type SectorScore, type TdxSector, type TdxSectorResult } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useDecisionFlow } from "@/hooks/useDecisionFlow";
import { DecisionFlow } from "@/components/workspace/DecisionFlow";
import { EmptyState, LoadingState, PageHeader, ScoreBar, SourceBadge, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";

const dimensions: Array<[keyof SectorScore, string]> = [["momentum", "动量"], ["earnings", "盈利"], ["fund_flow", "资金"], ["breadth", "宽度"], ["valuation", "估值"], ["risk", "风险"]];
function pct(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `${n > 0 ? "+" : ""}${n.toFixed(2)}%` : "—"; }

export function SectorRanking() {
  const [params] = useSearchParams();
  const [view, setView] = useState<"realtime" | "research">(params.get("view") === "research" ? "research" : "realtime");
  return <WorkspacePage><DecisionFlow current={2} /><PageHeader eyebrow="A-SHARE / INDUSTRY" title="选择行业" description="先用实时强度看市场正在交易什么，再用研究评分验证盈利、估值与风险；选择行业后只看该板块的龙头候选。" actions={<div className="inline-flex rounded-lg border bg-card p-1"><button onClick={() => setView("realtime")} className={cn("rounded-md px-3 py-1.5 text-sm", view === "realtime" ? "bg-foreground text-background" : "text-muted-foreground")}>实时强度</button><button onClick={() => setView("research")} className={cn("rounded-md px-3 py-1.5 text-sm", view === "research" ? "bg-foreground text-background" : "text-muted-foreground")}>研究评分</button></div>} />{view === "realtime" ? <RealtimeSectors /> : <ResearchSectors />}</WorkspacePage>;
}

function RealtimeSectors() {
  const navigate = useNavigate();
  const { flow, selectSector } = useDecisionFlow();
  const [category, setCategory] = useState("行业");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<TdxSectorResult | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => { setLoading(true); try { setData(await api.getTdxSectors({ category, query, limit: 500 })); } finally { setLoading(false); } }, [category, query]);
  useEffect(() => { void load(); }, [load]);
  const choose = (row: TdxSector) => { selectSector({ code: row.code, name: row.name }); navigate(`/screener?sector=${encodeURIComponent(row.code)}&flow=1`); };
  return <>
    {flow.macro_headline ? <div className="rounded-xl border bg-muted/30 p-4 text-sm"><span className="text-muted-foreground">上游宏观约束：</span><span className="font-medium">{flow.macro_headline}</span><span className="ml-2 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary">{flow.macro_stance}</span></div> : null}
    <section className="flex flex-col gap-4 rounded-xl border bg-card p-4 shadow-sm lg:flex-row lg:items-center lg:justify-between"><div className="flex flex-wrap gap-2">{(data?.categories || ["全部", "行业", "概念", "地区", "风格"]).map((item) => <button key={item} onClick={() => setCategory(item)} className={cn("rounded-full px-3 py-1.5 text-xs", category === item ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>{item}</button>)}</div><label className="relative"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><input onKeyDown={(event) => event.key === "Enter" && setQuery(event.currentTarget.value.trim())} placeholder="搜索行业或板块" className="w-52 rounded-lg border bg-background py-2 pl-9 pr-3 text-sm" /></label></section>
    {loading ? <LoadingState /> : !data?.items.length ? <EmptyState title="暂无板块行情" body="请在数据中心更新板块模块。" /> : <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="overflow-x-auto"><table className="w-full min-w-[1040px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="p-4">板块</th><th className="p-4 text-right">涨跌幅</th><th className="p-4 text-right">上涨 / 下跌</th><th className="p-4 text-right">上涨宽度</th><th className="p-4 text-right">成交量（手）</th><th className="p-4">市场领涨</th><th className="p-4 text-right">成分数</th><th className="p-4">决策动作</th></tr></thead><tbody className="divide-y">{data.items.map((row) => <tr key={row.code} className={cn("hover:bg-muted/30", flow.sector_code === row.code && "bg-primary/5")}><td className="p-4"><Link to={`/market/sectors/${row.code}`} className="font-medium hover:text-primary">{row.name}</Link><div className="mt-0.5 text-[11px] text-muted-foreground">{row.category} · {row.code}</div></td><td className={cn("p-4 text-right font-mono", Number(row.change_pct) > 0 && "text-market-up", Number(row.change_pct) < 0 && "text-market-down")}>{pct(row.change_pct)}</td><td className="p-4 text-right font-mono"><span className="text-market-up">{row.up}</span> / <span className="text-market-down">{row.down}</span></td><td className="p-4 text-right font-mono">{pct(row.breadth_pct)}</td><td className="p-4 text-right font-mono">{formatNumber(row.volume_lots, 0)}</td><td className="p-4">{row.leader ? <Link to={`/company/CN/${row.leader.code}`} className="hover:text-primary"><span className="font-medium">{row.leader.name}</span><span className="ml-2 font-mono text-xs text-market-up">{pct(row.leader.change_pct)}</span></Link> : "—"}</td><td className="p-4 text-right font-mono">{formatNumber(row.member_count, 0)}</td><td className="p-4"><button onClick={() => choose(row)} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground">选定并找龙头<ArrowRight className="h-3.5 w-3.5" /></button></td></tr>)}</tbody></table></div><div className="border-t p-4 text-xs text-muted-foreground">共 {data.total} 个板块 · 数据时间 {data.as_of ? new Date(data.as_of).toLocaleString("zh-CN", { hour12: false }) : "—"}</div></section>}
  </>;
}

function ResearchSectors() {
  const navigate = useNavigate();
  const { flow, selectSector } = useDecisionFlow();
  const [items, setItems] = useState<SectorScore[]>([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<keyof SectorScore>("rank");
  useEffect(() => { api.getSectorRankings("CN").then((result) => setItems(result.items)).finally(() => setLoading(false)); }, []);
  const sorted = useMemo(() => [...items].sort((a, b) => sort === "rank" ? a.rank - b.rank : Number(b[sort]) - Number(a[sort])), [items, sort]);
  const choose = (row: SectorScore) => { selectSector({ code: row.sector_name, name: row.sector_name }); navigate(`/screener?sector=${encodeURIComponent(row.sector_name)}&flow=1`); };
  if (loading) return <LoadingState />;
  if (!items.length) return <EmptyState title="暂无研究评分" body="可切换到实时强度选择行业；研究评分需要先运行研究任务。" />;
  return <><div className="rounded-xl border bg-muted/30 p-4 text-sm"><span className="text-muted-foreground">宏观环境：</span>{flow.macro_headline || "尚未选择，可先返回第一步"}</div><section className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="flex flex-wrap gap-2 border-b p-4"><span className="mr-2 text-sm text-muted-foreground">排序字段</span>{[["rank", "综合"], ...dimensions].map(([key, label]) => <button key={String(key)} onClick={() => setSort(key as keyof SectorScore)} className={cn("rounded-full px-3 py-1 text-xs", sort === key ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>{label}</button>)}</div><div className="overflow-x-auto"><table className="w-full min-w-[1080px] text-sm"><thead className="bg-muted/40 text-left text-xs text-muted-foreground"><tr><th className="p-4">排名</th><th className="p-4">行业</th><th className="p-4">综合分</th>{dimensions.map(([, label]) => <th key={label} className="p-4">{label}</th>)}<th className="p-4">Agent判断</th><th className="p-4">决策动作</th></tr></thead><tbody className="divide-y">{sorted.map((row) => <tr key={row.id} className={cn(flow.sector_code === row.sector_code && "bg-primary/5")}><td className="p-4 font-mono">#{row.rank}</td><td className="p-4"><div className="font-medium">{row.sector_name}</div><div className="text-xs text-muted-foreground">{row.taxonomy} · {row.sector_code}</div></td><td className="min-w-44 p-4"><ScoreBar value={row.final_score} /></td>{dimensions.map(([key]) => <td key={String(key)} className="p-4 font-mono text-xs">{Number(row[key]).toFixed(0)}</td>)}<td className="max-w-xs p-4"><div className={cn("inline-flex items-center gap-1 font-mono text-xs", row.agent_adjustment > 0 ? "text-market-up" : row.agent_adjustment < 0 ? "text-market-down" : "text-muted-foreground")}>{row.agent_adjustment >= 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}{row.agent_adjustment >= 0 ? "+" : ""}{row.agent_adjustment}</div><div className="mt-1 text-xs text-muted-foreground">{row.agent_reason}</div></td><td className="p-4"><button onClick={() => choose(row)} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground">选定并找龙头<ArrowRight className="h-3.5 w-3.5" /></button></td></tr>)}</tbody></table></div><div className="border-t p-4"><SourceBadge status={items[0].source_status} asOf={items[0].as_of} /></div></section></>;
}
