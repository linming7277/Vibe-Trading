"""Read-only, deterministic entry-research synthesis for Value Line.

This module ranks research attention only.  It neither persists a result nor
changes a Company Thesis, invokes an LLM, or emits execution instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.company_thesis.store import CompanyThesisRepository
from src.research_workspace.store import normalize_market, normalize_symbol
from src.value_price_zones import ValuePriceZoneService, get_value_price_zone_service


@dataclass(frozen=True)
class EntryResearchConfig:
    valuation_weight: float = .40
    historical_valuation_weight: float = .25
    support_weight: float = .25
    thesis_weight: float = .10
    near_resistance_buffer: float = .03


DEFAULT_CONFIG = EntryResearchConfig()

VALUATION_SCORES = {
    "DEEPLY_UNDERVALUED": 100, "UNDERVALUED": 80, "FAIR": 50,
    "OVERVALUED": 20, "DEEPLY_OVERVALUED": 0,
}
HISTORICAL_SCORES = {
    "VERY_CHEAP": 100, "CHEAP": 80, "NORMAL": 50,
    "EXPENSIVE": 20, "VERY_EXPENSIVE": 0,
}
THESIS_SCORES = {
    "STRENGTHENING": 100, "UNCHANGED": 80, "FORMING": 60,
    "WEAKENING": 30, "FALSIFIED": 0,
}
LEVELS = ((85, "HIGH_ATTENTION"), (70, "ATTENTION"), (50, "WATCH"), (0, "WAIT"))
LEVEL_LABELS = {
    "HIGH_ATTENTION": "价格条件高度值得关注", "ATTENTION": "价格条件值得关注", "WATCH": "价格条件继续观察",
    "WAIT": "当前价格条件等待", "BLOCKED": "当前研究条件存在阻断",
}
THESIS_LABELS = {
    "STRENGTHENING": "逻辑正在增强", "UNCHANGED": "基本稳定", "FORMING": "正在形成",
    "WEAKENING": "逻辑正在减弱", "FALSIFIED": "核心逻辑失效",
}


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


class EntryResearchService:
    def __init__(
        self, *, price_zone_service: ValuePriceZoneService | None = None,
        thesis_repository: CompanyThesisRepository | None = None,
        config: EntryResearchConfig = DEFAULT_CONFIG,
    ) -> None:
        self.price_zone_service = price_zone_service or get_value_price_zone_service()
        self.thesis_repository = thesis_repository or self.price_zone_service.thesis_repository
        self.config = config

    @staticmethod
    def _in_zone(price: float | None, zone: dict[str, Any]) -> bool:
        if price is None:
            return False
        low, high = _finite_number(zone.get("low")), _finite_number(zone.get("high"))
        return (low is None or price >= low) and (high is None or price <= high)

    def _near_zone(self, price: float | None, zone: dict[str, Any]) -> bool:
        if self._in_zone(price, zone):
            return True
        if price is None:
            return False
        low, high = _finite_number(zone.get("low")), _finite_number(zone.get("high"))
        anchors = [value for value in (low, high) if value is not None and value > 0]
        return bool(anchors) and min(abs(price / anchor - 1) for anchor in anchors) <= self.config.near_resistance_buffer

    @staticmethod
    def _range(label: str, zone: dict[str, Any] | None, *, kind: str) -> dict[str, Any] | None:
        if not zone:
            return None
        low, high = _finite_number(zone.get("low")), _finite_number(zone.get("high"))
        if low is None and high is None:
            return None
        return {"label": label, "low": round(low, 2) if low is not None else None,
                "high": round(high, 2) if high is not None else None, "kind": kind,
                "strength": zone.get("strength") or zone.get("support_strength")}

    @staticmethod
    def _level(score: float) -> str:
        return next(level for floor, level in LEVELS if score >= floor)

    @staticmethod
    def _thesis_is_pit_safe(thesis: dict[str, Any] | None, as_of: str | None) -> bool:
        if not thesis or not as_of:
            return bool(thesis)
        target = str(as_of)[:10]
        # A current thesis is not a historical source unless both its own data
        # boundary and its creation time were available by the requested date.
        source_as_of = str(thesis.get("source_data_as_of") or "")[:10]
        created_at = str(thesis.get("created_at") or "")[:10]
        return bool(source_as_of and created_at and source_as_of <= target and created_at <= target)

    @staticmethod
    def _metric_reason_codes(historical: dict[str, Any]) -> list[str]:
        output: list[str] = []
        metrics = historical.get("historical_percentiles") or {}
        for metric, prefix in (("pe_ttm", "HISTORICAL_PE"), ("pb_mrq", "HISTORICAL_PB"), ("dividend_yield", "HISTORICAL_DIVIDEND_YIELD")):
            state = str((metrics.get(metric) or {}).get("state") or "")
            if state in {"VERY_CHEAP", "CHEAP"}:
                output.append(f"{prefix}_LOW")
            elif state in {"EXPENSIVE", "VERY_EXPENSIVE"}:
                output.append(f"{prefix}_HIGH")
        return output

    def get_entry_research(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        target = str(as_of)[:10] if as_of else None
        if target:
            date.fromisoformat(target)
        zones = self.price_zone_service.get_price_zones(normalized_market, symbol, as_of=target)
        current_price = _finite_number(zones.get("current_price"))
        valuation = dict(zones.get("valuation") or {})
        historical = dict(zones.get("historical_valuation") or {})
        quality = dict(zones.get("data_quality") or {})
        thesis = self.thesis_repository.get_current_thesis(normalized_market, symbol)
        thesis = thesis if self._thesis_is_pit_safe(thesis, target) else None

        reason_codes: list[str] = []
        missing: list[str] = []
        valuation_status = str(valuation.get("status") or "INSUFFICIENT_DATA")
        if valuation_status in VALUATION_SCORES and _finite_number(valuation.get("fair_value_low")) is not None and _finite_number(valuation.get("fair_value_high")) is not None:
            valuation_score = VALUATION_SCORES[valuation_status]
            reason_codes.append(f"VALUATION_{valuation_status}")
        else:
            valuation_score = 0
            missing.append("FAIR_VALUE")

        historical_state = str(historical.get("historical_valuation_status") or "INSUFFICIENT_DATA")
        historical_coverage = str(((historical.get("coverage") or {}).get("coverage_status")) or "INSUFFICIENT")
        if historical_state in HISTORICAL_SCORES and historical_coverage in {"READY", "PARTIAL"}:
            historical_score = HISTORICAL_SCORES[historical_state]
            reason_codes.append(f"HISTORICAL_VALUATION_{historical_state}")
            reason_codes.extend(self._metric_reason_codes(historical))
        else:
            historical_score = 0
            missing.append("HISTORICAL_VALUATION")

        confluence = list(zones.get("confluence_zones") or [])
        supports = list(zones.get("support_zones") or [])
        resistances = list(zones.get("resistance_zones") or [])
        matching_confluence = next((zone for zone in confluence if self._in_zone(current_price, zone)), None)
        matching_support = next((zone for zone in supports if self._in_zone(current_price, zone)), None)
        if matching_confluence:
            strength = str(matching_confluence.get("support_strength") or "LOW")
            support_score = {"HIGH": 100, "MEDIUM": 80, "LOW": 60}.get(strength, 60)
            reason_codes.extend(["VALUATION_SUPPORT_CONFLUENCE", f"{strength}_SUPPORT"])
        elif matching_support:
            support_score = 40
            reason_codes.append("HISTORICAL_SUPPORT")
        else:
            support_score = 20
            if str((quality.get("daily_history") or {}).get("status") or "MISSING") not in {"READY", "PARTIAL"}:
                missing.append("SUPPORT_HISTORY")
            else:
                reason_codes.append("NO_NEAR_SUPPORT")
        near_resistance = next((zone for zone in resistances if self._near_zone(current_price, zone)), None)
        if near_resistance:
            support_score = max(0, support_score - 20)
            reason_codes.append("NEAR_RESISTANCE")

        thesis_status = str((thesis or {}).get("status") or "MISSING")
        thesis_confidence = str((thesis or {}).get("confidence") or "")
        if thesis_status in THESIS_SCORES:
            thesis_score = THESIS_SCORES[thesis_status]
            reason_codes.append(f"THESIS_{thesis_status}")
        else:
            thesis_score = 50
            missing.append("THESIS")
            reason_codes.append("THESIS_MISSING")

        if missing:
            reason_codes.append("DATA_PARTIAL")

        score = round(
            valuation_score * self.config.valuation_weight + historical_score * self.config.historical_valuation_weight
            + support_score * self.config.support_weight + thesis_score * self.config.thesis_weight,
            2,
        )
        if thesis_status == "FALSIFIED":
            level, safety_gate = "BLOCKED", "FALSIFIED"
            reason_codes.append("THESIS_FALSIFIED")
        else:
            level, safety_gate = self._level(score), None
            if thesis_status == "WEAKENING" and level in {"HIGH_ATTENTION", "ATTENTION"}:
                level, safety_gate = "WATCH", "WEAKENING_CAP"
            if missing and level in {"HIGH_ATTENTION", "ATTENTION"}:
                level, safety_gate = "WATCH", "DATA_QUALITY_CAP"

        if len(missing) >= 2:
            confidence = "LOW"
        elif missing or thesis_confidence == "LOW":
            confidence = "MEDIUM"
        else:
            confidence = "HIGH"

        under_valued = next((zone for zone in zones.get("valuation_zones") or [] if zone.get("name") == "低估关注区"), None)
        high_margin = next((zone for zone in zones.get("valuation_zones") or [] if zone.get("name") == "较高安全边际区"), None)
        nearest_support = matching_support or next((zone for zone in supports if self._near_zone(current_price, zone)), supports[0] if supports else None)
        focus = matching_confluence or matching_support or under_valued or nearest_support
        focus_zones = {
            "fair_value": self._range("合理价值区间", {"low": valuation.get("fair_value_low"), "high": valuation.get("fair_value_high")}, kind="FAIR_VALUE"),
            "undervalued_attention": self._range("低估关注区", under_valued, kind="UNDERVALUED"),
            "historical_support": self._range("历史支撑区", nearest_support, kind="SUPPORT"),
            "valuation_support_confluence": self._range("价值与支撑重叠区", matching_confluence, kind="CONFLUENCE"),
            "focus_zone": self._range("重点观察区", focus, kind="FOCUS"),
            "high_margin_zone": self._range("较高安全边际区", high_margin, kind="UNDERVALUED"),
        }
        if level == "BLOCKED":
            explanation = "公司的核心逻辑已被标记为失效，即使价格或估值看起来较低，当前价格研究条件仍存在阻断。"
        elif thesis_status == "WEAKENING":
            explanation = "价格和估值信号可以继续跟踪，但公司的核心经营逻辑正在减弱，因此目前只保留继续观察。"
        elif missing:
            explanation = f"部分关键估值、历史价格或公司逻辑资料不足，当前为“{LEVEL_LABELS[level]}”，等待数据完整后再评估。"
        else:
            valuation_text = {"DEEPLY_UNDERVALUED": "当前估值明显低于系统估算的合理区间", "UNDERVALUED": "当前估值低于系统估算的合理区间", "FAIR": "当前估值处在系统估算的合理区间", "OVERVALUED": "当前估值偏高", "DEEPLY_OVERVALUED": "当前估值明显偏高"}.get(valuation_status, "当前估值资料不足")
            historical_text = {"VERY_CHEAP": "历史估值也处于很低位置", "CHEAP": "历史估值处于较低位置", "NORMAL": "历史估值处于中性位置", "EXPENSIVE": "历史估值偏高", "VERY_EXPENSIVE": "历史估值明显偏高"}.get(historical_state, "历史估值资料不足")
            support_text = "价格进入价值与历史支撑的重叠区域" if matching_confluence else "价格靠近历史支撑区域" if matching_support else "当前尚未靠近明确的历史支撑区域"
            explanation = f"{valuation_text}，{historical_text}，{support_text}；公司核心逻辑当前为{THESIS_LABELS.get(thesis_status, '资料不足')}。"
        return {
            "stock_code": symbol, "as_of": zones.get("as_of"), "current_price": current_price,
            "entry_score": score, "entry_level": level, "entry_level_label": LEVEL_LABELS[level],
            "confidence": confidence, "valuation_score": valuation_score,
            "historical_valuation_score": historical_score, "support_score": support_score,
            "thesis_score": thesis_score, "thesis_status": thesis_status if thesis_status != "MISSING" else None,
            "thesis_confidence": thesis_confidence or None, "safety_gate": safety_gate,
            "focus_zones": focus_zones, "reason_codes": list(dict.fromkeys(reason_codes)),
            "data_gaps": missing, "plain_explanation": explanation,
            "formula_version": "value-line-entry-research-v1.0.0",
            "weights": {"valuation": self.config.valuation_weight, "historical_valuation": self.config.historical_valuation_weight,
                        "support": self.config.support_weight, "thesis": self.config.thesis_weight},
        }


_service: EntryResearchService | None = None


def get_entry_research_service() -> EntryResearchService:
    global _service
    if _service is None:
        _service = EntryResearchService()
    return _service
