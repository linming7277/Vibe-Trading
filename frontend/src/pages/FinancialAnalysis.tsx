import { useEffect, useState } from "react";
import { Bot, Loader2, RefreshCw } from "lucide-react";
import { Link, Navigate, useParams, useSearchParams } from "react-router";
import { api, type BusinessClaim, type BusinessResearchSnapshot, type CompanyResearchOverview, type CompanyResearchOverviewCitation, type CompanyThesis, type CompanyThesisReview, type FinancialAnalysisSnapshot, type FinancialClaim, type FinancialDossier, type FinancialForecastScenario } from "@/lib/api";
import { companyResearchPath, inferReturnLabel, safeInternalPath } from "@/lib/routeContext";
import { SourceReferenceCard, claimTypeLabel, confidenceLabel, thesisStatusLabel } from "@/components/value/SourceReferenceCard";

const trendLabels: Record<string, string> = {
  IMPROVING: "改善", STABLE: "稳定", WEAKENING: "走弱", VOLATILE: "波动",
  INSUFFICIENT_DATA: "数据不足",
};
const metricLabels: Record<string, string> = {
  growth_trend: "经营增长", profitability_trend: "盈利能力",
  cash_flow_trend: "现金流", balance_sheet_trend: "资产负债",
};
const statusLabels: Record<string, string> = {
  READY: "已完成", COMPLETED: "已生成", PARTIAL: "部分可用", LIMITED: "有限可用",
  INSUFFICIENT_DATA: "数据不足", CONFIGURATION_REQUIRED: "待配置财报研究员",
  NOT_RUN: "尚未生成解读", FAILED: "生成失败",
};
function statusLabel(status: string) { return statusLabels[status] || status; }
function businessText(value: string | undefined, fallback = "资料不足") {
  const text = String(value || "").trim();
  return !text || text === "UNKNOWN" || text.startsWith("UNKNOWN：") ? fallback : text;
}

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

function ClaimCitations({ claim }: { claim: FinancialClaim }) {
  const citations = claim.citations || [];
  if (claim.type === "UNKNOWN" && citations.length === 0) {
    return <p className="mt-2 text-xs text-muted-foreground">当前数据不足，未绑定具体事实来源。</p>;
  }
  return <SourceReferenceCard citations={citations} researchContent={claim.statement} emptyMessage="当前财务观点暂无可展示的依据。" />;
}

const businessTopicLabels: Record<string, string> = {
  MAIN_BUSINESS: "主营业务", PRODUCT: "产品结构", BUSINESS_MODEL: "商业模式", BUSINESS_CHANGE: "经营变化",
};

function BusinessClaimSources({ claim }: { claim: BusinessClaim }) {
  const citations = claim.citations || [];
  return <SourceReferenceCard citations={citations} researchContent={claim.text} emptyMessage="当前经营观点暂无可展示的依据。" />;
}

export function BusinessResearchCard({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<BusinessResearchSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setLoading(true); setError("");
    api.getCompanyBusinessResearch(stockCode).then(setData)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, [stockCode]);
  const analyze = async () => {
    setAnalyzing(true); setError("");
    try { setData(await api.analyzeCompanyBusinessResearch(stockCode, { force: true })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setAnalyzing(false); }
  };
  return <section className="rounded-xl border border-border bg-card p-5"><div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start"><div><h2 className="font-semibold">公司经营</h2><p className="mt-1 text-xs text-muted-foreground">回答公司主要做什么、卖什么、怎么赚钱，以及最近经营上有没有可验证的变化。</p></div><button type="button" onClick={() => void analyze()} disabled={analyzing || loading || !data} className="inline-flex items-center justify-center rounded-md border px-3 py-2 text-sm font-medium disabled:opacity-50">{analyzing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}生成经营研究</button></div>{loading ? <div className="mt-4 text-sm text-muted-foreground">读取本地公司经营资料…</div> : error ? <div className="mt-4 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error}</div> : data ? <div className="mt-4 space-y-4">{data.analysis?.summary ? <div className="rounded-lg border border-primary/20 bg-primary/[0.04] p-3 text-sm leading-6">{data.analysis.summary}</div> : <div className="rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground">已读取经营资料；点击“生成经营研究”后才会形成带依据的解释。</div>}<div className="grid gap-3 sm:grid-cols-2"><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">公司主要做什么</div><p className="mt-2 text-sm leading-6">{businessText(data.main_business)}</p></article><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">主要产品</div><p className="mt-2 text-sm leading-6">{data.products.length ? data.products.join("、") : "资料不足"}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{data.product_note}</p></article><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">怎么赚钱</div><p className="mt-2 text-sm leading-6">{businessText(data.business_model)}</p></article><article className="rounded-lg border border-border p-3"><div className="text-xs text-muted-foreground">最近经营变化</div><p className="mt-2 text-sm leading-6">{businessText(data.business_changes.join("；"), "暂无足够资料，尚无法判断最近经营变化。")}</p></article></div><p className="text-xs text-muted-foreground">资料状态：{statusLabel(data.data_quality.status)} · 数据日期：{data.data_as_of || "—"}</p>{data.analysis?.claims?.length ? <div><h3 className="text-sm font-semibold">经营研究观点</h3><div className="mt-2 grid gap-2">{data.analysis.claims.map((claim, index) => <article key={`${claim.topic}-${index}`} className="rounded-lg border border-border/70 bg-muted/20 p-3"><div className="flex flex-wrap items-center gap-2"><span className="rounded bg-primary/10 px-2 py-0.5 text-[11px] text-primary">{claimTypeLabel(claim.type)}</span><span className="text-xs font-medium">{businessTopicLabels[claim.topic] || "经营观察"}</span><span className="text-[11px] text-muted-foreground">置信度：{confidenceLabel(claim.confidence)}</span></div><p className="mt-2 text-sm leading-6">{claim.text}</p><BusinessClaimSources claim={claim} /></article>)}</div></div> : null}{data.data_quality.limitations.length ? <details className="rounded-lg border border-border"><summary className="cursor-pointer px-3 py-2 text-xs font-medium">数据边界</summary><div className="space-y-1 border-t border-border p-3 text-xs text-muted-foreground">{data.data_quality.limitations.map((item) => <div key={item}>• {item}</div>)}</div></details> : null}</div> : null}</section>;
}

export function ThesisReviewCard({ stockCode }: { stockCode: string }) {
  const [thesis, setThesis] = useState<CompanyThesis | null>(null);
  const [review, setReview] = useState<CompanyThesisReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const load = async () => {
    setLoading(true);
    try {
      const [thesisResult, reviewResult] = await Promise.all([
        api.getCompanyThesis(stockCode), api.getCompanyThesisReview(stockCode),
      ]);
      setThesis(thesisResult.thesis);
      setReview(reviewResult.review);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [stockCode]);
  const refresh = async () => {
    setRefreshing(true);
    try {
      const result = await api.refreshCompanyThesisReview(stockCode);
      setReview(result.review);
      if (result.status === "NO_ACTIVE_EVIDENCE") setError("当前公司核心逻辑尚无有效研究证据，无法生成研究复核。");
      else setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setRefreshing(false); }
  };
  const sources = review?.metadata?.evidence_source_summary || {};
  return <section className="rounded-xl border border-border bg-card p-5"><div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start"><div><h2 className="font-semibold">研究复核</h2><p className="mt-1 text-xs text-muted-foreground">基于当前公司核心逻辑下的全部有效研究证据生成建议；不会自动改变公司核心逻辑。</p></div><button type="button" onClick={() => void refresh()} disabled={refreshing || !thesis} className="inline-flex items-center justify-center rounded-md border px-3 py-2 text-sm font-medium disabled:opacity-50">{refreshing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}重新生成复核</button></div>{loading ? <div className="mt-4 text-sm text-muted-foreground">读取研究复核…</div> : !thesis ? <div className="mt-4 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">尚未建立公司核心逻辑；建立后才能基于研究证据重新生成复核。</div> : <div className="mt-4 space-y-3"><div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"><span>当前逻辑：{thesisStatusLabel(thesis.status)} · 置信度：{confidenceLabel(thesis.confidence)}</span>{review ? <span>复核状态：{review.review_status === "PENDING" ? "待人工复核" : review.review_status === "APPLIED" ? "已人工确认" : review.review_status === "DISMISSED" ? "未采纳" : "已复核"}</span> : <span>尚未生成研究复核</span>}</div>{review?.is_stale ? <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">已有新的研究证据，当前研究复核已过期。请显式重新生成。</div> : null}{review ? <article className="rounded-lg border border-border/70 bg-muted/20 p-3"><div className="flex flex-wrap justify-between gap-2"><strong className="text-sm">建议逻辑状态：{thesisStatusLabel(review.recommended_status)} · 置信度：{confidenceLabel(review.recommended_confidence)}</strong><span className="text-xs text-muted-foreground">支持 {review.support_count} · 挑战 {review.challenge_count} · 其他观察 {review.neutral_count}</span></div><p className="mt-2 text-sm leading-6 text-muted-foreground">{review.review_reason}</p>{Object.keys(sources).length ? <p className="mt-2 text-xs text-muted-foreground">研究依据覆盖：{Object.values(sources).reduce((total, count) => total + count, 0)} 条</p> : null}</article> : <div className="rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground">尚无可显示的当前研究复核。</div>}</div>}{error ? <div className="mt-3 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error}</div> : null}</section>;
}

function OverviewSources({ citations }: { citations: CompanyResearchOverviewCitation[] }) {
  return <SourceReferenceCard citations={citations} />;
}

function OverviewEvidenceList({ title, items, empty }: { title: string; items: CompanyResearchOverview["supporting_evidence"]; empty: string }) {
  return <article className="rounded-lg border border-border p-4"><h3 className="text-sm font-semibold">{title}</h3>{items.length ? <div className="mt-3 space-y-3">{items.map((item) => <div key={item.evidence_id} className="rounded border border-border/70 bg-muted/20 p-3"><div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground"><span>研究领域：{item.research_domain === "FINANCIAL" ? "财务" : item.research_domain === "BUSINESS" ? "经营" : "综合"}</span><span>置信度：{confidenceLabel(item.confidence)}</span><span>{item.created_by === "HUMAN" ? "人工整理" : "系统整理"}</span></div><p className="mt-2 text-sm leading-6">{item.claim}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{item.summary}</p><SourceReferenceCard citations={item.citations} researchContent={item.claim} /></div>)}</div> : <p className="mt-3 text-sm text-muted-foreground">{empty}</p>}</article>;
}

export function CompanyResearchOverviewCard({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<CompanyResearchOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    setLoading(true); setError("");
    api.getCompanyResearchOverview(stockCode).then(setData)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, [stockCode]);
  if (loading) return <section className="rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />读取公司研究总览…</section>;
  if (error || !data) return <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">公司研究总览</h2><p className="mt-3 text-sm text-muted-foreground">{error || "总览资料暂不可用。"}</p></section>;
  const business = data.business_summary;
  const financial = data.financial_summary;
  const primaryBusinessClaim = business.claims.find((item) => item.topic === "MAIN_BUSINESS");
  return <section className="rounded-xl border border-primary/25 bg-card p-5"><div className="flex flex-wrap items-end justify-between gap-3"><div><div className="text-xs font-medium text-primary">公司研究总览</div><h2 className="mt-1 text-lg font-semibold">公司研究总览</h2><p className="mt-1 text-xs text-muted-foreground">只汇总已有研究资料；不会重新分析、刷新研究复核或修改公司核心逻辑。</p></div><div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground"><span>财务：{statusLabel(data.data_status.financial)}</span><span>经营：{statusLabel(data.data_status.business)}</span><span>核心逻辑：{thesisStatusLabel(data.data_status.thesis)}</span><span>研究复核：{data.data_status.review === "NOT_CREATED" ? "尚未生成" : statusLabel(data.data_status.review)}</span></div></div><div className="mt-4 grid gap-3 lg:grid-cols-2"><article className="rounded-lg border border-border p-4"><h3 className="text-sm font-semibold">这家公司是做什么的</h3><p className="mt-2 text-sm leading-6">{business.description || "尚未生成公司经营研究。"}</p><p className="mt-2 text-xs text-muted-foreground">主要产品：{business.products?.length ? business.products.join("、") : "资料不足"}</p><p className="mt-1 text-xs text-muted-foreground">怎么赚钱：{business.business_model || "资料不足"}</p><OverviewSources citations={primaryBusinessClaim?.citations || []} /></article><article className="rounded-lg border border-border p-4"><h3 className="text-sm font-semibold">最近经营发生了什么</h3><div className="mt-2 space-y-2 text-sm leading-6">{business.changes.length ? business.changes.map((item, index) => <p key={index}>{item}</p>) : <p>尚未生成公司经营研究。</p>}</div></article></div><article className="mt-3 rounded-lg border border-border p-4"><h3 className="text-sm font-semibold">财务表现</h3>{financial.items.length ? <div className="mt-3 grid gap-2 lg:grid-cols-2">{financial.items.slice(0, 5).map((item, index) => <div key={`${item.category}-${index}`} className="rounded border border-border/70 bg-muted/20 p-3"><p className="text-sm leading-6">{item.text}</p><SourceReferenceCard citations={item.citations} researchContent={item.text} /></div>)}</div> : <p className="mt-3 text-sm text-muted-foreground">{financial.message || "尚未生成财务研究。"}</p>}</article><div className="mt-3 grid gap-3 lg:grid-cols-2"><OverviewEvidenceList title="支持当前逻辑" items={data.supporting_evidence} empty="当前没有可展示的支持当前逻辑的研究证据。" /><OverviewEvidenceList title="挑战当前逻辑" items={data.challenging_evidence} empty="当前没有可展示的挑战当前逻辑的研究证据。" /></div><div className="mt-3 grid gap-3 lg:grid-cols-2"><article className="rounded-lg border border-border p-4"><h3 className="text-sm font-semibold">当前逻辑</h3>{data.thesis ? <div className="mt-2"><p className="text-sm leading-6">{data.thesis.core_thesis}</p><p className="mt-2 text-xs text-muted-foreground">{thesisStatusLabel(data.thesis.status)} · 置信度：{confidenceLabel(data.thesis.confidence)}</p></div> : <p className="mt-3 text-sm text-muted-foreground">当前尚未建立公司核心逻辑。</p>}{data.review ? <div className={`mt-3 rounded p-3 text-xs ${data.review.is_stale ? "bg-amber-500/10 text-amber-700 dark:text-amber-300" : "bg-muted/30 text-muted-foreground"}`}>{data.review.is_stale ? "有新的研究证据，当前研究复核已过期，需要重新评估。" : `研究复核：支持 ${data.review.support_count} · 挑战 ${data.review.challenge_count} · 其他观察 ${data.review.neutral_count}`}</div> : <p className="mt-3 text-xs text-muted-foreground">尚未生成研究复核。</p>}</article><article className="rounded-lg border border-border p-4"><h3 className="text-sm font-semibold">接下来重点观察</h3>{data.watch_items.length ? <ul className="mt-3 space-y-2 text-sm leading-6">{data.watch_items.map((item, index) => <li key={`${item.source}-${index}`}>• {item.text}</li>)}</ul> : <p className="mt-3 text-sm text-muted-foreground">当前没有已记录的重点观察项。</p>}<p className="mt-3 text-xs text-muted-foreground">其他观察 {data.neutral_evidence_count} 条。</p></article></div></section>;
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
  const dataDates = identity.data_dates || {};
  const financialCaution = identity.metric_applicability_notes?.includes("FINANCIAL_SECTOR_METRIC_CAUTION");

  return <div className="space-y-5">
    <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div><div className="text-xs font-medium text-primary">财务研究</div><h2 className="mt-2 text-xl font-semibold">{data.stock_name} · 财务研究 <span className="font-mono text-sm font-normal text-muted-foreground">{data.stock_code}</span></h2><div className="mt-2 text-sm text-muted-foreground">历史财务、财务趋势、情景预测与财报研究员解读</div></div>
      <button type="button" onClick={() => void analyze()} disabled={analyzing} className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{analyzing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}重新分析</button>
    </div>
    {error && <div className="rounded-md border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>}
    {financialCaution && <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">金融行业限制：传统企业现金流、毛利率和负债率指标仅供辅助；情景预测可能显示“有限可用”，不据此形成经营质量定论。</div>}

    <section className="grid gap-3 rounded-xl border border-border bg-card p-5 sm:grid-cols-2 lg:grid-cols-4">
      <div><div className="text-xs text-muted-foreground">行业排名</div><strong>#{identity.leader_rank ?? "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">行业内龙头评分</div><strong>{identity.leader_score?.toFixed(2) ?? "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">行情时间</div><strong className="text-sm">{dataDates.quote_as_of ? String(dataDates.quote_as_of).replace("T", " ").slice(0, 16) : "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">估值更新时间</div><strong className="text-sm">{dataDates.valuation_as_of ? String(dataDates.valuation_as_of).replace("T", " ").slice(0, 16) : valuation?.as_of || "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">最新财务报告期</div><strong>{dataDates.financial_report_date || "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">财报公告日</div><strong>{dataDates.financial_announcement_date || "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">龙头排名日期</div><strong>{dataDates.leader_as_of || "—"}</strong></div>
      <div><div className="text-xs text-muted-foreground">分析可见性基准</div><strong>{data.historical_cutoff}</strong></div>
      <div><div className="text-xs text-muted-foreground">历史财务</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${badge(data.feature_status)}`}>{statusLabel(data.feature_status)}</span></div>
      <div><div className="text-xs text-muted-foreground">情景预测</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${badge(data.forecast_status)}`}>{statusLabel(data.forecast_status)}</span></div>
      <div><div className="text-xs text-muted-foreground">财报研究员</div><span className={`mt-1 inline-block rounded px-2 py-0.5 text-xs ${badge(data.analysis_status)}`}>{statusLabel(data.analysis_status)}</span></div>
    </section>

    <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">历史趋势</h2><p className="mt-1 text-xs text-muted-foreground">最近 5 个完整年度及 PIT 可见的最新报告期；公告日期不得晚于 {data.as_of}。</p><div className="mt-4 overflow-x-auto"><table className="w-full min-w-[760px] text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="px-4 py-3 text-left">报告期</th><th className="px-4 py-3 text-left">公告日</th><th className="px-4 py-3 text-right">营收</th><th className="px-4 py-3 text-right">净利润</th><th className="px-4 py-3 text-right">经营现金流</th><th className="px-4 py-3 text-right">ROE</th></tr></thead><tbody>{data.history.map((row) => <tr key={`${row.report_date}-${row.announcement_date}`} className="border-t border-border/70"><td className="px-4 py-3">{String(row.report_date)}</td><td className="px-4 py-3 text-muted-foreground">{String(row.announcement_date)}</td><td className="px-4 py-3 text-right tabular-nums">{money(row.revenue)}</td><td className="px-4 py-3 text-right tabular-nums">{money(row.net_profit)}</td><td className="px-4 py-3 text-right tabular-nums">{money(row.operating_cash_flow)}</td><td className="px-4 py-3 text-right tabular-nums">{percent(row.roe)}</td></tr>)}</tbody></table></div></section>

    <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">财务质量</h2><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{Object.entries(data.feature.trends).map(([key, status]) => <div key={key} className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">{metricLabels[key] || key}</div><div className={`mt-2 inline-block rounded px-2 py-1 text-sm font-medium ${badge(status)}`}>{trendLabels[status] || status}</div></div>)}</div><div className="mt-4 flex flex-wrap gap-4 text-xs text-muted-foreground"><span>数据覆盖率：{(data.feature.data_quality.coverage * 100).toFixed(0)}%</span><span>完整年度：{data.feature.data_quality.annual_period_count}</span><span>财务特征版本：{data.financial_feature_version}</span></div>{data.data_gaps.length > 0 && <div className="mt-3 text-xs text-muted-foreground">数据缺口：{data.data_gaps.join("、")}</div>}</section>

    <section className="rounded-xl border border-border bg-card p-5"><div className="flex flex-col justify-between gap-2 sm:flex-row"><div><h2 className="font-semibold">未来 1–3 年情景</h2><p className="mt-1 text-xs text-muted-foreground">{data.forecast.disclaimer}</p></div><span className="font-mono text-xs text-muted-foreground">预测版本 {data.forecast_version}</span></div>{years.length ? <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[720px] text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="px-4 py-3 text-left">年度 / 指标</th><th className="px-4 py-3 text-right">谨慎情景</th><th className="px-4 py-3 text-right">基准情景</th><th className="px-4 py-3 text-right">乐观情景</th></tr></thead><tbody>{years.flatMap((year, index) => ([<tr key={`${year}-revenue`} className="border-t border-border/70"><td className="px-4 py-3">{year} 营业收入</td><ForecastCell scenario={bear} rowIndex={index} field="revenue" /><ForecastCell scenario={base} rowIndex={index} field="revenue" /><ForecastCell scenario={bull} rowIndex={index} field="revenue" /></tr>, <tr key={`${year}-profit`} className="border-t border-border/70"><td className="px-4 py-3">{year} 净利润</td><ForecastCell scenario={bear} rowIndex={index} field="net_profit" /><ForecastCell scenario={base} rowIndex={index} field="net_profit" /><ForecastCell scenario={bull} rowIndex={index} field="net_profit" /></tr>]))}</tbody></table></div> : <div className="mt-4 rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">当前情景预测：{statusLabel(data.forecast_status)}。{data.forecast.assumption_notes.join(" ")}</div>}<div className="mt-4 space-y-1 text-xs text-muted-foreground">{data.forecast.assumption_notes.map((note) => <div key={note}>• {note}</div>)}</div></section>

    <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">当前估值快照</h2>{valuation ? <><p className="mt-1 text-xs text-muted-foreground">数据日期 {valuation.as_of || "—"} · {valuation.source || "本地价值线快照"}</p><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">PE</div><strong className="mt-1 block text-lg tabular-nums">{number(valuation.pe)?.toFixed(2) ?? "—"}</strong></div><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">PB</div><strong className="mt-1 block text-lg tabular-nums">{number(valuation.pb)?.toFixed(2) ?? "—"}</strong></div><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">股息率</div><strong className="mt-1 block text-lg tabular-nums">{percent(valuation.dividend_yield)}</strong></div><div className="rounded-lg border border-border/70 p-4"><div className="text-xs text-muted-foreground">总市值</div><strong className="mt-1 block text-lg tabular-nums">{number(valuation.market_cap) == null ? "—" : `${number(valuation.market_cap)!.toFixed(2)} 亿`}</strong></div></div><p className="mt-4 text-xs leading-5 text-muted-foreground">{valuation.limitations?.join(" ") || "当前估值快照仅用于解释现有财务与估值口径。"}</p></> : <div className="mt-4 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">当前快照尚未写入 PE/PB 等估值口径；重新预建财务摘要后即可展示。</div>}</section>

    <section className="rounded-xl border border-border bg-card p-5"><div className="flex flex-col justify-between gap-2 sm:flex-row"><div><h2 className="font-semibold">研究档案与追问记录</h2><p className="mt-1 text-xs text-muted-foreground">财务快照按数据版本保存；公司专属问答会归档并在后续分析时作为历史上下文。</p></div><span className="text-xs text-muted-foreground">已归档 {dossier?.archive_summary.chat_entry_count ?? 0} 条</span></div>{dossier?.chat_entries.length ? <div className="mt-4 space-y-3">{dossier.chat_entries.slice(-12).map((entry) => <article key={entry.id} className={`rounded-lg border p-3 text-sm ${entry.role === "user" ? "border-primary/30 bg-primary/[0.035]" : "border-border bg-muted/20"}`}><div className="flex justify-between gap-3 text-xs text-muted-foreground"><span>{entry.role === "user" ? "研究问题" : "财报研究员结论"}</span><time>{new Date(entry.created_at).toLocaleString("zh-CN", { hour12: false })}</time></div><p className="mt-2 whitespace-pre-wrap leading-6">{entry.content}</p></article>)}</div> : <div className="mt-4 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">尚无公司专属追问记录。通过财报 Agent 提问后，问题与结论会自动归档到这里。</div>}</section>

    <section className="rounded-xl border border-border bg-card p-5"><div className="flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><Bot className="h-5 w-5 text-primary" /><h2 className="font-semibold">财报研究员解读</h2></div>{analysis?.analysis_metadata?.citation_stats ? <span className="text-xs text-muted-foreground">已绑定依据 {analysis.analysis_metadata.citation_stats.resolved_source_keys ?? 0} 条</span> : null}</div>{data.analysis_status === "CONFIGURATION_REQUIRED" ? <div className="mt-4 rounded-lg bg-muted/40 p-5 text-sm text-muted-foreground">历史财务和情景预测已正常生成。请先在 <Link to="/settings/researchers" className="text-primary underline">研究员设置</Link> 中配置并启用“财报研究员”。</div> : data.analysis_status === "FAILED" ? <div className="mt-4 rounded-lg border border-danger/30 bg-danger/10 p-5 text-sm text-danger">财报研究员分析失败：{data.agent_error || "未知错误"}。历史财务和情景预测不受影响，可重新分析。</div> : analysis ? <div className="mt-4 space-y-5"><p className="leading-7">{analysis.executive_summary}</p><div className="grid gap-4 lg:grid-cols-2"><div className="rounded-lg bg-muted/30 p-4"><h3 className="text-sm font-medium">主要优势</h3><ul className="mt-2 space-y-2 text-sm text-muted-foreground">{analysis.financial_strengths.map((item) => <li key={item}>• {item}</li>)}</ul></div><div className="rounded-lg bg-muted/30 p-4"><h3 className="text-sm font-medium">主要风险</h3><ul className="mt-2 space-y-2 text-sm text-muted-foreground">{analysis.financial_risks.map((item) => <li key={item}>• {item}</li>)}</ul></div></div><div><h3 className="text-sm font-medium">研究观点</h3><div className="mt-2 space-y-2">{analysis.claims.map((claim, index) => <div key={`${claim.type}-${index}`} className="rounded-md border border-border/70 px-3 py-2 text-sm"><div className="flex gap-3"><span className="shrink-0 text-xs font-medium text-primary">{claimTypeLabel(claim.type)}</span><span>{claim.statement}</span></div><ClaimCitations claim={claim} /></div>)}</div></div><div><h3 className="text-sm font-medium">持续跟踪</h3><p className="mt-2 text-sm text-muted-foreground">{analysis.key_metrics_to_monitor.join("、") || "暂无"}</p></div></div> : <div className="mt-4 rounded-lg bg-muted/40 p-5 text-sm text-muted-foreground">历史财务与情景预测已准备，可点击“重新分析”调用财报研究员。页面打开时不会自动调用模型。</div>}</section>
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
