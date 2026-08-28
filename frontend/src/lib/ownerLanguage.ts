/**
 * Owner-facing labels for persisted research statuses.  Internal codes stay
 * available in the API/audit layer, but must not leak into the main reading
 * flow as if they were investment conclusions.
 */
const labels: Record<string, string> = {
  ACTIVE: "当前生效",
  READY: "资料可用",
  PARTIAL: "资料部分缺失",
  UNKNOWN: "资料不足，暂无法判断",
  MISSING: "资料不足，暂无法判断",
  STALE: "资料已过期，需要更新",
  CREATED: "已建立",
  NOT_CREATED: "尚未建立",
  AI_PROVISIONAL: "AI 初步结论，待人工复核",
  HUMAN_CONFIRMED: "已人工确认",
  LEGACY_UNVERIFIED: "历史资料，尚未核验",
  PIT_LIMITED: "历史可见性受限",
  SUPPORTED: "现有资料支持",
  INFERENCE: "研究推断，仍待验证",
  DEEPLY_UNDERVALUED: "深度低估",
  UNDERVALUED: "低估关注",
  FAIR: "合理观察",
  OVERVALUED: "估值偏高",
  DEEPLY_OVERVALUED: "明显偏高",
  INSUFFICIENT_DATA: "资料不足，暂无法判断",
};

export function ownerStatus(value?: string | null, fallback = "资料不足，暂无法判断") {
  const key = String(value || "").trim().toUpperCase();
  return labels[key] || fallback;
}

export function ownerAuthority(value?: string | null) {
  return ownerStatus(value, "核心逻辑尚未建立");
}
