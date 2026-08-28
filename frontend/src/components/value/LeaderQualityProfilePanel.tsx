import { useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { api, type LeaderQualityPeerMetric, type LeaderQualityProfile } from "@/lib/api";
import { formatNumber } from "@/components/workspace/WorkspaceUI";

const statusLabel: Record<string, string> = {
  STRONG: "明显领先同行", ABOVE_AVERAGE: "高于同行", NORMAL: "接近同行", BELOW_AVERAGE: "低于同行",
  UNKNOWN: "资料不足", NOT_SCORED: "仅展示相对投入", SHORT_WINDOW_STABLE: "短窗内保持前列",
  SHORT_WINDOW_MIXED: "短窗内有变化", SHORT_WINDOW_VOLATILE: "短窗内波动较大", INSUFFICIENT_HISTORY: "历史不足",
  STRONG_PROXY: "较强代理", MODERATE_PROXY: "中等代理", WEAK_PROXY: "较弱代理",
};
const dimensionLabel: Record<string, string> = { industry_position: "规模与经营位置", profitability: "盈利", growth_stability: "成长稳定性", cash_flow: "现金质量", valuation: "同行相对估值", governance_risk: "财务稳健与波动" };

function value(metric: LeaderQualityPeerMetric, raw: number | null) {
  if (raw == null) return "—";
  if (metric.unit === "元") return `${formatNumber(raw / 100_000_000)} 亿`;
  return `${formatNumber(raw)}${metric.unit ? ` ${metric.unit}` : ""}`;
}

function MetricTable({ items }: { items: LeaderQualityPeerMetric[] }) {
  return <div className="overflow-x-auto rounded-lg border border-border"><table className="min-w-[760px] w-full text-sm"><thead className="bg-muted/40 text-xs text-muted-foreground"><tr><th className="px-3 py-2 text-left font-medium">维度</th><th className="px-3 py-2 text-left font-medium">指标</th><th className="px-3 py-2 text-right font-medium">公司</th><th className="px-3 py-2 text-right font-medium">同行中位数</th><th className="px-3 py-2 text-right font-medium">同行位置</th><th className="px-3 py-2 text-left font-medium">结论</th></tr></thead><tbody className="divide-y divide-border">{items.map((item) => <tr key={item.metric}><td className="px-3 py-2 text-muted-foreground">{item.dimension_label}</td><td className="px-3 py-2 font-medium">{item.label}</td><td className="px-3 py-2 text-right tabular-nums">{value(item, item.company_value)}</td><td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{value(item, item.peer_median)}</td><td className="px-3 py-2 text-right tabular-nums">{item.peer_percentile == null ? "—" : `前 ${Math.max(0, 100 - item.peer_percentile).toFixed(0)}%`}</td><td className="px-3 py-2"><span className="rounded bg-muted px-2 py-1 text-xs">{statusLabel[item.status] || "资料不足"}</span>{item.data_quality !== "READY" ? <span className="ml-1 text-xs text-amber-700">样本{item.valid_peer_count}家</span> : null}</td></tr>)}</tbody></table></div>;
}

export function LeaderQualityProfilePanel({ stockCode, asOf, compact = false }: { stockCode: string; asOf?: string; compact?: boolean }) {
  const [data, setData] = useState<LeaderQualityProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { setLoading(true); setError(""); api.getLeaderQualityProfile(stockCode, asOf).then(setData).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoading(false)); }, [asOf, stockCode]);
  if (loading) return <section className="rounded-xl border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />读取已保存的龙头质量资料…</section>;
  if (error || !data) return <section className="rounded-xl border bg-card p-5"><h2 className="font-semibold">龙头质量</h2><p className="mt-2 text-sm text-muted-foreground">{error || "当前资料暂不可用。"}</p></section>;
  const position = data.leader_position;
  if (position.status !== "READY") return <section className="rounded-xl border bg-card p-5"><h2 className="font-semibold">龙头质量</h2><p className="mt-2 text-sm text-muted-foreground">当前没有可用的行业内排名资料，页面不会触发重算。</p></section>;
  if (compact) {
    const strengths = data.strengths.slice(0, 3);
    const weaknesses = data.weaknesses.slice(0, 2);
    const smallPeerSample = data.data_quality.small_peer_sample || (position.valid_peer_count ?? 0) <= 4;
    return <section className="rounded-xl border border-primary/25 bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="text-xs font-medium text-primary">龙头排名依据</div><h2 className="mt-1 text-lg font-semibold">为什么是龙头</h2><p className="mt-1 text-sm text-muted-foreground">这是 L3 行业内的量化排名，不等同于市场份额第一，也不能直接证明长期竞争优势。</p></div>
        <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">研究日期 {data.research_as_of || "—"}</span>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">当前 L3 行业排名</div><strong className="mt-1 block text-lg">Top {position.rank ?? "—"}</strong><p className="mt-1 text-xs text-muted-foreground">{position.level3?.name || "行业资料不足"}</p></article>
        <article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">与下一名差距</div><strong className="mt-1 block text-lg tabular-nums">{position.gap_to_next == null ? "资料不足" : formatNumber(position.gap_to_next)}</strong><p className="mt-1 text-xs text-muted-foreground">仅说明当前排名差距</p></article>
        <article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">同行有效样本</div><strong className="mt-1 block text-lg tabular-nums">{position.valid_peer_count ?? 0} 家</strong><p className="mt-1 text-xs text-muted-foreground">行业成员 {position.total_peer_count ?? 0} 家</p></article>
      </div>
      {smallPeerSample ? <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-800">同行样本较少，排名参考性有限；请结合行业资料进一步核验。</p> : null}
      <p className="mt-3 rounded-lg bg-muted/40 p-3 text-sm leading-6">{position.plain_explanation || "当前没有可解释的行业排名说明。"}</p>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <article className="rounded-lg border border-emerald-500/20 bg-emerald-500/[0.03] p-4"><h3 className="text-sm font-semibold">相对同行的主要优势</h3>{strengths.length ? <ul className="mt-2 space-y-1 text-sm text-muted-foreground">{strengths.map((item) => <li key={`${item.dimension}-${item.label}`}>• {item.label}：{statusLabel[item.status] || "当前资料支持"}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">资料不足，暂无法归纳优势。</p>}</article>
        <article className="rounded-lg border border-amber-500/25 bg-amber-500/[0.03] p-4"><h3 className="text-sm font-semibold">需要继续核验</h3>{weaknesses.length ? <ul className="mt-2 space-y-1 text-sm text-muted-foreground">{weaknesses.map((item) => <li key={`${item.dimension}-${item.label}`}>• {item.label}：{statusLabel[item.status] || "需要继续观察"}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">当前没有显著短板结论，但不代表资料完整。</p>}</article>
      </div>
      <details className="mt-4 rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2 text-sm font-medium">查看完整同行对比、短窗稳定性与定价权代理</summary><div className="space-y-4 border-t p-4"><MetricTable items={data.peer_advantages} /><div className="grid gap-3 lg:grid-cols-2"><article className="rounded-lg border p-3 text-sm"><strong>短窗龙头位置</strong><p className="mt-2">{statusLabel[data.leader_stability.status] || "资料不足"} · {data.leader_stability.run_count || 0} 次运行</p><p className="mt-1 text-xs text-muted-foreground">{data.leader_stability.disclaimer}</p></article><article className="rounded-lg border p-3 text-sm"><strong>定价权代理</strong><p className="mt-2">{statusLabel[data.pricing_power_proxy.status] || "资料不足"}</p><p className="mt-1 text-xs text-muted-foreground">{data.pricing_power_proxy.disclaimer}</p></article></div><p className="text-xs text-muted-foreground">进一步查看竞争优势研究，请进入“更多研究”。</p></div></details>
    </section>;
  }
  return <section className="rounded-xl border border-primary/25 bg-card p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-medium text-primary">LEADER QUALITY · 只读事实画像</div><h2 className="mt-1 text-lg font-semibold">龙头质量</h2><p className="mt-1 text-sm text-muted-foreground">解释当前为什么进入 L3 候选及相对同行的事实；不是护城河评分，也不产生买卖结论。</p></div><span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">截至 {data.research_as_of || "—"}</span></div>
    {data.data_quality.small_peer_sample ? <div className="mt-4 flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-800"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />当前行业仅有 {position.valid_peer_count} 家可评分同行；分位用于描述当前样本，可靠性较低。</div> : null}
    <div className="mt-4 grid gap-3 md:grid-cols-4"><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">当前行业位置</div><strong className="mt-1 block text-lg">第 {position.rank}</strong><p className="mt-1 text-xs text-muted-foreground">{position.level1?.name} / {position.level2?.name} / {position.level3?.name}</p></article><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">可评分同行</div><strong className="mt-1 block text-lg tabular-nums">{position.valid_peer_count} 家</strong><p className="mt-1 text-xs text-muted-foreground">总成员 {position.total_peer_count} 家</p></article><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">当前排名分数</div><strong className="mt-1 block text-lg tabular-nums">{formatNumber(position.leader_score)}</strong><p className="mt-1 text-xs text-muted-foreground">仅行业内相对排序</p></article><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">与下一名分差</div><strong className="mt-1 block text-lg tabular-nums">{position.gap_to_next == null ? "—" : formatNumber(position.gap_to_next)}</strong><p className="mt-1 text-xs text-muted-foreground">不是“护城河宽度”</p></article></div>
    <p className="mt-3 rounded-lg bg-muted/40 p-3 text-sm leading-6">{position.plain_explanation}</p>
    <div className="mt-5"><h3 className="font-semibold">为什么当前排在前面</h3><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{Object.entries(position.score_components || {}).map(([key, score]) => <article key={key} className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">{dimensionLabel[key] || key}</div><strong className="mt-1 block tabular-nums">{score == null ? "资料不足" : formatNumber(score)}</strong></article>)}</div></div>
    <div className="mt-5"><h3 className="font-semibold">同行对比</h3><p className="mt-1 text-xs text-muted-foreground">“明显领先/高于/接近/低于”均来自当前 L3 行业真实同行分位，不是新的综合评分。资本开支强度只展示相对水平，不预设高低好坏。</p><div className="mt-3"><MetricTable items={data.peer_advantages} /></div></div>
    <div className="mt-5 grid gap-3 lg:grid-cols-3"><article className="rounded-lg border border-border p-4"><h3 className="font-semibold">盈利与现金质量</h3><p className="mt-2 text-sm">盈利：{statusLabel[data.profitability_quality.status] || "资料不足"}<br />现金质量：{statusLabel[data.profitability_quality.cash_quality_status || "UNKNOWN"] || "资料不足"}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">{data.profitability_quality.disclaimer}</p></article><article className="rounded-lg border border-border p-4"><h3 className="font-semibold">短窗龙头位置</h3><p className="mt-2 text-sm">{statusLabel[data.leader_stability.status] || "资料不足"} · {data.leader_stability.run_count || 0} 次运行</p><p className="mt-1 text-xs text-muted-foreground">Top1 {data.leader_stability.top1_count || 0} 次；Top2 {data.leader_stability.top2_count || 0} 次</p><p className="mt-2 text-xs leading-5 text-muted-foreground">{data.leader_stability.disclaimer}</p></article><article className="rounded-lg border border-border p-4"><h3 className="font-semibold">定价权代理</h3><p className="mt-2 text-sm">{statusLabel[data.pricing_power_proxy.status] || "资料不足"}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">{data.pricing_power_proxy.disclaimer}</p></article></div>
    <div className="mt-5 rounded-lg border border-dashed border-border p-4"><h3 className="font-semibold">尚不能确认的长期竞争优势</h3><p className="mt-1 text-xs text-muted-foreground">这些数据缺口意味着页面不能把财务领先直接叫作品牌、技术、渠道或成本护城河。</p><div className="mt-3 flex flex-wrap gap-2">{data.moat_data_gaps.map((gap) => <span key={gap} className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">{gap}</span>)}</div><p className="mt-3 text-xs text-muted-foreground">L3 Run：{data.source_traceability?.l3_run_id || "—"} · 财报期：{data.source_traceability?.financial?.report_date || "—"} · 财报公告：{data.source_traceability?.financial?.announcement_date || "—"}</p></div>
  </section>;
}
