import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, type CompanyResearchConclusion, type CompanyResearchOverview, type EntryResearch, type ExitResearch, type ValuePriceZones } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SourceReferenceCard, entryStatusLabel, exitStatusLabel, thesisStatusLabel, confidenceLabel } from "@/components/value/SourceReferenceCard";

type DetailTab = "研究结论" | "公司全貌" | "估值与时机" | "核心逻辑";

function rangeText(value: { low: number | null; high: number | null } | null | undefined) {
  if (!value) return "资料不足";
  if (value.low == null && value.high == null) return "资料不足";
  if (value.low == null) return `低于 ${value.high}`;
  if (value.high == null) return `高于 ${value.low}`;
  return `${value.low} – ${value.high}`;
}

function unavailable(label: string) {
  return <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">{label}暂未建立或资料不足。</div>;
}

export function CompanyResearchModalDetails({ stockCode }: { stockCode: string }) {
  const [tab, setTab] = useState<DetailTab>("研究结论");
  const [conclusion, setConclusion] = useState<CompanyResearchConclusion | null>(null);
  const [overview, setOverview] = useState<CompanyResearchOverview | null>(null);
  const [zones, setZones] = useState<ValuePriceZones | null>(null);
  const [entry, setEntry] = useState<EntryResearch | null>(null);
  const [exit, setExit] = useState<ExitResearch | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([
      api.getCompanyResearchConclusion(stockCode), api.getCompanyResearchOverview(stockCode), api.getCompanyPriceZones(stockCode),
      api.getCompanyEntryResearch(stockCode), api.getCompanyExitResearch(stockCode),
    ]).then(([conclusionResult, overviewResult, zonesResult, entryResult, exitResult]) => {
      if (cancelled) return;
      setConclusion(conclusionResult.status === "fulfilled" ? conclusionResult.value : null);
      setOverview(overviewResult.status === "fulfilled" ? overviewResult.value : null);
      setZones(zonesResult.status === "fulfilled" ? zonesResult.value : null);
      setEntry(entryResult.status === "fulfilled" ? entryResult.value : null);
      setExit(exitResult.status === "fulfilled" ? exitResult.value : null);
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [stockCode]);
  const tabs: DetailTab[] = ["研究结论", "公司全貌", "估值与时机", "核心逻辑"];
  return <section className="mt-5 rounded-xl border border-primary/25 bg-card p-4"><div className="flex flex-wrap items-end justify-between gap-3"><div><div className="text-xs font-medium text-primary">公司详情</div><h3 className="mt-1 font-semibold">公司详情</h3><p className="mt-1 text-xs text-muted-foreground">只读取已有公司研究档案；数据不足时明确显示，不自动补造。</p></div>{loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}</div><div role="tablist" aria-label="公司详情内容" className="mt-4 flex max-w-full overflow-x-auto rounded-lg border border-border bg-background p-1">{tabs.map((item) => <button key={item} type="button" role="tab" aria-selected={tab === item} onClick={() => setTab(item)} className={cn("shrink-0 rounded-md px-3 py-2 text-sm", tab === item ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>{item}</button>)}</div><div className="mt-4">{loading ? <div className="flex h-32 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取公司详情资料…</div> : tab === "研究结论" ? <Conclusion data={conclusion} /> : tab === "公司全貌" ? <CompanyOverview data={overview} /> : tab === "估值与时机" ? <ValuationAndTiming zones={zones} entry={entry} exit={exit} /> : <ThesisAndEvidence overview={overview} />}</div></section>;
}

function Conclusion({ data }: { data: CompanyResearchConclusion | null }) {
  if (!data) return unavailable("公司研究结论");
  return <div className="space-y-3"><div className="rounded-lg bg-muted/40 p-3 text-sm leading-6">{data.research_conclusion || "当前尚无可展示的一句话研究结论。"}</div><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4"><div className="rounded-lg border p-3"><span className="text-xs text-muted-foreground">当前逻辑</span><strong className="mt-1 block text-sm">{data.thesis ? thesisStatusLabel(data.thesis.status) : "尚未建立"}</strong></div><div className="rounded-lg border p-3"><span className="text-xs text-muted-foreground">入场研究</span><strong className="mt-1 block text-sm">{entryStatusLabel(data.entry.level)}</strong></div><div className="rounded-lg border p-3"><span className="text-xs text-muted-foreground">退出复核</span><strong className="mt-1 block text-sm">{exitStatusLabel(data.exit.level)}</strong></div><div className="rounded-lg border p-3"><span className="text-xs text-muted-foreground">研究证据</span><strong className="mt-1 block text-sm">支持 {data.evidence_counts.support} · 挑战 {data.evidence_counts.challenge}</strong></div></div><div className="grid gap-2 sm:grid-cols-2"><div className="rounded-lg border p-3"><span className="text-xs text-muted-foreground">合理价值区间</span><strong className="mt-1 block tabular-nums">{rangeText(data.fair_value_range)}</strong></div><div className="rounded-lg border p-3"><span className="text-xs text-muted-foreground">重点观察区</span><strong className="mt-1 block tabular-nums">{rangeText(data.focus_zone)}</strong></div></div></div>;
}

function CompanyOverview({ data }: { data: CompanyResearchOverview | null }) {
  if (!data) return unavailable("公司经营与财务总览");
  const business = data.business_summary;
  const financial = data.financial_summary;
  return <div className="space-y-3"><div className="grid gap-3 lg:grid-cols-2"><article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">这家公司是做什么的</h4><p className="mt-2 text-sm leading-6">{business.description || "尚未生成公司经营研究。"}</p><p className="mt-2 text-xs text-muted-foreground">主要产品：{business.products?.length ? business.products.join("、") : "资料不足"}</p><p className="mt-1 text-xs text-muted-foreground">怎么赚钱：{business.business_model || "资料不足"}</p></article><article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">最近经营变化</h4>{business.changes.length ? <ul className="mt-2 space-y-2 text-sm leading-6">{business.changes.slice(0, 5).map((item, index) => <li key={index}>• {item}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">尚未生成公司经营研究。</p>}</article></div><article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">财务表现</h4>{financial.items.length ? <div className="mt-2 grid gap-2 lg:grid-cols-2">{financial.items.slice(0, 6).map((item, index) => <div key={`${item.category}-${index}`} className="rounded bg-muted/30 p-2"><p className="text-sm leading-6">{item.text}</p><SourceReferenceCard citations={item.citations} researchContent={item.text} /></div>)}</div> : <p className="mt-2 text-sm text-muted-foreground">{financial.message || "尚未生成财务研究。"}</p>}</article></div>;
}

function ValuationAndTiming({ zones, entry, exit }: { zones: ValuePriceZones | null; entry: EntryResearch | null; exit: ExitResearch | null }) {
  if (!zones && !entry && !exit) return unavailable("估值与时机研究");
  return <div className="space-y-3">{zones ? <article className="rounded-lg border p-3"><div className="flex flex-wrap justify-between gap-2"><h4 className="text-sm font-semibold">估值与价格位置</h4><span className="text-xs text-muted-foreground">数据截至 {zones.as_of || "—"}</span></div><div className="mt-3 grid gap-2 sm:grid-cols-3"><div><span className="text-xs text-muted-foreground">当前价格</span><strong className="mt-1 block tabular-nums">{zones.current_price ?? "—"}</strong></div><div><span className="text-xs text-muted-foreground">合理价值</span><strong className="mt-1 block tabular-nums">{rangeText({ low: zones.valuation.fair_value_low, high: zones.valuation.fair_value_high })}</strong></div><div><span className="text-xs text-muted-foreground">当前估值判断</span><strong className="mt-1 block">{zones.valuation.status || "资料不足"}</strong></div></div><p className="mt-3 text-sm leading-6 text-muted-foreground">{zones.plain_summary}</p></article> : null}<div className="grid gap-3 lg:grid-cols-2"><article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">入场研究</h4>{entry ? <><p className="mt-2 font-medium">{entryStatusLabel(entry.entry_level)}</p><p className="mt-2 text-sm leading-6 text-muted-foreground">{entry.plain_explanation}</p>{entry.data_gaps.length ? <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">资料缺口：{entry.data_gaps.join("、")}</p> : null}</> : <p className="mt-2 text-sm text-muted-foreground">当前资料不足。</p>}</article><article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">退出复核</h4>{exit ? <><p className="mt-2 font-medium">{exitStatusLabel(exit.exit_level)}</p><p className="mt-2 text-sm leading-6 text-muted-foreground">{exit.plain_explanation}</p>{exit.data_gaps.length ? <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">资料缺口：{exit.data_gaps.join("、")}</p> : null}</> : <p className="mt-2 text-sm text-muted-foreground">当前资料不足。</p>}</article></div></div>;
}

function ThesisAndEvidence({ overview }: { overview: CompanyResearchOverview | null }) {
  if (!overview) return unavailable("核心逻辑与研究证据");
  return <div className="space-y-3"><article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">当前核心逻辑</h4>{overview.thesis ? <><p className="mt-2 text-sm leading-6">{overview.thesis.core_thesis}</p><p className="mt-2 text-xs text-muted-foreground">逻辑状态：{thesisStatusLabel(overview.thesis.status)} · 置信度：{confidenceLabel(overview.thesis.confidence)}</p>{overview.review ? <p className="mt-2 rounded bg-muted/40 p-2 text-xs text-muted-foreground">{overview.review.is_stale ? "有新研究证据，当前研究复核已过期。" : overview.review.review_reason}</p> : null}</> : <p className="mt-2 text-sm text-muted-foreground">当前尚未建立公司核心逻辑。</p>}</article><div className="grid gap-3 lg:grid-cols-2"><EvidenceList title="支持当前逻辑" items={overview.supporting_evidence} empty="没有可展示的支持当前逻辑的研究证据。" /><EvidenceList title="挑战当前逻辑" items={overview.challenging_evidence} empty="没有可展示的挑战当前逻辑的研究证据。" /></div><article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">接下来重点观察</h4>{overview.watch_items.length ? <ul className="mt-2 space-y-1 text-sm leading-6">{overview.watch_items.slice(0, 6).map((item, index) => <li key={`${item.source}-${index}`}>• {item.text}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">当前没有已记录的重点观察项。</p>}</article></div>;
}

function EvidenceList({ title, items, empty }: { title: string; items: CompanyResearchOverview["supporting_evidence"]; empty: string }) {
  return <article className="rounded-lg border p-3"><h4 className="text-sm font-semibold">{title}</h4>{items.length ? <div className="mt-2 space-y-2">{items.slice(0, 4).map((item) => <div key={item.evidence_id} className="rounded bg-muted/30 p-2"><p className="text-sm leading-6">{item.claim}</p><p className="mt-1 text-xs text-muted-foreground">{item.summary}</p><SourceReferenceCard citations={item.citations} researchContent={item.claim} /></div>)}</div> : <p className="mt-2 text-sm text-muted-foreground">{empty}</p>}</article>;
}
