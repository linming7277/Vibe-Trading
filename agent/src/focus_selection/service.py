"""Deterministic A/B/C research-priority projection for low-value leaders.

The service intentionally consumes durable Value Line results only.  It does
not calculate valuations or risk, persist rankings, call an LLM, or alter a
leader-pool / Thesis record.
"""

from __future__ import annotations

from typing import Any

from src.company_thesis import CompanyThesisRepository
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.low_value_risk_snapshot.store import LowValueRiskSnapshotRepository
from src.risk_research_preparation.store import RiskResearchPreparationRepository


HARD_C_REASONS = {
    "THESIS_FALSIFIED": "核心逻辑已失效，当前不纳入优先研究。",
    "RISK_HIGH": "存在已确认高等级风险，需要先完成重点复核。",
    "ENTRY_BLOCKED": "当前研究条件被阻断，暂不纳入优先研究。",
    "FINANCIAL_NOT_READY": "财务资料不足，当前无法进行可靠的重点研究判断。",
}
HISTORICAL_RANK = {
    "VERY_CHEAP": 0,
    "CHEAP": 1,
    "NORMAL": 2,
    "EXPENSIVE": 3,
    "VERY_EXPENSIVE": 4,
}


def _day(value: Any) -> str | None:
    text = str(value or "")[:10]
    return text or None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _discount_to_mid(item: dict[str, Any]) -> float | None:
    price, midpoint = _number(item.get("current_price")), _number(item.get("fair_value_mid"))
    if price is None or midpoint is None or midpoint == 0:
        return None
    return round((midpoint - price) / midpoint, 6)


def _fair_value_gap_percent(item: dict[str, Any]) -> float | None:
    price, midpoint = _number(item.get("current_price")), _number(item.get("fair_value_mid"))
    if price is None or midpoint is None or price <= 0:
        return None
    return round((midpoint / price - 1) * 100, 2)


def _valuation_quality(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Read valuation-method evidence already persisted with the pool row.

    The low-value pool membership remains untouched.  This only prevents a
    sparse or extreme comparable-based value range from being presented as an
    A-tier research conclusion before its inputs are reviewed.
    """
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    raw = metadata.get("valuation_quality") if isinstance(metadata.get("valuation_quality"), dict) else {}
    method_count = _number(raw.get("method_count"))
    min_peer_count = _number(raw.get("min_peer_count"))
    fair_value_gap_percent = _fair_value_gap_percent(item)
    cautions: list[str] = []
    if method_count is not None and int(method_count) < 2:
        cautions.append("合理价值仅由单一估值方法支撑，需要先核验")
    if min_peer_count is not None and int(min_peer_count) < 5:
        cautions.append(f"估值可比样本仅 {int(min_peer_count)} 家，需要先核验")
    if fair_value_gap_percent is not None and fair_value_gap_percent >= 300.0:
        cautions.append("合理价值中枢与现价偏离过大，需要先核验估值输入")
    return {
        "method_count": int(method_count) if method_count is not None else None,
        "min_peer_count": int(min_peer_count) if min_peer_count is not None else None,
        "method_names": [str(value) for value in raw.get("method_names") or [] if value],
        "status": "REVIEW_REQUIRED" if cautions else "READY",
    }, cautions


class FocusSelectionService:
    """Classify active low-value leaders without changing upstream research."""

    def __init__(
        self,
        *,
        pool_repository: LowValueLeaderPoolRepository | Any | None = None,
        risk_snapshot_repository: LowValueRiskSnapshotRepository | Any | None = None,
        preparation_repository: RiskResearchPreparationRepository | Any | None = None,
        thesis_repository: CompanyThesisRepository | Any | None = None,
    ) -> None:
        self.pool_repository = pool_repository or LowValueLeaderPoolRepository()
        db_path = getattr(self.pool_repository, "db_path", None)
        self.risk_snapshot_repository = risk_snapshot_repository or LowValueRiskSnapshotRepository(db_path)
        self.preparation_repository = preparation_repository or RiskResearchPreparationRepository(db_path)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(db_path)

    @staticmethod
    def _selection_as_of(active: list[dict[str, Any]], requested: str | None) -> str:
        dates = sorted({_day(item.get("source_as_of")) for item in active if _day(item.get("source_as_of"))})
        if not dates:
            raise ValueError("FOCUS_SELECTION_NO_ACTIVE_LOW_VALUE_POOL")
        # An explicit historical date is verified against the immutable pool
        # snapshot by get_focus_selection().  The latest active projection
        # only carries one date, so rejecting it here would make ?as_of
        # unusable for audited historical reads.
        return _day(requested) or dates[-1]

    @staticmethod
    def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        valuation_rank = 0 if item.get("valuation_status") == "DEEPLY_UNDERVALUED" else 1
        discount = _discount_to_mid(item)
        return (
            valuation_rank,
            -(_number(item.get("leader_score")) or 0.0),
            -(discount if discount is not None else float("-inf")),
            HISTORICAL_RANK.get(str(item.get("historical_valuation_status") or ""), 5),
            0 if item.get("support_status") == "AVAILABLE" else 1,
            str(item.get("stock_code") or ""),
        )

    @staticmethod
    def _thesis_for_as_of(thesis: dict[str, Any] | None, as_of: str) -> dict[str, Any] | None:
        if not thesis:
            return None
        source_date, created_date = _day(thesis.get("source_data_as_of")), _day(thesis.get("created_at"))
        if source_date and created_date and source_date <= as_of and created_date <= as_of:
            return thesis
        return None

    @staticmethod
    def _peer_count(item: dict[str, Any]) -> int | None:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        quality = metadata.get("data_quality") if isinstance(metadata.get("data_quality"), dict) else {}
        peers = quality.get("peer_comparables") if isinstance(quality.get("peer_comparables"), dict) else {}
        value = _number(peers.get("peer_count"))
        return int(value) if value is not None else None

    @staticmethod
    def _reasons(item: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if item.get("valuation_status") == "DEEPLY_UNDERVALUED":
            reasons.append("当前处于深度低估区域")
        else:
            reasons.append("当前处于低估区域")
        if int(item.get("leader_rank") or 0) == 1:
            reasons.append("当前 L3 细分行业排名 Top1")
        elif int(item.get("leader_rank") or 0) == 2:
            reasons.append("当前 L3 细分行业排名 Top2")
        historical = str(item.get("historical_valuation_status") or "")
        if historical == "VERY_CHEAP":
            reasons.append("历史估值也处于较低位置")
        elif historical == "CHEAP":
            reasons.append("历史估值处于较低位置")
        discount = _discount_to_mid(item)
        if len(reasons) < 3 and discount is not None and discount > 0:
            reasons.append(f"当前价格低于合理价值中枢约 {discount * 100:.1f}%")
        return reasons[:3]

    @staticmethod
    def _cautions(
        *,
        risk: dict[str, Any] | None,
        preparation: dict[str, Any] | None,
        thesis: dict[str, Any] | None,
        peer_count: int | None,
        valuation_quality_cautions: list[str],
    ) -> tuple[list[str], bool]:
        # Method quality comes first: a sparse or extreme comparable-based
        # value range must not be visually buried below generic risk notes.
        cautions: list[str] = list(valuation_quality_cautions)
        soft = False
        overall = str((risk or {}).get("overall_risk") or "UNKNOWN")
        trap = str((risk or {}).get("value_trap_risk") or "UNKNOWN")
        if overall == "MEDIUM":
            cautions.append("当前风险为中等，需要继续观察")
        elif overall == "UNKNOWN":
            cautions.append("风险资料不足，暂无法完整判断")
            soft = True
        if trap == "HIGH_TRAP_RISK" and overall != "HIGH":
            cautions.append("低估原因需要继续核验")
            soft = True
        elif trap == "MEDIUM_TRAP_RISK":
            cautions.append("低估原因仍需继续核验")
        status = str((thesis or {}).get("status") or "MISSING")
        authority = str((thesis or {}).get("authority_status") or "")
        if status == "WEAKENING":
            cautions.append("公司核心逻辑正在减弱")
            soft = True
        elif authority == "AI_PROVISIONAL":
            cautions.append("公司核心逻辑由 AI 初步形成，待人工复核")
        elif not thesis:
            cautions.append("当前尚未建立可用于该日期的公司核心逻辑")
            soft = True
        # Profile completeness and peer sample size are important disclosure
        # cautions, but not an automatic B-tier gate.  The active pool has many
        # PARTIAL profiles and AI-provisional theses; treating either as a
        # mandatory exclusion would contradict the product's representative
        # case (605108) and make the A tier a data-completeness ranking.
        if str((preparation or {}).get("business_profile_status") or "MISSING") == "PARTIAL":
            cautions.append("主营业务资料仅部分完整")
        if peer_count is not None and peer_count < 5 and not any("估值可比样本" in note for note in cautions):
            cautions.append(f"同行有效样本仅 {peer_count} 家，同行比较需谨慎解读")
        return cautions[:3], soft

    @staticmethod
    def _hard_reason(*, risk: dict[str, Any] | None, preparation: dict[str, Any] | None,
                     thesis: dict[str, Any] | None, item: dict[str, Any]) -> str | None:
        if str((thesis or {}).get("status") or "") == "FALSIFIED":
            return "THESIS_FALSIFIED"
        if str((risk or {}).get("overall_risk") or "UNKNOWN") == "HIGH":
            return "RISK_HIGH"
        if str(item.get("entry_level") or "") == "BLOCKED":
            return "ENTRY_BLOCKED"
        if str((preparation or {}).get("financial_status") or "MISSING") != "READY":
            return "FINANCIAL_NOT_READY"
        return None

    def _project_company(self, item: dict[str, Any], *, as_of: str) -> dict[str, Any]:
        market, code = str(item.get("market") or "CN").upper(), str(item.get("stock_code") or "").upper()
        risk = self.risk_snapshot_repository.get(market, code, as_of)
        # Same-day preparation is materialized asynchronously and may still be
        # running when this projection executes; fall back to the latest PIT
        # preparation on or before the research day so a still-running same-day
        # job cannot silently hard-demote the whole A tier.
        preparation = (
            self.preparation_repository.get(market, code, as_of)
            or (self.preparation_repository.latest_on_or_before(market, code, as_of)
                if hasattr(self.preparation_repository, "latest_on_or_before") else None)
        )
        thesis = self._thesis_for_as_of(self.thesis_repository.get_current_thesis(market, code), as_of)
        peer_count = self._peer_count(item)
        valuation_quality, valuation_quality_cautions = _valuation_quality(item)
        hard = self._hard_reason(risk=risk, preparation=preparation, thesis=thesis, item=item)
        cautions, soft = self._cautions(
            risk=risk, preparation=preparation, thesis=thesis, peer_count=peer_count,
            valuation_quality_cautions=valuation_quality_cautions,
        )
        soft = soft or bool(valuation_quality_cautions)
        discount = _discount_to_mid(item)
        source_dates = {
            "low_value_pool": _day(item.get("source_as_of")),
            "risk_snapshot": _day((risk or {}).get("source_as_of")),
            "preparation": _day((preparation or {}).get("research_as_of")),
            "thesis_source": _day((thesis or {}).get("source_data_as_of")),
            "thesis_created": _day((thesis or {}).get("created_at")),
        }
        return {
            "stock_code": code,
            "company_name": str(item.get("company_name") or code),
            "industry_code": str(item.get("industry_code") or ""),
            "industry_name": str(item.get("industry_name") or "资料不足"),
            "leader_rank": int(item.get("leader_rank") or 0),
            "leader_score": _number(item.get("leader_score")),
            "valuation_status": str(item.get("valuation_status") or "INSUFFICIENT_DATA"),
            "current_price": _number(item.get("current_price")),
            "fair_value_mid": _number(item.get("fair_value_mid")),
            "discount_to_mid": discount,
            "historical_valuation_status": item.get("historical_valuation_status"),
            "support_status": item.get("support_status"),
            "entry_level": item.get("entry_level"),
            "risk_status": str((risk or {}).get("overall_risk") or "UNKNOWN"),
            "value_trap_risk": str((risk or {}).get("value_trap_risk") or "UNKNOWN"),
            "thesis_status": str((thesis or {}).get("status") or "MISSING"),
            "thesis_authority": (thesis or {}).get("authority_status"),
            "financial_status": str((preparation or {}).get("financial_status") or "MISSING"),
            "business_profile_status": str((preparation or {}).get("business_profile_status") or "MISSING"),
            "peer_count": peer_count,
            "valuation_quality": valuation_quality,
            "focus_reasons": self._reasons(item),
            "focus_cautions": cautions,
            "primary_demotion_reason": HARD_C_REASONS.get(hard) if hard else None,
            "_hard": hard is not None,
            "_soft": soft,
            "_sort_key": self._sort_key(item),
            "source_dates": source_dates,
        }

    @staticmethod
    def _public(item: dict[str, Any], tier: str, *, primary_demotion_reason: str | None = None) -> dict[str, Any]:
        return {key: value for key, value in item.items() if not key.startswith("_")} | {
            "tier": tier,
            "primary_demotion_reason": primary_demotion_reason if primary_demotion_reason is not None else item.get("primary_demotion_reason"),
        }

    def get_focus_selection(self, *, as_of: str | None = None) -> dict[str, Any]:
        current_active = list(self.pool_repository.active("CN"))
        research_as_of = self._selection_as_of(current_active, as_of)
        # The current pool is a projection.  For an explicitly requested past
        # day, read its immutable pool snapshot instead of mixing it with the
        # latest ACTIVE rows.
        if as_of and _day(as_of) != _day(current_active[0].get("source_as_of")):
            active = list(self.pool_repository.snapshots_for_as_of(research_as_of, "CN"))
        else:
            active = current_active
        active = [item for item in active if _day(item.get("source_as_of")) == research_as_of]
        if not active:
            raise ValueError(f"FOCUS_SELECTION_AS_OF_UNAVAILABLE: {research_as_of}")
        projected = [self._project_company(item, as_of=research_as_of) for item in active]
        projected.sort(key=lambda item: item["_sort_key"])

        hard_c = [item for item in projected if item["_hard"]]
        eligible = [item for item in projected if not item["_hard"]]
        normal = [item for item in eligible if not item["_soft"]]
        a_source = normal[:10]
        a_codes = {item["stock_code"] for item in a_source}
        b_source = [item for item in eligible if item["stock_code"] not in a_codes][:20]
        b_codes = {item["stock_code"] for item in b_source}
        selected_codes = a_codes | b_codes
        c_source = [item for item in projected if item["_hard"] or item["stock_code"] not in selected_codes]

        a = [self._public(item, "A") for item in a_source]
        b = [self._public(item, "B", primary_demotion_reason=(
            "合理价值依据需要先核验" if str((item.get("valuation_quality") or {}).get("status") or "") == "REVIEW_REQUIRED"
            else "存在需要继续观察的风险或资料缺口" if item["_soft"]
            else "A 档名额已满，当前排序位于后续研究序列"
        )) for item in b_source]
        c = [self._public(item, "C", primary_demotion_reason=(
            item.get("primary_demotion_reason") or "当前排序未进入优先研究范围"
        )) for item in c_source]
        return {
            "research_as_of": research_as_of,
            "total_low_value": len(projected),
            "hard_c_count": len(hard_c),
            "soft_demote_count": sum(1 for item in eligible if item["_soft"]),
            "A_count": len(a), "B_count": len(b), "C_count": len(c),
            "A": a, "B": b, "C": c,
            "selection_boundary": "仅用于确定当前低估龙头的研究优先级，不构成买卖建议、收益预测或交易信号。",
            "read_only": True,
        }


_service: FocusSelectionService | None = None


def get_focus_selection_service() -> FocusSelectionService:
    global _service
    if _service is None:
        _service = FocusSelectionService()
    return _service
