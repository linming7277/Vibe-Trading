import type { ReactNode } from "react";

export type SourceReference = {
  source_key?: string;
  status?: string;
  source_type?: string;
  source?: string;
  source_id?: string;
  source_snapshot_id?: string | null;
  source_hash?: string | null;
  field?: string | null;
  metric?: string | null;
  period?: string | null;
  value?: string | number | null;
  unit?: string | null;
  data_as_of?: string | null;
  scenario?: string | null;
  forecast_year?: string | null;
  forecast_version?: string | null;
  profile_role?: string | null;
};

const sourceTypeLabels: Record<string, string> = {
  FINANCIAL_HISTORY: "财务数据",
  FINANCIAL_FEATURE: "财务指标",
  DETERMINISTIC_FORECAST: "情景预测",
  TDX_BUSINESS_PROFILE: "经营资料",
  AGENT_FINANCIAL: "财务研究",
  AGENT_BUSINESS: "经营研究",
  HUMAN: "人工研究",
  SYSTEM: "系统整理",
  UNKNOWN: "已保存研究资料",
};

const sourceNameLabels: Record<string, string> = {
  "TDX PIT 财务": "通达信财务资料",
  "财务特征引擎": "财务指标计算",
  "Forecast Engine": "情景预测计算",
  "通达信经营资料": "通达信经营资料",
};

const metricLabels: Record<string, string> = {
  revenue: "营业收入",
  net_profit: "净利润",
  operating_cash_flow: "经营现金流",
  roe: "ROE",
  debt_ratio: "资产负债率",
  gross_margin: "毛利率",
  net_margin: "净利率",
  capex: "资本开支",
  revenue_change_percent: "营业收入同比变化",
  net_profit_change_percent: "净利润同比变化",
  operating_cash_flow_change_percent: "经营现金流同比变化",
  roe_change_percent: "ROE 变化",
  main_business: "主营业务",
  main_products: "主要产品",
  business_scope: "经营范围",
  company_description: "公司介绍",
};

const claimTypeLabels: Record<string, string> = {
  FACT: "事实依据",
  INFERENCE: "分析判断",
  FORECAST: "情景预测",
  UNKNOWN: "暂无足够资料",
};

const thesisStatusLabels: Record<string, string> = {
  FORMING: "正在形成",
  STRENGTHENING: "逻辑正在增强",
  UNCHANGED: "逻辑基本稳定",
  WEAKENING: "逻辑正在减弱",
  FALSIFIED: "核心逻辑已失效",
};

const entryStatusLabels: Record<string, string> = {
  HIGH_ATTENTION: "价格条件高度值得关注",
  ATTENTION: "价格条件值得关注",
  WATCH: "价格条件继续观察",
  WAIT: "当前价格条件等待",
  BLOCKED: "当前研究条件存在阻断",
};

const exitStatusLabels: Record<string, string> = {
  CRITICAL_REVIEW: "需要立即复核核心研究逻辑",
  REVIEW: "需要重点复核",
  WATCH: "存在复核事项",
  NORMAL: "当前暂无明显复核压力",
};

export function claimTypeLabel(value?: string | null) { return claimTypeLabels[String(value || "")] || "研究观点"; }
export function thesisStatusLabel(value?: string | null) { return thesisStatusLabels[String(value || "")] || "尚未建立"; }
export function entryStatusLabel(value?: string | null) { return entryStatusLabels[String(value || "")] || "资料不足"; }
export function exitStatusLabel(value?: string | null) { return exitStatusLabels[String(value || "")] || "资料不足"; }
export function confidenceLabel(value?: string | null) {
  const labels: Record<string, string> = { HIGH: "高", MEDIUM: "中等", LOW: "低" };
  return labels[String(value || "")] || "—";
}

/** Keeps persisted research unchanged while removing internal labels from owner-facing rich text. */
export function ownerFacingText(value?: string | null) {
  return String(value || "")
    .replace(/Company Thesis/gi, "公司核心逻辑")
    .replace(/\bthesis\b/gi, "公司核心逻辑")
    .replace(/\bEvidence\b/gi, "研究证据")
    .replace(/\bReview\b/gi, "研究复核")
    .replace(/\bClaims?\b/gi, "研究观点")
    .replace(/该研究观点/gi, "该研究证据");
}

function sourceTypeLabel(citation: SourceReference) {
  return sourceTypeLabels[String(citation.source_type || "")] || "已保存研究资料";
}

function sourceName(citation: SourceReference) {
  const name = String(citation.source || "").trim();
  return sourceNameLabels[name] || name || sourceTypeLabel(citation);
}

export function metricLabel(value?: string | null) {
  const key = String(value || "").trim();
  return metricLabels[key] || "相关指标";
}

function fieldLabel(citation: SourceReference) {
  return metricLabel(citation.field || citation.metric);
}

function rawValue(citation: SourceReference) {
  if (citation.value == null || citation.value === "") return "—";
  if (citation.unit === "CNY" && typeof citation.value === "number") return `${(citation.value / 100_000_000).toFixed(2)} 亿`;
  return `${citation.value}${citation.unit ? ` ${citation.unit}` : ""}`;
}

function metadataRows(citation: SourceReference) {
  return [
    ["来源标识", citation.source_key],
    ["资料记录", citation.source_id],
    ["快照标识", citation.source_snapshot_id],
    ["校验标识", citation.source_hash ? `${citation.source_hash.slice(0, 12)}…` : ""],
    ["预测版本", citation.forecast_version],
  ].filter(([, value]) => Boolean(value));
}

function InfoItem({ label, children }: { label: string; children: ReactNode }) {
  return <div><div className="text-muted-foreground">{label}</div><div className="mt-0.5 break-words text-foreground">{children}</div></div>;
}

/** A single owner-facing presentation for financial, business and thesis research sources. */
export function SourceReferenceCard({
  citations,
  researchContent,
  emptyMessage = "当前研究观点尚未绑定可展示的依据。",
}: {
  citations?: SourceReference[] | null;
  researchContent?: string;
  emptyMessage?: string;
}) {
  const items = citations || [];
  if (!items.length) return <p className="mt-2 text-xs text-muted-foreground">{emptyMessage}</p>;
  return <details className="mt-2 rounded-lg border border-border/70 bg-background"><summary className="cursor-pointer px-3 py-2 text-xs font-medium text-primary">查看依据（{items.length}）</summary><div className="space-y-2 border-t border-border/70 p-3 text-xs">{items.map((citation, index) => {
    const key = citation.source_key || citation.source_id || `${citation.source_type || "source"}-${index}`;
    if (citation.status === "UNRESOLVED") return <article key={key} className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-amber-800 dark:text-amber-200"><strong>依据暂无法解析</strong><p className="mt-1">该观点保留，但当前无法读取对应原始资料。</p></article>;
    const auditRows = metadataRows(citation);
    return <article key={key} className="rounded-md border border-border/70 bg-muted/20 p-3"><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{researchContent ? <InfoItem label="研究内容"><span>{ownerFacingText(researchContent)}</span></InfoItem> : null}<InfoItem label="来源类型">{sourceTypeLabel(citation)}</InfoItem><InfoItem label="来源名称">{sourceName(citation)}</InfoItem><InfoItem label="数据日期">{citation.data_as_of || "—"}</InfoItem><InfoItem label="指标 / 字段">{fieldLabel(citation)}</InfoItem><InfoItem label="原始值">{rawValue(citation)}</InfoItem>{citation.period ? <InfoItem label="时间">{citation.period}</InfoItem> : null}{citation.scenario ? <InfoItem label="情景">{citation.scenario}</InfoItem> : null}{citation.forecast_year ? <InfoItem label="预测年度">{citation.forecast_year}</InfoItem> : null}{citation.profile_role ? <InfoItem label="资料期">{citation.profile_role === "PREVIOUS" ? "对比期" : "当前期"}</InfoItem> : null}</div>{citation.source_type === "DETERMINISTIC_FORECAST" ? <p className="mt-2 rounded bg-amber-500/10 px-2 py-1.5 text-amber-800 dark:text-amber-200">情景预测用于研究推演，不是已发生的历史事实。</p> : null}{auditRows.length ? <details className="mt-2"><summary className="cursor-pointer text-[11px] text-muted-foreground">查看审计信息</summary><div className="mt-1 grid gap-1 rounded bg-muted/50 p-2 text-[11px] text-muted-foreground">{auditRows.map(([label, value]) => <div key={label}>{label}：{value}</div>)}</div></details> : null}</article>;
  })}</div></details>;
}
