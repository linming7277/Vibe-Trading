import { type ReactNode, useEffect, useState } from "react";
import { AlertTriangle, Database, Loader2, RefreshCw } from "lucide-react";
import { api, type CapitalAllocationFacts, type CapitalAllocationRatio, type CapitalAllocationResearch, type CompanyActionResponse } from "@/lib/api";
import { formatNumber } from "@/components/workspace/WorkspaceUI";

function amount(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${formatNumber(value / 100_000_000, 2)} 亿元`;
}

function percent(value: CapitalAllocationRatio | null | undefined) {
  return value?.value == null ? "未知" : `${formatNumber(value.value, 2)}%`;
}

function statusText(value: string) {
  return value === "READY" ? "已具备" : value === "PARTIAL" ? "部分具备" : "暂不可用";
}

export function CapitalAllocationFactsPanel({ stockCode, asOf }: { stockCode: string; asOf?: string }) {
  const [data, setData] = useState<CapitalAllocationFacts | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    api.getCapitalAllocationFacts(stockCode, asOf)
      .then((value) => { if (active) setData(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [asOf, stockCode]);

  if (loading) return <section className="rounded-xl border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在读取已缓存的资本配置事实…</section>;
  if (error || !data) return <section className="rounded-xl border bg-card p-5"><h2 className="font-semibold">资本配置</h2><p className="mt-2 text-sm text-muted-foreground">{error || "当前没有可用的资本配置事实。"}</p></section>;
  const timeline = data.financial_timeline.items;
  return <div className="space-y-4">
    <section className="rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">资本配置</h2><p className="mt-1 text-sm text-muted-foreground">只展示已保存的财务、分红和股本事实，不对管理层或资本配置质量作评价。</p></div><div className="flex flex-wrap gap-2 text-xs"><span className="rounded border px-2 py-1">完整度：{statusText(data.allocation_completeness)}</span><span className="rounded border px-2 py-1">财务 PIT：严格</span>{data.pit_status === "PIT_LIMITED" ? <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-amber-700">公司行动 PIT：受限</span> : null}</div></div>
      {data.pit_status === "PIT_LIMITED" ? <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 p-3 text-xs leading-5 text-muted-foreground"><AlertTriangle className="mr-1 inline h-3.5 w-3.5 text-amber-600" />分红与股本资料只有事件/除权日期和当前缓存时间，无法证明它在任一历史日期当时已经可见；因此只作历史事实展示，不作严格历史回放。</p> : null}
    </section>

    <CapitalAllocationResearchCard stockCode={stockCode} asOf={asOf} />
    <CompanyActionTimeline stockCode={stockCode} asOf={asOf} />

    <FactTable title="公司赚到的现金、再投资与负债" note="年度数据只使用公告日不晚于查询日期的通达信专业财务。金额单位：亿元。">
      <table className="w-full min-w-[900px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th>年度</th><th>经营现金流</th><th>资本开支</th><th>资本开支 / OCF</th><th>现金余额</th><th>总负债</th><th>负债率</th><th>带息债务率</th></tr></thead><tbody className="divide-y">{timeline.map((row) => <tr key={row.report_date}><td><strong>{row.year}</strong><small>公告 {row.announcement_date}</small></td><td>{amount(row.operating_cash_flow)}</td><td>{amount(row.capex)}</td><td>{percent(row.capex_to_ocf)}</td><td>{amount(row.cash_and_equivalents)}</td><td>{amount(row.liabilities)}</td><td>{row.debt_ratio == null ? "—" : `${formatNumber(row.debt_ratio, 2)}%`}</td><td>{row.interest_bearing_debt_ratio == null ? "—" : `${formatNumber(row.interest_bearing_debt_ratio, 2)}%`}</td></tr>)}</tbody></table>
      {!timeline.length ? <p className="p-4 text-sm text-muted-foreground">尚无可见年度专业财务记录。</p> : null}
    </FactTable>

    <FactTable title="已确认的分红历史" note="仅标准化通达信中已确认的 Type=1 现金分红字段；“每10股”来自通达信原始口径。支付率仅在能按事件日期连接到最近可见年度且有股本数据时显示。">
      <table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th>事件日期</th><th>现金分红（每10股）</th><th>估算现金总额</th><th>关联年度</th><th>分红 / 净利润</th><th>分红 / OCF</th></tr></thead><tbody className="divide-y">{data.dividend_history.events.map((row, index) => <tr key={`${row.event_date}-${index}`}><td>{row.event_date}</td><td>{formatNumber(row.cash_dividend_per_ten_shares, 4)} 元</td><td>{amount(row.cash_dividend_total)}</td><td>{row.linked_annual_report_date || "未能连接"}</td><td>{percent(row.dividend_to_net_profit)}</td><td>{percent(row.dividend_to_ocf)}</td></tr>)}</tbody></table>
      {!data.dividend_history.events.length ? <p className="p-4 text-sm text-muted-foreground">当前缓存中没有可可靠标准化的现金分红事件。</p> : null}
      {data.dividend_history.raw_unknown_fields.length ? <p className="border-t px-4 py-3 text-xs text-muted-foreground">另有 {data.dividend_history.raw_unknown_fields.length} 条原始分红字段无法确认口径，已保留为“未知原始字段”，未计入任何支付率。</p> : null}
    </FactTable>

    <FactTable title="股本变化" note="总股本变化默认不说明原因；只有在公司行动层中与送股/配股的日期和比例同时匹配时，才显示已匹配的事实原因。其余不能归因为增发、回购、激励或转债转股。">
      <table className="w-full min-w-[680px] text-left text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th>事件日期</th><th>变动前总股本</th><th>变动后总股本</th><th>变动幅度</th><th>原因</th></tr></thead><tbody className="divide-y">{data.share_capital_history.events.map((row, index) => <tr key={`${row.event_date}-${index}`}><td>{row.event_date}</td><td>{formatNumber(row.total_shares_before, 0)} 股</td><td>{formatNumber(row.total_shares_after, 0)} 股</td><td>{percent(row.change_pct)}</td><td>{row.change_reason === "UNKNOWN" ? "未知，需进一步核验" : `已匹配：${actionLabel(row.change_reason)}`}</td></tr>)}</tbody></table>
      {!data.share_capital_history.events.length ? <p className="p-4 text-sm text-muted-foreground">当前缓存没有可识别的总股本变动事件。</p> : null}
    </FactTable>

    <section className="rounded-xl border bg-card p-5"><h3 className="font-semibold">当前还无法解释</h3><p className="mt-1 text-sm text-muted-foreground">这些不是负面结论，而是本事实层尚缺少可靠来源或结构化处理的项目。</p><div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{data.data_gaps.map((item) => <article key={item.item} className="rounded-lg border p-3"><div className="flex items-center justify-between gap-2"><strong className="text-sm">{gapLabel(item.item)}</strong><span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{gapStatus(item.status)}</span></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{item.reason}</p></article>)}</div></section>

    <section className="rounded-xl border bg-card p-4 text-xs text-muted-foreground"><Database className="mr-1 inline h-3.5 w-3.5" />来源：通达信专业财务（公告日 PIT）及已缓存的单股分红/股本详情。公式版本 {data.formula_version}；本页请求不会刷新通达信或下载公告。</section>
  </div>;
}

function CapitalAllocationResearchCard({ stockCode, asOf }: { stockCode: string; asOf?: string }) {
  const [data, setData] = useState<CapitalAllocationResearch | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    api.getCapitalAllocationResearch(stockCode, asOf)
      .then((value) => { if (active) setData(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [asOf, stockCode]);
  if (loading) return <section className="rounded-xl border bg-card p-5 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在基于已缓存事实整理资本配置研究…</section>;
  if (error || !data) return <section className="rounded-xl border bg-card p-5"><h3 className="font-semibold">资本配置研究</h3><p className="mt-2 text-sm text-muted-foreground">{error || "当前研究资料暂不可用。"}</p></section>;
  const rows = [
    ["reinvestment", "再投资"], ["dividend", "分红"], ["debt_management", "债务"], ["equity_dilution", "股本"], ["cash_management", "现金"], ["buyback", "回购"], ["m_and_a", "并购"],
  ] as const;
  return <section className="rounded-xl border border-primary/25 bg-card p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-medium text-primary">资本配置研究</div><h3 className="mt-1 text-lg font-semibold">现有事实支持什么，还需要观察什么</h3><p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">{data.capital_allocation_summary}</p></div><span className="rounded border px-2 py-1 text-xs text-muted-foreground">不含资本配置总分</span></div><div className="mt-4 grid gap-3 lg:grid-cols-2">{rows.map(([key, label]) => { const item = data.dimensions[key]; return <article key={key} className="rounded-lg border p-4"><div className="flex items-center justify-between gap-3"><h4 className="font-medium">{label}</h4><div className="flex gap-1"><span className="rounded bg-muted px-2 py-0.5 text-xs">{researchStatus(item.status)}</span><span className={directionClass(item.direction)}>{directionLabel(item.direction)}</span></div></div><p className="mt-3 text-sm leading-6">{item.observation}</p><div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"><span>事实来源 {item.fact_refs.length} 条</span><span>{item.pit_status === "PIT_LIMITED" ? "公司行动 PIT 受限" : "财务 PIT 严格"}</span>{item.signal ? <span>观察信号：{signalLabel(item.signal)}</span> : null}</div>{item.data_gaps.length ? <p className="mt-3 border-t pt-3 text-xs leading-5 text-muted-foreground">仍缺：{item.data_gaps.map(gapLabel).join("、")}</p> : null}</article>; })}</div>{data.cautions.length ? <div className="mt-4 rounded-lg border border-amber-500/25 bg-amber-500/5 p-3 text-sm"><strong>需要继续核验</strong><ul className="mt-2 space-y-1 text-muted-foreground">{data.cautions.map((item) => <li key={item.dimension}>• {dimensionLabel(item.dimension)}：{item.observation}</li>)}</ul></div> : null}<p className="mt-4 text-xs text-muted-foreground">研究版本 {data.formula_version} · 仅从资本配置事实层读取，不调用模型、不修改核心逻辑或风险研究。</p></section>;
}

function CompanyActionTimeline({ stockCode, asOf }: { stockCode: string; asOf?: string }) {
  const [data, setData] = useState<CompanyActionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  const load = () => { setLoading(true); setError(""); return api.getCompanyActions(stockCode, asOf).then(setData).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoading(false)); };
  useEffect(() => { void load(); }, [asOf, stockCode]); // eslint-disable-line react-hooks/exhaustive-deps
  const prepare = async () => { setPreparing(true); try { await api.prepareCompanyActions(stockCode); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setPreparing(false); } };
  return <section className="rounded-xl border bg-card p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold">公司行动</h3><p className="mt-1 text-sm text-muted-foreground">分红、送股、配股和股本变化的事件时间线。回购、定增、转债和激励没有正式来源时会如实保留为空。</p></div><button onClick={() => void prepare()} disabled={preparing} className="inline-flex items-center gap-1.5 rounded-md border bg-card px-2.5 py-1.5 text-xs font-medium disabled:opacity-50"><RefreshCw className={preparing ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />从本地缓存准备事件</button></div>{loading ? <p className="mt-4 text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在读取已保存事件…</p> : error ? <p className="mt-4 text-sm text-destructive">{error}</p> : <><p className="mt-3 rounded-lg bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">准备操作只整理当前已缓存的通达信详情，不会联网、刷新客户端或调用模型。分红与股本事件目前没有公告日，因此均标记为 PIT 受限。</p>{data?.events.length ? <div className="mt-4 space-y-3">{data.events.map((item) => <article key={item.id} className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-start"><div className="min-w-28 text-sm"><strong>{item.event_date || item.announcement_date || "日期未知"}</strong><div className="mt-1 text-xs text-muted-foreground">{item.pit_status === "PIT_LIMITED" ? "PIT 受限" : "严格 PIT"}</div></div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><strong>{actionLabel(item.event_type)}</strong><span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{stageLabel(item.event_stage)}</span></div><p className="mt-2 text-sm">{item.summary}</p><p className="mt-2 text-xs text-muted-foreground">{actionDetails(item)} · 来源 {item.source_refs.length} 条</p></div></article>)}</div> : <div className="mt-4 rounded-lg border border-dashed p-5 text-sm text-muted-foreground">尚未准备公司行动事件。可点击右上角按钮，将当前已缓存的分红和股本原始资料整理为可追溯事件。</div>}<div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{Object.entries(data?.capabilities || {}).map(([key, value]) => <div key={key} className="rounded border p-2 text-xs"><span className="font-medium">{actionLabel(key)}</span><span className="float-right text-muted-foreground">{capabilityLabel(value.status)}</span><p className="mt-1 text-muted-foreground">{value.source}</p></div>)}</div></>}</section>;
}

function FactTable({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return <section className="rounded-xl border bg-card"><div className="border-b px-5 py-4"><h3 className="font-semibold">{title}</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">{note}</p></div><div className="overflow-x-auto capital-fact-table">{children}</div></section>;
}

function gapLabel(key: string) {
  const labels: Record<string, string> = { investment_cash_flow: "投资活动现金流", financing_cash_flow: "筹资活动现金流", buyback: "回购与注销", m_and_a: "并购与商誉", equity_financing_reason: "股本变动原因", debt_maturity: "债务到期结构", debt_cost: "融资成本" };
  return labels[key] || key;
}

function gapStatus(status: string) { return status === "MISSING" ? "缺少数据" : status === "RAW_NOT_STRUCTURED" ? "原始资料未结构化" : "尚未收集"; }
function researchStatus(status: string) { return status === "SUPPORTED" ? "现有事实支持" : status === "PARTIAL" ? "需要继续观察" : "资料不足"; }
function directionLabel(direction: string) { return direction === "POSITIVE" ? "改善迹象" : direction === "CAUTION" ? "需要核验" : direction === "NEUTRAL" ? "暂无明确方向" : "暂不判断"; }
function directionClass(direction: string) { return direction === "CAUTION" ? "rounded bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700" : direction === "POSITIVE" ? "rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700" : "rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground"; }
function dimensionLabel(key: string) { const labels: Record<string, string> = { reinvestment: "再投资", dividend: "分红", debt_management: "债务", equity_dilution: "股本", cash_management: "现金", buyback: "回购", m_and_a: "并购" }; return labels[key] || key; }
function signalLabel(signal: string) { const labels: Record<string, string> = { EFFECTIVE_SIGNAL: "后续经营改善迹象", MIXED_SIGNAL: "结果不一致", PRESSURE_SIGNAL: "后续结果承压", INSUFFICIENT_HISTORY: "历史不足", OUTCOME_NOT_YET_OBSERVABLE: "结果尚不可观察" }; return labels[signal] || signal; }
function actionLabel(type: string) { const labels: Record<string, string> = { CASH_DIVIDEND: "现金分红", BONUS_SHARE: "送股", RIGHTS_ISSUE: "配股", SHARE_REPURCHASE: "股份回购", SHARE_CANCELLATION: "股份注销", PRIVATE_PLACEMENT: "定增", CONVERTIBLE_BOND: "可转债", EQUITY_INCENTIVE: "股权激励", SHARE_CAPITAL_CHANGE: "股本变化" }; return labels[type] || type; }
function stageLabel(stage: string) { const labels: Record<string, string> = { PROPOSED: "预案", APPROVED: "已通过", IMPLEMENTED: "实施", COMPLETED: "完成", CANCELLED: "取消", REPORTED_EFFECTIVE_DATE: "已记录生效日" }; return labels[stage] || stage; }
function capabilityLabel(status: string) { return status === "READY" ? "可用" : status === "PARTIAL" ? "部分可用" : status === "RAW_NOT_STRUCTURED" ? "原文未结构化" : status === "MISSING" ? "缺少来源" : "未收集"; }
function actionDetails(item: { event_type: string; payload: Record<string, unknown>; shares_before: number | null; shares_after: number | null; reason: string | null }) { const payload = item.payload || {}; if (item.event_type === "CASH_DIVIDEND") return `每10股 ${formatNumber(Number(payload.cash_per_10_shares), 4)} 元`; if (item.event_type === "BONUS_SHARE") return `每10股送 ${formatNumber(Number(payload.share_bonus_per_10_shares), 4)} 股`; if (item.event_type === "RIGHTS_ISSUE") return `每10股配 ${formatNumber(Number(payload.rights_issue_per_10_shares), 4)} 股，配股价 ${formatNumber(Number(payload.price))} 元`; if (item.event_type === "SHARE_CAPITAL_CHANGE") return `总股本 ${formatNumber(item.shares_before, 0)} → ${formatNumber(item.shares_after, 0)}；原因：${item.reason === "UNKNOWN" ? "未知" : actionLabel(item.reason || "")}`; return "已保存事件事实"; }
