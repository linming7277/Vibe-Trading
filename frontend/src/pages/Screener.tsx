import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { AlertTriangle, ArrowRight, ChevronLeft, ChevronRight, RotateCcw, Search, SlidersHorizontal } from "lucide-react";
import { api, type TdxScreenerFilters, type TdxScreenerResult } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EmptyState, LoadingState, PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";
import { DecisionFlow } from "@/components/workspace/DecisionFlow";
import { useDecisionFlow } from "@/hooks/useDecisionFlow";

const SORTS = [["change_pct", "涨跌幅"], ["volume_lots", "成交量"], ["turnover_rate", "换手率"], ["market_cap_100m", "总市值"], ["pe_ttm", "PE"], ["pb_mrq", "PB"], ["dividend_yield", "股息率"]] as const;
function pct(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `${n > 0 ? "+" : ""}${n.toFixed(2)}%` : "—"; }
function numberOrUndefined(value: string) { const number = Number(value); return value.trim() && Number.isFinite(number) ? number : undefined; }

export function Screener() {
  const navigate = useNavigate();
  const { flow, selectLeader } = useDecisionFlow();
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Number(params.get("page") || 1));
  const initialSector = params.get("sector") || "";
  const [filters, setFilters] = useState<TdxScreenerFilters>({ query: params.get("q") || "", sector: initialSector || undefined, sort: "change_pct", direction: "desc", limit: 50 });
  const [draft, setDraft] = useState<Record<string, string>>({ sector: initialSector });
  const [data, setData] = useState<TdxScreenerResult | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => { setLoading(true); try { setData(await api.screenTdxSecurities({ ...filters, offset: (page - 1) * 50 })); } finally { setLoading(false); } }, [filters, page]);
  useEffect(() => { void load(); }, [load]);
  const apply = () => setFilters((current) => ({ ...current,
    query: draft.query ?? current.query, sector: draft.sector || undefined,
    min_price: numberOrUndefined(draft.min_price || ""), max_price: numberOrUndefined(draft.max_price || ""),
    min_change: numberOrUndefined(draft.min_change || ""), max_change: numberOrUndefined(draft.max_change || ""),
    min_turnover: numberOrUndefined(draft.min_turnover || ""), max_pe: numberOrUndefined(draft.max_pe || ""),
    max_pb: numberOrUndefined(draft.max_pb || ""), min_dividend_yield: numberOrUndefined(draft.min_dividend_yield || ""),
    min_market_cap: numberOrUndefined(draft.min_market_cap || ""), min_revenue: numberOrUndefined(draft.min_revenue || ""),
    min_net_profit: numberOrUndefined(draft.min_net_profit || ""), min_eps: numberOrUndefined(draft.min_eps || ""),
  }));
  const reset = () => { setDraft({}); setFilters({ sort: "change_pct", direction: "desc", limit: 50 }); setParams({}); };
  const chooseLeader = (row: { code: string; name: string }) => { selectLeader({ symbol: row.code, name: row.name }); navigate(`/company/CN/${encodeURIComponent(row.code)}?tab=${encodeURIComponent("研究结论")}&flow=1`); };
  const setPage = (next: number) => { const nextParams = new URLSearchParams(params); nextParams.set("page", String(next)); setParams(nextParams); };
  return <WorkspacePage><DecisionFlow current={3} /><PageHeader eyebrow="A-SHARE / LEADER" title="选择龙头" description={flow.sector_name ? `当前只筛选「${flow.sector_name}」成分股。行情强势只是候选依据，选定后必须进入深度研究。` : "对通达信缓存中的有效A股报价组合筛选；默认排除ST、退市和北交所证券。"} />
    <section className="rounded-xl border bg-card p-5 shadow-sm"><div className="grid gap-5 xl:grid-cols-4">
      <FilterGroup title="行情"><Inputs draft={draft} setDraft={setDraft} fields={[["query", "代码/名称"], ["min_price", "最低价"], ["max_price", "最高价"], ["min_change", "最低涨幅%"], ["max_change", "最高涨幅%"]]} /></FilterGroup>
      <FilterGroup title="估值"><Inputs draft={draft} setDraft={setDraft} fields={[["max_pe", "最高PE"], ["max_pb", "最高PB"], ["min_dividend_yield", "最低股息率%"], ["min_market_cap", "最低市值(亿元)"]]} /></FilterGroup>
      <FilterGroup title="财务"><Inputs draft={draft} setDraft={setDraft} fields={[["min_revenue", "最低营收(万元)"], ["min_net_profit", "最低净利润(万元)"], ["min_eps", "最低EPS"]]} /></FilterGroup>
      <FilterGroup title="属性与板块"><Inputs draft={draft} setDraft={setDraft} fields={[["sector", "板块代码/名称"], ["min_turnover", "最低换手率%"]]} /><div className="mt-3 flex flex-wrap gap-3 text-xs">{[["include_st", "含ST"], ["include_quit", "含退市"], ["include_bj", "含北交所"], ["is_hs300", "沪深300"], ["is_margin", "融资融券"], ["is_connect", "沪深港通"]].map(([key, label]) => <label key={key} className="inline-flex items-center gap-1.5"><input type="checkbox" checked={Boolean(filters[key as keyof TdxScreenerFilters])} onChange={(event) => setFilters((current) => ({ ...current, [key]: event.target.checked }))} />{label}</label>)}</div></FilterGroup>
    </div><div className="mt-5 flex flex-wrap items-center gap-2 border-t pt-4"><SlidersHorizontal className="h-4 w-4 text-muted-foreground" /><span className="text-xs text-muted-foreground">排序</span>{SORTS.map(([key, label]) => <button key={key} onClick={() => setFilters((current) => ({ ...current, sort: key }))} className={cn("rounded-full px-3 py-1 text-xs", filters.sort === key ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>{label}</button>)}<button onClick={() => setFilters((current) => ({ ...current, direction: current.direction === "desc" ? "asc" : "desc" }))} className="rounded-full border px-3 py-1 text-xs">{filters.direction === "desc" ? "降序" : "升序"}</button><div className="ml-auto flex gap-2"><button onClick={reset} className="inline-flex items-center gap-1 rounded-lg border px-3 py-2 text-sm"><RotateCcw className="h-4 w-4" />重置</button><button onClick={apply} className="inline-flex items-center gap-1 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"><Search className="h-4 w-4" />应用条件</button></div></div>
      <div className="mt-4 grid gap-3 border-t pt-4 md:grid-cols-2"><div className="rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground"><span className="font-medium text-foreground">技术条件：</span>行情K线缓存完善后可扩展均线、周期涨幅和形态条件；当前不会用不完整历史数据产生误导性筛选。</div><Link to="/formula" className="rounded-lg bg-primary/10 p-3 text-xs text-primary"><span className="font-medium">公式条件：</span>使用独立“公式选股”后台扫描，可选择通达信技术指标、条件选股、专家系统和K线形态。</Link></div>
    </section>
    {data && data.coverage.fundamentals < data.coverage.quotes ? <div className="flex items-center gap-2 rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm text-warning"><AlertTriangle className="h-4 w-4" />财务缓存覆盖 {data.coverage.fundamentals} / {data.coverage.quotes}；设置估值或财务条件时仅匹配有有效数据的股票。</div> : null}
    {loading ? <LoadingState /> : !data?.items.length ? <EmptyState title="没有符合条件的龙头候选" body="可减少条件、清空板块限制或允许北交所后重试。" /> : <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-4"><h2 className="font-semibold">龙头候选 <span className="ml-1 text-xs font-normal text-muted-foreground">{data.total} 只</span></h2><span className="text-xs text-muted-foreground">选定后进入深度研究</span></div><div className="overflow-x-auto"><table className="w-full min-w-[1040px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="p-4">证券</th><th className="p-4 text-right">现价</th><th className="p-4 text-right">涨跌幅</th><th className="p-4 text-right">换手率</th><th className="p-4 text-right">总市值(亿元)</th><th className="p-4 text-right">PE</th><th className="p-4 text-right">PB</th><th className="p-4">板块</th><th className="p-4">决策动作</th></tr></thead><tbody className="divide-y">{data.items.map((row) => <tr key={row.code} className={cn("hover:bg-muted/30", flow.symbol === row.code && "bg-primary/5")}><td className="p-4"><Link to={`/company/CN/${row.code}`} className="font-medium hover:text-primary">{row.name}</Link><div className="font-mono text-[11px] text-muted-foreground">{row.code}</div></td><td className="p-4 text-right font-mono">{formatNumber(row.price)}</td><td className={cn("p-4 text-right font-mono", Number(row.change_pct) > 0 && "text-market-up", Number(row.change_pct) < 0 && "text-market-down")}>{pct(row.change_pct)}</td><td className="p-4 text-right font-mono">{pct(row.turnover_rate)}</td><td className="p-4 text-right font-mono">{formatNumber(row.market_cap_100m)}</td><td className="p-4 text-right font-mono">{formatNumber(row.pe_ttm)}</td><td className="p-4 text-right font-mono">{formatNumber(row.pb_mrq)}</td><td className="max-w-48 p-4 text-xs text-muted-foreground">{row.sectors?.slice(0, 2).map((sector) => sector.name).join(" · ") || "—"}</td><td className="p-4"><button onClick={() => chooseLeader({ code: row.code, name: row.name })} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground">选为龙头并研究<ArrowRight className="h-3.5 w-3.5" /></button></td></tr>)}</tbody></table></div><div className="flex items-center justify-between border-t p-4"><span className="text-xs text-muted-foreground">第 {page} 页</span><div className="flex gap-2"><button disabled={page <= 1} onClick={() => setPage(page - 1)} className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40"><ChevronLeft className="h-4 w-4" />上一页</button><button disabled={page * 50 >= data.total} onClick={() => setPage(page + 1)} className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40">下一页<ChevronRight className="h-4 w-4" /></button></div></div></section>}
  </WorkspacePage>;
}

function FilterGroup({ title, children }: { title: string; children: React.ReactNode }) { return <div><h3 className="mb-3 text-sm font-semibold">{title}</h3>{children}</div>; }
function Inputs({ draft, setDraft, fields }: { draft: Record<string, string>; setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>>; fields: string[][] }) { return <div className="grid grid-cols-2 gap-2">{fields.map(([key, label]) => <input key={key} value={draft[key] || ""} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} placeholder={label} className={cn("rounded-lg border bg-background px-3 py-2 text-xs outline-none focus:border-primary", key === "query" || key === "sector" ? "col-span-2" : "")} />)}</div>; }
