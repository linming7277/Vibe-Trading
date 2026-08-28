import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  api,
  type CompanyResearchOverviewCitation,
  type CompanyThesisEvidence,
  type CompanyThesisEvidenceResponse,
  type CompanyThesisHistoryItem,
} from "@/lib/api";
import { SourceReferenceCard, thesisStatusLabel, confidenceLabel, ownerFacingText } from "@/components/value/SourceReferenceCard";
import { ownerStatus } from "@/lib/ownerLanguage";

const effectLabels: Record<string, string> = {
  SUPPORT: "支持当前逻辑",
  CHALLENGE: "挑战当前逻辑",
  NEUTRAL: "其他观察",
};

export function CitationList({ citations }: { citations: CompanyResearchOverviewCitation[] }) {
  return <SourceReferenceCard citations={citations} />;
}

function EvidenceGroup({ effect, evidence }: { effect: string; evidence: CompanyThesisEvidence[] }) {
  return <article className="rounded-xl border border-border bg-card p-5"><div className="flex items-center justify-between gap-3"><h2 className="font-semibold">{effectLabels[effect] || "其他观察"}</h2><span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{evidence.length} 条</span></div>{evidence.length ? <div className="mt-4 space-y-3">{evidence.map((item) => <div key={item.evidence_id} className="rounded-lg border border-border/70 bg-muted/20 p-3"><div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground"><span>研究领域：{item.metadata?.research_domain === "FINANCIAL" ? "财务" : item.metadata?.research_domain === "BUSINESS" ? "经营" : "综合"}</span><span>置信度：{confidenceLabel(item.confidence)}</span><span>记录时间：{item.created_at || "—"}</span></div><p className="mt-2 text-sm leading-6">{ownerFacingText(item.claim || item.summary || "未填写研究证据内容。")}</p>{item.summary && item.summary !== item.claim ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{ownerFacingText(item.summary)}</p> : null}<SourceReferenceCard citations={item.metadata?.resolved_citations || []} researchContent={item.claim || item.summary} /></div>)}</div> : <p className="mt-4 text-sm text-muted-foreground">当前没有此类有效研究证据。</p>}</article>;
}

export function CompanyResearchEvidencePanel({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<CompanyThesisEvidenceResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null); setError("");
    api.getCompanyThesisEvidence(stockCode).then(setData).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [stockCode]);
  if (error) return <section className="rounded-xl border border-danger/30 bg-danger/10 p-5 text-sm text-danger">读取研究证据失败：{error}</section>;
  if (!data) return <section className="rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />读取当前有效研究证据…</section>;
  if (!data.current_thesis) return <section className="rounded-xl border border-dashed border-border bg-card p-5"><h2 className="font-semibold">研究证据</h2><p className="mt-3 text-sm text-muted-foreground">当前尚未建立公司核心逻辑，因此没有可归属的正式研究证据。</p></section>;
  return <div className="space-y-5"><section className="rounded-xl border border-primary/20 bg-primary/[0.035] p-5"><h1 className="text-lg font-semibold">研究证据</h1><p className="mt-1 text-sm text-muted-foreground">仅展示当前公司核心逻辑下仍有效的证据，共 {data.summary?.active ?? data.evidence.length} 条。</p></section>{["SUPPORT", "CHALLENGE", "NEUTRAL"].map((effect) => <EvidenceGroup key={effect} effect={effect} evidence={data.evidence.filter((item) => item.effect === effect)} />)}</div>;
}

export function CompanyThesisSummaryPanel({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<CompanyThesisEvidenceResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null); setError("");
    api.getCompanyThesisEvidence(stockCode).then(setData).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [stockCode]);
  if (error) return <section className="rounded-xl border border-danger/30 bg-danger/10 p-5 text-sm text-danger">读取公司核心逻辑失败：{error}</section>;
  if (!data) return <section className="rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />读取公司核心逻辑…</section>;
  const thesis = data.current_thesis;
  if (!thesis) return <section className="rounded-xl border border-dashed border-border bg-card p-5"><h2 className="font-semibold">公司核心逻辑</h2><p className="mt-3 text-sm text-muted-foreground">当前尚未建立公司核心逻辑。</p></section>;
  const invalidConditions = thesis.invalid_conditions || [];
  const authorityLabels: Record<string, string> = {
    AI_PROVISIONAL: "AI初步核心逻辑 · 待人工复核",
    HUMAN_CONFIRMED: "已人工确认",
    LEGACY_UNVERIFIED: "历史逻辑 · 尚未核验来源权限",
  };
  const provisional = thesis.authority_status === "AI_PROVISIONAL";
  const authority = authorityLabels[thesis.authority_status || ""] || "核心逻辑来源待确认";
  return <section className="rounded-xl border border-primary/25 bg-card p-5"><div className="flex flex-wrap justify-between gap-3"><div><h2 className="font-semibold">公司核心逻辑</h2><p className="mt-1 text-xs text-muted-foreground">当前逻辑状态：{thesisStatusLabel(thesis.status)} · 置信度：{confidenceLabel(thesis.confidence)}</p></div><div className="flex flex-wrap items-center gap-2"><span className={provisional ? "rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-800 dark:text-amber-200" : "rounded bg-primary/10 px-2 py-1 text-xs text-primary"}>{authority}</span></div></div>{provisional ? <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-100">这份逻辑由已保存的研究资料自动整理，尚未经过人工确认；它用于后续研究与复核，不代表已确认结论。</p> : null}<p className="mt-4 text-sm leading-7">{ownerFacingText(thesis.core_thesis || "当前版本未记录核心逻辑正文。")}</p><div className="mt-4 rounded-lg border border-border/70 p-3"><h3 className="text-sm font-medium">逻辑失效条件</h3>{invalidConditions.length ? <ul className="mt-2 space-y-1 text-sm text-muted-foreground">{invalidConditions.map((item, index) => <li key={`${item.condition || item.text || "condition"}-${index}`}>• {ownerFacingText(item.condition || item.text || "未命名条件")}{item.status ? `（${ownerStatus(item.status, "待确认")}）` : ""}</li>)}</ul> : <p className="mt-2 text-sm text-muted-foreground">当前版本未记录逻辑失效条件。</p>}</div></section>;
}

export function ThesisHistoryTimeline({ stockCode }: { stockCode: string }) {
  const [items, setItems] = useState<CompanyThesisHistoryItem[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setItems(null); setError("");
    api.getCompanyThesisHistory(stockCode).then((result) => setItems(result.items)).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [stockCode]);
  return <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">历史变化</h2><p className="mt-1 text-xs text-muted-foreground">记录公司核心逻辑变化的原因，以及当时引用的研究证据。</p>{error ? <p className="mt-3 text-sm text-danger">读取版本历史失败：{error}</p> : items == null ? <p className="mt-3 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />读取版本历史…</p> : !items.length ? <p className="mt-3 rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground">暂无版本变更历史。</p> : <ol className="mt-4 space-y-3 border-l border-border pl-4">{items.map((item) => <li key={item.history_id} className="relative"><span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-primary" /><div className="rounded-lg border border-border/70 bg-muted/20 p-3"><div className="flex flex-wrap justify-between gap-2"><strong className="text-sm">版本 {item.from_version} → {item.to_version}</strong><span className="text-xs text-muted-foreground">{item.created_at || "—"}</span></div><p className="mt-1 text-sm">{thesisStatusLabel(item.old_status)} → {thesisStatusLabel(item.new_status)}</p><p className="mt-2 text-sm leading-6 text-muted-foreground">{ownerFacingText(item.change_reason || "未记录变更原因。")}</p><p className="mt-2 text-xs text-muted-foreground">当时引用研究证据：{item.evidence_ids_json?.length || 0} 条</p></div></li>)}</ol>}</section>;
}
