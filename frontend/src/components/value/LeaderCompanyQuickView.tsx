import { useEffect, useState, type ReactNode } from "react";
import { ArrowRight, Loader2, MessageCircle, X } from "lucide-react";
import { Link } from "react-router";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { LazyDetails } from "@/components/value/LazyDetails";
import {
  api,
  type CompanyResearchConclusion,
  type CompactDailyBars,
  type EntryResearch,
  type Level3Leader,
  type PriceBar,
  type ValuePriceZones,
} from "@/lib/api";

type DetailState = {
  conclusion: CompanyResearchConclusion | null;
  zones: ValuePriceZones | null;
  dailyBars: CompactDailyBars | null;
};

const emptyDetails: DetailState = { conclusion: null, zones: null, dailyBars: null };

function frontDailyBars(snapshot: CompactDailyBars | null): PriceBar[] {
  return (snapshot?.bars || []).map((bar) => ({
    time: bar.date,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume || 0,
  }));
}

function number(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function money(value: number | null | undefined) {
  const formatted = number(value);
  return formatted === "—" ? "资料不足" : `${formatted} 元`;
}

function moneyRange(low: number | null | undefined, high: number | null | undefined) {
  if (low == null && high == null) return "资料不足";
  if (low == null) return `低于 ${money(high)}`;
  if (high == null) return `高于 ${money(low)}`;
  return `${number(low)} – ${number(high)} 元`;
}

function leaderReason(leader: Level3Leader, index: number) {
  const item = leader.explanation?.strongest?.[index];
  if (!item) return null;
  const labels: Record<string, string> = {
    industry_position: "行业内规模与经营地位靠前",
    profitability: "盈利能力领先同行",
    growth_stability: "成长稳定性优于同行",
    cash_flow: "现金流质量相对较好",
    valuation: "同行相对估值更有吸引力",
    governance_risk: "财务稳健与波动表现相对较好",
  };
  return labels[item.key] || `${item.label}在同行中表现靠前`;
}

function percentage(value: number) {
  return `${Math.abs(value * 100).toFixed(1)}%`;
}

function distanceToZone(price: number, low: number | null, high: number | null) {
  if (low != null && price < low) return (low - price) / low;
  if (high != null && price > high) return (price - high) / high;
  return 0;
}

function nearestZone(price: number | null, zones: ValuePriceZones["support_zones"] | ValuePriceZones["confluence_zones"]) {
  if (price == null || !zones.length) return null;
  return [...zones].sort((left, right) => distanceToZone(price, left.low, left.high) - distanceToZone(price, right.low, right.high))[0] || null;
}

function fairValueMidpoint(low: number | null | undefined, high: number | null | undefined) {
  return low != null && high != null ? (low + high) / 2 : null;
}

function midpointDistance(price: number | null, midpoint: number | null) {
  if (price == null || midpoint == null || midpoint === 0) return "资料不足";
  const distance = (midpoint - price) / midpoint;
  if (distance > 0) return `低于价值中枢 ${percentage(distance)}`;
  if (distance < 0) return `高于价值中枢 ${percentage(distance)}`;
  return "与价值中枢基本一致";
}

function historicalValuationLabel(status: string | null | undefined) {
  const normalized = String(status || "").toUpperCase();
  if (["VERY_CHEAP", "DEEPLY_UNDERVALUED", "DEEP_CHEAP", "CHEAP", "UNDERVALUED"].includes(normalized)) return "偏低";
  if (["VERY_EXPENSIVE", "DEEPLY_OVERVALUED", "DEEP_EXPENSIVE", "EXPENSIVE", "OVERVALUED"].includes(normalized)) return "偏高";
  if (["FAIR", "NORMAL"].includes(normalized)) return "正常";
  return "资料不足";
}

function supportRelation(price: number | null, support: { low: number | null; high: number | null } | null) {
  if (price == null || !support) return { distance: "资料不足", judgment: "尚未形成可用历史支撑参考。" };
  const above = support.high != null && price > support.high;
  const below = support.low != null && price < support.low;
  if (!above && !below) return { distance: "0.0%", judgment: "位于支撑区域内" };
  const magnitude = distanceToZone(price, support.low, support.high);
  const signed = `${above ? "+" : "-"}${percentage(magnitude)}`;
  return {
    distance: signed,
    judgment: above ? `高于支撑区域 ${percentage(magnitude)}` : `低于历史支撑区域 ${percentage(magnitude)}`,
  };
}

function DetailField({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-border bg-background px-3 py-2"><div className="text-[11px] text-muted-foreground">{label}</div><strong className="mt-1 block text-sm tabular-nums">{value}</strong></div>;
}

function ExplanationSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="border-b border-border pb-4 last:border-b-0 last:pb-0"><h3 className="text-sm font-semibold">{title}</h3><div className="mt-1.5 text-sm leading-6 text-muted-foreground">{children}</div></section>;
}

function ExplanationDetail({ title, children }: { title: string; children: ReactNode }) {
  return <details className="rounded-lg border border-border bg-card"><summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold">{title}</summary><div className="space-y-3 border-t border-border px-4 py-3 text-sm leading-6 text-muted-foreground">{children}</div></details>;
}

function methodRange(methods: ValuePriceZones["valuation"]["methods"]) {
  const values = methods.flatMap((method) => method.fair_values || []).filter((value) => Number.isFinite(value));
  return values.length ? moneyRange(Math.min(...values), Math.max(...values)) : null;
}

function valuationMethodRows(zones: ValuePriceZones | null) {
  const methods = zones?.valuation.methods || [];
  const definitions = [
    { label: "PE估值", match: (name: string) => /PE/i.test(name), note: "本次与预测利润、同行 PE 水平共同计算。" },
    { label: "PB估值", match: (name: string) => /PB/i.test(name), note: "本次结合同行 PB 水平计算。" },
    { label: "预测利润估值", match: (name: string) => name.includes("预测"), note: "预测利润是 PE 可比估值的共同输入，不单独产生另一套价格。" },
    { label: "行业比较估值", match: (name: string) => name.includes("同行") || name.includes("行业"), note: "同行 PE、PB 方法共同提供行业比较参考。" },
  ];
  return definitions.map((definition) => {
    const matched = methods.filter((method) => method.status === "READY" && definition.match(method.name));
    return { ...definition, range: methodRange(matched), sourceNames: matched.map((method) => method.name) };
  });
}

function valuationStatusLabel(status: string | null | undefined) {
  const labels: Record<string, string> = {
    DEEPLY_UNDERVALUED: "深度低估",
    UNDERVALUED: "低估关注",
    FAIR: "合理观察",
    OVERVALUED: "估值偏高",
    DEEPLY_OVERVALUED: "明显偏高",
    INSUFFICIENT_DATA: "数据不足",
  };
  return labels[String(status || "INSUFFICIENT_DATA").toUpperCase()] || "数据不足";
}

function valuationStatusReason(zones: ValuePriceZones | null, currentPrice: number | null) {
  const valuation = zones?.valuation;
  const low = valuation?.fair_value_low;
  const high = valuation?.fair_value_high;
  if (currentPrice == null || low == null || high == null) return "当前价格或合理价值边界不足，无法展开状态原因。";
  const status = String(valuation?.status || "").toUpperCase();
  if (status === "DEEPLY_UNDERVALUED" || status === "UNDERVALUED") {
    return `当前价格 ${money(currentPrice)}，合理价值下限 ${money(low)}，低于下限 ${percentage((low - currentPrice) / low)}；系统现有规则将其归为“${valuationStatusLabel(status)}”。`;
  }
  if (status === "FAIR") return `当前价格 ${money(currentPrice)}，位于系统已有合理价值范围 ${moneyRange(low, high)} 内，因此当前状态为“合理观察”。`;
  if (status === "OVERVALUED" || status === "DEEPLY_OVERVALUED") {
    return `当前价格 ${money(currentPrice)}，合理价值上限 ${money(high)}，高于上限 ${percentage((currentPrice - high) / high)}；系统现有规则将其归为“${valuationStatusLabel(status)}”。`;
  }
  return "系统当前未形成可用的价值状态。";
}

function metricPercentile(metric: { status?: string; percentile?: number | null } | undefined) {
  if (!metric || metric.status !== "READY" || metric.percentile == null) return "数据不足";
  return `${number(metric.percentile, 1)}%`;
}

function overlap(left: { low: number | null; high: number | null }, right: { low: number | null; high: number | null }) {
  const leftLow = left.low ?? Number.NEGATIVE_INFINITY;
  const leftHigh = left.high ?? Number.POSITIVE_INFINITY;
  const rightLow = right.low ?? Number.NEGATIVE_INFINITY;
  const rightHigh = right.high ?? Number.POSITIVE_INFINITY;
  return Math.max(leftLow, rightLow) <= Math.min(leftHigh, rightHigh);
}

const entryReasonLabels: Record<string, string> = {
  VALUATION_DEEPLY_UNDERVALUED: "当前价格明显低于系统合理价值范围",
  VALUATION_UNDERVALUED: "当前价格低于系统合理价值范围",
  VALUATION_FAIR: "当前价格处于系统合理价值范围",
  VALUATION_OVERVALUED: "当前价格高于系统合理价值范围",
  VALUATION_DEEPLY_OVERVALUED: "当前价格明显高于系统合理价值范围",
  HISTORICAL_VALUATION_VERY_CHEAP: "历史估值处于很低位置",
  HISTORICAL_VALUATION_CHEAP: "历史估值处于较低位置",
  HISTORICAL_VALUATION_NORMAL: "历史估值处于中间位置",
  HISTORICAL_VALUATION_EXPENSIVE: "历史估值偏高",
  HISTORICAL_VALUATION_VERY_EXPENSIVE: "历史估值明显偏高",
  HISTORICAL_PE_LOW: "PE 处于自身历史较低位置",
  HISTORICAL_PE_HIGH: "PE 处于自身历史较高位置",
  HISTORICAL_PB_LOW: "PB 处于自身历史较低位置",
  HISTORICAL_PB_HIGH: "PB 处于自身历史较高位置",
  HISTORICAL_DIVIDEND_YIELD_LOW: "股息率对应的历史估值位置偏低",
  HISTORICAL_DIVIDEND_YIELD_HIGH: "股息率对应的历史估值位置偏高",
  VALUATION_SUPPORT_CONFLUENCE: "价值区域与历史支撑区域出现重合",
  HISTORICAL_SUPPORT: "当前价格位于历史支撑区域",
  NO_NEAR_SUPPORT: "当前价格尚未靠近明确的历史支撑区域",
  NEAR_RESISTANCE: "当前价格靠近历史压力区域",
  THESIS_FORMING: "公司核心逻辑正在形成",
  THESIS_STRENGTHENING: "公司核心逻辑正在增强",
  THESIS_UNCHANGED: "公司核心逻辑基本稳定",
  THESIS_WEAKENING: "公司核心逻辑正在减弱",
  THESIS_FALSIFIED: "公司核心逻辑已失效",
  THESIS_MISSING: "公司核心逻辑尚未建立",
  DATA_PARTIAL: "部分关键研究资料不足",
};

function entryExplanationLabel(entry: EntryResearch | null) {
  if (!entry) return "资料不足";
  const labels: Record<EntryResearch["entry_level"], string> = {
    HIGH_ATTENTION: "重点关注",
    ATTENTION: "值得关注",
    WATCH: "继续观察",
    WAIT: "暂不优先研究",
    BLOCKED: "暂停研究",
  };
  return labels[entry.entry_level];
}

export function DataExplanationModal({ zones, currentPrice, entry, onClose }: { zones: ValuePriceZones | null; currentPrice: number | null; entry: EntryResearch | null; onClose: () => void }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  const valuation = zones?.valuation;
  const fairMid = valuation?.fair_value_mid;
  const methods = valuationMethodRows(zones);
  const history = zones?.historical_valuation;
  const metrics = history?.historical_percentiles;
  const support = nearestZone(currentPrice, zones?.support_zones || []);
  const supportStatus = supportRelation(currentPrice, support);
  const confluence = nearestZone(currentPrice, zones?.confluence_zones || []);
  const valueSource = confluence ? (zones?.valuation_zones || []).find((zone) => zone.kind === "UNDERVALUED" && overlap(zone, confluence)) : null;
  const supportSource = confluence ? (zones?.support_zones || []).find((zone) => overlap(zone, confluence)) : null;
  const entryReasons = (entry?.reason_codes || []).map((code) => entryReasonLabels[code]).filter((item): item is string => Boolean(item));
  return <>
    <button type="button" aria-label="关闭价格分析数据说明" className="fixed inset-0 z-[60] cursor-default bg-black/40" onClick={onClose} />
    <section role="dialog" aria-modal="true" aria-label="价格分析数据说明" className="fixed inset-4 z-[70] flex max-h-[calc(100vh-2rem)] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl md:inset-y-10 md:left-1/2 md:right-auto md:w-[min(760px,calc(100vw-3rem))] md:-translate-x-1/2">
      <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4"><div><p className="text-xs font-medium text-primary">Quick View 数据说明</p><h2 className="mt-1 text-xl font-semibold">价格分析数据说明</h2><p className="mt-1 text-xs text-muted-foreground">用于理解页面数据，不构成买卖建议。</p></div><button type="button" aria-label="关闭价格分析数据说明" onClick={onClose} className="rounded-md border border-border p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-4 w-4" /></button></header>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <section><h3 className="text-sm font-semibold">当前公司结果解释</h3><p className="mt-1 text-xs text-muted-foreground">以下内容直接使用当前 Quick View 已加载的研究结果，不重新计算或刷新数据。</p></section>
        <div className="space-y-2">
          <ExplanationDetail title="合理价值计算详情">
            <div className="grid gap-2 sm:grid-cols-2">
              {methods.map((method) => <div key={method.label} className="rounded-md bg-muted/40 px-3 py-2"><div className="flex items-center justify-between gap-3"><strong className="text-foreground">{method.label}</strong><span className="tabular-nums text-foreground">{method.range || "本次未使用（数据不足）"}</span></div>{method.range ? <p className="mt-1 text-xs">{method.note}</p> : null}{method.sourceNames.length ? <p className="mt-1 text-[11px]">已有来源：{method.sourceNames.join("；")}</p> : null}</div>)}
            </div>
            <p>综合有效方法形成合理价值区间：<strong className="text-foreground">{moneyRange(valuation?.fair_value_low, valuation?.fair_value_high)}</strong>。</p>
            <p>合理价值中枢：<strong className="text-foreground">{money(fairMid)}</strong>；当前价格相对中枢：<strong className="text-foreground">{midpointDistance(currentPrice, fairMid ?? null)}</strong>。中枢只是中心参考值，不是目标价，也不保证未来达到。</p>
            <div className="rounded-md border border-border px-3 py-2"><strong className="text-foreground">当前价值状态：{valuationStatusLabel(valuation?.status)}</strong><p className="mt-1">{valuationStatusReason(zones, currentPrice)}</p></div>
          </ExplanationDetail>

          <ExplanationDetail title="历史估值详情">
            <div className="grid gap-2 sm:grid-cols-3"><DetailField label="PE 历史位置" value={metricPercentile(metrics?.pe_ttm)} /><DetailField label="PB 历史位置" value={metricPercentile(metrics?.pb_mrq)} /><DetailField label="股息率历史位置" value={metricPercentile(metrics?.dividend_yield)} /></div>
            <p>系统综合当前可用指标后，现有历史估值结果为：<strong className="text-foreground">{historicalValuationLabel(history?.historical_valuation_status)}</strong>。缺少的指标会明确显示“数据不足”，不会用猜测值补齐。</p>
          </ExplanationDetail>

          <ExplanationDetail title="支撑详情">
            <div className="grid gap-2 sm:grid-cols-3"><DetailField label="最近历史支撑" value={support ? moneyRange(support.low, support.high) : "资料不足"} /><DetailField label="当前价格" value={money(currentPrice)} /><DetailField label="当前关系" value={supportStatus.judgment} /></div>
            {support?.reasons?.length ? <p>形成依据：{support.reasons.join("；")}。</p> : <p>当前没有可展示的支撑形成依据。</p>}
            {confluence ? <div className="rounded-md bg-muted/40 px-3 py-2"><p>价值区域：<strong className="text-foreground">{valueSource ? moneyRange(valueSource.low, valueSource.high) : "已有价值区域"}</strong></p><p>支撑区域：<strong className="text-foreground">{supportSource ? moneyRange(supportSource.low, supportSource.high) : "已有支撑区域"}</strong></p><p>重合区域：<strong className="text-foreground">{moneyRange(confluence.low, confluence.high)}</strong></p><p className="mt-1">估值区域和历史价格区域出现重合，但这不是买入信号。</p></div> : <p>当前没有形成价值和支撑重合区域。</p>}
          </ExplanationDetail>

          <ExplanationDetail title="当前研究判断详情">
            <p>当前研究等级：<strong className="text-foreground">{entryExplanationLabel(entry)}</strong></p>
            <p>{entry?.plain_explanation || "当前缺少可用的入场研究解释。"}</p>
            {entryReasons.length ? <ul className="list-disc space-y-1 pl-5">{entryReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>当前没有可展示的分项原因。</p>}
            {entry?.data_gaps?.length ? <p>资料缺口：{entry.data_gaps.join("、")}。</p> : null}
          </ExplanationDetail>
        </div>

        <section className="border-t border-border pt-4"><h3 className="text-sm font-semibold">指标含义</h3><p className="mt-1 text-xs text-muted-foreground">下面保留 V1 的通用解释，便于理解各项数据代表什么。</p></section>
        <ExplanationSection title="1. 当前价格">股票当前的市场交易价格，用来比较合理价值和历史价格位置。价格日期可能与财务报告日期不同。</ExplanationSection>
        <ExplanationSection title="2. 合理价值区间">系统参考公司的盈利能力、资产价值和同行业估值水平，估算一个可供研究的价格范围。可用参考包括预测利润估值、PE 估值、PB 估值和行业比较；资料不足的方法不会被强行补齐。</ExplanationSection>
        <ExplanationSection title="3. 合理价值中枢">合理价值范围中的中心参考位置。例如范围为 80–120 元时，中枢可接近 100 元。它用于理解当前价格相对价值中心的位置，不是目标价，也不代表未来一定达到的价格。</ExplanationSection>
        <ExplanationSection title="4. 历史估值位置">把当前 PE、PB、股息率与公司自身历史水平比较。偏低表示当前估值低于历史多数时期；正常表示处在历史中间范围；偏高表示高于历史多数时期。它和合理价值区间是两个不同角度，可以同时存在。</ExplanationSection>
        <ExplanationSection title="5. 历史支撑区域">过去价格运行中，市场曾出现较多交易或反弹的位置。系统参考历史重要低点、成交密集区域和长周期均线附近形成区间。它是历史价格结构参考，不代表未来一定会获得支撑。</ExplanationSection>
        <ExplanationSection title="6. 价值与支撑关系">当估值较低的区域与历史价格支撑区域出现交集时，表示价值判断和价格位置同时提供参考。它不是买入信号，仍需要结合公司的经营和风险资料核验。</ExplanationSection>
        <ExplanationSection title="7. 是否值得进一步研究">当前页面状态为“{entryExplanationLabel(entry)}”。它综合当前价值位置、历史估值位置、历史价格位置和公司核心逻辑，用来帮助安排研究优先级，不是买卖建议。</ExplanationSection>
        <ExplanationSection title="8. PE / PB / 股息率"><p>PE：市场给予公司盈利的估值倍数参考。</p><p>PB：市场给予公司净资产的估值倍数参考。</p><p>股息率：公司过去分红收益水平参考。</p></ExplanationSection>
      </div>
      <footer className="flex justify-end border-t border-border px-5 py-4"><button type="button" onClick={onClose} className="rounded-md border border-border px-3 py-2 text-sm font-medium hover:bg-muted">关闭说明</button></footer>
    </section>
  </>;
}

function PriceJudgmentCard({
  zones,
  currentPrice,
  currentPe,
  currentPb,
  currentYield,
  loading,
}: {
  zones: ValuePriceZones | null;
  currentPrice: number | null | undefined;
  currentPe: number | null | undefined;
  currentPb: number | null | undefined;
  currentYield: number | null | undefined;
  loading: boolean;
}) {
  const fair = zones?.valuation;
  const fairMidpoint = fair?.fair_value_mid ?? fairValueMidpoint(fair?.fair_value_low, fair?.fair_value_high);
  const midDistance = midpointDistance(currentPrice ?? null, fairMidpoint);
  return <section className="mt-4 rounded-xl border border-border bg-card p-3">
    <div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold">价格判断</span>{loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}</div>
    <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4"><DetailField label="当前价格" value={money(currentPrice)} /><DetailField label="合理价值中枢" value={money(fairMidpoint)} /><DetailField label="相对价值中枢" value={midDistance} /><DetailField label="合理价值区间" value={moneyRange(fair?.fair_value_low, fair?.fair_value_high)} /></div>
    <div className="mt-2 grid grid-cols-3 gap-2"><DetailField label="PE" value={currentPe == null ? "资料不足" : `${number(currentPe)} 倍`} /><DetailField label="PB" value={currentPb == null ? "资料不足" : `${number(currentPb)} 倍`} /><DetailField label="股息率" value={currentYield == null ? "资料不足" : `${number(currentYield)}%`} /></div>
    <p className="mt-3 text-xs leading-5 text-muted-foreground">{fair?.status ? `当前价值状态：${valuationStatusLabel(fair.status)}。` : "估值资料不足，暂无法形成价值状态。"}</p>
  </section>;
}

function PriceZoneList({ title, zones, emptyText }: { title: string; zones: Array<{ low: number | null; high: number | null; reasons?: string[] }>; emptyText: string }) {
  return <article className="rounded-lg border border-border bg-background p-3"><h4 className="text-sm font-semibold">{title}</h4>{zones.length ? <div className="mt-2 space-y-2">{zones.slice(0, 3).map((zone, index) => <div key={`${zone.low}-${zone.high}-${index}`} className="rounded-md bg-muted/40 px-3 py-2"><div className="font-medium tabular-nums">{moneyRange(zone.low, zone.high)}</div>{zone.reasons?.length ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{zone.reasons.join("；")}</p> : <p className="mt-1 text-xs text-muted-foreground">基于已保存的历史价格区间。</p>}</div>)}</div> : <p className="mt-2 text-sm text-muted-foreground">{emptyText}</p>}</article>;
}

function SupportPressureDetails({ zones, currentPrice, stockCode }: { zones: ValuePriceZones | null; currentPrice: number | null; stockCode: string }) {
  const support = nearestZone(currentPrice, zones?.support_zones || []);
  const relation = supportRelation(currentPrice, support);
  return <LazyDetails className="mt-4 rounded-xl border border-border bg-card" summary="查看历史支撑与压力">
    <div className="border-t border-border p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">历史支撑与压力</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">这是历史价格结构参考，不是买卖信号，也不保证未来一定有效。</p></div><Link to={`/company/CN/${encodeURIComponent(stockCode)}?tab=valuation`} className="text-sm text-primary hover:underline">到公司估值页查看完整区间</Link></div><div className="mt-3 grid gap-2 sm:grid-cols-3"><DetailField label="当前价格" value={money(currentPrice)} /><DetailField label="最近历史支撑" value={support ? moneyRange(support.low, support.high) : "资料不足"} /><DetailField label="当前位置" value={relation.judgment} /></div><div className="mt-3 grid gap-3 lg:grid-cols-2"><PriceZoneList title="历史支撑区域" zones={zones?.support_zones || []} emptyText="历史日线不足，暂无法形成可用支撑区。" /><PriceZoneList title="历史压力区域" zones={zones?.resistance_zones || []} emptyText="当前没有可展示的历史压力区。" /></div></div>
  </LazyDetails>;
}

/**
 * A deliberately small discovery surface for the leader pool.
 * It only reads persisted research and never triggers analysis or a refresh.
 */
export function LeaderCompanyQuickView({
  leader,
  onClose,
  onChat,
}: {
  leader: Level3Leader;
  onClose: () => void;
  onChat: () => void;
}) {
  const [details, setDetails] = useState<DetailState>(emptyDetails);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setDetails(emptyDetails);
    Promise.allSettled([
      api.getCompanyResearchConclusion(leader.stock_code),
      api.getCompanyPriceZones(leader.stock_code),
      api.getCompanyCompactDailyBars(leader.stock_code),
    ]).then(([conclusion, zones, dailyBars]) => {
      if (cancelled) return;
      setDetails({
        conclusion: conclusion.status === "fulfilled" ? conclusion.value : null,
        zones: zones.status === "fulfilled" ? zones.value : null,
        dailyBars: dailyBars.status === "fulfilled" ? dailyBars.value : null,
      });
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [leader.stock_code]);

  const bars = frontDailyBars(details.dailyBars);
  const reasons = [0, 1, 2].map((index) => leaderReason(leader, index)).filter((item): item is string => Boolean(item));
  const historicalMetrics = details.zones?.historical_valuation?.historical_percentiles;
  const currentPe = historicalMetrics?.pe_ttm?.current ?? null;
  const currentPb = historicalMetrics?.pb_mrq?.current ?? null;
  const currentYield = historicalMetrics?.dividend_yield?.current ?? null;
  const currentPrice = details.zones?.current_price ?? null;
  const openResearchPath = `/company/CN/${encodeURIComponent(leader.stock_code)}?tab=overview`;
  const fullKlinePath = `/company/CN/${encodeURIComponent(leader.stock_code)}?tab=raw&raw=kline`;

  return <>
    <button type="button" aria-label="关闭龙头快速判断" className="fixed inset-0 z-40 cursor-default bg-black/35 backdrop-blur-[1px]" onClick={onClose} />
    <section role="dialog" aria-modal="true" aria-label="龙头快速判断" className="fixed inset-3 z-50 flex flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl md:inset-y-8 md:left-1/2 md:right-auto md:w-[min(900px,calc(100vw-3rem))] md:-translate-x-1/2">
      <header className="border-b border-border px-5 py-4">
        <div className="flex items-start justify-between gap-4"><div className="min-w-0"><p className="text-xs font-medium text-primary">龙头快速判断</p><div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1"><h2 className="truncate text-xl font-semibold">{leader.stock_name} <span className="font-mono text-sm font-normal text-muted-foreground">{leader.stock_code}</span></h2>{reasons.length ? <span className="flex flex-wrap items-center gap-1.5"><span className="text-xs font-semibold">为什么入选</span>{reasons.map((reason) => <span key={reason} className="rounded bg-primary/10 px-2 py-1 text-[11px] text-primary">{reason}</span>)}</span> : <span className="text-xs text-muted-foreground">该公司在当前三级行业内排名第 {leader.leader_rank}，进入量化龙头候选池。</span>}</div><p className="mt-1 truncate text-xs text-muted-foreground">{leader.level3_name} · {leader.level1_name} / {leader.level2_name}</p></div><button type="button" aria-label="关闭龙头快速判断" onClick={onClose} className="rounded-md border border-border p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-4 w-4" /></button></div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <PriceJudgmentCard zones={details.zones} currentPrice={currentPrice} currentPe={currentPe} currentPb={currentPb} currentYield={currentYield} loading={loading} />

        <SupportPressureDetails zones={details.zones} currentPrice={currentPrice} stockCode={leader.stock_code} />

        <section className="mt-4 rounded-xl border border-border bg-card p-4"><div className="flex items-center justify-between gap-3"><div><h3 className="font-semibold">最近价格走势</h3><p className="mt-1 text-xs text-muted-foreground">最近约 6 个月日K · 前复权</p></div><Link to={fullKlinePath} className="inline-flex items-center gap-1 text-sm text-primary hover:underline">查看完整行情 <ArrowRight className="h-4 w-4" /></Link></div>{details.dailyBars?.coverage_status === "PARTIAL" ? <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">历史行情较短，以下走势仅供参考。</p> : null}{bars.length ? <div className="mt-3"><CandlestickChart data={bars} height={250} compact /></div> : <div className="mt-3 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">历史行情不足，暂无法展示走势。</div>}</section>

        <section className="mt-4 rounded-xl border border-primary/20 bg-primary/[0.025] p-4"><h3 className="font-semibold">一句话研究结论</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{details.conclusion?.research_conclusion || "当前研究结论尚未建立；可先查看价格状态，再进入完整公司研究补齐资料。"}</p></section>

        <p className="mt-4 rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">行业内量化排名只用于同一三级行业比较，不等同于市场份额第一。完整同行对比与排名依据请在公司研究中查看。</p>
      </div>

      <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-5 py-4"><Link to={openResearchPath} className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm font-medium hover:bg-muted">打开公司研究 <ArrowRight className="h-4 w-4" /></Link><button type="button" onClick={onChat} className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><MessageCircle className="h-4 w-4" />问投研主管</button></footer>
    </section>
  </>;
}
