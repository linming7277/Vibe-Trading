import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { Activity, BarChart3, Database, RefreshCw, TrendingDown, TrendingUp, Wifi, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { api, type TdxMarketOverview, type TdxQuote } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EmptyState, LoadingState, MetricCard, PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";

function pct(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number > 0 ? "+" : ""}${number.toFixed(2)}%` : "—";
}

function time(value?: string | null) {
  if (!value) return "尚未更新";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function QuoteRow({ item }: { item: TdxQuote }) {
  const change = Number(item.change_pct ?? 0);
  return <Link to={`/company/CN/${item.code}`} className="grid grid-cols-[1fr_90px_80px] items-center gap-3 px-4 py-3 text-sm hover:bg-muted/40"><div><div className="font-medium">{item.name}</div><div className="font-mono text-[11px] text-muted-foreground">{item.code}</div></div><div className="text-right font-mono">{formatNumber(item.price)}</div><div className={cn("text-right font-mono", change > 0 && "text-market-up", change < 0 && "text-market-down")}>{pct(change)}</div></Link>;
}

export function MarketOverview() {
  const [data, setData] = useState<TdxMarketOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await api.getTdxMarketOverview()); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取市场缓存"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      let job = await api.startTdxUpdate("quote");
      while (["queued", "running"].includes(job.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 900));
        job = await api.getTdxJob(job.id);
      }
      if (job.status === "failed") throw new Error(job.error || "实时行情更新失败");
      await load();
      toast.success("A股实时行情已更新");
    } catch (reason) { toast.error(reason instanceof Error ? reason.message : "实时行情更新失败"); }
    finally { setRefreshing(false); }
  };

  const maxDistribution = useMemo(() => Math.max(1, ...(data?.distribution.map((item) => item.count) ?? [1])), [data]);
  if (loading) return <WorkspacePage><LoadingState label="正在汇总A股市场数据…" /></WorkspacePage>;
  if (!data) return <WorkspacePage><EmptyState title="市场全景暂不可用" body={error || "请先在数据中心更新行情和指数。"} /></WorkspacePage>;
  const { breadth } = data;
  return <WorkspacePage>
    <PageHeader eyebrow="A-SHARE / TONGDAXIN" title="市场全景" description="实时行情、市场宽度、涨跌分布、主要指数与成交活跃度。页面只读取本地缓存。" actions={<><span className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs", data.client_running ? "border-success/30 bg-success/10 text-success" : "border-danger/30 bg-danger/5 text-danger")}>{data.client_running ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}{data.client_running ? "通达信已连接" : "通达信未运行"}</span><button onClick={() => void refresh()} disabled={refreshing} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"><RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />刷新实时行情</button></>} />
    <div className="text-right text-xs text-muted-foreground">数据时间（北京时间）：{time(data.as_of)}</div>
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
      <MetricCard label="有效报价" value={formatNumber(breadth.valid, 0)} icon={<Database className="h-4 w-4" />} />
      <MetricCard label="上涨" value={formatNumber(breadth.up, 0)} tone="positive" icon={<TrendingUp className="h-4 w-4" />} />
      <MetricCard label="下跌" value={formatNumber(breadth.down, 0)} tone="negative" icon={<TrendingDown className="h-4 w-4" />} />
      <MetricCard label="平盘" value={formatNumber(breadth.flat, 0)} />
      <MetricCard label="涨跌比" value={formatNumber(breadth.up_down_ratio)} icon={<Activity className="h-4 w-4" />} />
      <MetricCard label="中位涨幅" value={pct(breadth.median_change_pct)} tone={Number(breadth.median_change_pct) > 0 ? "positive" : Number(breadth.median_change_pct) < 0 ? "negative" : undefined} />
    </section>
    <section className="grid gap-5 xl:grid-cols-[1.4fr_.6fr]">
      <article className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-5"><div><div className="text-xs font-semibold text-primary">MAJOR INDEX</div><h2 className="mt-1 text-lg font-semibold">主要指数</h2></div><BarChart3 className="h-5 w-5 text-muted-foreground" /></div><div className="grid sm:grid-cols-2 lg:grid-cols-3">{data.indices.map((item) => <div key={item.code} className="border-b border-r p-4 last:border-r-0"><div className="flex items-center justify-between"><span className="text-sm font-medium">{item.name}</span><span className="font-mono text-[10px] text-muted-foreground">{item.code}</span></div><div className="mt-3 flex items-end justify-between"><span className="text-xl font-semibold">{formatNumber(item.price)}</span><span className={cn("font-mono text-sm", Number(item.change_pct) > 0 ? "text-market-up" : Number(item.change_pct) < 0 ? "text-market-down" : "text-muted-foreground")}>{pct(item.change_pct)}</span></div></div>)}</div></article>
      <article className="rounded-xl border bg-card p-5 shadow-sm"><div className="text-xs font-semibold text-primary">DISTRIBUTION</div><h2 className="mt-1 text-lg font-semibold">涨跌分布</h2><div className="mt-5 space-y-4">{data.distribution.map((item) => <div key={item.label} className="grid grid-cols-[64px_1fr_44px] items-center gap-2 text-xs"><span className="text-muted-foreground">{item.label}</span><div className="h-2.5 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full", item.label.includes("-") ? "bg-market-down/70" : "bg-market-up/70")} style={{ width: `${item.count / maxDistribution * 100}%` }} /></div><span className="text-right font-mono">{item.count}</span></div>)}</div></article>
    </section>
    <section className="grid gap-5 xl:grid-cols-2">
      <article className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-5"><div><div className="text-xs font-semibold text-primary">ACTIVITY</div><h2 className="mt-1 text-lg font-semibold">成交活跃股票</h2></div><Link to="/market/ranks?category=成交量榜" className="text-sm text-primary">完整榜单</Link></div><div className="divide-y">{data.activity.slice(0, 8).map((item) => <QuoteRow key={item.code} item={item} />)}</div></article>
      <article className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="flex items-center justify-between border-b p-5"><div><div className="text-xs font-semibold text-primary">SPECIAL LIST</div><h2 className="mt-1 text-lg font-semibold">昨日涨停预览</h2></div><Link to="/market/ranks?category=昨日涨停" className="text-sm text-primary">特色榜单</Link></div>{data.limit_up_preview.length ? <div className="divide-y">{data.limit_up_preview.map((item) => <QuoteRow key={item.code} item={item} />)}</div> : <div className="p-8 text-center text-sm text-muted-foreground">特色榜单缓存尚未更新</div>}</article>
    </section>
  </WorkspacePage>;
}
