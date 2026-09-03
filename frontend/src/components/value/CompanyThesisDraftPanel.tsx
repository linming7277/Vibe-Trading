import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Check, FileText, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { api, type CompanyThesisDraft } from "@/lib/api";
import { confidenceLabel, thesisStatusLabel } from "@/components/value/SourceReferenceCard";

type EditableDraft = Pick<CompanyThesisDraft, "title" | "core_thesis" | "status" | "confidence"> & {
  invalid_conditions_text: string;
  supporting_conditions_text: string;
  key_metrics_text: string;
};

function metricText(item: { text?: string; condition?: string; metric?: string } | string) {
  if (typeof item === "string") return item;
  return String(item.text || item.condition || item.metric || "");
}

function asEditable(draft: CompanyThesisDraft): EditableDraft {
  return {
    title: draft.title,
    core_thesis: draft.core_thesis,
    status: draft.status,
    confidence: draft.confidence,
    invalid_conditions_text: draft.invalid_conditions.map((item) => item.condition).filter(Boolean).join("\n"),
    supporting_conditions_text: (draft.key_assumptions || []).map((item) => item.text || item.condition || "").filter(Boolean).join("\n"),
    key_metrics_text: (draft.key_metrics_to_monitor || []).map((item) => metricText(item)).filter(Boolean).join("\n"),
  };
}

export function CompanyThesisDraftPanel({ stockCode, onConfirmed }: { stockCode: string; onConfirmed?: () => void }) {
  const [draft, setDraft] = useState<CompanyThesisDraft | null>(null);
  const [form, setForm] = useState<EditableDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<"generate" | "confirm" | "reject" | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.getCompanyThesisDraft(stockCode);
      setDraft(result.draft);
      setForm(result.draft ? asEditable(result.draft) : null);
      setMessage("");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [stockCode]);

  useEffect(() => { void load(); }, [load]);

  const generate = async () => {
    setWorking("generate");
    setMessage("");
    try {
      const result = await api.generateCompanyThesisDraft(stockCode);
      if (!result.draft) {
        setMessage(result.message || (result.status === "THESIS_EXISTS" ? "当前已有正式公司核心逻辑，无需再生成初始草案。" : "资料不足，暂不能生成草案。"));
        return;
      }
      setDraft(result.draft);
      setForm(asEditable(result.draft));
      toast.success(result.status === "EXISTING" ? "已读取当前草案" : "已生成可编辑草案");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(null);
    }
  };

  const confirm = async () => {
    if (!draft || !form) return;
    setWorking("confirm");
    setMessage("");
    try {
      await api.confirmCompanyThesisDraft(stockCode, draft.draft_id, {
        title: form.title,
        core_thesis: form.core_thesis,
        status: form.status,
        confidence: form.confidence,
        invalid_conditions: form.invalid_conditions_text.split("\n").map((condition) => condition.trim()).filter(Boolean).map((condition) => ({ condition, status: "ACTIVE" })),
        supporting_conditions: form.supporting_conditions_text.split("\n").map((condition) => condition.trim()).filter(Boolean).map((condition) => ({ condition, status: "ACTIVE" })),
        key_metrics_to_monitor: form.key_metrics_text.split("\n").map((text) => text.trim()).filter(Boolean).map((text) => ({ text })),
      });
      toast.success("已人工确认并建立正式公司核心逻辑");
      setDraft(null);
      setForm(null);
      onConfirmed?.();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(null);
    }
  };

  const reject = async () => {
    if (!draft) return;
    setWorking("reject");
    setMessage("");
    try {
      await api.rejectCompanyThesisDraft(stockCode, draft.draft_id);
      setDraft(null);
      setForm(null);
      toast.success("草案未采纳，未改动正式公司核心逻辑");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(null);
    }
  };

  if (loading) return <section className="rounded-xl border border-dashed border-border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />读取核心逻辑草案…</section>;
  if (!draft || !form) return <section className="rounded-xl border border-dashed border-primary/30 bg-primary/5 p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-medium text-primary">初步核心逻辑</div><h2 className="mt-1 font-semibold">生成待人工确认的草案</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">只读取已经完成且带来源的财务、经营研究，生成一份可编辑草案。不会自动建立正式公司核心逻辑、写入研究证据或触发研究复核。</p></div><button type="button" onClick={() => void generate()} disabled={working != null} className="inline-flex items-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{working === "generate" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileText className="mr-2 h-4 w-4" />}生成草案</button></div>{message ? <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">{message}</p> : null}</section>;

  const set = <K extends keyof EditableDraft>(key: K, value: EditableDraft[K]) => setForm((current) => current ? { ...current, [key]: value } : current);
  const moatItems = draft.competitive_advantages || [];
  const moatLabel: Record<string, string> = { SUPPORTED: "较充分证据支持", PARTIAL: "初步判断", UNKNOWN: "当前无法确认" };
  return <section className="rounded-xl border border-primary/35 bg-card p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-medium text-primary">初步核心逻辑草案</div><h2 className="mt-1 font-semibold">请核对后再确认</h2><p className="mt-1 text-xs text-muted-foreground">来源：财务、经营与竞争优势研究的已保存资料。确认前不属于正式公司核心逻辑。</p></div><span className="rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-800 dark:text-amber-200">待人工确认</span></div><div className="mt-4 grid gap-3"><label className="grid gap-1.5 text-sm font-medium">逻辑标题<input value={form.title} onChange={(event) => set("title", event.target.value)} className="rounded-md border bg-background px-3 py-2 text-sm font-normal" /></label><label className="grid gap-1.5 text-sm font-medium">核心逻辑<textarea value={form.core_thesis} onChange={(event) => set("core_thesis", event.target.value)} rows={5} className="rounded-md border bg-background px-3 py-2 text-sm font-normal leading-6" /></label><div className="grid gap-3 sm:grid-cols-2"><label className="grid gap-1.5 text-sm font-medium">逻辑状态<select value={form.status} onChange={(event) => set("status", event.target.value as CompanyThesisDraft["status"])} className="rounded-md border bg-background px-3 py-2 text-sm font-normal">{(["FORMING", "STRENGTHENING", "UNCHANGED", "WEAKENING", "FALSIFIED"] as const).map((value) => <option key={value} value={value}>{thesisStatusLabel(value)}</option>)}</select></label><label className="grid gap-1.5 text-sm font-medium">判断把握<select value={form.confidence} onChange={(event) => set("confidence", event.target.value as CompanyThesisDraft["confidence"])} className="rounded-md border bg-background px-3 py-2 text-sm font-normal">{(["LOW", "MEDIUM", "HIGH"] as const).map((value) => <option key={value} value={value}>{confidenceLabel(value)}</option>)}</select></label></div><label className="grid gap-1.5 text-sm font-medium">需要重新评估的情况（每行一条）<textarea value={form.invalid_conditions_text} onChange={(event) => set("invalid_conditions_text", event.target.value)} rows={3} className="rounded-md border bg-background px-3 py-2 text-sm font-normal leading-6" /></label>
<label className="grid gap-1.5 text-sm font-medium">正向验证假设（每行一条）<textarea value={form.supporting_conditions_text} onChange={(event) => set("supporting_conditions_text", event.target.value)} rows={3} className="rounded-md border bg-background px-3 py-2 text-sm font-normal leading-6" /></label>
<label className="grid gap-1.5 text-sm font-medium">需要持续跟踪的指标（每行一条）<textarea value={form.key_metrics_text} onChange={(event) => set("key_metrics_text", event.target.value)} rows={3} className="rounded-md border bg-background px-3 py-2 text-sm font-normal leading-6" /></label></div>{moatItems.length ? <section className="mt-4 rounded-lg border border-border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-medium">竞争优势研究如何进入本草案</h3><p className="mt-1 text-xs text-muted-foreground">只有“较充分证据支持”才会作为事实依据；初步判断和无法确认均保留原始边界。</p></div><Link to={`/company/CN/${encodeURIComponent(stockCode)}?tab=moat-research`} className="text-xs text-primary underline-offset-2 hover:underline">查看竞争优势研究</Link></div><div className="mt-3 space-y-2">{moatItems.map((item, index) => <div key={`${item.moat_dimension || item.dimension || "moat"}-${index}`} className="rounded bg-muted/30 p-2 text-sm"><span className="mr-2 rounded bg-background px-1.5 py-0.5 text-xs text-muted-foreground">{moatLabel[item.assessment || "UNKNOWN"] || "当前无法确认"}</span>{item.text}{item.counter_evidence_ids?.length ? <p className="mt-1 text-xs text-amber-800">已同步反证，需在失效条件中复核。</p> : null}</div>)}</div></section> : null}<details className="mt-4 rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2 text-sm font-medium">查看本草案依据（{draft.source_refs.length} 条）</summary><div className="space-y-2 border-t p-3 text-sm leading-6">{draft.source_refs.map((item, index) => <p key={`${item.domain}-${index}`} className="rounded bg-muted/30 p-2"><span className="mr-2 rounded bg-background px-1.5 py-0.5 text-xs text-muted-foreground">{item.domain === "FINANCIAL" ? "财务" : item.domain === "MOAT_RESEARCH" ? "竞争优势" : "经营"} · {item.type === "FACT" ? "事实依据" : item.type === "UNKNOWN" ? "资料不足" : "分析判断"}</span>{item.text}</p>)}</div></details><div className="mt-4 flex flex-wrap justify-end gap-2"><button type="button" onClick={() => void reject()} disabled={working != null} className="inline-flex items-center rounded-md border px-3 py-2 text-sm font-medium disabled:opacity-50">{working === "reject" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <X className="mr-2 h-4 w-4" />}不采纳</button><button type="button" onClick={() => void confirm()} disabled={working != null || !form.title.trim() || !form.core_thesis.trim()} className="inline-flex items-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{working === "confirm" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}确认并建立正式逻辑</button></div>{message ? <p className="mt-3 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{message}</p> : null}</section>;
}
