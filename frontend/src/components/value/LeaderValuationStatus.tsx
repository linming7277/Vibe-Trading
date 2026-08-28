import type { ValuePriceZones } from "@/lib/api";

export type LeaderValuationStatus =
  | "DEEPLY_UNDERVALUED"
  | "UNDERVALUED"
  | "FAIR"
  | "OVERVALUED"
  | "DEEPLY_OVERVALUED"
  | "INSUFFICIENT_DATA";

const statusMeta: Record<LeaderValuationStatus, { label: string; title: string; tone: string }> = {
  DEEPLY_UNDERVALUED: {
    label: "深度低估",
    title: "历史估值处于很低位置，值得进一步做价值研究。",
    tone: "border-amber-500/35 bg-amber-500/10 text-amber-800 dark:text-amber-200",
  },
  UNDERVALUED: {
    label: "低估关注",
    title: "历史估值处于较低位置，值得进一步研究。",
    tone: "border-orange-500/35 bg-orange-500/10 text-orange-800 dark:text-orange-200",
  },
  FAIR: {
    label: "合理观察",
    title: "当前估值处于相对正常区间，继续结合公司研究观察。",
    tone: "border-blue-500/35 bg-blue-500/10 text-blue-800 dark:text-blue-200",
  },
  OVERVALUED: {
    label: "估值偏高",
    title: "历史估值处于较高位置，需要更谨慎地评估价格。",
    tone: "border-red-500/35 bg-red-500/10 text-red-700 dark:text-red-300",
  },
  DEEPLY_OVERVALUED: {
    label: "明显偏高",
    title: "历史估值处于很高位置，价格需要重点复核。",
    tone: "border-rose-600/40 bg-rose-600/10 text-rose-800 dark:text-rose-200",
  },
  INSUFFICIENT_DATA: {
    label: "数据不足",
    title: "尚无足够的历史估值资料，不能判断当前价值状态。",
    tone: "border-border bg-muted text-muted-foreground",
  },
};

const deepCheap = new Set(["DEEPLY_UNDERVALUED", "VERY_CHEAP", "DEEP_CHEAP"]);
const cheap = new Set(["UNDERVALUED", "CHEAP"]);
const fair = new Set(["FAIR", "NORMAL"]);
const expensive = new Set(["OVERVALUED", "EXPENSIVE"]);
const deepExpensive = new Set(["DEEPLY_OVERVALUED", "VERY_EXPENSIVE", "DEEP_EXPENSIVE"]);

/**
 * Presentation-only mapping. It deliberately does not create a combined
 * score or reinterpret Entry Research; leader quality and price status stay
 * separate dimensions.
 */
export function deriveLeaderValuationStatus(zones: ValuePriceZones | null | undefined): LeaderValuationStatus {
  if (!zones) return "INSUFFICIENT_DATA";
  const historical = String(zones.historical_valuation?.historical_valuation_status || "").toUpperCase();
  const fallback = String(zones.valuation?.status || "").toUpperCase();
  const status = historical || fallback;
  if (deepCheap.has(status)) return "DEEPLY_UNDERVALUED";
  if (cheap.has(status)) return "UNDERVALUED";
  if (fair.has(status)) return "FAIR";
  if (deepExpensive.has(status)) return "DEEPLY_OVERVALUED";
  if (expensive.has(status)) return "OVERVALUED";
  return "INSUFFICIENT_DATA";
}

export function leaderValuationStatusLabel(status: LeaderValuationStatus) {
  return statusMeta[status].label;
}

export function LeaderValuationStatusBadge({
  status,
  className = "",
}: {
  status: LeaderValuationStatus;
  className?: string;
}) {
  const meta = statusMeta[status];
  return <span title={meta.title} className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${meta.tone} ${className}`}><span aria-hidden="true">●</span>{meta.label}</span>;
}
