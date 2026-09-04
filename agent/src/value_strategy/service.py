"""Unified, read-only owner projection for Value Line strategy semantics."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.company_thesis import CompanyThesisRepository
from src.entry_research import get_entry_research_service
from src.exit_research import get_exit_research_service
from src.focus_selection import get_focus_selection_service
from src.leader_quality_profile import get_leader_quality_profile_service
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.research_workspace.store import normalize_market, normalize_symbol
from src.risk_research import get_risk_research_service
from src.value_price_zones import get_value_price_zone_service

from .reliability import valuation_reliability
from .suspension import infer_suspension
from .trading_calendar import cached_trading_dates
from .semantics import (
    ELIGIBILITY_LABELS,
    PRICE_ATTENTION_LABELS,
    PRICE_STRUCTURE_FRESHNESS_LABELS,
    PRIMARY_ACTION_LABELS,
    PRIORITY_LABELS,
    REVIEW_PRESSURE_LABELS,
)

FORMULA_VERSION = "value-strategy-state-projection-v1.0.0"


def _day(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in {float("inf"), float("-inf")} else None


def quote_as_of_probe(zones: dict[str, Any]) -> str | None:
    """Latest quote date visible to the read-only zone projection."""
    return _day(zones.get("price_as_of") or zones.get("as_of"))


def last_bar_volume(zones: dict[str, Any]) -> float | None:
    bars = _zone_bars(zones)
    return _number((bars[-1] or {}).get("volume")) if bars else None


def last_bar_amount(zones: dict[str, Any]) -> float | None:
    bars = _zone_bars(zones)
    return _number((bars[-1] or {}).get("amount")) if bars else None


def _zone_bars(zones: dict[str, Any]) -> list[dict[str, Any]]:
    bars = (zones.get("data_quality") or {}).get("daily_history") or {}
    raw = bars.get("last_bar") if isinstance(bars, dict) else None
    return [dict(raw)] if isinstance(raw, dict) else []


class ValueStrategyStateService:
    """Combine existing research outputs without changing or persisting them."""

    def __init__(
        self,
        *,
        pool_repository: Any | None = None,
        focus_service: Any | None = None,
        entry_service: Any | None = None,
        exit_service: Any | None = None,
        price_zone_service: Any | None = None,
        risk_service: Any | None = None,
        thesis_repository: Any | None = None,
        leader_service: Any | None = None,
    ) -> None:
        self.pool_repository = pool_repository or LowValueLeaderPoolRepository()
        self.focus_service = focus_service or get_focus_selection_service()
        self.entry_service = entry_service or get_entry_research_service()
        self.exit_service = exit_service or get_exit_research_service()
        self.price_zone_service = price_zone_service or get_value_price_zone_service()
        self.risk_service = risk_service or get_risk_research_service()
        db_path = getattr(self.pool_repository, "db_path", None)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(db_path)
        self.leader_service = leader_service or get_leader_quality_profile_service()

    @staticmethod
    def valuation_reliability(zones: dict[str, Any]) -> dict[str, Any]:
        # Delegates to the shared, versioned contract so PIT snapshot writers
        # persist exactly the rules this projection applies at read time.
        return valuation_reliability(zones)

    @staticmethod
    def price_structure_freshness(
        zones: dict[str, Any],
        trading_days: list[str] | None = None,
    ) -> dict[str, Any]:
        quality = dict((zones.get("data_quality") or {}).get("daily_history") or {})
        last_bar = _day(quality.get("last_date"))
        quote_date = _day(zones.get("price_as_of") or zones.get("as_of"))
        if not last_bar or not quote_date:
            status, gap, gap_trading = "UNKNOWN", None, None
        else:
            calendar_gap = max(0, (date.fromisoformat(quote_date) - date.fromisoformat(last_bar)).days)
            gap = calendar_gap
            gap_trading = None
            semantics = "CALENDAR_DAYS_FALLBACK"
            if trading_days:
                from .trading_calendar import TRADING_DAYS, trading_days_between

                computed, computed_semantics = trading_days_between(last_bar, quote_date, list(trading_days))
                if computed is not None:
                    gap_trading, semantics = computed, TRADING_DAYS
            else:
                semantics = "CALENDAR_DAYS_FALLBACK"
            effective = gap_trading if gap_trading is not None else calendar_gap
            status = (
                "FRESH" if effective == 0
                else "ACCEPTABLE" if effective <= 3
                else "STALE" if effective <= 5
                else "EXPIRED"
            )
        return {
            "status": status,
            "label": PRICE_STRUCTURE_FRESHNESS_LABELS[status],
            "last_bar_date": last_bar,
            "current_quote_date": quote_date,
            "gap_calendar_days": gap,
            "gap_trading_days": gap_trading,
            "gap_semantics": semantics if last_bar and quote_date else "CALENDAR_DAYS_FALLBACK",
        }

    @staticmethod
    def _find_focus(selection: dict[str, Any], symbol: str) -> dict[str, Any] | None:
        return next((item for tier in ("A", "B", "C") for item in selection.get(tier) or [] if item.get("stock_code") == symbol), None)

    @staticmethod
    def _authority_caution(thesis: dict[str, Any] | None) -> str | None:
        authority = str((thesis or {}).get("authority_status") or "")
        if authority == "AI_PROVISIONAL":
            return "当前核心逻辑为AI初步研究，尚未人工确认"
        if authority == "LEGACY_UNVERIFIED":
            return "历史核心逻辑尚未完成当前权威验证"
        if authority == "HUMAN_REJECTED":
            return "当前核心逻辑未获人工认可，需要重新复核"
        return None

    @staticmethod
    def _effective_price_attention(raw_level: str, reliability: str, freshness: str, reason_codes: list[str]) -> tuple[str, list[str]]:
        cautions: list[str] = []
        if reliability == "INSUFFICIENT":
            return "DATA_REVIEW_REQUIRED", ["估值依据不足，原始价格关注结果仅作为计算明细保留"]
        if reliability == "WEAK" and raw_level in {"HIGH_ATTENTION", "ATTENTION"}:
            return "VALUATION_REVIEW_REQUIRED", ["估值依据偏弱，不能把原始高关注结果作为老板主结论"]
        structure_used = bool({"VALUATION_SUPPORT_CONFLUENCE", "HISTORICAL_SUPPORT", "HIGH_SUPPORT", "MEDIUM_SUPPORT", "LOW_SUPPORT"} & set(reason_codes))
        if freshness in {"STALE", "EXPIRED"} and structure_used:
            cautions.append("支撑数据已偏旧，不再作为高关注主理由")
            if raw_level in {"HIGH_ATTENTION", "ATTENTION"}:
                return "WATCH", cautions
        return raw_level, cautions

    @staticmethod
    def _primary_action(*, eligible: bool, thesis_status: str, authority: str, risk: str, reliability: str, tier: str) -> str:
        if not eligible:
            return "OUTSIDE_VALUE_SCOPE"
        if thesis_status == "FALSIFIED" or authority == "HUMAN_REJECTED":
            return "THESIS_REVIEW"
        if risk == "HIGH":
            return "RISK_REVIEW"
        if reliability == "INSUFFICIENT":
            return "VALUATION_DATA_REVIEW"
        if tier == "A":
            return "PRIORITY_RESEARCH"
        if tier == "B":
            return "CONTINUE_OBSERVE"
        if tier == "C":
            return "DEFER_RESEARCH"
        return "CONTINUE_OBSERVE"

    @staticmethod
    def _summary(*, eligible: bool, tier: str, raw_level: str, reliability: str, risk: str, authority_caution: str | None, freshness_note: str | None) -> str:
        if not eligible:
            text = "当前不属于低估龙头研究范围；这不代表公司质量较差，只表示当前不同时满足L3有效龙头与低估资格。"
        elif risk == "HIGH":
            text = f"{PRICE_ATTENTION_LABELS.get(raw_level, '价格条件可供参考')}，但高风险已阻断整体研究优先级，当前应先完成风险复核。"
        elif tier == "B" and risk == "UNKNOWN":
            text = "估值条件较有吸引力，但风险资料仍不完整，继续观察并补充风险证据。"
        elif reliability == "INSUFFICIENT":
            text = "当前估值依据不足，应先核验估值数据，再判断价格关注条件。"
        elif reliability == "WEAK":
            text = "估值模型显示折价，但同行样本或合理价值结果可靠性偏弱，建议先核验估值依据。"
        else:
            text = f"当前属于{PRIORITY_LABELS.get(tier, '研究观察')}范围；{PRICE_ATTENTION_LABELS.get(raw_level, '价格条件待核验')}。"
        if authority_caution:
            text += f" {authority_caution}。"
        if freshness_note:
            text += f" {freshness_note}"
        return text

    def get_strategy_state(self, market: str, stock_code: str, *, research_as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        if normalized_market != "CN":
            raise ValueError("Value Strategy State 当前仅支持 A 股（CN）")

        active = list(self.pool_repository.active(normalized_market))
        latest_pool_as_of = max((_day(item.get("source_as_of")) or "" for item in active), default=None)
        pool_item = next((item for item in active if item.get("stock_code") == symbol), None)
        low_value_as_of = _day((pool_item or {}).get("source_as_of")) or latest_pool_as_of
        eligible = pool_item is not None
        try:
            selection = self.focus_service.get_focus_selection(as_of=research_as_of or low_value_as_of)
        except ValueError:
            selection = {"research_as_of": research_as_of or low_value_as_of, "A": [], "B": [], "C": []}
        focus = self._find_focus(selection, symbol) if eligible else None
        tier = str((focus or {}).get("tier") or "NOT_APPLICABLE") if eligible else "NOT_APPLICABLE"

        zones = self.price_zone_service.get_price_zones(normalized_market, symbol, as_of=research_as_of)
        entry = self.entry_service.get_entry_research(normalized_market, symbol, as_of=research_as_of)
        exit_result = self.exit_service.get_exit_research(normalized_market, symbol, as_of=research_as_of)
        risk = self.risk_service.get_risk_research(normalized_market, symbol, as_of=research_as_of)
        thesis = self.thesis_repository.get_current_thesis(normalized_market, symbol)
        leader = self.leader_service.get_profile(normalized_market, symbol, as_of=research_as_of)

        reliability = self.valuation_reliability(zones)
        trading_days = cached_trading_dates()
        structure_freshness = self.price_structure_freshness(
            zones, trading_days=trading_days,
        )
        suspension = infer_suspension(
            as_of=quote_as_of_probe(zones),
            last_bar_date=structure_freshness.get("last_bar_date"),
            last_bar_volume=last_bar_volume(zones),
            last_bar_amount=last_bar_amount(zones),
            trading_days=trading_days,
        )
        raw_level = str(entry.get("entry_level") or "WAIT")
        # 停牌推断（V1，非官方字段）：让"停牌没更新"不再被读成"数据坏了"。
        # 封顶必须在 _effective_price_attention 之前——停牌时 K 线停在停牌前
        # 是正常现象，不应被"支撑旧 → WATCH"降级逻辑误伤。
        if suspension["status"] == "SUSPENDED_INFERRED":
            price_cautions_seed = [f"停牌中（推断）：{suspension['reason']}"]
            if structure_freshness["status"] in {"STALE", "EXPIRED"}:
                structure_freshness = {
                    **structure_freshness,
                    "status": "ACCEPTABLE",
                    "label": PRICE_STRUCTURE_FRESHNESS_LABELS["ACCEPTABLE"],
                    "suspension_capped": True,
                }
        else:
            price_cautions_seed = []
        effective, price_cautions = self._effective_price_attention(
            raw_level, reliability["status"], structure_freshness["status"], list(entry.get("reason_codes") or []),
        )
        price_cautions = [*price_cautions_seed, *price_cautions]
        authority = str((thesis or {}).get("authority_status") or "MISSING")
        thesis_status = str((thesis or {}).get("status") or "MISSING")
        authority_caution = self._authority_caution(thesis)
        if authority_caution:
            price_cautions.append(authority_caution)
        risk_overall = str(risk.get("overall_risk") or "UNKNOWN")
        action = self._primary_action(
            eligible=eligible, thesis_status=thesis_status, authority=authority,
            risk=risk_overall, reliability=reliability["status"], tier=tier,
        )
        quote_as_of = _day(zones.get("price_as_of") or zones.get("as_of"))
        focus_as_of = _day(selection.get("research_as_of"))
        freshness_note = None
        if quote_as_of and focus_as_of and quote_as_of > focus_as_of:
            freshness_note = "日内行情已更新，研究优先级仍以最近完成的日终研究为准。"
        if not eligible and str((zones.get("valuation") or {}).get("status")) in {"UNDERVALUED", "DEEPLY_UNDERVALUED"} and quote_as_of and low_value_as_of and quote_as_of > low_value_as_of:
            freshness_note = "最新行情显示估值状态已变化，等待下一次日终研究刷新资格。"

        position = dict(leader.get("leader_position") or {})
        company = dict(leader.get("company") or {})
        summary = self._summary(
            eligible=eligible, tier=tier, raw_level=raw_level, reliability=reliability["status"],
            risk=risk_overall, authority_caution=authority_caution, freshness_note=freshness_note,
        )
        review_level = str(exit_result.get("exit_level") or "NORMAL")
        return {
            "stock_code": symbol,
            "stock_name": str((pool_item or {}).get("company_name") or company.get("stock_name") or symbol),
            "market": normalized_market,
            "research_as_of": research_as_of or focus_as_of or low_value_as_of or quote_as_of,
            "eligibility": {
                "status": "IN_VALUE_SCOPE" if eligible else "OUTSIDE_VALUE_SCOPE",
                "label": ELIGIBILITY_LABELS["IN_VALUE_SCOPE" if eligible else "OUTSIDE_VALUE_SCOPE"],
                "reason": "当前同时满足L3有效龙头Top1/Top2与低估/深度低估资格。" if eligible else "当前未同时满足L3有效龙头Top1/Top2与低估/深度低估资格；不代表公司质量较差。",
            },
            "priority": {"tier": tier, "label": PRIORITY_LABELS[tier], "reasons": list((focus or {}).get("focus_reasons") or [])},
            "price_attention": {
                "primary": eligible,
                "raw_level": raw_level,
                "raw_label": PRICE_ATTENTION_LABELS.get(raw_level, raw_level),
                "effective_status": effective,
                "effective_label": PRICE_ATTENTION_LABELS[effective],
                "score": entry.get("entry_score"),
                "valuation_reliability": reliability,
                "reasons": list(entry.get("reason_codes") or []),
                "cautions": list(dict.fromkeys(price_cautions)),
            },
            "review_pressure": {
                "primary": eligible,
                "raw_level": review_level,
                "effective_status": review_level,
                "effective_label": REVIEW_PRESSURE_LABELS[review_level],
                "score": exit_result.get("exit_score"),
                "reasons": list(exit_result.get("reason_codes") or []),
                "cautions": [authority_caution] if authority_caution else [],
            },
            "risk": {"overall": risk_overall, "trap": risk.get("value_trap_risk"), "summary": risk.get("summary")},
            "thesis": {
                "status": thesis_status,
                "authority": authority,
                "strategy_role": "AUTHORITATIVE" if authority == "HUMAN_CONFIRMED" else "REJECTED" if authority == "HUMAN_REJECTED" else "EXPLANATORY_ONLY",
                "caution": authority_caution,
            },
            "leader": {
                "rank": (pool_item or {}).get("leader_rank") or position.get("rank"),
                "state": position.get("status") or ("READY" if pool_item else "UNKNOWN"),
                "industry_name": (pool_item or {}).get("industry_name") or ((position.get("level3") or {}).get("name")),
                "as_of": _day(position.get("as_of") or leader.get("research_as_of")),
            },
            "freshness": {
                "market_price_as_of": quote_as_of,
                "low_value_as_of": low_value_as_of,
                "focus_as_of": focus_as_of,
                "historical_valuation_as_of": _day((zones.get("historical_valuation") or {}).get("as_of")),
                "price_structure_as_of": structure_freshness.get("last_bar_date"),
                "risk_as_of": _day(risk.get("as_of")),
                "thesis_as_of": _day((thesis or {}).get("source_data_as_of") or (thesis or {}).get("created_at")),
                "price_structure": structure_freshness,
                "suspension": suspension,
                "notice": freshness_note,
            },
            "summary": summary,
            "primary_action": {"status": action, "label": PRIMARY_ACTION_LABELS[action]},
            "reasons": list((focus or {}).get("focus_reasons") or []),
            "cautions": list(dict.fromkeys([
                *list((focus or {}).get("focus_cautions") or []),
                *price_cautions,
                *reliability.get("reasons", []),
            ])),
            "formula_version": FORMULA_VERSION,
            "read_only": True,
        }


_service: ValueStrategyStateService | None = None


def get_value_strategy_state_service() -> ValueStrategyStateService:
    global _service
    if _service is None:
        _service = ValueStrategyStateService()
    return _service
