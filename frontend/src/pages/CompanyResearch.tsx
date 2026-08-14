import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { AlertTriangle, ArrowLeft, ArrowRight, FilePlus2, Loader2, RefreshCw, Scale } from "lucide-react";
import { toast } from "sonner";
import { api, type CompanyDossier, type MarketCode, type PriceBar, type TdxSecurityOverview } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { EmptyState, LoadingState, MetricCard, PageHeader, SourceBadge, WorkspacePage, formatNumber } from "@/components/workspace/WorkspaceUI";
import { DecisionFlow } from "@/components/workspace/DecisionFlow";
import { useDecisionFlow } from "@/hooks/useDecisionFlow";

const TABS = ["概览", "行情K线", "财务估值", "题材板块", "分红股本", "资金结构", "研究结论"] as const;
type Tab = typeof TABS[number];
type Dict = Record<string, unknown>;
function pct(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `${n > 0 ? "+" : ""}${n.toFixed(2)}%` : "—"; }
function asDict(value: unknown): Dict { return value && typeof value === "object" && !Array.isArray(value) ? value as Dict : {}; }
function asRows(value: unknown): Dict[] { return Array.isArray(value) ? value.filter((item): item is Dict => Boolean(item && typeof item === "object")) : []; }

export function CompanyResearch() {
  const { market: rawMarket, symbol = "" } = useParams<{ market: string; symbol: string }>();
  const market = (rawMarket?.toUpperCase() ?? "CN") as MarketCode;
  if (market !== "CN") return <LegacyCompany market={market} symbol={symbol} />;
  return <TdxCompany symbol={symbol} />;
}

function TdxCompany({ symbol }: { symbol: string }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { flow, selectLeader, update: updateFlow } = useDecisionFlow();
  const [data, setData] = useState<TdxSecurityOverview | null>(null);
  const [dossier, setDossier] = useState<CompanyDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<"refresh" | "kline" | "research" | "committee" | null>(null);
  const requestedTab = params.get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(TABS.includes(requestedTab as Tab) ? requestedTab as Tab : "概览");
  const [period, setPeriod] = useState("1d");
  const [dividendType, setDividendType] = useState("front");
  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await api.getTdxSecurityOverview(symbol)); }
    catch { setData(null); }
    finally { setLoading(false); }
  }, [symbol]);
  useEffect(() => { void load(); api.getCompanyDossier("CN", symbol).then(setDossier).catch(() => setDossier(null)); }, [load, symbol]);
  useEffect(() => { if (params.get("flow") === "1" && data && flow.symbol !== symbol) selectLeader({ symbol, name: data.name }); }, [data, flow.symbol, params, selectLeader, symbol]);

  const refresh = async () => { setRunning("refresh"); try { await api.refreshTdxSecurity(symbol); await load(); toast.success("单股行情与财务快照已更新"); } catch (reason) { toast.error(reason instanceof Error ? reason.message : "单股更新失败"); } finally { setRunning(null); } };
  const refreshKline = async () => { setRunning("kline"); try { await api.getTdxKline({ symbol, period, count: 500, dividend_type: dividendType }); await load(); toast.success("K线缓存已更新"); } catch (reason) { toast.error(reason instanceof Error ? reason.message : "K线更新失败"); } finally { setRunning(null); } };
  const research = async () => { setRunning("research"); try { const result = await api.researchCompany("CN", symbol); setDossier(result.dossier); setTab("研究结论"); updateFlow({ research_report_id: result.report?.id, research_completed_at: new Date().toISOString() }); toast.success("深度研究底稿已生成，可继续形成买卖点"); } catch (reason) { toast.error(reason instanceof Error ? reason.message : "研究失败"); } finally { setRunning(null); } };
  const committee = async () => { setRunning("committee"); try { const result = await api.createCommittee({ market: "CN", symbol, company_name: data?.name || dossier?.name || symbol }); navigate(`/committee/${result.id}`); } catch (reason) { toast.error(reason instanceof Error ? reason.message : "启动投委会失败"); } finally { setRunning(null); } };
  const createPlan = () => navigate(`/signals?new=1&market=CN&symbol=${encodeURIComponent(symbol)}&name=${encodeURIComponent(data?.name || dossier?.name || symbol)}&flow=1`);

  if (loading) return <WorkspacePage><CompanyBackLink /><LoadingState label="正在读取通达信缓存…" /></WorkspacePage>;
  if (!data) return <WorkspacePage><CompanyBackLink /><EmptyState title="未找到A股证券" body="请先在数据中心更新实时行情；公司详情不会自动触发全市场财务更新。" /></WorkspacePage>;
  const quote = asDict(data.quote);
  const finance = asDict(data.fundamental);
  const detail = asDict(data.detail);
  const snapshot = asDict(detail.snapshot);
  const extended = asDict(detail.extended);
  return <WorkspacePage><CompanyBackLink /><DecisionFlow current={4} />
    <PageHeader eyebrow={`A-SHARE / ${data.code}`} title={`${data.name} · 深度研究`} description={`${flow.sector_name ? `上游行业：${flow.sector_name} · ` : ""}${data.source} · 数据时间（北京时间）${new Date(data.as_of).toLocaleString("zh-CN", { hour12: false })}`} actions={<><button onClick={() => void refresh()} disabled={Boolean(running)} className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium"><RefreshCw className={cn("h-4 w-4", running === "refresh" && "animate-spin")} />刷新单股</button><button onClick={() => void research()} disabled={Boolean(running)} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">{running === "research" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FilePlus2 className="h-4 w-4" />}生成深度研究</button><button onClick={() => void committee()} disabled={Boolean(running)} className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm font-medium">{running === "committee" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Scale className="h-4 w-4" />}投委会复核</button></>} />
    {data.cache?.stale ? <div className="flex items-center gap-2 rounded-xl border border-warning/30 bg-warning/5 p-3 text-sm text-warning"><AlertTriangle className="h-4 w-4" />行情缓存已超过15分钟</div> : null}
    <div className="overflow-x-auto"><div className="inline-flex min-w-max rounded-lg border bg-card p-1">{TABS.map((item) => <button key={item} onClick={() => setTab(item)} className={cn("rounded-md px-4 py-2 text-sm", tab === item ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>{item}</button>)}</div></div>
    {tab === "概览" ? <OverviewTab quote={quote} finance={finance} snapshot={snapshot} /> : null}
    {tab === "行情K线" ? <KlineTab data={data} symbol={symbol} period={period} setPeriod={setPeriod} dividendType={dividendType} setDividendType={setDividendType} refresh={() => void refreshKline()} loading={running === "kline"} /> : null}
    {tab === "财务估值" ? <FinanceTab finance={finance} professional={data.professional_finance_available} /> : null}
    {tab === "题材板块" ? <SectorsTab sectors={data.sectors} /> : null}
    {tab === "分红股本" ? <DividendTab detail={detail} /> : null}
    {tab === "资金结构" ? <MicroTab snapshot={snapshot} extended={extended} detail={detail} /> : null}
    {tab === "研究结论" ? <ResearchTab dossier={dossier} running={running === "research"} reportId={flow.research_report_id} onResearch={() => void research()} onCreatePlan={createPlan} /> : null}
  </WorkspacePage>;
}

function OverviewTab({ quote, finance, snapshot }: { quote: Dict; finance: Dict; snapshot: Dict }) {
  const change = Number(quote.change_pct);
  return <><section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6"><MetricCard label="现价（元）" value={formatNumber(quote.price)} tone={change > 0 ? "positive" : change < 0 ? "negative" : undefined} /><MetricCard label="涨跌幅" value={pct(change)} tone={change > 0 ? "positive" : change < 0 ? "negative" : undefined} /><MetricCard label="开 / 高 / 低" value={`${formatNumber(snapshot.Open)} / ${formatNumber(snapshot.Max)} / ${formatNumber(snapshot.Min)}`} /><MetricCard label="成交量（手）" value={formatNumber(quote.volume_lots, 0)} /><MetricCard label="成交额（万元）" value={formatNumber(snapshot.Amount)} /><MetricCard label="换手率" value={pct(finance.turnover_rate)} /></section>
     <section className="grid gap-5 xl:grid-cols-[1.2fr_.8fr]"><article className="rounded-xl border bg-card p-5 shadow-sm"><h2 className="font-semibold">核心财务与估值</h2><div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[["总市值", `${formatNumber(finance.market_cap_100m)}亿元`], ["营收", `${formatNumber(finance.revenue_10k)}万元`], ["净利润", `${formatNumber(finance.net_profit_10k)}万元`], ["EPS", formatNumber(finance.eps)], ["PE(TTM)", formatNumber(finance.pe_ttm)], ["PB", formatNumber(finance.pb_mrq)], ["股息率", pct(finance.dividend_yield)], ["报告日期", formatDate(finance.report_date)]].map(([label, value]) => <div key={label}><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 font-mono font-medium">{value}</div></div>)}</div></article><article className="rounded-xl border bg-card p-5 shadow-sm"><h2 className="font-semibold">盘口快照</h2>{snapshot.ItemNum ? <div className="mt-4 grid grid-cols-2 gap-3 text-sm">{[["内盘", snapshot.Inside], ["外盘", snapshot.Outside], ["均价", snapshot.AvgPrice], ["现量", snapshot.NowVol], ["买一", asRows(snapshot.BuyPrice)[0] ?? snapshot.BuyPrice], ["卖一", asRows(snapshot.SellPrice)[0] ?? snapshot.SellPrice]].map(([label, value]) => <div key={String(label)} className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{String(label)}</div><div className="mt-1 font-mono">{Array.isArray(value) ? String(value[0] ?? "—") : String(value ?? "—")}</div></div>)}</div> : <p className="mt-4 text-sm text-muted-foreground">暂无盘口数据</p>}</article></section></>;
}

function KlineTab({ data, symbol, period, setPeriod, dividendType, setDividendType, refresh, loading }: { data: TdxSecurityOverview; symbol: string; period: string; setPeriod: (value: string) => void; dividendType: string; setDividendType: (value: string) => void; refresh: () => void; loading: boolean }) {
  const bars = useMemo(() => klineBars(data, symbol, period, dividendType), [data, dividendType, period, symbol]);
  return <section className="rounded-xl border bg-card p-5 shadow-sm"><div className="flex flex-wrap items-center gap-2 border-b pb-4"><span className="text-xs text-muted-foreground">周期</span>{[["1d", "日线"], ["1w", "周线"], ["1m", "月线"], ["5m", "5分钟"], ["15m", "15分钟"], ["30m", "30分钟"], ["60m", "60分钟"]].map(([key, label]) => <button key={key} onClick={() => setPeriod(key)} className={cn("rounded-full px-3 py-1 text-xs", period === key ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>{label}</button>)}<span className="ml-3 text-xs text-muted-foreground">复权</span>{[["front", "前复权"], ["back", "后复权"], ["none", "不复权"]].map(([key, label]) => <button key={key} onClick={() => setDividendType(key)} className={cn("rounded-full px-3 py-1 text-xs", dividendType === key ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary")}>{label}</button>)}<button onClick={refresh} disabled={loading} className="ml-auto inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />更新当前K线</button></div>{bars.length ? <div className="pt-4"><CandlestickChart data={bars} height={520} /></div> : <div className="py-20 text-center text-sm text-muted-foreground">暂无该周期K线</div>}</section>;
}

function FinanceTab({ finance, professional }: { finance: Dict; professional: boolean }) {
  const groups = [{ title: "资产负债", rows: [["总资产", finance.total_assets_10k], ["净资产", finance.net_assets_10k], ["总股本", finance.total_shares_10k], ["流通股本", finance.float_shares_10k]] }, { title: "经营结果", rows: [["营业收入", finance.revenue_10k], ["营业利润", finance.operating_profit_10k], ["净利润", finance.net_profit_10k], ["研发费用", finance.rd_expense_10k]] }, { title: "每股与估值", rows: [["EPS", finance.eps], ["每股净资产", finance.bps], ["PE动态 / TTM", `${formatNumber(finance.pe_dynamic)} / ${formatNumber(finance.pe_ttm)}`], ["PB / 股息率", `${formatNumber(finance.pb_mrq)} / ${pct(finance.dividend_yield)}`]] }];
  return <>{!professional ? <div className="flex items-center gap-2 rounded-xl border border-warning/30 bg-warning/5 p-3 text-sm text-warning"><AlertTriangle className="h-4 w-4 shrink-0" />历史财务数据不可用</div> : null}<section className="grid gap-5 lg:grid-cols-3">{groups.map((group) => <article key={group.title} className="rounded-xl border bg-card p-5 shadow-sm"><h2 className="font-semibold">{group.title}</h2><div className="mt-4 divide-y">{group.rows.map(([label, value]) => <div key={String(label)} className="flex justify-between gap-4 py-3 text-sm"><span className="text-muted-foreground">{String(label)}</span><span className="text-right font-mono">{typeof value === "number" ? `${formatNumber(value)}${String(label).includes("EPS") || String(label).includes("每股") ? "" : " 万元"}` : String(value ?? "—")}</span></div>)}</div></article>)}</section><div className="rounded-xl border bg-card p-5 text-sm"><span className="text-muted-foreground">主营业务：</span>{String(finance.main_business ?? "—")}<span className="ml-6 text-muted-foreground">最新报告日期：</span>{formatDate(finance.report_date)}<span className="ml-6 text-muted-foreground">股东人数：</span>{formatNumber(finance.shareholders, 0)}</div></>;
}

function SectorsTab({ sectors }: { sectors: Dict[] }) { return <section className="rounded-xl border bg-card p-5 shadow-sm"><h2 className="font-semibold">行业、概念、地区与风格关系</h2>{sectors.length ? <div className="mt-4 flex flex-wrap gap-2">{sectors.map((row, index) => <Link key={String(row.sector_code ?? row.code ?? index)} to={`/market/sectors/${String(row.sector_code ?? row.code ?? "")}`} className="rounded-full border bg-muted/40 px-3 py-1.5 text-sm hover:border-primary hover:text-primary">{String(row.sector_name ?? row.name ?? "板块")}</Link>)}</div> : <p className="mt-4 text-sm text-muted-foreground">尚未更新板块成分缓存。</p>}</section>; }

function DividendTab({ detail }: { detail: Dict }) { const dividends = asRows(detail.dividends); const capital = asRows(detail.capital); return <section className="grid gap-5 xl:grid-cols-2"><DataTable title="历史分红送配" rows={dividends} empty="点击“刷新单股”获取分红记录" /><DataTable title="股本变化" rows={capital} empty="点击“刷新单股”获取股本记录" /></section>; }
function MicroTab({ snapshot, extended, detail }: { snapshot: Dict; extended: Dict; detail: Dict }) { const micro = asRows(detail.microstructure); return <><div className="flex items-center gap-2 rounded-xl border border-warning/30 bg-warning/5 p-3 text-sm text-warning"><AlertTriangle className="h-4 w-4 shrink-0" />部分字段为实验数据，请谨慎参考</div><section className="grid gap-5 xl:grid-cols-2"><article className="rounded-xl border bg-card p-5 shadow-sm"><h2 className="font-semibold">已确认盘口与资金字段</h2><div className="mt-4 grid grid-cols-2 gap-3">{[["内盘（手）", snapshot.Inside], ["外盘（手）", snapshot.Outside], ["主力资金（万元）", extended.Zjl], ["总买单（手）", extended.TotalBVol], ["总卖单（手）", extended.TotalSVol], ["撤买 / 撤卖", `${extended.BCancel ?? "—"} / ${extended.SCancel ?? "—"}`]].map(([label, value]) => <div key={String(label)} className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{String(label)}</div><div className="mt-1 font-mono">{String(value ?? "—")}</div></div>)}</div></article><DataTable title="日内微观数组（实验数据）" rows={micro.slice(0, 50)} empty="暂无微观统计缓存" /></section></>; }

function ResearchTab({ dossier, running, reportId, onResearch, onCreatePlan }: { dossier: CompanyDossier | null; running: boolean; reportId?: string; onResearch: () => void; onCreatePlan: () => void }) {
  if (!dossier) return <section className="rounded-xl border border-dashed bg-card p-10 text-center"><FilePlus2 className="mx-auto h-9 w-9 text-primary" /><h2 className="mt-4 text-lg font-semibold">尚无研究底稿</h2><button onClick={onResearch} disabled={running} className="mt-5 inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50">{running ? <Loader2 className="h-4 w-4 animate-spin" /> : <FilePlus2 className="h-4 w-4" />}生成深度研究</button></section>;
  return <><section className="grid gap-5 xl:grid-cols-[1.2fr_.8fr]"><article className="rounded-xl border bg-card p-6"><SourceBadge status={dossier.source_status} asOf={dossier.data_as_of} /><p className="mt-4 leading-8">{dossier.overview}</p><div className="mt-5 grid gap-4 md:grid-cols-2"><div className="rounded-xl border border-market-up/30 bg-market-up/5 p-4"><h3 className="font-semibold text-market-up">正向证据</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{dossier.bull_thesis}</p></div><div className="rounded-xl border border-market-down/30 bg-market-down/5 p-4"><h3 className="font-semibold text-market-down">风险与反证</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{dossier.bear_thesis}</p></div></div></article><div className="space-y-5"><ListCard title="催化剂" items={dossier.catalysts} /><ListCard title="风险" items={dossier.risks} /><div className="grid grid-cols-2 gap-2"><button onClick={onResearch} className="rounded-lg border px-4 py-2 text-sm font-medium">重新生成</button>{reportId ? <Link to={`/reports/${reportId}`} className="rounded-lg border px-4 py-2 text-center text-sm font-medium text-primary">查看底稿</Link> : null}</div></div></section><section className="flex flex-col justify-between gap-4 rounded-xl border border-primary/30 bg-primary/5 p-5 sm:flex-row sm:items-center"><div className="font-semibold">形成买卖点</div><button onClick={onCreatePlan} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground">下一步<ArrowRight className="h-4 w-4" /></button></section></>;
}

function CompanyBackLink() {
  const [params] = useSearchParams();
  const fromValue = params.get("from") === "value";
  return <div className="flex flex-wrap items-center gap-2 text-sm"><Link to={fromValue ? "/value" : "/screener"} className="inline-flex items-center gap-1 text-primary hover:underline"><ArrowLeft className="h-4 w-4" />{fromValue ? "返回价值龙头" : "返回股票筛选"}</Link></div>;
}
function LegacyCompany({ market, symbol }: { market: MarketCode; symbol: string }) { const [data, setData] = useState<CompanyDossier | null>(null); const [loading, setLoading] = useState(true); useEffect(() => { api.getCompanyDossier(market, symbol).then(setData).catch(() => setData(null)).finally(() => setLoading(false)); }, [market, symbol]); if (loading) return <WorkspacePage><CompanyBackLink /><LoadingState /></WorkspacePage>; if (!data) return <WorkspacePage><CompanyBackLink /><EmptyState title="未找到公司研究档案" body="港股、美股继续使用现有研究数据；当前缺少真实数据时不会显示示例行情。" /></WorkspacePage>; return <WorkspacePage><CompanyBackLink /><PageHeader eyebrow={`${market} / ${symbol}`} title={data.name} description={`${data.exchange} · ${data.sector_name}`} actions={<SourceBadge status={data.source_status} asOf={data.data_as_of} />} /><ResearchTab dossier={data} running={false} onResearch={() => undefined} onCreatePlan={() => undefined} /></WorkspacePage>; }

function DataTable({ title, rows, empty }: { title: string; rows: Dict[]; empty: string }) { const keys = rows.length ? Object.keys(rows[0]).slice(0, 8) : []; return <article className="overflow-hidden rounded-xl border bg-card shadow-sm"><div className="border-b p-5"><h2 className="font-semibold">{title}</h2></div>{rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[620px] text-left text-xs"><thead className="bg-muted/50 text-muted-foreground"><tr>{keys.map((key) => <th key={key} className="p-3">{key}</th>)}</tr></thead><tbody className="divide-y">{rows.map((row, index) => <tr key={index}>{keys.map((key) => <td key={key} className="max-w-40 truncate p-3 font-mono" title={String(row[key] ?? "")}>{String(row[key] ?? "—")}</td>)}</tr>)}</tbody></table></div> : <p className="p-8 text-center text-sm text-muted-foreground">{empty}</p>}</article>; }
function ListCard({ title, items }: { title: string; items: string[] }) { return <article className="rounded-xl border bg-card p-5"><h3 className="font-semibold">{title}</h3><ul className="mt-3 space-y-2 text-sm">{items.map((item) => <li key={item} className="rounded-lg bg-muted/50 p-3">{item}</li>)}</ul></article>; }
function formatDate(value: unknown) { const text = String(value ?? ""); return /^\d{8}$/.test(text) ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6)}` : text || "—"; }
function klineBars(data: TdxSecurityOverview, symbol: string, period: string, dividendType: string): PriceBar[] { const item = data.klines.find((row) => row.period === period && row.dividend_type === dividendType); if (!item) return []; const source = asDict(item.data); const rows = (name: string) => asRows(source[name]); const map = (name: string) => new Map(rows(name).map((row) => [String(row.index), Number(row[symbol])])); const open = map("Open"), high = map("High"), low = map("Low"), close = map("Close"), volume = map("Volume"); return [...close.keys()].sort().map((time) => ({ time: time.slice(0, 10), open: open.get(time) ?? 0, high: high.get(time) ?? 0, low: low.get(time) ?? 0, close: close.get(time) ?? 0, volume: volume.get(time) ?? 0 })).filter((bar) => bar.close > 0); }
