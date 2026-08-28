import { useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { api, type RiskResearch } from "@/lib/api";
import { cn } from "@/lib/utils";

const overallLabel: Record<string, string> = { LOW: "暂无明显压力", MEDIUM: "需要继续观察", HIGH: "需要重点复核", UNKNOWN: "资料不足，暂无法完整判断" };
const trapLabel: Record<string, string> = { LOW_TRAP_RISK: "低", MEDIUM_TRAP_RISK: "中", HIGH_TRAP_RISK: "高", UNKNOWN: "资料不足，暂无法完整判断" };
const tone: Record<string, string> = { LOW: "border-slate-500/30 bg-slate-500/10 text-slate-700", MEDIUM: "border-amber-500/40 bg-amber-500/10 text-amber-800", HIGH: "border-red-500/40 bg-red-500/10 text-red-800", UNKNOWN: "border-slate-500/30 bg-slate-500/10 text-slate-600" };

export function RiskResearchCard({ stockCode, asOf }: { stockCode: string; asOf?: string }) {
  const [data, setData] = useState<RiskResearch | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setData(null); setError("");
    api.getCompanyRiskResearch(stockCode, asOf).then((value) => { if (!cancelled) setData(value); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, [stockCode, asOf]);
  if (error) return <section className="rounded-xl border border-red-500/30 bg-red-500/10 p-5 text-sm text-red-800">读取风险研究失败：{error}</section>;
  if (!data) return <section className="rounded-xl border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在读取风险研究…</section>;
  const materialRisks = data.risks.filter((item) => item.severity !== "LOW");
  return <section className="rounded-xl border border-border bg-card p-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-medium text-primary">风险研究</div><h2 className="mt-1 text-lg font-semibold">当前需要复核的事项</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">{data.summary}</p></div><span className={cn("rounded border px-3 py-1.5 text-sm font-semibold", tone[data.overall_risk])}>{overallLabel[data.overall_risk]}</span></div>
    {data.overall_risk === "UNKNOWN" ? <p className="mt-4 rounded-lg border border-slate-500/30 bg-slate-500/5 p-3 text-sm text-muted-foreground">风险资料尚未完整，暂无法完整判断；这不是“低风险”结论。</p> : null}
    {data.value_trap_risk !== "NOT_APPLICABLE" ? <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3"><div className="flex items-center gap-2 text-sm font-semibold"><AlertTriangle className="h-4 w-4 text-amber-700" />低估陷阱检查：{trapLabel[data.value_trap_risk]}</div><p className="mt-1 text-xs leading-5 text-muted-foreground">仅在当前有效行业龙头且处于低估区域时检查；它用于复核低估原因，不代表交易建议。</p></div> : null}
    {materialRisks.length ? <div className="mt-4 space-y-3">{materialRisks.map((item) => <article key={item.risk_type} className="rounded-lg border border-border p-3"><div className="flex flex-wrap items-start justify-between gap-2"><div className="font-medium">{item.text}</div><span className={cn("rounded border px-2 py-0.5 text-xs", tone[item.severity])}>{item.severity === "HIGH" ? "重点复核" : "继续观察"}</span></div><p className="mt-2 text-sm leading-6 text-muted-foreground">为什么重要：{item.why_it_matters}</p>{item.watch_item ? <p className="mt-1 text-sm leading-6">下一步看什么：{item.watch_item}</p> : null}{(item.source_keys.length || item.evidence_ids.length) ? <details className="mt-2 text-xs text-muted-foreground"><summary className="cursor-pointer font-medium">查看依据</summary><div className="mt-2 break-all">{item.source_keys.length ? <div>数据依据：{item.source_keys.join("、")}</div> : null}{item.evidence_ids.length ? <div className="mt-1">研究证据：{item.evidence_ids.join("、")}</div> : null}</div></details> : null}</article>)}</div> : <p className="mt-4 rounded-lg bg-muted/40 p-3 text-sm text-muted-foreground">当前没有已确认的重点风险；这不代表所有风险均已覆盖。</p>}
    {data.data_quality.missing.length ? <details className="mt-4 rounded-lg border border-border p-3 text-sm"><summary className="cursor-pointer font-medium">资料不足项（{data.data_quality.missing.length}）</summary><p className="mt-2 leading-6 text-muted-foreground">{data.data_quality.missing.join("、")}</p></details> : null}
  </section>;
}
