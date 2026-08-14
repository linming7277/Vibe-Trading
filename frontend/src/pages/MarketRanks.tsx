import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { AlertTriangle, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { api, type TdxRankResult } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EmptyState, LoadingState, PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";

const COMMON = ["涨幅榜", "跌幅榜", "成交量榜", "成交额榜", "换手率榜", "市值榜", "PE榜", "PB榜", "股息率榜"];
const SPECIAL = ["昨日涨停", "昨日连板", "最近多板", "昨日断板", "融资增加", "近期强势", "近期弱势", "高贝塔值", "持续增长"];
function pct(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `${n > 0 ? "+" : ""}${n.toFixed(2)}%` : "—"; }

export function MarketRanks() {
  const [params, setParams] = useSearchParams();
  const category = params.get("category") || "涨幅榜";
  const query = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page") || 1));
  const [data, setData] = useState<TdxRankResult | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await api.getTdxMarketRanks({ category, query, limit: 50, offset: (page - 1) * 50 })); }
    finally { setLoading(false); }
  }, [category, page, query]);
  useEffect(() => { void load(); }, [load]);
  const set = (next: Record<string, string>) => setParams({ category, ...(query ? { q: query } : {}), ...next });
  const isValuation = ["PE榜", "PB榜", "股息率榜", "市值榜"].includes(category);
  return <WorkspacePage>
    <PageHeader eyebrow="A-SHARE / RANKS" title="市场榜单" description="普通行情榜由全市场报价实时计算，通达信特色榜单直接读取动态板块成分。" />
    <section className="rounded-xl border bg-card p-5 shadow-sm"><div className="grid gap-5 lg:grid-cols-[1fr_1fr]"><div><div className="mb-2 text-xs font-medium text-muted-foreground">普通榜单</div><div className="flex flex-wrap gap-2">{COMMON.map((item) => <button key={item} onClick={() => set({ category: item, page: "1" })} className={cn("rounded-full px-3 py-1.5 text-xs", item === category ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>{item}</button>)}</div></div><div><div className="mb-2 text-xs font-medium text-muted-foreground">通达信特色榜单</div><div className="flex flex-wrap gap-2">{SPECIAL.map((item) => <button key={item} onClick={() => set({ category: item, page: "1" })} className={cn("rounded-full px-3 py-1.5 text-xs", item === category ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary")}>{item}</button>)}</div></div></div>
      <label className="relative mt-5 block max-w-md"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><input defaultValue={query} onKeyDown={(event) => event.key === "Enter" && set({ q: event.currentTarget.value.trim(), page: "1" })} placeholder="搜索代码或名称后回车" className="w-full rounded-lg border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:border-primary" /></label>
    </section>
    {isValuation && data && data.coverage.fundamental_pct < 95 ? <div className="flex items-start gap-2 rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm text-warning"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><div><div className="font-medium">全市场财务缓存覆盖率仅 {data.coverage.fundamental_pct}%</div><p className="mt-1 text-xs opacity-80">当前榜单只对有效财务值排序，缺失值不会用 0 替代。可在数据中心单独更新“财务估值”。</p></div></div> : null}
    {loading ? <LoadingState label="正在生成榜单…" /> : !data?.items.length ? <EmptyState title="当前榜单没有可展示数据" body="可能尚未更新对应缓存，或搜索条件没有命中。" /> : <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-4"><div><span className="font-semibold">{category}</span><span className="ml-2 text-xs text-muted-foreground">共 {formatNumber(data.total, 0)} 条</span></div><span className="text-xs text-muted-foreground">缓存时间 {data.as_of ? new Date(data.as_of).toLocaleString("zh-CN", { hour12: false }) : "—"}</span></div><div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="p-3">排名</th><th className="p-3">证券</th><th className="p-3 text-right">现价</th><th className="p-3 text-right">涨跌幅</th><th className="p-3 text-right">成交量（手）</th><th className="p-3 text-right">成交额（万元）</th><th className="p-3 text-right">换手率</th><th className="p-3 text-right">PE / PB</th><th className="p-3">板块</th></tr></thead><tbody className="divide-y">{data.items.map((item, index) => { const change = Number(item.change_pct); return <tr key={`${item.code}:${index}`} className="hover:bg-muted/30"><td className="p-3 font-mono text-xs text-muted-foreground">#{Number(item.rank) || (page - 1) * 50 + index + 1}</td><td className="p-3"><Link to={`/company/CN/${item.code}`} className="font-medium hover:text-primary">{item.name}</Link><div className="font-mono text-[11px] text-muted-foreground">{item.code}</div></td><td className="p-3 text-right font-mono">{formatNumber(item.price)}</td><td className={cn("p-3 text-right font-mono", change > 0 && "text-market-up", change < 0 && "text-market-down")}>{pct(change)}</td><td className="p-3 text-right font-mono">{formatNumber(item.volume_lots, 0)}</td><td className="p-3 text-right font-mono">{formatNumber(item.amount_10k)}</td><td className="p-3 text-right font-mono">{pct(item.turnover_rate)}</td><td className="p-3 text-right font-mono">{formatNumber(item.pe_ttm)} / {formatNumber(item.pb_mrq)}</td><td className="max-w-52 p-3 text-xs text-muted-foreground">{item.sectors?.slice(0, 2).map((sector) => <Link key={sector.code} to={`/market/sectors/${sector.code}`} className="mr-2 hover:text-primary">{sector.name}</Link>) || "—"}</td></tr>; })}</tbody></table></div><div className="flex items-center justify-between border-t p-4"><span className="text-xs text-muted-foreground">第 {page} / {Math.max(1, Math.ceil(data.total / 50))} 页</span><div className="flex gap-2"><button disabled={page <= 1} onClick={() => set({ page: String(page - 1) })} className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40"><ChevronLeft className="h-4 w-4" />上一页</button><button disabled={page * 50 >= data.total} onClick={() => set({ page: String(page + 1) })} className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40">下一页<ChevronRight className="h-4 w-4" /></button></div></div></section>}
  </WorkspacePage>;
}
