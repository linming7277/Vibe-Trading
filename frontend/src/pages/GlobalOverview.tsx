import { useCallback, useEffect, useMemo, useState } from "react";
import { BarChart3, CircleAlert, Globe2, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, type TdxMarketCatalogQuotes, type TdxStatus } from "@/lib/api";
import { PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";
import { cn } from "@/lib/utils";

type GlobalMarket = "HK" | "US";

const MARKETS: Array<{ code: GlobalMarket; label: string; currency: string; scope: string }> = [
  { code: "HK", label: "港股", currency: "HKD", scope: "通达信港股全市场" },
  { code: "US", label: "美股", currency: "USD", scope: "通达信美股全市场" },
];

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function percent(value?: number | null) {
  return typeof value === "number" ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}%` : "—";
}

function statusLabel(status?: string | null) {
  if (status === "completed") return "快照已发布";
  if (status === "running" || status === "queued") return "更新中";
  if (status === "failed") return "更新失败";
  return "尚未刷新";
}

export function GlobalOverview() {
  const [status, setStatus] = useState<TdxStatus | null>(null);
  const [quotes, setQuotes] = useState<Record<GlobalMarket, TdxMarketCatalogQuotes | null>>({ HK: null, US: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState<GlobalMarket | "">("");

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextStatus, hkQuotes, usQuotes] = await Promise.all([
        api.getTdxStatus(), api.getTdxMarketCatalogQuotes("HK", 12), api.getTdxMarketCatalogQuotes("US", 12),
      ]);
      setStatus(nextStatus);
      setQuotes({ HK: hkQuotes, US: usQuotes });
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取通达信全球市场数据");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const active = status?.active_job;
  useEffect(() => {
    if (!active || !["queued", "running"].includes(active.status)) return;
    const timer = window.setInterval(() => void load(true), 1500);
    return () => window.clearInterval(timer);
  }, [active?.id, active?.status, load]);

  const catalogs = useMemo(
    () => Object.fromEntries((status?.market_catalogs ?? []).map((item) => [item.market, item])) as Partial<Record<GlobalMarket, NonNullable<TdxStatus["market_catalogs"]>[number]>>,
    [status?.market_catalogs],
  );

  const refresh = async (market: GlobalMarket) => {
    setRefreshing(market);
    try {
      const job = await api.refreshTdxMarketCatalog(market);
      setStatus((current) => current ? { ...current, active_job: job } : current);
      toast.success(`${market === "HK" ? "港股" : "美股"}通达信快照已启动刷新`);
      void load(true);
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : "启动市场刷新失败");
    } finally {
      setRefreshing("");
    }
  };

  const activeMessage = active && ["queued", "running"].includes(active.status) ? active.message : "";

  return (
    <WorkspacePage>
      <PageHeader
        eyebrow="GLOBAL / TONGDAXIN"
        title="全球策略"
        description="港股与美股行情、基础财务和估值优先使用本机通达信客户端。公司公告、原始财报和监管披露后续接入各市场官方来源。"
        actions={<div className="inline-flex items-center gap-2 rounded-full border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"><span className={cn("h-1.5 w-1.5 rounded-full", status?.available && status?.client_process_running ? "bg-success" : "bg-warning")} />{status?.available && status?.client_process_running ? "通达信已连接" : "等待通达信连接"}</div>}
      />

      {loading && !status ? <div className="flex min-h-48 items-center justify-center gap-2 rounded-xl border border-dashed text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取全球市场快照…</div> : null}
      {error ? <div className="rounded-xl border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{error}</div> : null}
      {activeMessage ? <section className="mb-4 rounded-xl border border-primary/25 bg-primary/5 p-4 text-sm"><span className="inline-flex items-center gap-2 font-medium"><Loader2 className="h-4 w-4 animate-spin" />{activeMessage}</span><span className="ml-2 font-mono text-xs text-muted-foreground">{active?.progress}/{active?.total}</span></section> : null}

      <section className="grid gap-4 lg:grid-cols-2">
        {MARKETS.map((market) => {
          const catalog = catalogs[market.code];
          const coverage = catalog?.securities ? (catalog.quotes / catalog.securities) * 100 : null;
          const busy = Boolean(activeMessage) || Boolean(refreshing);
          return <article key={market.code} className="rounded-xl border bg-card p-5 shadow-sm">
            <div className="flex items-start justify-between gap-4"><div><div className="text-xs font-semibold text-primary">TDX / {market.code}</div><h2 className="mt-1 text-xl font-semibold">{market.label}市场快照</h2><p className="mt-1 text-sm text-muted-foreground">{market.scope} · 计价货币 {market.currency}</p></div><span className={cn("rounded-full px-2.5 py-1 text-xs font-medium", catalog?.latest_refresh?.status === "completed" ? "bg-success/10 text-success" : catalog?.latest_refresh?.status === "failed" ? "bg-danger/10 text-danger" : "bg-warning/10 text-warning")}>{statusLabel(catalog?.latest_refresh?.status)}</span></div>
            <div className="mt-5 grid grid-cols-3 gap-3"><Metric label="证券目录" value={catalog ? formatNumber(catalog.securities, 0) : "—"} /><Metric label="有效行情" value={catalog ? formatNumber(catalog.quotes, 0) : "—"} /><Metric label="行情覆盖" value={coverage == null ? "—" : `${coverage.toFixed(1)}%`} tone={coverage != null && coverage >= 90 ? "success" : "warning"} /></div>
            <div className="mt-4 rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground"><div className="flex justify-between gap-3"><span>快照版本</span><span className="truncate font-mono text-foreground">{catalog?.latest_refresh?.snapshot_id ?? "—"}</span></div><div className="mt-2 flex justify-between gap-3"><span>最近刷新</span><span>{formatTime(catalog?.latest_refresh?.completed_at)}</span></div></div>
            <button onClick={() => void refresh(market.code)} disabled={busy} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"><RefreshCw className={cn("h-4 w-4", refreshing === market.code && "animate-spin")} />刷新{market.label}快照</button>
          </article>;
        })}
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-2">{MARKETS.map((market) => <MarketMovers key={market.code} market={market} data={quotes[market.code]} />)}</section>

      <section className="mt-4 rounded-xl border bg-card p-5 shadow-sm"><div className="flex items-start gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><Globe2 className="h-4 w-4" /></div><div><h2 className="font-semibold">全球策略数据边界</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">当前页面展示通达信的港美股全市场目录与行情快照，并已成为公司研究的优先行情、基础财务和估值来源。行业分类、指数成分、长期历史行情与官方披露会以独立版本继续接入，避免不同来源在同一次策略运行中混用。</p></div></div></section>
    </WorkspacePage>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "success" | "warning" }) {
  return <div className="rounded-lg border bg-muted/20 p-3"><div className="text-[11px] text-muted-foreground">{label}</div><div className={cn("mt-1 text-lg font-semibold", tone === "success" && "text-success", tone === "warning" && "text-warning")}>{value}</div></div>;
}

function MarketMovers({ market, data }: { market: { code: GlobalMarket; label: string; currency: string }; data: TdxMarketCatalogQuotes | null }) {
  return <article className="overflow-hidden rounded-xl border bg-card shadow-sm">
    <div className="flex items-center justify-between border-b p-5"><div><div className="text-xs font-semibold text-primary">MARKET MOVERS / {market.code}</div><h2 className="mt-1 text-lg font-semibold">{market.label}涨幅居前</h2></div><BarChart3 className="h-5 w-5 text-muted-foreground" /></div>
    <div className="grid grid-cols-[1.1fr_.75fr_.7fr_.85fr] gap-2 border-b bg-muted/40 px-4 py-2 text-[11px] font-medium text-muted-foreground"><span>证券</span><span>现价</span><span>涨跌幅</span><span>成交量</span></div>
    {data?.items?.length ? data.items.map((item) => <div key={item.code} className="grid grid-cols-[1.1fr_.75fr_.7fr_.85fr] gap-2 border-b px-4 py-3 text-xs last:border-b-0"><div className="min-w-0"><div className="truncate font-medium" title={item.name}>{item.name || item.code}</div><div className="mt-0.5 font-mono text-[10px] text-muted-foreground">{item.code}</div></div><div className="self-center font-medium">{typeof item.price === "number" ? item.price.toFixed(2) : "—"}</div><div className={cn("self-center font-medium", typeof item.change_pct === "number" && item.change_pct > 0 ? "text-success" : typeof item.change_pct === "number" && item.change_pct < 0 ? "text-danger" : "text-muted-foreground")}>{percent(item.change_pct)}</div><div className="self-center text-muted-foreground">{typeof item.volume_lots === "number" ? formatNumber(item.volume_lots, 0) : "—"}</div></div>) : <div className="p-8 text-center text-sm text-muted-foreground"><CircleAlert className="mr-1 inline h-4 w-4" />尚无可展示的市场行情快照</div>}
    <div className="border-t bg-muted/20 px-4 py-2 text-[11px] text-muted-foreground">数据日期：{formatTime(data?.as_of)} · 按涨跌幅排序 · 不构成交易信号</div>
  </article>;
}
