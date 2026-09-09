"""Evidence Semantic Guard V1：证据键 → metric_type → 允许表达语义的一致性校验。

纯确定性（0 LLM）：在结构校验之后运行，检查——
- 语义替换禁令（§三）：背景指数≠宽度/涨跌家数/赚钱效应；波动≠方向；宏观轴≠必涨必跌；
- 行业理由来源标签（§五）：按引用证据的 metric_type 确定性推导 PRICE_MOMENTUM /
  MACRO_ALIGNMENT / MIXED；
- Invalidation 与 Outcome 分离（§七/§十/§十二）：失效条件只允许"cutoff 后新信息使
  前提改变"，禁止 T 日结果评价语句；
- 反向证据语义（§九）：与主判断相反的既有信息合法，不算 invalidation。

对既有留档：只产生衍生 semantic_validation_result 与修正渲染件，不改原始预测输出。
"""

from __future__ import annotations

import re
from typing import Any

# v1.1.0: CNY 官方中间价语义守则（非市场收盘/非CNH/非资金流；禁行业传导）
SEMANTIC_GUARD_VERSION = "forecast-semantic-guard-v1.1.0"

# §二 metric_type 投影（由完整键模式确定性推导）。
_METRIC_RULES: tuple[tuple[str, str], ...] = (
    (r"^MKT_BENCH_RET_\d+D$", "MARKET_RETURN"),
    (r"^MKT_BENCH_VOL_20D$", "MARKET_VOLATILITY"),
    (r"^MKT_BENCH_AMOUNT_RATIO_20D$", "MARKET_AMOUNT"),
    (r"^MKT_BREADTH_20D$", "MARKET_BREADTH"),
    (r"^MKT_RISK_APPETITE$", "RISK_APPETITE"),
    (r"^MKT_REF_.+_RET_5D$", "BACKGROUND_INDEX_RETURN"),
    (r"^MACRO_AXIS_GROWTH$", "MACRO_GROWTH"),
    (r"^MACRO_AXIS_INFLATION$", "MACRO_INFLATION"),
    (r"^MACRO_AXIS_LIQUIDITY$", "MACRO_LIQUIDITY"),
    (r"^MACRO_AXIS_CREDIT$", "MACRO_CREDIT"),
    (r"^MACRO_AXIS_FINANCIAL_CONDITIONS$", "MACRO_FINANCIAL_CONDITION"),
    (r"^MACRO_REGIME_LATEST$", "MACRO_REGIME"),
    (r"^MACRO_EVENT_", "MACRO_EVENT"),
    (r"^MACRO_COVERAGE_GROUPS$", "MACRO_COVERAGE"),
    (r"^IND_.+_RET_5D$", "INDUSTRY_RETURN"),
    (r"^IND_.+_RELATIVE_5D$", "INDUSTRY_RELATIVE_RETURN"),
    (r"^IND_.+_TC$", "INDUSTRY_TREND"),
    (r"^IND_.+_VOL_20D$", "INDUSTRY_VOLATILITY"),
    (r"^IND_.+_MACRO_STANCE$", "INDUSTRY_MACRO_STANCE"),
    (r"^XCM_USDCNY_MID", "FX_OFFICIAL_MID"),
)


def metric_type(evidence_key: str) -> str:
    for pattern, metric in _METRIC_RULES:
        if re.match(pattern, evidence_key):
            return metric
    return "UNKNOWN"


def annotate_catalog(catalog: dict[str, str] | set | list) -> dict[str, str]:
    keys = catalog.keys() if isinstance(catalog, dict) else iter(catalog)
    return {key: metric_type(key) for key in keys}


# §三 语义替换禁令的触发词族。
_BREADTH_WORDS = ("宽度", "上涨家数", "下跌家数", "赚钱效应", "家数占比", "涨家数", "跌家数")
# 宽度类短语（宽度词+紧随修饰，限同一标点段内），用于确定性删除；
# 不贪前缀，避免吞掉短语前的正常内容（悬空连接词由清理步骤处理）。
_BREADTH_PHRASE = re.compile(
    r"(?:市场)?(?:宽度|上涨家数|下跌家数|涨家数|跌家数|赚钱效应|家数占比)[^，,；;。]{0,6}")
_DIRECTION_WORDS = ("上涨", "下跌", "大涨", "大跌", "单边上行", "单边下行", "必涨", "必跌")
_NECESSITY_WORDS = ("必涨", "必跌", "必定上涨", "必定下跌", "一定会涨", "一定会跌")
# §十二 invalidation 禁用 outcome 短语（只对 invalidation 字段）。
_OUTCOME_PHRASES = (
    "收盘涨", "收盘跌", "涨幅超过", "跌幅超过", "跑赢", "跑输", "上涨超过", "下跌超过",
    "涨跌幅超出", "收盘价超出", "收盘跌破", "收盘站上", "最终涨", "最终跌",
)
# §八 兜底失效条件（cutoff 后新信息语义，非交易止损）。
FALLBACK_INVALIDATION = "信息截止后若出现未纳入输入包的重大政策或跨市场冲击，本次判断需重新评估。"


def classify_reason_basis(resolved_keys: list[str]) -> str:
    """§五：按引用证据类型推导理由来源。"""
    types = {metric_type(key) for key in resolved_keys}
    price = types & {"INDUSTRY_RETURN", "INDUSTRY_RELATIVE_RETURN", "INDUSTRY_TREND", "INDUSTRY_VOLATILITY"}
    macro = types & {"INDUSTRY_MACRO_STANCE", "MACRO_GROWTH", "MACRO_INFLATION", "MACRO_LIQUIDITY",
                     "MACRO_CREDIT", "MACRO_FINANCIAL_CONDITION", "MACRO_REGIME", "MACRO_EVENT"}
    if price and macro:
        return "MIXED"
    if macro:
        return "MACRO_ALIGNMENT"
    return "PRICE_MOMENTUM"


def outcome_phrase_violations(invalidation_texts: list[str]) -> list[dict[str, str]]:
    """§十二：invalidation 中的结果评价语句（逐条判定）。"""
    violations = []
    for index, text in enumerate(invalidation_texts):
        cleaned = str(text or "").strip()
        if not cleaned:
            continue
        hit = next((phrase for phrase in _OUTCOME_PHRASES if phrase in cleaned), None)
        if hit:
            violations.append({"index": index, "phrase": hit, "text": cleaned})
    return violations


def _split_clauses(text: str) -> list[str]:
    return [clause for clause in re.split(r"[，,；;。]", text) if clause.strip()]


def remove_breadth_phrases(text: str) -> tuple[str, list[str]]:
    """确定性删除宽度类短语（同一标点段内），并清理悬空连接词。"""
    removed: list[str] = []

    def _drop(match: re.Match) -> str:
        removed.append(match.group(0))
        return "§CUT§"

    cut = _BREADTH_PHRASE.sub(_drop, text)
    segments = [segment.strip() for segment in cut.split("§CUT§")]
    kept: list[str] = []
    for segment in segments:
        cleaned = re.sub(r"^[且但而同时、\s]+|[且但而同时、\s]+$", "", segment)
        cleaned = re.sub(r"[，,]{2,}", "，", cleaned).strip("，, ")
        if cleaned:
            kept.append(cleaned)
    revised = "，".join(kept)
    return (revised or text), removed


class SemanticGuard:
    """对一次（已通过结构校验的）模型输出做语义审计与确定性修复视图。"""

    def __init__(self, *, catalog_types: dict[str, str],
                 alias_map: dict[str, str] | None = None) -> None:
        self.catalog_types = catalog_types
        self.alias_map = alias_map or {}

    def _resolve(self, key: str) -> str:
        return self.alias_map.get(key, key)

    def _types_of(self, keys: list[str]) -> set[str]:
        return {self.catalog_types.get(self._resolve(key), metric_type(self._resolve(key)))
                for key in keys if str(key).strip()}

    # -- 市场摘要：宽度声明必须有 MARKET_BREADTH 证据 ------------------------
    def audit_market_summary(self, summary: str, evidence_keys: list[str]) -> dict[str, Any]:
        text = str(summary or "")
        mentions_breadth = any(word in text for word in _BREADTH_WORDS)
        types = self._types_of(evidence_keys)
        has_breadth_evidence = "MARKET_BREADTH" in types
        background_cited = any(t == "BACKGROUND_INDEX_RETURN" for t in types)
        unsupported = mentions_breadth and not has_breadth_evidence
        # 波动 → 方向替换：引用了波动类证据且文本把波动表述为方向结论
        volatility_direction = bool(
            {"MARKET_VOLATILITY"} & types
            and re.search(r"(波动|震荡)[^。]{0,12}(导致|推动|意味着|说明)[^。]{0,8}(上涨|下跌|大涨|大跌)", text)
        )
        # v1.1.0：官方中间价 ≠ 市场收盘/离岸CNH/资金流；引用 CNY 证据时禁语检查
        cny_forbidden_words = ("离岸人民币", "市场汇率", "市场收盘", "资金流入", "外资流入", "北向资金")
        mentions_cny_mid = "FX_OFFICIAL_MID" in types
        cny_violation_words = sorted({word for word in cny_forbidden_words if word in text}) if mentions_cny_mid else []
        revised = text
        removed_clauses: list[str] = []
        if unsupported:
            revised, removed_clauses = remove_breadth_phrases(text)
        if cny_violation_words:
            unsupported = True
        return {
            "breadth_claim": ("SUPPORTED" if mentions_breadth and has_breadth_evidence
                              else ("REMOVED" if mentions_breadth else "NONE")),
            "breadth_claim_removed_clauses": removed_clauses,
            "background_index_substitution": unsupported and background_cited,
            "volatility_direction_substitution": volatility_direction,
            "cny_official_mid_violation_words": cny_violation_words,
            "revised_summary": revised,
            "status": "UNSUPPORTED_SEMANTIC_CLAIM" if unsupported or volatility_direction else "PASS",
        }

    # -- 行业条目：理由来源 + 宏观必然性措辞 --------------------------------
    def audit_industry_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        keys = [str(key) for key in (entry.get("evidence_keys") or [])]
        basis = classify_reason_basis(keys)
        reason = str(entry.get("reason") or "")
        necessity = any(word in reason for word in _NECESSITY_WORDS)
        macro_wording = any(word in reason for word in
                            ("宏观", "政策", "信用", "流动性", "通胀", "人民币", "汇率", "中间价"))
        overstated = False
        unsupported = False
        if necessity:
            unsupported = True  # 宏观轴/任何证据不得支撑必然涨跌
        elif macro_wording and basis == "PRICE_MOMENTUM":
            overstated = True  # 纯动量证据被包装成宏观驱动（降级显示，不删除）
        return {
            "industry_id": entry.get("industry_id"), "display_name": entry.get("display_name"),
            "side": entry.get("side"), "reason_basis": basis,
            "classification": "UNSUPPORTED" if unsupported else ("OVERSTATED" if overstated else "SUPPORTED"),
            "issue": ("必然性涨跌措辞无证据可支撑" if unsupported
                      else ("宏观措辞但证据仅为价格动量，降级为动量表述" if overstated else "")),
            "revised_reason": self._downgrade_reason(reason) if overstated else reason,
        }

    @staticmethod
    def _downgrade_reason(reason: str) -> str:
        """§六：纯动量理由的宏观措辞确定性降级（不重写事实，只去宏观归因）。"""
        text = reason
        for token in ("宏观利好", "宏观驱动", "受益于宏观", "政策利好", "信用宽松利好", "流动性利好"):
            text = text.replace(token, "同期相对走势数据")
        return text

    # -- 失效条件：与结果评价分离 -------------------------------------------
    def audit_invalidation(self, invalidation_texts: list[str]) -> dict[str, Any]:
        """先按 ；/; 拆分子条件再逐条判定：合法的子条件保留，outcome 子条件移出。"""
        texts: list[str] = []
        for item in invalidation_texts:
            for part in re.split(r"[；;]", str(item or "")):
                cleaned = part.strip()
                if cleaned:
                    texts.append(cleaned)
        violations = outcome_phrase_violations(texts)
        removed_originals = [str(item).strip() for item in invalidation_texts if str(item).strip()]
        kept = [text for index, text in enumerate(texts)
                if index not in {int(v["index"]) for v in violations}]
        used_fallback = False
        if not kept:
            kept = [FALLBACK_INVALIDATION]
            used_fallback = True
        removed = [v["text"] for v in violations]
        if used_fallback:
            removed = removed_originals
        return {
            "outcome_phrases": violations,
            "kept": kept,
            "removed": removed,
            "used_fallback": used_fallback,
            "status": "ISSUE" if violations else "PASS",
        }

    def run(self, model_output: dict[str, Any], validation_entries: list[dict[str, Any]]) -> dict[str, Any]:
        market = model_output.get("market") or {}
        summary_audit = self.audit_market_summary(market.get("summary") or "",
                                                  [str(k) for k in (market.get("evidence_keys") or [])])
        industry_audits = [self.audit_industry_entry(entry) for entry in validation_entries]
        invalidation_audit = self.audit_invalidation(market.get("invalidation_conditions") or [])
        unsupported_count = (1 if summary_audit["status"] != "PASS" else 0) + sum(
            1 for item in industry_audits if item["classification"] == "UNSUPPORTED")
        basis_counts = {
            "PRICE_MOMENTUM": sum(1 for item in industry_audits if item["reason_basis"] == "PRICE_MOMENTUM"),
            "MACRO_ALIGNMENT": sum(1 for item in industry_audits if item["reason_basis"] == "MACRO_ALIGNMENT"),
            "MIXED": sum(1 for item in industry_audits if item["reason_basis"] == "MIXED"),
        }
        return {
            "semantic_guard_version": SEMANTIC_GUARD_VERSION,
            "market_summary": summary_audit,
            "industries": industry_audits,
            "invalidation": invalidation_audit,
            "basis_counts": basis_counts,
            "unsupported_count": unsupported_count,
            "status": "PASS" if unsupported_count == 0 and not invalidation_audit["outcome_phrases"] else "ISSUE",
        }


def revised_output_from(report: dict[str, Any], model_output: dict[str, Any]) -> dict[str, Any]:
    """由语义报告生成修正视图（原始输出不动）：删/降级非法声明。"""
    revised = json_copy(model_output)
    market = revised.get("market") or {}
    market["summary"] = report["market_summary"]["revised_summary"]
    market["invalidation_conditions"] = report["invalidation"]["kept"]
    industries = revised.get("industries") or {}
    audits = {(item["industry_id"], item["side"]): item for item in report["industries"]}
    for side_key in ("relative_strong", "relative_weak"):
        for item in industries.get(side_key) or []:
            audit = audits.get((item.get("id") or item.get("industry_id"),
                                "RELATIVE_STRONG" if side_key == "relative_strong" else "RELATIVE_WEAK"))
            if audit:
                item["reason"] = audit["revised_reason"]
                item["reason_basis"] = audit["reason_basis"]
    revised["_semantic"] = {
        "guard_version": SEMANTIC_GUARD_VERSION,
        "removed_breadth_clauses": report["market_summary"]["breadth_claim_removed_clauses"],
        "removed_invalidations": report["invalidation"]["removed"],
        "used_fallback_invalidation": report["invalidation"]["used_fallback"],
    }
    return revised


def json_copy(value: Any) -> Any:
    import copy

    return copy.deepcopy(value)
