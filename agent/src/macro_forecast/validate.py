"""模型输出结构化校验器（预测引擎 V1 §16-§23 §30-§32 §47-§48）。

校验失败 → 该次输出 INVALID_OUTPUT；引擎不做内容重试（§26）。
逐项拒绝语义：无源证据只让该项无效，不整批报废——大盘与行业分别判定。
"""

from __future__ import annotations

import re
from typing import Any

from src.macro_forecast.prompt import MARKET_DIRECTIONS, NO_COUNTER_KEY

VALIDATOR_VERSION = "forecast-validator-v1.1.0"

# §17 schema 明确禁止的字段（出现即泄漏）。
FORBIDDEN_FIELDS = (
    "probability", "probabilities", "confidence_pct", "target_price", "target_index",
    "target_point", "position", "positions", "buy", "sell", "stop_loss", "stop_profit",
    "expected_return", "仓位", "目标价", "止损", "止盈", "买入", "卖出",
)
# §30 交易语言/点位泄漏扫描（对 reason/summary/cautions 文本）。
_BANNED_TEXT = (
    "买入", "卖出", "建仓", "加仓", "减仓", "止盈", "止损", "下单", "目标价",
    "开仓", "平仓", "调仓", "清仓", "概率是", "概率为", "上涨概率", "下跌概率",
    "胜率", "目标点位", "预计涨到", "预计跌到", "会涨到", "会跌到",
)
# 未经校准的概率表述（§17：禁止输出未经校准的概率）。
# 只拦"概率/胜率/置信度 + 数值"断言；证据数值本身带 %（如 5日-1.33%）合法。
_PROBABILITY_PATTERN = re.compile(
    r"概率\s*[约为达]?\s*\d|\d\s*[%％]\s*的概率|胜率\s*[约为达]?\s*\d|置信度\s*[约为达]?\s*\d"
    r"|confidence\s*[:=]\s*\d|likelihood\s*[:=]", re.I)
# 指数点位泄漏（§17：禁止目标点位/目标指数）。
_POINT_PATTERN = re.compile(r"\d{3,}\s*点|点位\s*\d|目标位|看到\s*\d{4}")
# 泛泛失效条件（§21）。
_GENERIC_INVALIDATION = ("市场存在不确定性", "存在不确定性", "市场波动", "不可预见", "风险较大", "存在风险")

GENERIC_INVALIDATION_HINT = "失效条件必须可执行（如具体事件/阈值），不得使用空泛表述"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _scan_text(value: Any, issues: list[str], label: str) -> None:
    text = _clean(value)
    for banned in _BANNED_TEXT:
        if banned in text:
            issues.append(f"{label}: 含交易语言/点位泄漏「{banned}」")
    if _PROBABILITY_PATTERN.search(text):
        issues.append(f"{label}: 疑似未校准概率表述")
    if _POINT_PATTERN.search(text):
        issues.append(f"{label}: 疑似指数点位泄漏")


def _scan_forbidden_fields(node: Any, path: str, issues: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            lowered = key_text.lower().replace("-", "_").replace(" ", "_")
            for forbidden in FORBIDDEN_FIELDS:
                if lowered == forbidden.lower():
                    issues.append(f"{path}.{key_text}: 禁止字段")
            _scan_forbidden_fields(value, f"{path}.{key_text}", issues)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _scan_forbidden_fields(value, f"{path}[{index}]", issues)


class ForecastValidator:
    """校验一次模型输出；返回结构化报告（逐项拒绝，不整批报废）。

    v1.1：模型引用短键别名（如 M2/S1_REL5）与候选短 id（S*/W*）；
    校验先经 ``alias_map``（短键→完整键）与 ``candidate_alias``（短 id→
    {industry_id, name}）解析，再对完整目录/候选集合校验。别名层不改变
    证据含义（§六），validator 契约不变（§22-10）。
    """

    def __init__(self, *, evidence_catalog: dict[str, str],
                 candidate_ids: set[str], industry_names: dict[str, str],
                 market_features: dict[str, Any] | None = None,
                 alias_map: dict[str, str] | None = None,
                 candidate_alias: dict[str, dict[str, str]] | None = None) -> None:
        self.evidence_catalog = set(evidence_catalog) | {NO_COUNTER_KEY}
        self.candidate_ids = set(candidate_ids)
        self.industry_names = industry_names
        self.market_features = market_features or {}
        self.alias_map = dict(alias_map or {})
        self.candidate_alias = dict(candidate_alias or {})

    def _resolve_key(self, key: str) -> str:
        return self.alias_map.get(key, key)

    def validate(self, output: dict[str, Any]) -> dict[str, Any]:
        issues: list[str] = []
        if not isinstance(output, dict):
            return self._report(False, ["输出不是 JSON 对象"], market_valid=False, industries=[])
        _scan_forbidden_fields(output, "$", issues)

        abstain = bool(output.get("abstain"))
        abstain_reason = _clean(output.get("abstain_reason"))
        market = output.get("market") or {}
        market_valid = True

        if abstain:
            if not abstain_reason:
                market_valid = False
                issues.append("abstain=true 但缺少 abstain_reason")
            elif not self._abstain_reason_grounded(abstain_reason, output):
                market_valid = False
                issues.append("abstain_reason 未对应真实缺口/信号冲突/日历问题（疑似滥用弃权）")
            if market.get("direction") not in (None, "ABSTAIN"):
                market_valid = False
                issues.append("abstain=true 时 market.direction 必须为 ABSTAIN 或缺省")
        else:
            direction = market.get("direction")
            if direction not in MARKET_DIRECTIONS[:3]:
                market_valid = False
                issues.append(f"market.direction 非法：{direction!r}")
            summary = _clean(market.get("summary"))
            if not summary:
                market_valid = False
                issues.append("market.summary 缺失")
            _scan_text(summary, issues, "market.summary")
            evidence, evidence_issues = self._valid_keys(market.get("evidence_keys"), min_count=2,
                                                         label="market.evidence_keys")
            issues.extend(evidence_issues)
            if not evidence or evidence_issues:
                # §18：未知 key → 该项无效；§19：大盘至少 2 个有效证据。
                market_valid = False
                if not evidence:
                    issues.append("market 证据不足：至少 2 个有效 evidence key")
            counter = [key for key in (market.get("counter_evidence_keys") or []) if _clean(key)]
            counter_valid = [
                key for key in counter
                if key == NO_COUNTER_KEY or self._resolve_key(_clean(key)) in self.evidence_catalog
            ]
            if len(counter_valid) != len(counter):
                market_valid = False
                issues.append("market.counter_evidence_keys 含未知键")
            if counter == [NO_COUNTER_KEY] and self._bundle_has_contradiction(direction):
                market_valid = False
                issues.append(
                    "NO_MATERIAL_COUNTER_EVIDENCE_IN_BUNDLE 不成立：输入包内存在与方向相反的信号")
            if not counter:
                market_valid = False
                issues.append("market 缺少反向证据（至少 1 个或 NO_MATERIAL_COUNTER_EVIDENCE_IN_BUNDLE）")
            invalidations = [item for item in (market.get("invalidation_conditions") or []) if _clean(item)]
            if not invalidations:
                market_valid = False
                issues.append("market 缺少失效条件")
            for item in invalidations:
                _scan_text(item, issues, "market.invalidation")
                if any(generic in _clean(item) for generic in _GENERIC_INVALIDATION):
                    market_valid = False
                    issues.append(GENERIC_INVALIDATION_HINT)
            for caution in output.get("overall_cautions") or []:
                _scan_text(caution, issues, "overall_cautions")

        industries = self._validate_industries(output.get("industries") or {}, issues)
        strong_ids = {entry["industry_id"] for entry in industries if entry["side"] == "RELATIVE_STRONG"}
        weak_ids = {entry["industry_id"] for entry in industries if entry["side"] == "RELATIVE_WEAK"}
        overlap = strong_ids & weak_ids
        if overlap:
            issues.append(f"同一行业同时出现在 strong/weak：{sorted(overlap)}")
        overall_valid = not issues and market_valid
        return self._report(overall_valid, issues, market_valid=market_valid, industries=industries)

    def _report(self, valid: bool, issues: list[str], *, market_valid: bool,
                industries: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "validator_version": VALIDATOR_VERSION,
            "valid": valid,
            "market_valid": market_valid,
            "industry_entries": industries,
            "issues": issues,
        }

    def _valid_keys(self, raw: Any, *, min_count: int, label: str) -> tuple[list[str], list[str]]:
        keys = [_clean(key) for key in (raw or []) if _clean(key)]
        issues: list[str] = []
        resolved = [self._resolve_key(key) for key in keys]
        unknown = [key for key in resolved if key != NO_COUNTER_KEY and key not in self.evidence_catalog]
        if unknown:
            issues.append(f"{label}: 未知 evidence key {unknown[:5]}")
        return [key for key in resolved if key == NO_COUNTER_KEY or key in self.evidence_catalog], issues

    def _abstain_reason_grounded(self, reason: str, output: dict[str, Any]) -> bool:
        """§23：弃权理由必须映射到数据缺口/信号冲突/日历问题。"""
        text = reason.lower()
        grounded_tokens = (
            "缺口", "缺失", "不足", "gap", "missing", "insufficient", "stale", "过期",
            "冲突", "分裂", "conflict", "divergen", "日历", "calendar", "unverified",
            "异常", "inconsistent",
        )
        return any(token in text for token in grounded_tokens)

    def _bundle_has_contradiction(self, direction: Any) -> bool:
        """§20：包内已有与方向相反的信号时，禁用 NO_MATERIAL 反向证据声明。"""
        features = self.market_features or {}
        ret_1d, ret_5d = features.get("ret_1d"), features.get("ret_5d")
        if direction == "STRONGER":
            return any(value is not None and value < 0 for value in (ret_1d, ret_5d))
        if direction == "WEAKER":
            return any(value is not None and value > 0 for value in (ret_1d, ret_5d))
        return False

    def _validate_industries(self, industries: Any, issues: list[str]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        sides = {"relative_strong": "RELATIVE_STRONG", "relative_weak": "RELATIVE_WEAK"}
        for key, side in sides.items():
            raw_list = industries.get(key)
            if raw_list is None:
                raw_list = []
            if not isinstance(raw_list, list):
                issues.append(f"industries.{key} 不是列表")
                continue
            if len(raw_list) > 3:
                issues.append(f"industries.{key} 超过 3 项")
            for item in raw_list[:3]:
                short_id = _clean(item.get("id") or item.get("industry_id"))
                reason = _clean(item.get("reason"))
                resolved_candidate = self.candidate_alias.get(short_id)
                if resolved_candidate is None and short_id not in self.candidate_ids:
                    issues.append(f"industries.{key}: 非候选行业 {short_id!r}")
                    continue
                if resolved_candidate is not None:
                    industry_id = resolved_candidate["industry_id"]
                else:
                    industry_id = short_id
                full_prefix = f"IND_{industry_id.replace('tdx:', '').replace('.', '_')}_"
                resolved_raw = [self._resolve_key(_clean(k)) for k in (item.get("evidence_keys") or [])]
                own_keys = [key2 for key2 in resolved_raw if key2.startswith(full_prefix)]
                valid_keys = [key2 for key2 in resolved_raw
                              if key2 in self.evidence_catalog or key2 == NO_COUNTER_KEY]
                if not own_keys:
                    issues.append(f"industries.{key}[{short_id}]: 缺少该行业自身 IND_* 证据键")
                    continue
                mentions_macro = any(token in reason for token in ("宏观", "政策", "流动性", "利率", "信用", "通胀"))
                has_macro_key = any(key2.startswith("MACRO_") for key2 in valid_keys)
                if mentions_macro and not has_macro_key:
                    issues.append(f"industries.{key}[{short_id}]: 理由提及宏观但未引用 MACRO_* 键")
                    continue
                _scan_text(reason, issues, f"industries.{key}[{short_id}].reason")
                entries.append({
                    "side": side, "industry_id": industry_id,
                    "display_name": self.industry_names.get(industry_id,
                                                            (resolved_candidate or {}).get("name", industry_id)),
                    "reason": reason, "evidence_keys": valid_keys,
                })
        return entries
