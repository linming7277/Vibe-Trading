export interface ReturnContext {
  from: string;
  fromLabel?: string;
  sectorCode?: string;
  sectorName?: string;
}

export function safeInternalPath(value: string | null | undefined, fallback = "/value") {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return fallback;
  try {
    const parsed = new URL(value, "http://hengzhi.local");
    if (parsed.origin !== "http://hengzhi.local") return fallback;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

export function inferReturnLabel(path: string) {
  if (path.startsWith("/market/ranks")) return "市场榜单";
  if (path.startsWith("/market/sectors")) return "板块行情";
  if (path.startsWith("/screener")) return "股票筛选";
  if (path.startsWith("/value/research") || path.startsWith("/value/operations")) return "研究运行";
  if (path.startsWith("/value/opportunities") || path.startsWith("/value/valuation") || path.startsWith("/value/timing")) return "机会与风险";
  if (path.startsWith("/value/focus") || path.startsWith("/value/plans")) return "低估龙头池";
  if (path.startsWith("/value/leaders") || path.startsWith("/value")) return "行业龙头";
  return "上一级";
}

export function companyResearchPath(symbol: string, context: ReturnContext, tab?: string) {
  const params = new URLSearchParams();
  params.set("from", safeInternalPath(context.from));
  if (context.fromLabel) params.set("from_label", context.fromLabel);
  if (context.sectorCode) params.set("sector_code", context.sectorCode);
  if (context.sectorName) params.set("sector_name", context.sectorName);
  if (tab) params.set("tab", tab);
  return `/company/CN/${encodeURIComponent(symbol)}?${params.toString()}`;
}

export function financialAnalysisPath(symbol: string, options: {
  asOf?: string | null;
  companyPath: string;
  parentFrom?: string;
  parentLabel?: string;
}) {
  const params = new URLSearchParams();
  if (options.asOf) params.set("as_of", options.asOf);
  params.set("from", safeInternalPath(options.companyPath, `/company/CN/${encodeURIComponent(symbol)}?tab=financial`));
  params.set("from_label", "公司详情");
  if (options.parentFrom) params.set("parent_from", safeInternalPath(options.parentFrom));
  if (options.parentLabel) params.set("parent_label", options.parentLabel);
  return `/value/company/${encodeURIComponent(symbol)}/financial?${params.toString()}`;
}
