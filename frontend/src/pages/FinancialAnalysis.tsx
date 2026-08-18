import { useEffect, useState } from "react";
import { Bot, Loader2, RefreshCw } from "lucide-react";
import { Link, Navigate, useParams, useSearchParams } from "react-router";
import { api, type FinancialAnalysisSnapshot, type FinancialDossier, type FinancialForecastScenario } from "@/lib/api";
import { companyResearchPath, inferReturnLabel, safeInternalPath } from "@/lib/routeContext";

const trendLabels: Record<string, string> = {
  IMPROVING: "改善", STABLE: "稳定", WEAKENING: "走弱", VOLATILE: "波动",
  INSUFFICIENT_DATA: "数据不足",
};
const metricLabels: Record<string, string> = {
  growth_trend: "经营增长", profitability_trend: "盈利能力",
  cash_flow_trend: "现金流", balance_sheet_trend: "资产负债",
};
const claimLabels: Record<string, string> = { FACT: "事实", INFERENCE: "推断", FORECAST: "情景", UNKNOWN: "未知" };
const statusLabels: Record<string, string> = {
  READY: "已完成", COMPLETED: "已生成", PARTIAL: "部分可用", LIMITED: "有限可用",
  INSUFFICIENT_DATA: "数据不足", CONFIGURATION_REQUIRED: "待配置财报研究员",
  NOT_RUN: "尚未生成解读", FAILED: "生成失败",
};
function statusLabel(status: string) { return statusLabels[status] || status; }

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function money(value: unknown): string {
  const amount = number(value);
  return amount == null ? "—" : `${(amount / 100_000_000).toFixed(2)} 亿`;
}
function percent(value: unknown): string {
  const amount = number(value);
  return amount == null ? "—" : `${amount.toFixed(2)}%`;
}
function badge(status: string): string {
  if (["READY", "COMPLETED", "IMPROVING"].includes(status)) return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  if (["FAILED", "WEAKENING"].includes(status)) return "bg-danger/10 text-danger";
  if (["PARTIAL", "LIMITED", "CONFIGURATION_REQUIRED", "VOLATILE"].includes(status)) return "bg-amber-500/10 text-amber-700 dark:text-amber-300";
  return "bg-muted text-muted-foreground";
}

function ForecastCell({ scenario, rowIndex, field }: { scenario?: FinancialForecastScenario; rowIndex: number; field: "revenue" | "net_profit" }) {
  const row = scenario?.forecast[rowIndex];
  return <td className="px-4 py-3 text-right tabular-nums">{money(row?.[field])}</td>;
}

export function FinancialAnalysisContent({ stockCode, asOf }: { stockCode: string; asOf?: string }) {
  const [data, setData] = useState<FinancialAnalysisSnapshot | null>(null);
  const [dossier, setDossier] = useState<FinancialDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true); setError("");
    api.getCompanyFinancialAnalysis(stockCode, asOf)
      .then(setData)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
    api.getCompanyFinancialDossier(stockCode, asOf).then(setDossier).catch(() => setDossier(null));
  }, [asOf, stockCode]);

  async function analyze() {
    setAnalyzing(true); setError("");
    try { setData(await api.analyzeCompanyFinancials(stockCode, { as_of: asOf, refresh: true })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally {
      api.getCompanyFinancialDossier(stockCode, asOf).then(setDossier).catch(() => setDossier(null));
      setAnalyzing(false);
    }
  }

  if (loading) return <div className="flex min-h-72 items-center justify-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />读取真实财务历史…</div>;
  if (!data) return <div className="rounded-lg border border-danger/30 bg-danger/10 p-4 text-danger">{error || "财务数据不可用"}</div>;

  const identity = data.identity;
  const bear = data.forecast.scenarios.BEAR;
  const base = data.forecast.scenarios.BASE;
  const bull = data.forecast.scenarios.BULL;
  const years = base?.forecast.map((row) => row.year) ?? bear?.forecast.map((row) => row.year) ?? bull?.forecast.map((row) => row.year) ?? [];
  const analysis = data.analysis;
  const valuation = identity.market_valuation;
  const financialCaution = identity.metric_applicability_notes?.includes("FINANCIAL_SECTOR_METRIC_CAUTION");

  return <div className="space-y-5">
    <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div><div className="text-xs font-medium text-primary">公司研究 · 财务与估值</div><h2 className="mt-2 text-xl font-semibold">{data.stock_name} · 财务、估值与情景预测 <span className="font-mono text-sm font-normal text-muted-foreground">{data.stock_code}</span></h2><div className="mt-2 text-sm text-muted-foreground">{identity.level1_name || "—"} → {identity.level2_name || "—"} → {identity.level3_name || "—"}</div></div>
      <button type="button" onClick={() => void analyze()} disabled={analyzing} className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{analyzing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}重新分析</button>
    </div>
    {error && <div className="rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>}
    {financialCaution && <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">金融行业限制：传统企业现金流、毛利率和负债率指标仅供辅助；情景预测可能显示“有限可用”，不据此形成经营质量定论。</div>}

    <section className="grid gap-3 rounded-xl border border-border bg-card p-5 sm:grid-cols-3 lg:grid-cols-6">
      <div><div className="text-xs text-muted-foreground">行业排名</div><strong>#{identity.leader_rank ?? "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">行业内龙头评分</div><strong>{identity.leader_score?.toFixed(2) ?? "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">数据截止</div><strong>{data.historical_cutoff}</strong></div>
      <div><div className="text-xs text-muted-foreground">历史财务</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${badge(data.feature_status)}`}>{statusLabel(data.feature_status)}</span></div>
      <div><div className="text-xs text-muted-foreground">情景预测</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${badge(data.forecast_status)}`}>{statusLabel(data.forecast_status)}</span></div>
      <div><div className="text-xs text-muted-foreground">财报研究员</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${badge(data.analysis_status)}`}>{statusLabel(data.analysis_status)}</span></div>
    </section>

    <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">历史趋势</h2><p className="mt-1 text-xs text-muted-foreground">最近 5 个完整年度及 PIT 可见的最新报告期；公告日期不得晚于 {data.as_of}。</p><div className="mt-4 overflow-x-auto"><table className="w-full min-w-[760px] text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="px-4 py-3 text-left">报告期</th><th className="px-4 py-3 text-left">公告日</th><th className="px-4 py-3 text-right">营收</th><th className="px-4 py-3 text-right">净利润</th><th className="px-4 py-3 text-right">经营现金流</th><th className="px-4 py-3 text-right">ROE</th></tr></thead><tbody>{data.history.map((row) => <tr key={`${row.report_date}-${row.announcement_date}`} className="border-t border-border/70"><td className="px-4 py-3">{String(row.report_date)}</td><td className="px-4 py-3 text-muted-foreground">{String(row.announcement_date)}</td><td className="px-4 py-3 text-right tabular-nums">{money(row.revenue)}</td><td className="px-4 py-3 text-right tabular-nums">{money(row.net_profit)}</td><td className="px-4 py-3 text-right tabular-nums">{money(row.operating_cash_flow)}</td><td className="px-4 py-3 text-right tabular-nums">{percent(row.roe)}</td></tr>)}</tbody></table></div></section>

    <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">财务质量</h2><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{Object.entries(data.feature.trends).map(([key, status]) => <div key={key} className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">{metricLabels[key] || key}</div><div className={`mt-2 inline-block rounded px-2 py-1 text-sm font-medium ${badge(status)}`}>{trendLabels[status] || status}</div></div>)}</div><div className="mt-4 flex flex-wrap gap-4 text-xs text-muted-foreground"><span>数据覆盖率：{(data.feature.data_quality.coverage * 100).toFixed(0)}%</span><span>完整年度：{data.feature.data_quality.annual_period_count}</span><span>财务特征版本：{data.financial_feature_version}</span></div>{data.data_gaps.length > 0 && <div className="mt-3 text-xs text-muted-foreground">数据缺口：{data.data_gaps.join("、")}</div>}</section>

    <section className="rounded-xl border border-border bg-card p-5"><div className="flex flex-col justify-between gap-2 sm:flex-row"><div><h2 className="font-semibold">未来 1–3 年情景</h2><p className="mt-1 text-xs text-muted-foreground">{data.forecast.disclaimer}</p></div><span className="font-mono text-xs text-muted-foreground">预测版本 {data.forecast_version}</span></div>{years.length ? <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[720px] text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="px-4 py-3 text-left">年度 / 指标</th><th className="px-4 py-3 text-right">谨慎情景</th><th className="px-4 py-3 text-right">基准情景</th><th className="px-4 py-3 text-right">乐观情景</th></tr></thead><tbody>{years.flatMap((year, index) => ([<tr key={`${year}-revenue`} className="border-t border-border/70"><td className="px-4 py-3">{year} 营业收入</td><ForecastCell scenario={bear} rowIndex={index} field="revenue" /><ForecastCell scenario={base} rowIndex={index} field="revenue" /><ForecastCell scenario={bull} rowIndex={index} field="revenue" /></tr>, <tr key={`${year}-profit`} className="border-t border-border/70"><td className="px-4 py-3">{year} 净利润</td><ForecastCell scenario={bear} rowIndex={index} field="net_profit" /><ForecastCell scenario={base} rowIndex={index} field="net_profit" /><ForecastCell scenario={bull} rowIndex={index} field="net_profit" /></tr>]))}</tbody></table></div> : <div className="mt-4 rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">当前情景预测：{statusLabel(data.forecast_status)}。{data.forecast.assumption_notes.join(" ")}</div>}<div className="mt-4 space-y-1 text-xs text-muted-foreground">{data.forecast.assumption_notes.map((note) => <div key={note}>• {note}</div>)}</div></section>

    <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">当前估值快照</h2>{valuation ? <><p className="mt-1 text-xs text-muted-foreground">数据日期 {valuation.as_of || "—"} · {valuation.source || "本地价值线快照"}</p><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">PE</div><strong className="mt-1 block text-lg tabular-nums">{number(valuation.pe)?.toFixed(2) ?? "—"}</strong></div><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">PB</div><strong className="mt-1 block text-lg tabular-nums">{number(valuation.pb)?.toFixed(2) ?? "—"}</strong></div><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">股息率</div><strong className="mt-1 block text-lg tabular-nums">{percent(valuation.dividend_yield)}</strong></div><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">总市值</div><strong className="mt-1 block text-lg tabular-nums">{number(valuation.market_cap) == null ? "—" : `${number(valuation.market_cap)!.toFixed(2)} 亿`}</strong></div></div><p className="mt-4 text-xs leading-5 text-muted-foreground">{valuation.limitations?.join(" ") || "当前估值快照仅用于解释现有财务与估值口径。"}</p></> : <div className="mt-4 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">当前快照尚未写入 PE/PB 等估值口径；重新预建财务摘要后即可展示。</div>}</section>

    <section className="rounded-xl border border-border bg-card p-5"><div className="flex flex-col justify-between gap-2 sm:flex-row"><div><h2 className="font-semibold">研究档案与追问记录</h2><p className="mt-1 text-xs text-muted-foreground">财务快照按数据版本保存；公司专属问答会归档并在后续分析时作为历史上下文。</p></div><span className="text-xs text-muted-foreground">已归档 {dossier?.archive_summary.chat_entry_count ?? 0} 条</span></div>{dossier?.chat_entries.length ? <div className="mt-4 space-y-3">{dossier.chat_entries.slice(-12).map((entry) => <article key={entry.id} className={`rounded-lg border p-3 text-sm ${entry.role === "user" ? "border-primary/30 bg-primary/[0.035]" : "border-border bg-muted/20"}`}><div className="flex justify-between gap-3 text-xs text-muted-foreground"><span>{entry.role === "user" ? "研究问题" : "财报研究员结论"}</span><time>{new Date(entry.created_at).toLocaleString("zh-CN", { hour12: false })}</time></div><p className="mt-2 whitespace-pre-wrap leading-6">{entry.content}</p></article>)}</div> : <div className="mt-4 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">尚无公司专属追问记录。通过财报 Agent 提问后，问题与结论会自动归档到这里。</div>}</section>

    <section className="rounded-xl border border-border bg-card p-5"><div className="flex items-center gap-2"><Bot className="h-5 w-5 text-primary" /><h2 className="font-semibold">财报研究员解读</h2></div>{data.analysis_status === "CONFIGURATION_REQUIRED" ? <div className="mt-4 rounded-lg bg-muted/40 p-5 text-sm text-muted-foreground">历史财务和情景预测已正常生成。请先在 <Link to="/settings/researchers" className="text-primary underline">研究员设置</Link> 中配置并启用“财报研究员”。</div> : data.analysis_status === "FAILED" ? <div className="mt-4 rounded-lg border border-danger/30 bg-danger/10 p-5 text-sm text-danger">财报研究员分析失败：{data.agent_error || "未知错误"}。历史财务和情景预测不受影响，可重新分析。</div> : analysis ? <div className="mt-4 space-y-5"><p className="leading-7">{analysis.executive_summary}</p><div className="grid gap-4 lg:grid-cols-2"><div className="rounded-lg bg-muted/30 p-4"><h3 className="text-sm font-medium">主要优势</h3><ul className="mt-2 space-y-2 text-sm text-muted-foreground">{analysis.financial_strengths.map((item) => <li key={item}>• {item}</li>)}</ul></div><div className="rounded-lg bg-muted/30 p-4"><h3 className="text-sm font-medium">主要风险</h3><ul className="mt-2 space-y-2 text-sm text-muted-foreground">{analysis.financial_risks.map((item) => <li key={item}>• {item}</li>)}</ul></div></div><div><h3 className="text-sm font-medium">事实、推断与情景</h3><div className="mt-2 space-y-2">{analysis.claims.map((claim, index) => <div key={`${claim.type}-${index}`} className="flex gap-3 rounded-md border border-border/70 px-3 py-2 text-sm"><span className="shrink-0 text-xs font-medium text-primary">{claimLabels[claim.type]}</span><span>{claim.statement}</span></div>)}</div></div><div><h3 className="text-sm font-medium">持续跟踪</h3><p className="mt-2 text-sm text-muted-foreground">{analysis.key_metrics_to_monitor.join("、") || "暂无"}</p></div></div> : <div className="mt-4 rounded-lg bg-muted/40 p-5 text-sm text-muted-foreground">历史财务与情景预测已准备，可点击“重新分析”调用财报研究员。页面打开时不会自动调用模型。</div>}</section>
  </div>;
}

/** Legacy address compatibility: full financial analysis now lives in the company research page. */
export function FinancialAnalysis() {
  const { stockCode = "" } = useParams();
  const [search] = useSearchParams();
  const asOf = search.get("as_of") || undefined;
  const parentPath = safeInternalPath(search.get("parent_from"), "/value");
  const parentLabel = search.get("parent_label") || inferReturnLabel(parentPath);
  const path = companyResearchPath(stockCode, { from: parentPath, fromLabel: parentLabel }, "financial");
  const destination = asOf ? `${path}&as_of=${encodeURIComponent(asOf)}` : path;
  return <Navigate to={destination} replace />;
}
