"""Read-only, deterministic exit-research synthesis for Value Line."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.company_thesis.evidence_store import CompanyThesisEvidenceRepository
from src.company_thesis.review_store import CompanyThesisReviewRepository
from src.company_thesis.store import CompanyThesisRepository
from src.research_workspace.store import normalize_market, normalize_symbol
from src.value_price_zones import ValuePriceZoneService, get_value_price_zone_service


@dataclass(frozen=True)
class ExitResearchConfig:
    valuation_weight: float = .35
    historical_valuation_weight: float = .20
    resistance_weight: float = .20
    thesis_risk_weight: float = .25
    near_resistance_buffer: float = .03
    max_challenge_evidence: int = 5


DEFAULT_CONFIG = ExitResearchConfig()
VALUATION_PRESSURES = {
    "DEEPLY_OVERVALUED": 100, "OVERVALUED": 80, "FAIR": 40,
    "UNDERVALUED": 15, "DEEPLY_UNDERVALUED": 0,
}
HISTORICAL_PRESSURES = {
    "VERY_EXPENSIVE": 100, "EXPENSIVE": 80, "NORMAL": 40,
    "CHEAP": 15, "VERY_CHEAP": 0,
}
THESIS_RISKS = {
    "STRENGTHENING": 0, "UNCHANGED": 20, "FORMING": 35,
    "WEAKENING": 80, "FALSIFIED": 100,
}
LEVELS = ((85, "CRITICAL_REVIEW"), (70, "REVIEW"), (50, "WATCH"), (0, "NORMAL"))
LEVEL_LABELS = {
    "CRITICAL_REVIEW": "需要立即复核核心研究逻辑", "REVIEW": "需要重点复核",
    "WATCH": "存在复核事项", "NORMAL": "当前暂无明显复核压力",
}
THESIS_LABELS = {
    "STRENGTHENING": "逻辑正在增强", "UNCHANGED": "基本稳定", "FORMING": "正在形成",
    "WEAKENING": "逻辑正在减弱", "FALSIFIED": "核心逻辑失效",
}
CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


class ExitResearchService:
    def __init__(
        self, *, price_zone_service: ValuePriceZoneService | None = None,
        thesis_repository: CompanyThesisRepository | None = None,
        evidence_repository: CompanyThesisEvidenceRepository | None = None,
        review_repository: CompanyThesisReviewRepository | None = None,
        config: ExitResearchConfig = DEFAULT_CONFIG,
    ) -> None:
        self.price_zone_service = price_zone_service or get_value_price_zone_service()
        self.thesis_repository = thesis_repository or self.price_zone_service.thesis_repository
        db_path = self.thesis_repository.db_path
        self.evidence_repository = evidence_repository or CompanyThesisEvidenceRepository(db_path)
        self.review_repository = review_repository or CompanyThesisReviewRepository(db_path)
        self.config = config
        self._owns_evidence = evidence_repository is None
        self._owns_review = review_repository is None

    def close(self) -> None:
        if self._owns_evidence:
            self.evidence_repository.close()
        if self._owns_review:
            self.review_repository.close()

    @staticmethod
    def _available_at(row: dict[str, Any] | None, as_of: str | None) -> bool:
        if not row or not as_of:
            return bool(row)
        target = str(as_of)[:10]
        created = str(row.get("created_at") or "")[:10]
        data_as_of = str(row.get("data_as_of") or row.get("source_data_as_of") or "")[:10]
        return bool(created and created <= target and (not data_as_of or data_as_of <= target))

    @staticmethod
    def _in_zone(price: float | None, zone: dict[str, Any]) -> bool:
        if price is None:
            return False
        low, high = _number(zone.get("low")), _number(zone.get("high"))
        return (low is None or price >= low) and (high is None or price <= high)

    def _near_zone(self, price: float | None, zone: dict[str, Any]) -> bool:
        if self._in_zone(price, zone):
            return True
        if price is None:
            return False
        anchors = [item for item in (_number(zone.get("low")), _number(zone.get("high"))) if item is not None and item > 0]
        return bool(anchors) and min(abs(price / anchor - 1) for anchor in anchors) <= self.config.near_resistance_buffer

    @staticmethod
    def _level(score: float) -> str:
        return next(level for floor, level in LEVELS if score >= floor)

    @staticmethod
    def _zone(label: str, value: dict[str, Any] | None, kind: str) -> dict[str, Any] | None:
        if not value:
            return None
        low, high = _number(value.get("low")), _number(value.get("high"))
        if low is None and high is None:
            return None
        return {"label": label, "low": round(low, 2) if low is not None else None,
                "high": round(high, 2) if high is not None else None, "kind": kind,
                "strength": value.get("strength") or value.get("support_strength")}

    @staticmethod
    def _evidence_text(row: dict[str, Any]) -> str:
        # Evidence is already human-authored or deterministically extracted;
        # choose its plain summary first instead of inventing an interpretation.
        return str(row.get("summary") or row.get("claim") or "").strip()

    def _challenge_evidence(self, thesis: dict[str, Any] | None, as_of: str | None) -> list[dict[str, Any]]:
        if not thesis:
            return []
        active = self.evidence_repository.list_active_evidence_for_thesis(str(thesis["thesis_id"]))
        challenges = [
            row for row in active
            if str(row.get("effect") or "").upper() == "CHALLENGE" and self._available_at(row, as_of)
        ]
        # Confidence has priority; within the same confidence group, newest
        # evidence is shown first. ISO timestamps sort lexicographically.
        challenges.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        challenges.sort(key=lambda row: CONFIDENCE_ORDER.get(str(row.get("confidence") or "").upper(), 9))
        return [{
            "evidence_id": row.get("evidence_id"), "confidence": row.get("confidence"),
            "text": self._evidence_text(row), "created_at": row.get("created_at"),
            "source_title": row.get("source_title"), "source_date": row.get("source_date"),
        } for row in challenges[:self.config.max_challenge_evidence] if self._evidence_text(row)]

    def get_exit_research(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        normalized_market, symbol = normalize_market(market), normalize_symbol(normalize_market(market), stock_code)
        target = str(as_of)[:10] if as_of else None
        if target:
            date.fromisoformat(target)
        zones = self.price_zone_service.get_price_zones(normalized_market, symbol, as_of=target)
        valuation, historical = dict(zones.get("valuation") or {}), dict(zones.get("historical_valuation") or {})
        quality, current_price = dict(zones.get("data_quality") or {}), _number(zones.get("current_price"))
        thesis = self.thesis_repository.get_current_thesis(normalized_market, symbol)
        thesis = thesis if self._available_at(thesis, target) else None
        review = self.review_repository.get_latest_review(normalized_market, symbol)
        review = review if self._available_at(review, target) else None
        challenges = self._challenge_evidence(thesis, target)
        missing: list[str] = []
        codes: list[str] = []

        valuation_status = str(valuation.get("status") or "INSUFFICIENT_DATA")
        if valuation_status in VALUATION_PRESSURES and _number(valuation.get("fair_value_low")) is not None and _number(valuation.get("fair_value_high")) is not None:
            valuation_pressure = VALUATION_PRESSURES[valuation_status]
            codes.append(f"VALUATION_{valuation_status}")
        else:
            valuation_pressure = 0
            missing.append("FAIR_VALUE")

        historical_status = str(historical.get("historical_valuation_status") or "INSUFFICIENT_DATA")
        historical_coverage = str((historical.get("coverage") or {}).get("coverage_status") or "INSUFFICIENT")
        if historical_status in HISTORICAL_PRESSURES and historical_coverage in {"READY", "PARTIAL"}:
            historical_pressure = HISTORICAL_PRESSURES[historical_status]
            codes.append(f"HISTORICAL_VALUATION_{historical_status}")
        else:
            historical_pressure = 0
            missing.append("HISTORICAL_VALUATION")

        upper_review = list(zones.get("upper_review_zones") or [])
        resistances = list(zones.get("resistance_zones") or [])
        active_upper = next((zone for zone in upper_review if self._in_zone(current_price, zone)), None)
        active_resistance = next((zone for zone in resistances if self._in_zone(current_price, zone)), None)
        nearby_resistance = next((zone for zone in resistances if self._near_zone(current_price, zone)), None)
        if active_upper:
            strength = str(active_upper.get("support_strength") or "LOW")
            resistance_pressure = {"HIGH": 100, "MEDIUM": 80, "LOW": 60}.get(strength, 60)
            codes.extend(["VALUATION_RESISTANCE_CONFLUENCE", f"{strength}_RESISTANCE"])
        elif active_resistance:
            strength = str(active_resistance.get("strength") or "LOW")
            resistance_pressure = {"HIGH": 100, "MEDIUM": 80, "LOW": 60}.get(strength, 60)
            codes.append(f"{strength}_RESISTANCE")
        elif nearby_resistance:
            resistance_pressure = 40
            codes.append("NEAR_RESISTANCE")
        else:
            resistance_pressure = 20
            if str((quality.get("daily_history") or {}).get("status") or "MISSING") not in {"READY", "PARTIAL"}:
                missing.append("RESISTANCE_HISTORY")
            else:
                codes.append("NO_NEAR_RESISTANCE")

        thesis_status, thesis_confidence = str((thesis or {}).get("status") or "MISSING"), str((thesis or {}).get("confidence") or "")
        if thesis_status in THESIS_RISKS:
            thesis_risk = THESIS_RISKS[thesis_status]
            codes.append(f"THESIS_{thesis_status}")
        else:
            thesis_risk = 35
            missing.append("THESIS")
            codes.append("THESIS_MISSING")
        challenge_count = len(challenges)
        challenge_add = 20 if challenge_count >= 3 else 10 if challenge_count else 0
        thesis_risk = min(100, thesis_risk + challenge_add)
        if challenge_count:
            codes.append("MULTIPLE_CHALLENGES" if challenge_count >= 3 else "CHALLENGE_EVIDENCE")
        if review and bool(review.get("is_stale")):
            codes.append("REVIEW_STALE")
        elif not review:
            codes.append("REVIEW_NOT_CREATED")
        if missing:
            codes.append("DATA_PARTIAL")

        score = round(
            valuation_pressure * self.config.valuation_weight + historical_pressure * self.config.historical_valuation_weight
            + resistance_pressure * self.config.resistance_weight + thesis_risk * self.config.thesis_risk_weight,
            2,
        )
        safety_gate: str | None = None
        if thesis_status == "FALSIFIED":
            level, safety_gate = "CRITICAL_REVIEW", "FALSIFIED"
        else:
            level = self._level(score)
            high_risk_combo = valuation_pressure >= 80 and resistance_pressure == 100 and challenge_count >= 3
            if thesis_status == "STRENGTHENING" and level == "CRITICAL_REVIEW" and not high_risk_combo:
                level, safety_gate = "REVIEW", "STRENGTHENING_CAP"
            if missing and level in {"CRITICAL_REVIEW", "REVIEW"}:
                level, safety_gate = "WATCH", "DATA_QUALITY_CAP"
        confidence = "LOW" if len(missing) >= 2 else "MEDIUM" if missing or thesis_confidence == "LOW" else "HIGH"

        best_upper = active_upper or next((zone for zone in upper_review if self._near_zone(current_price, zone)), None)
        best_resistance = active_resistance or nearby_resistance or (resistances[0] if resistances else None)
        focus = best_upper or best_resistance
        focus_zones = {
            "fair_value_high": self._zone("合理价值上沿", {"low": _number(valuation.get("fair_value_high")), "high": None}, "FAIR_VALUE"),
            "historical_resistance": self._zone("历史压力区", best_resistance, "RESISTANCE"),
            "valuation_resistance_confluence": self._zone("高估与压力重叠区", best_upper, "CONFLUENCE"),
            "focus_zone": self._zone("重点复核区", focus, "FOCUS"),
        }
        if thesis_status == "FALSIFIED":
            explanation = "公司的核心逻辑已被标记为失效，需要重点重新检查基本面和估值假设；这不是自动执行指令。"
        elif missing:
            explanation = f"部分关键估值、历史价格或公司逻辑资料不足，当前为“{LEVEL_LABELS[level]}”，等待资料完整后再复核。"
        else:
            valuation_text = {"DEEPLY_OVERVALUED": "当前价格明显高于系统估算的合理价值区间", "OVERVALUED": "当前价格高于系统估算的合理价值区间", "FAIR": "当前价格仍在系统估算的合理价值区间", "UNDERVALUED": "当前价格低于系统估算的合理价值区间", "DEEPLY_UNDERVALUED": "当前价格明显低于系统估算的合理价值区间"}.get(valuation_status, "估值资料不足")
            historical_text = {"VERY_EXPENSIVE": "历史估值也处于很高位置", "EXPENSIVE": "历史估值处于较高位置", "NORMAL": "历史估值处于中性位置", "CHEAP": "历史估值处于较低位置", "VERY_CHEAP": "历史估值处于很低位置"}.get(historical_status, "历史估值资料不足")
            pressure_text = "当前价格进入高估与历史压力的重叠区域" if active_upper else "当前价格进入历史压力区域" if active_resistance else "当前价格接近历史压力区域" if nearby_resistance else "当前没有接近明确的历史压力区域"
            challenge_text = f"目前还有 {challenge_count} 条需要注意的挑战证据" if challenge_count else "当前没有已记录的挑战证据"
            explanation = f"{valuation_text}，{historical_text}，{pressure_text}；公司逻辑{THESIS_LABELS.get(thesis_status, '资料不足')}，{challenge_text}。"
        return {
            "stock_code": symbol, "as_of": zones.get("as_of"), "current_price": current_price,
            "exit_score": score, "exit_level": level, "exit_level_label": LEVEL_LABELS[level], "confidence": confidence,
            "valuation_pressure": valuation_pressure, "historical_valuation_pressure": historical_pressure,
            "resistance_pressure": resistance_pressure, "thesis_risk": thesis_risk,
            "thesis_status": thesis_status if thesis_status != "MISSING" else None, "thesis_confidence": thesis_confidence or None,
            "challenge_count": challenge_count, "challenge_evidence": challenges,
            "latest_review": None if not review else {"review_id": review.get("review_id"), "review_status": review.get("review_status"),
                                                       "recommended_status": review.get("recommended_status"), "is_stale": bool(review.get("is_stale")),
                                                       "challenge_count": review.get("challenge_count")},
            "upper_review_zones": upper_review, "focus_zones": focus_zones,
            "reason_codes": list(dict.fromkeys(codes)), "data_gaps": missing, "safety_gate": safety_gate,
            "plain_explanation": explanation, "formula_version": "value-line-exit-research-v1.0.0",
            "weights": {"valuation": self.config.valuation_weight, "historical_valuation": self.config.historical_valuation_weight,
                        "resistance": self.config.resistance_weight, "thesis_risk": self.config.thesis_risk_weight},
        }


_service: ExitResearchService | None = None


def get_exit_research_service() -> ExitResearchService:
    global _service
    if _service is None:
        _service = ExitResearchService()
    return _service
