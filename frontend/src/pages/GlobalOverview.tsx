import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { BarChart3, CircleAlert, Globe2, Loader2, RefreshCw, Sparkles } from "lucide-react";
import ReactMarkdown, { type Options as ReactMarkdownOptions } from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { api, type MacroOverview, type MacroOverviewSeries, type TdxMarketCatalogQuotes, type TdxStatus } from "@/lib/api";
import { PageHeader, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";
import { cn } from "@/lib/utils";

type GlobalMarket = "HK" | "US";

const MARKETS: Array<{ code: GlobalMarket; label: string; currency: string; scope: string }> = [
  { code: "HK", label: "港股", currency: "HKD", scope: "通达信港股全市场" },
  { code: "US", label: "美股", currency: "USD", scope: "通达信美股全市场" },
];

const MARKDOWN_OPTIONS: ReactMarkdownOptions = {
  remarkPlugins: [remarkGfm],
  components: {
    h1: ({ children }) => <h3 className="mt-3 text-base font-semibold">{children}</h3>,
    h2: ({ children }) => <h3 className="mt-3 text-sm font-semibold">{children}</h3>,
    h3: ({ children }) => <h4 className="mt-2 text-sm font-semibold">{children}</h4>,
    p: ({ children }) => <p className="mt-1.5 text-sm leading-6">{children}</p>,
    ul: ({ children }) => <ul className="mt-1.5 list-disc pl-5 text-sm leading-6">{children}</ul>,
    ol: ({ children }) => <ol className="mt-1.5 list-decimal pl-5 text-sm leading-6">{children}</ol>,
    table: ({ children }) => <div className="mt-2 overflow-x-auto"><table className="w-full text-xs">{children}</table></div>,
    th: ({ children }) => <th className="border-b px-2 py-1 text-left font-medium">{children}</th>,
    td: ({ children }) => <td className="border-b px-2 py-1">{children}</td>,
  },
};

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatDay(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("zh-CN");
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

function formatSeriesValue(item: MacroOverviewSeries) {
  if (typeof item.value !== "number") return "—";
  switch (item.unit) {
    case "%":
    case "percent":
      return `${item.value.toFixed(3)}%`;
    case "USD_per_barrel":
      return `$${item.value.toFixed(2)}`;
    case "CNY_per_USD":
      return item.value.toFixed(4);
    case "USD_per_metric_ton":
      return `$${formatNumber(item.value, 0)}`;
    case "元":
      return `¥${item.value.toFixed(4)}`;
    case "指数":
      return formatNumber(item.value, 1);
    case "比值":
      return item.value.toFixed(3);
    case "亿元":
      return `${formatNumber(item.value, 0)} 亿`;
    default:
      return formatNumber(item.value, 2);
  }
}

function formatChange(item: MacroOverviewSeries) {
  if (typeof item.change !== "number" || item.change === 0) return null;
  const magnitude = Math.abs(item.change);
  const text = item.unit === "percent" || item.unit === "%" ? `${item.change >= 0 ? "+" : ""}${item.change.toFixed(3)}pp` : `${item.change >= 0 ? "+" : ""}${magnitude < 1 ? item.change.toFixed(3) : formatNumber(item.change, 1)}`;
  return text;
}

function freshnessBadge(item: MacroOverviewSeries) {
  if (item.status === "READY") return { text: "新鲜", className: "bg-success/10 text-success" };
  if (item.status === "STALE") return { text: `滞后 ${item.lag_days ?? "?"} 天`, className: "bg-warning/10 text-warning" };
  if (item.status === "MISSING") return { text: "缺数据", className: "bg-danger/10 text-danger" };
  return { text: item.status, className: "bg-muted text-muted-foreground" };
}

function Sparkline({ points }: { points: Array<{ date: string; value: number }> }) {
  if (points.length < 2) return null;
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coords = points.map((point, index) => {
    const x = (index / (points.length - 1)) * 100;
    const y = 30 - ((point.value - min) / span) * 26 - 2;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const rising = values[values.length - 1] >= values[0];
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="mt-2 h-8 w-full">
      <polyline points={coords.join(" ")} fill="none" strokeWidth="1.6" className={rising ? "stroke-danger" : "stroke-success"} style={{ vectorEffect: "non-scaling-stroke" }} />
    </svg>
  );
}

export function GlobalOverview() {
  const [overview, setOverview] = useState<MacroOverview | null>(null);
  const [status, setStatus] = useState<TdxStatus | null>(null);
  const [quotes, setQuotes] = useState<Record<GlobalMarket, TdxMarketCatalogQuotes | null>>({ HK: null, US: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState<GlobalMarket | "">("");

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [nextOverview, nextStatus, hkQuotes, usQuotes] = await Promise.all([
        api.getMacroOverview(), api.getTdxStatus(),
        api.getTdxMarketCatalogQuotes("HK", 12), api.getTdxMarketCatalogQuotes("US", 12),
      ]);
      setOverview(nextOverview);
      setStatus(nextStatus);
      setQuotes({ HK: hkQuotes, US: usQuotes });
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取宏观数据");
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
  const projection = overview?.projection;
  const macro = projection?.macro;
  const axes = macro?.axes ?? [];
  const missingAxes = axes.filter((axis) => axis.state === "资料不足");
  const forecast = overview?.forecast;

  return (
    <WorkspacePage>
      <PageHeader
        eyebrow="MACRO / CROSS MARKET"
        title="宏观总览"
        description="宏观环境、跨市场关键序列、国内宏观读数与下一交易日前瞻。宏观只说明当前经济和资金面的松紧，不预测涨跌，也不是交易指令。"
        actions={<div className="inline-flex items-center gap-2 rounded-full border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"><span className={cn("h-1.5 w-1.5 rounded-full", status?.available && status?.client_process_running ? "bg-success" : "bg-warning")} />{status?.available && status?.client_process_running ? "通达信已连接" : "等待通达信连接"}</div>}
      />

      {loading && !overview ? <div className="flex min-h-48 items-center justify-center gap-2 rounded-xl border border-dashed text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取宏观与跨市场数据…</div> : null}
      {error ? <div className="rounded-xl border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{error}</div> : null}
      {activeMessage ? <section className="mb-4 rounded-xl border border-primary/25 bg-primary/5 p-4 text-sm"><span className="inline-flex items-center gap-2 font-medium"><Loader2 className="h-4 w-4 animate-spin" />{activeMessage}</span><span className="ml-2 font-mono text-xs text-muted-foreground">{active?.progress}/{active?.total}</span></section> : null}

      {projection && !projection.available ? (
        <section className="rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm text-muted-foreground">宏观环境暂不可用：{projection.reason ?? "无快照"}</section>
      ) : null}

      {macro ? (
        <section className="rounded-xl border border-primary/25 bg-card p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-1.5 text-xs font-medium text-primary"><Globe2 className="h-3.5 w-3.5" />宏观环境</div>
              <h2 className="mt-2 text-2xl font-semibold">{macro.regime}</h2>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{macro.regime_label}</p>
            </div>
            <div className="flex items-center gap-2">
              {projection?.as_of ? <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">数据截至 {projection.as_of}</span> : null}
              <Link to="/macro" className="rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-muted">环境详情</Link>
            </div>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-5">
            {axes.map((axis) => {
              const prev = overview?.axes_trend?.[axis.key]?.prev;
              const delta = axis.score != null && prev?.score != null ? axis.score - prev.score : null;
              return (
                <div key={axis.key} className="rounded-lg border border-border p-3">
                  <div className="text-xs text-muted-foreground">{axis.label}</div>
                  <strong className={cn("mt-1 block text-lg", axis.state === "偏暖" && "text-emerald-600", axis.state === "偏冷" && "text-blue-600")}>{axis.state}</strong>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    {axis.score != null ? <span className="font-mono">{axis.score.toFixed(1)}</span> : null}
                    {delta != null && Math.abs(delta) >= 0.05 ? <span className={cn("ml-1 font-mono", delta > 0 ? "text-success" : "text-danger")}>{delta > 0 ? `↑${delta.toFixed(1)}` : `↓${Math.abs(delta).toFixed(1)}`}</span> : null}
                    {prev?.state && prev.state !== axis.state ? <div className="mt-0.5">前值 {prev.state}</div> : null}
                  </div>
                </div>
              );
            })}
          </div>
          {missingAxes.length > 0 ? (
            <p className="mt-4 rounded-lg border border-amber-500/25 bg-amber-500/[0.04] p-3 text-sm text-amber-800">缺 {missingAxes.length} 个轴的资料：{missingAxes.map((axis) => axis.label).join("、")}，整体结论需更谨慎。</p>
          ) : (
            <p className="mt-4 text-xs text-muted-foreground">五个轴资料完整。轴分值是与上一份快照对比后的变化；环境不改变哪些公司进入研究名单。</p>
          )}
        </section>
      ) : null}

      {forecast && forecast.narrative_md ? (
        <section className="mt-4 rounded-xl border bg-card p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              <h2 className="text-lg font-semibold">下一交易日前瞻</h2>
              {forecast.direction_cn ? <span className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">{forecast.direction_cn}</span> : null}
              {forecast.run_mode ? <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">影子运行 {forecast.run_mode}</span> : null}
            </div>
            <span className="text-xs text-muted-foreground">目标交易日 {forecast.target_trade_date || "—"} · 生成 {formatTime(forecast.created_at)}</span>
          </div>
          <div className="mt-2 text-xs text-muted-foreground">由宏观预测引擎在每日收盘链自动生成（SHADOW 影子模式），仅供参考，不作为结论，更不是交易指令。</div>
          <div className="mt-3 rounded-lg bg-muted/20 p-4 text-foreground">
            <ReactMarkdown {...MARKDOWN_OPTIONS}>{forecast.narrative_md}</ReactMarkdown>
          </div>
        </section>
      ) : null}

      {overview?.series?.length ? (
        <section className="mt-4">
          <div className="flex items-baseline justify-between">
            <h2 className="text-lg font-semibold">跨市场关键序列</h2>
            <span className="text-xs text-muted-foreground">每个工作日 07:35 自动刷新 · 生成于 {formatTime(overview.generated_at)}</span>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {overview.series.map((item) => {
              const badge = freshnessBadge(item);
              const changeText = formatChange(item);
              return (
                <article key={item.series_id} className="rounded-xl border bg-card p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-2">
                    <div className="text-xs text-muted-foreground">{item.label}</div>
                    <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", badge.className)}>{badge.text}</span>
                  </div>
                  <div className="mt-2 flex items-baseline gap-2">
                    <span className="text-2xl font-semibold">{formatSeriesValue(item)}</span>
                    {changeText ? <span className={cn("text-xs font-medium", item.change! > 0 ? "text-success" : "text-danger")}>{changeText}</span> : null}
                  </div>
                  <Sparkline points={item.sparkline} />
                  <div className="mt-1 text-[11px] text-muted-foreground">数据日期 {formatDay(item.observation_date)}{item.prev_observation_date ? `（前值 ${formatDay(item.prev_observation_date)}）` : ""}</div>
                </article>
              );
            })}
          </div>
        </section>
      ) : null}

      {overview?.domestic_series?.length ? (
        <section className="mt-4 rounded-xl border bg-card shadow-sm">
          <div className="border-b p-5"><h2 className="text-lg font-semibold">国内宏观读数</h2><p className="mt-1 text-xs text-muted-foreground">官方月度序列与市场内部指标；月度数据按官方发布节奏更新，滞后以徽标标注。</p></div>
          <div className="grid grid-cols-[1.4fr_1fr_.9fr_.9fr] gap-2 border-b bg-muted/40 px-4 py-2 text-[11px] font-medium text-muted-foreground"><span>指标</span><span className="text-right">最新值</span><span className="text-right">较前值</span><span className="text-right">数据日期</span></div>
          {overview.domestic_series.map((item) => {
            const badge = freshnessBadge(item);
            const changeText = formatChange(item);
            return (
              <div key={item.series_id} className="grid grid-cols-[1.4fr_1fr_.9fr_.9fr] items-center gap-2 border-b px-4 py-2.5 text-sm last:border-b-0">
                <div className="flex items-center gap-2"><span className="truncate">{item.label}</span><span className={cn("rounded-full px-1.5 py-0.5 text-[10px]", badge.className)}>{badge.text}</span></div>
                <div className="text-right font-medium">{formatSeriesValue(item)}</div>
                <div className={cn("text-right font-mono text-xs", changeText ? (item.change! > 0 ? "text-success" : "text-danger") : "text-muted-foreground")}>{changeText ?? "—"}</div>
                <div className="text-right text-xs text-muted-foreground">{formatDay(item.observation_date)}</div>
              </div>
            );
          })}
        </section>
      ) : null}

      <section className="mt-4">
        <h2 className="text-lg font-semibold">港美股市场快照</h2>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          {MARKETS.map((market) => {
            const catalog = catalogs[market.code];
            const coverage = catalog?.securities ? (catalog.quotes / catalog.securities) * 100 : null;
            const busy = Boolean(activeMessage) || Boolean(refreshing);
            return <article key={market.code} className="rounded-xl border bg-card p-5 shadow-sm">
              <div className="flex items-start justify-between gap-4"><div><div className="text-xs font-semibold text-primary">TDX / {market.code}</div><h3 className="mt-1 text-xl font-semibold">{market.label}市场快照</h3><p className="mt-1 text-sm text-muted-foreground">{market.scope} · 计价货币 {market.currency}</p></div><span className={cn("rounded-full px-2.5 py-1 text-xs font-medium", catalog?.latest_refresh?.status === "completed" ? "bg-success/10 text-success" : catalog?.latest_refresh?.status === "failed" ? "bg-danger/10 text-danger" : "bg-warning/10 text-warning")}>{statusLabel(catalog?.latest_refresh?.status)}</span></div>
              <div className="mt-5 grid grid-cols-3 gap-3"><Metric label="证券目录" value={catalog ? formatNumber(catalog.securities, 0) : "—"} /><Metric label="有效行情" value={catalog ? formatNumber(catalog.quotes, 0) : "—"} /><Metric label="行情覆盖" value={coverage == null ? "—" : `${coverage.toFixed(1)}%`} tone={coverage != null && coverage >= 90 ? "success" : "warning"} /></div>
              <div className="mt-4 rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground"><div className="flex justify-between gap-3"><span>快照版本</span><span className="truncate font-mono text-foreground">{catalog?.latest_refresh?.snapshot_id ?? "—"}</span></div><div className="mt-2 flex justify-between gap-3"><span>最近刷新</span><span>{formatTime(catalog?.latest_refresh?.completed_at)}</span></div></div>
              <button onClick={() => void refresh(market.code)} disabled={busy} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"><RefreshCw className={cn("h-4 w-4", refreshing === market.code && "animate-spin")} />刷新{market.label}快照</button>
            </article>;
          })}
        </div>
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-2">{MARKETS.map((market) => <MarketMovers key={market.code} market={market} data={quotes[market.code]} />)}</section>

      <section className="mt-4 rounded-xl border bg-card p-5 shadow-sm"><div className="flex items-start gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><Globe2 className="h-4 w-4" /></div><div><h2 className="font-semibold">数据边界</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">宏观环境来自本地宏观序列的确定性投影；跨市场序列每个工作日早上从官方与 FRED 渠道自动刷新，滞后直接标注在卡片上；前瞻由预测引擎在影子模式生成，不进入任何结论。港美股行情、基础财务和估值优先使用本机通达信客户端。宏观不筛名单：行业分类、指数成分与官方披露以独立版本继续接入。</p></div></div></section>
    </WorkspacePage>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "success" | "warning" }) {
  return <div className="rounded-lg border bg-muted/20 p-3"><div className="text-[11px] text-muted-foreground">{label}</div><div className={cn("mt-1 text-lg font-semibold", tone === "success" && "text-success", tone === "warning" && "text-warning")}>{value}</div></div>;
}

function MarketMovers({ market, data }: { market: { code: GlobalMarket; label: string; currency: string }; data: TdxMarketCatalogQuotes | null }) {
  return <article className="overflow-hidden rounded-xl border bg-card shadow-sm">
    <div className="flex items-center justify-between border-b p-5"><div><div className="text-xs font-semibold text-primary">MARKET MOVERS / {market.code}</div><h3 className="mt-1 text-lg font-semibold">{market.label}涨幅居前</h3></div><BarChart3 className="h-5 w-5 text-muted-foreground" /></div>
    <div className="grid grid-cols-[1.1fr_.75fr_.7fr_.85fr] gap-2 border-b bg-muted/40 px-4 py-2 text-[11px] font-medium text-muted-foreground"><span>证券</span><span>现价</span><span>涨跌幅</span><span>成交量</span></div>
    {data?.items?.length ? data.items.map((item) => <div key={item.code} className="grid grid-cols-[1.1fr_.75fr_.7fr_.85fr] gap-2 border-b px-4 py-3 text-xs last:border-b-0"><div className="min-w-0"><div className="truncate font-medium" title={item.name}>{item.name || item.code}</div><div className="mt-0.5 font-mono text-[10px] text-muted-foreground">{item.code}</div></div><div className="self-center font-medium">{typeof item.price === "number" ? item.price.toFixed(2) : "—"}</div><div className={cn("self-center font-medium", typeof item.change_pct === "number" && item.change_pct > 0 ? "text-success" : typeof item.change_pct === "number" && item.change_pct < 0 ? "text-danger" : "text-muted-foreground")}>{percent(item.change_pct)}</div><div className="self-center text-muted-foreground">{typeof item.volume_lots === "number" ? formatNumber(item.volume_lots, 0) : "—"}</div></div>) : <div className="p-8 text-center text-sm text-muted-foreground"><CircleAlert className="mr-1 inline h-4 w-4" />尚无可展示的市场行情快照</div>}
    <div className="border-t bg-muted/20 px-4 py-2 text-[11px] text-muted-foreground">数据日期：{formatTime(data?.as_of)} · 按涨跌幅排序 · 不构成交易信号</div>
  </article>;
}
