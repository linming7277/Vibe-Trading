import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft, ArrowRight, TrendingDown, TrendingUp } from "lucide-react";
import { api, type TdxQuote, type TdxSectorDetail } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useDecisionFlow } from "@/hooks/useDecisionFlow";
import { DecisionFlow } from "@/components/workspace/DecisionFlow";
import { EmptyState, LoadingState, MetricCard, PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";

function pct(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `${n > 0 ? "+" : ""}${n.toFixed(2)}%` : "—"; }
export function SectorDetail() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const { selectSector, selectLeader } = useDecisionFlow();
  const [data, setData] = useState<TdxSectorDetail | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => { api.getTdxSector(code).then(setData).catch(() => setData(null)).finally(() => setLoading(false)); }, [code]);
  if (loading) return <WorkspacePage><LoadingState /></WorkspacePage>;
  if (!data) return <WorkspacePage><EmptyState title="未找到板块" body="请确认板块代码，或先在数据中心更新板块缓存。" /></WorkspacePage>;
  const sector = data.sector;
  const screen = () => { selectSector({ code: sector.code, name: sector.name }); navigate(`/screener?sector=${encodeURIComponent(sector.code)}&flow=1`); };
  const research = (row: TdxQuote) => { selectSector({ code: sector.code, name: sector.name }); selectLeader({ symbol: row.code, name: row.name }); navigate(`/company/CN/${encodeURIComponent(row.code)}?tab=${encodeURIComponent("研究结论")}&flow=1`); };
  return <WorkspacePage><DecisionFlow current={2} /><Link to="/market/sectors?flow=1" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="h-4 w-4" />返回行业选择</Link><PageHeader eyebrow={`INDUSTRY / ${sector.code}`} title={sector.name} description="先验证行业宽度，再从成分股中选择龙头候选；市场领涨不等于基本面龙头，仍需进入深度研究。" actions={<button onClick={screen} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">筛选该行业龙头<ArrowRight className="h-4 w-4" /></button>} />
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5"><MetricCard label="板块指数" value={formatNumber(sector.price)} /><MetricCard label="涨跌幅" value={pct(sector.change_pct)} tone={Number(sector.change_pct) > 0 ? "positive" : Number(sector.change_pct) < 0 ? "negative" : undefined} /><MetricCard label="成分股" value={formatNumber(data.member_count, 0)} /><MetricCard label="上涨" value={formatNumber(data.up, 0)} tone="positive" icon={<TrendingUp className="h-4 w-4" />} /><MetricCard label="下跌" value={formatNumber(data.down, 0)} tone="negative" icon={<TrendingDown className="h-4 w-4" />} /></section>
    <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-5"><div><h2 className="font-semibold">龙头候选</h2><p className="mt-1 text-xs text-muted-foreground">当前按市场强度排序，估值与财务缺失时不以 0 代替。</p></div><span className="text-xs text-muted-foreground">{data.as_of ? new Date(data.as_of).toLocaleString("zh-CN", { hour12: false }) : "—"}</span></div><div className="overflow-x-auto"><table className="w-full min-w-[940px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="p-4">证券</th><th className="p-4 text-right">现价</th><th className="p-4 text-right">涨跌幅</th><th className="p-4 text-right">成交量（手）</th><th className="p-4 text-right">PE</th><th className="p-4 text-right">PB</th><th className="p-4 text-right">股息率</th><th className="p-4">下一步</th></tr></thead><tbody className="divide-y">{data.members.map((row) => <tr key={row.code} className="hover:bg-muted/30"><td className="p-4"><Link to={`/company/CN/${row.code}`} className="font-medium hover:text-primary">{row.name}</Link><div className="font-mono text-[11px] text-muted-foreground">{row.code}</div></td><td className="p-4 text-right font-mono">{formatNumber(row.price)}</td><td className={cn("p-4 text-right font-mono", Number(row.change_pct) > 0 && "text-market-up", Number(row.change_pct) < 0 && "text-market-down")}>{pct(row.change_pct)}</td><td className="p-4 text-right font-mono">{formatNumber(row.volume_lots, 0)}</td><td className="p-4 text-right font-mono">{formatNumber(row.pe_ttm)}</td><td className="p-4 text-right font-mono">{formatNumber(row.pb_mrq)}</td><td className="p-4 text-right font-mono">{pct(row.dividend_yield)}</td><td className="p-4"><button onClick={() => research(row)} className="inline-flex items-center gap-1 rounded-lg border border-primary/30 px-3 py-2 text-xs font-medium text-primary hover:bg-primary/5">选为龙头并研究<ArrowRight className="h-3.5 w-3.5" /></button></td></tr>)}</tbody></table></div></section>
  </WorkspacePage>;
}
