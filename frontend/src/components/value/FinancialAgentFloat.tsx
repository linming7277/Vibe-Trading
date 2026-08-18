import { useEffect, useState } from "react";
import { Bot, Loader2, Send, X } from "lucide-react";
import { api, type FinancialAgentProgress, type Level3Leader } from "@/lib/api";

type ChatMessage = { id: string; role: "user" | "assistant"; content: string };

export function FinancialAgentFloat({
  open,
  target,
  candidates = [],
  onClose,
}: {
  open: boolean;
  target: Level3Leader | null;
  candidates?: Level3Leader[];
  onClose: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState<FinancialAgentProgress[]>([]);
  useEffect(() => {
    if (!open) return;
    setError("");
    setProgress([]);
    setMessages([]);
    if (!target) return;
    api.getCompanyFinancialDossier(target.stock_code, target.as_of)
      .then((dossier) => setMessages(dossier.chat_entries.map((entry) => ({
        id: entry.id, role: entry.role, content: entry.content,
      }))))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [target?.stock_code, open]);

  async function submit() {
    const question = input.trim();
    if (!question || sending) return;
    setInput("");
    setError("");
    setProgress([]);
    setSending(true);
    const localId = `local-${Date.now()}`;
    setMessages((current) => [...current, { id: localId, role: "user", content: question }]);
    try {
      const history = messages.map((item) => ({ role: item.role, content: item.content }));
      const onProgress = (item: FinancialAgentProgress) => setProgress((current) => {
        const index = current.findIndex((entry) => entry.stage === item.stage);
        if (index < 0) return [...current, item];
        const next = [...current];
        next[index] = item;
        return next;
      });
      const reply = target
        ? await api.streamCompanyFinancials(target.stock_code, { question, as_of: target.as_of, history }, onProgress)
        : await api.streamFinancialAgent({
          question, history,
          candidates: candidates.map((company) => ({
            stock_code: company.stock_code, stock_name: company.stock_name, as_of: company.as_of,
            level3_name: company.level3_name, leader_rank: company.leader_rank,
            leader_score: company.leader_score, coverage: company.coverage,
          })),
        }, onProgress);
      const replyContent = !target && "scope" in reply && reply.scope === "company" && reply.stock_name
        ? `已识别公司：${reply.stock_name}（${reply.stock_code}）\n\n${reply.answer}`
        : !target && "data_context" in reply && reply.data_context
          ? `已查询本地龙头池：${reply.data_context.industry_count} 个三级行业、${reply.data_context.company_count} 家龙头，数据截至 ${reply.data_context.data_dates?.join(" / ") || "未标注"}。\n\n${reply.answer}`
          : reply.answer;
      setMessages((current) => [...current, { id: `assistant-${Date.now()}`, role: "assistant", content: replyContent }]);
      setSending(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setSending(false);
    }
  }

  if (!open) return null;
  const title = target ? `${target.stock_name} · 财报研究员` : "财报研究员";
  const subtitle = target ? `${target.stock_code} · ${target.level3_name} · 已加载研究档案` : "输入公司名称或代码可自动读取公司档案";
  return <section aria-label="财报研究员对话" className={`fixed bottom-5 z-[60] flex h-[min(620px,calc(100vh-2.5rem))] w-[min(420px,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-xl border border-primary/30 bg-background shadow-2xl ${target ? "right-5 md:right-[34rem]" : "right-5"}`}>
    <header className="flex items-center justify-between gap-3 border-b border-border bg-primary/[0.04] px-4 py-3">
      <div className="min-w-0"><div className="flex items-center gap-2"><Bot className="h-4 w-4 shrink-0 text-primary" /><h2 className="truncate text-sm font-semibold">{title}</h2></div><p className="mt-0.5 truncate pl-6 text-[11px] text-muted-foreground">{subtitle}</p></div>
      <button type="button" onClick={onClose} aria-label="关闭财报研究员" className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-4 w-4" /></button>
    </header>
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
      {messages.length === 0 ? <div className="rounded-lg border border-dashed border-border bg-muted/30 p-3 text-sm leading-6 text-muted-foreground">{target ? <>已锁定 <strong className="text-foreground">{target.stock_name}</strong>。可以问财报变化、经营质量、风险、估值假设或需要验证的指标。</> : "每次回答都会先查询当前本地龙头池；在问题里写出龙头公司名称或股票代码时，还会自动加载该公司的财务数据进行分析。"}</div> : null}
      {messages.map((message) => <div key={message.id} className={`max-w-[90%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm leading-6 ${message.role === "user" ? "ml-auto bg-primary text-primary-foreground" : "border border-border bg-card"}`}>{message.content}</div>)}
      {progress.length > 0 ? <div className="rounded-lg border border-primary/20 bg-primary/[0.035] p-3 text-xs"><div className="mb-2 flex items-center gap-2 font-medium text-primary"><Bot className="h-3.5 w-3.5" />本次财报研究过程{sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}</div><div className="space-y-1.5">{progress.map((item, index) => <div key={item.stage} className="flex items-start gap-2 text-muted-foreground"><span className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] text-emerald-600 dark:text-emerald-300">{index + 1}</span><span className={index === progress.length - 1 && sending ? "text-foreground" : ""}>{item.message}</span></div>)}</div></div> : null}
      {sending && progress.length === 0 ? <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在启动财报研究…</div> : null}
      {error ? <div role="alert" className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">{error}</div> : null}
    </div>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="flex gap-2 border-t border-border p-3">
      <input value={input} onChange={(event) => setInput(event.target.value)} disabled={sending} placeholder={target ? `问 ${target.stock_name} 的财报…` : "输入财报问题…"} className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:opacity-60" />
      <button type="submit" disabled={!input.trim() || sending} aria-label="发送财报问题" className="inline-flex items-center justify-center rounded-md bg-primary px-3 text-primary-foreground disabled:opacity-50"><Send className="h-4 w-4" /></button>
    </form>
  </section>;
}
