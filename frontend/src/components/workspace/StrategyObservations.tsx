import { Link } from "react-router";
import { AlertTriangle, ArrowRight, Database } from "lucide-react";
import type { TdxQuote, TdxSector } from "@/lib/api";
import type { StrategyObservations } from "@/lib/strategyObservations";
import { cn } from "@/lib/utils";
import { formatNumber } from "@/components/workspace/WorkspaceUI";

type StrategyLine = "value" | "emotion";

function pct(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number > 0 ? "+" : ""}${number.toFixed(2)}%` : "—";
}

function time(value?: string | null) {
  if (!value) return "尚未更新";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function Change({ value }: { value: unknown }) {
  const number = Number(value);
  return <span className={cn("font-mono", number > 0 && "text-market-up", number < 0 && "text-market-down")}>{pct(value)}</span>;
}

function PanelHeader({ title, description, asOf, to }: { title: string; description: string; asOf?: string | null; to?: string }) {
  const resolvedTo = to === "/emotion/temperature" ? "/market/overview"
    : to === "/value/sectors" ? "/market/sectors"
      : to === "/value/leaders" ? "/screener"
        : to === "/emotion/short" ? "/market/ranks" : to;
  return <div className="flex flex-col gap-3 border-b p-5 sm:flex-row sm:items-start sm:justify-between"><div><div className="flex items-center gap-2"><span className="text-xs font-semibold text-primary">REAL-TIME OBSERVATION</span><span className="rounded-full border border-warning/30 bg-warning/5 px-2 py-0.5 text-[10px] text-warning">不参与评分</span></div><h2 className="mt-1 text-lg font-semibold">{title}</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p></div><div className="shrink-0 text-right text-xs text-muted-foreground"><div>{time(asOf)}</div>{resolvedTo ? <Link to={resolvedTo} className="mt-2 inline-flex items-center gap-1 text-primary">查看完整数据<ArrowRight className="h-3.5 w-3.5" /></Link> : null}</div></div>;
}

function EmptyObservation({ market }: { market: string }) {
  return <div className="flex items-start gap-3 rounded-xl border border-warning/30 bg-warning/5 p-5 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" /><div><div className="font-medium">{market === "HK" ? "港股基础行情源尚未配置" : "实时观察数据暂不可用"}</div><p className="mt-1 text-xs leading-5 text-muted-foreground">{market === "HK" ? "请在数据与模型中配置 Futu 只读行情或 Yahoo 降级源；系统不会用零值或样例数据填充页面。" : "请到数据与模型检查通达信状态并刷新行情。评分不足不会影响已缓存原始行情的展示。"}</p></div></div>;
}

function MarketPanel({ observations }: { observations: StrategyObservations }) {
  const data = observations.overview;
  if (!data) return <EmptyObservation market="CN" />;
  return <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><PanelHeader title="市场实时快照" description="这是行情事实层，用于观察市场宽度与主要指数；它不代表情绪状态或交易结论。" asOf={data.as_of} to="/emotion/temperature" /><div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4"><div className="bg-card p-4"><div className="text-xs text-muted-foreground">有效报价</div><div className="mt-2 text-xl font-semibold">{formatNumber(data.breadth.valid, 0)}</div></div><div className="bg-card p-4"><div className="text-xs text-muted-foreground">上涨 / 下跌</div><div className="mt-2 text-xl font-semibold"><span className="text-market-up">{data.breadth.up}</span><span className="mx-1 text-muted-foreground">/</span><span className="text-market-down">{data.breadth.down}</span></div></div><div className="bg-card p-4"><div className="text-xs text-muted-foreground">涨跌比</div><div className="mt-2 text-xl font-semibold">{formatNumber(data.breadth.up_down_ratio)}</div></div><div className="bg-card p-4"><div className="text-xs text-muted-foreground">中位涨幅</div><div className="mt-2 text-xl font-semibold"><Change value={data.breadth.median_change_pct} /></div></div></div><div className="grid gap-px bg-border sm:grid-cols-2 lg:grid-cols-3">{data.indices.slice(0, 6).map((item) => <div key={item.code} className="flex items-center justify-between bg-card px-4 py-3 text-sm"><div><div className="font-medium">{item.name}</div><div className="font-mono text-[10px] text-muted-foreground">{item.code}</div></div><div className="text-right"><div className="font-mono">{formatNumber(item.price)}</div><Change value={item.change_pct} /></div></div>)}</div></section>;
}

function SectorPanel({ observations }: { observations: StrategyObservations }) {
  const data = observations.sectors;
  if (!data?.items.length) return <EmptyObservation market="CN" />;
  return <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><PanelHeader title="行业实时强度" description="按通达信板块行情展示当前强弱，仅作为待研究赛道或情绪热点观察池，不等于赛道评分。" asOf={data.as_of} to="/value/sectors" /><div className="divide-y">{data.items.slice(0, 10).map((item: TdxSector) => <Link key={item.code} to={`/market/sectors/${item.code}`} className="grid grid-cols-[1fr_84px_100px] items-center gap-3 px-5 py-3 text-sm hover:bg-muted/40"><div><div className="font-medium">{item.name}</div><div className="mt-0.5 text-[11px] text-muted-foreground">{item.category} · {item.member_count} 个成分 · 领涨 {item.leader?.name || "—"}</div></div><Change value={item.change_pct} /><span className="text-right text-xs text-muted-foreground">宽度 {pct(item.breadth_pct)}</span></Link>)}</div></section>;
}

function SecurityPanel({ observations, kind }: { observations: StrategyObservations; kind: "value" | "momentum" }) {
  const data = kind === "value" ? observations.valueUniverse : observations.momentum;
  const items = data?.items || [];
  if (!items.length) return <EmptyObservation market="CN" />;
  const value = kind === "value";
  return <section className="overflow-hidden rounded-xl border bg-card shadow-sm"><PanelHeader title={value ? "基础股票池快照" : "实时涨幅观察池"} description={value ? "按总市值展示可进入后续财务、估值和龙头识别的基础股票池；市值排序不是价值评分。" : "按实时涨幅展示异动股票；尚未通过流动性、新闻、资金、风险和状态机过滤，不是短线信号。"} asOf={data?.as_of} to={value ? "/value/leaders" : "/emotion/short"} /><div className="divide-y">{items.slice(0, 10).map((item: TdxQuote) => <Link key={item.code} to={`/company/CN/${item.code}`} className="grid grid-cols-[1fr_72px_80px_100px] items-center gap-3 px-5 py-3 text-sm hover:bg-muted/40"><div><div className="font-medium">{item.name}</div><div className="font-mono text-[10px] text-muted-foreground">{item.code} · {item.sectors?.slice(0, 2).map((sector) => sector.name).join(" / ") || "行业待映射"}</div></div><span className="text-right font-mono">{formatNumber(item.price)}</span><span className="text-right"><Change value={item.change_pct} /></span><span className="text-right text-xs text-muted-foreground">{value ? `市值 ${formatNumber(item.market_cap_100m)}亿` : `换手 ${pct(item.turnover_rate)}`}</span></Link>)}</div></section>;
}

export function StrategyObservationPanel({ line, section, market, observations }: { line: StrategyLine; section: string; market: string; observations: StrategyObservations }) {
  if (market !== "CN") return <EmptyObservation market={market} />;
  if (line === "value") {
    if (section === "macro") return <MarketPanel observations={observations} />;
    if (section === "sectors") return <SectorPanel observations={observations} />;
    return <SecurityPanel observations={observations} kind="value" />;
  }
  if (section === "temperature" || section === "cycle") return <MarketPanel observations={observations} />;
  if (section === "sectors") return <SectorPanel observations={observations} />;
  return <SecurityPanel observations={observations} kind="momentum" />;
}

export function ObservationPreview({ line, observations }: { line: StrategyLine; observations: StrategyObservations }) {
  const items = line === "value" ? observations.valueUniverse?.items : observations.sectors?.items;
  if (!items?.length) return <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">原始行情与评分结果当前都不可用</div>;
  return <div><div className="mb-3 flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning"><Database className="h-3.5 w-3.5" />实时观察数据 · 尚未进入确定性评分</div><div className="space-y-2">{items.slice(0, 4).map((item) => <Link key={item.code} to={"member_count" in item ? `/market/sectors/${item.code}` : `/company/CN/${item.code}`} className="flex items-center justify-between rounded-lg bg-muted/40 p-3 hover:bg-muted"><div><div className="font-medium">{item.name}</div><div className="font-mono text-[10px] text-muted-foreground">{item.code}</div></div><Change value={item.change_pct} /></Link>)}</div></div>;
}
