"""Point-in-time, traceable capital-allocation facts from existing caches.

This module intentionally reports facts and data gaps only.  It never refreshes
TongDaXin, downloads disclosures, invokes a model, writes research state, or
labels a company's capital allocation as good or bad.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.company_actions.store import CompanyActionEventStore
from src.research_workspace.store import normalize_market, normalize_symbol
from src.tdx_data.financial_history import FinancialHistoryService
from src.tdx_data.store import TdxDataStore


FACT_LAYER_VERSION = "capital-allocation-facts-v1.0.0"
FINANCIAL_SOURCE_TYPE = "TDX_PROFESSIONAL_FINANCE"
DIVIDEND_SOURCE_TYPE = "TDX_SECURITY_DETAILS_DIVID_FACTORS"
CAPITAL_SOURCE_TYPE = "TDX_SECURITY_DETAILS_GB_INFO"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


@lru_cache(maxsize=65536)
def _parse_date_text(text: str) -> str | None:
    raw = text.strip().replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _date_text(value: Any) -> str | None:
    # Every dividend event re-reads the whole share-capital timeline, so the
    # same handful of raw date strings is normalized hundreds of thousands of
    # times per company.  The parse is pure, so it is memoized by input text.
    return _parse_date_text(str(value or ""))


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _ratio(numerator: float | None, denominator: float | None) -> dict[str, Any]:
    """Return a percentage only where the denominator is economically usable."""
    if numerator is None or denominator is None or denominator <= 0:
        return {"value": None, "status": "UNKNOWN"}
    return {"value": round(numerator / denominator * 100, 4), "status": "READY"}


def _change(current: float | None, previous: float | None, *, percent: bool = False) -> dict[str, Any]:
    if current is None or previous is None:
        return {"value": None, "status": "UNKNOWN"}
    if percent:
        if previous <= 0:
            return {"value": None, "status": "UNKNOWN"}
        return {"value": round((current / previous - 1) * 100, 4), "status": "READY"}
    return {"value": round(current - previous, 4), "status": "READY"}


class CapitalAllocationFactService:
    """Read existing TDX financial and single-security cache records only."""

    def __init__(
        self,
        *,
        tdx_store: TdxDataStore | None = None,
        financial_history: FinancialHistoryService | None = None,
        action_db_path: Path | None = None,
    ) -> None:
        self.tdx_store = tdx_store or TdxDataStore()
        self.financial_history = financial_history or FinancialHistoryService(self.tdx_store)
        self.action_db_path = action_db_path

    @staticmethod
    def _financial_trace(row: dict[str, Any], symbol: str) -> dict[str, Any]:
        report_date = str(row.get("report_date") or "") or None
        announcement_date = str(row.get("announcement_date") or "") or None
        return {
            "source_type": FINANCIAL_SOURCE_TYPE,
            "source_record_id": f"{symbol}:{report_date or ''}:{announcement_date or ''}",
            "source": row.get("source"),
            "report_date": report_date,
            "announcement_date": announcement_date,
            "event_date": None,
            "data_as_of": row.get("data_as_of") or announcement_date,
            "source_hash": row.get("raw_version"),
            "raw_version": row.get("raw_version"),
            "pit_status": "STRICT",
        }

    @staticmethod
    def _latest_annual_rows(rows: list[dict[str, Any]], *, as_of: str | None) -> list[dict[str, Any]]:
        """Keep one annual record per fiscal year, never mixing report types.

        Revised annual reports retain the latest announcement that was visible
        at the requested cutoff.  The source record stays attached to output.
        """
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            report_date = str(row.get("report_date") or "")
            announced = str(row.get("announcement_date") or "")
            if row.get("period_type") != "annual" or not report_date or not announced:
                continue
            if as_of and announced > as_of:
                continue
            prior = selected.get(report_date)
            if prior is None or announced >= str(prior.get("announcement_date") or ""):
                selected[report_date] = row
        return [selected[key] for key in sorted(selected)]

    @staticmethod
    def _detail_trace(
        *,
        source_type: str,
        symbol: str,
        source_record_id: str,
        raw: dict[str, Any],
        event_date: str | None,
        detail_updated_at: str | None,
    ) -> dict[str, Any]:
        return {
            "source_type": source_type,
            "source_record_id": f"{symbol}:{source_record_id}",
            "source": "TongDaXin security_details cache",
            "report_date": None,
            "announcement_date": None,
            "event_date": event_date,
            "data_as_of": detail_updated_at,
            "source_hash": _hash(raw),
            "raw_version": None,
            # TDX's detail cache contains event/ex-right dates but no evidence
            # that the event was visible at an earlier historical cutoff.
            "pit_status": "PIT_LIMITED",
        }

    def _annual_timeline(self, symbol: str, as_of: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        response = self.financial_history.query(symbol, as_of=as_of, period_type="annual")
        rows = self._latest_annual_rows(list(response.get("items") or []), as_of=as_of)
        timeline: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            prior = rows[index - 1] if index else None
            ocf = _number(row.get("operating_cash_flow"))
            capex = _number(row.get("capex"))
            cash = _number(row.get("cash_and_equivalents"))
            debt_ratio = _number(row.get("debt_ratio"))
            shares = _number(row.get("total_shares"))
            prior_cash = _number(prior.get("cash_and_equivalents")) if prior else None
            prior_debt_ratio = _number(prior.get("debt_ratio")) if prior else None
            prior_shares = _number(prior.get("total_shares")) if prior else None
            trace = self._financial_trace(row, symbol)
            debt_context = {
                "liabilities": _number(row.get("liabilities")),
                "current_liabilities": _number(row.get("current_liabilities")),
                "non_current_liabilities": _number(row.get("non_current_liabilities")),
                "debt_ratio": debt_ratio,
                "interest_bearing_debt_ratio": _number(row.get("interest_bearing_debt_ratio")),
                "debt_ratio_change": _change(debt_ratio, prior_debt_ratio),
                "status": "READY" if debt_ratio is not None else "PARTIAL",
                "source_refs": [trace],
            }
            timeline.append({
                "year": str(row.get("report_date") or "")[:4],
                "report_date": row.get("report_date"),
                "announcement_date": row.get("announcement_date"),
                "data_as_of": row.get("data_as_of") or row.get("announcement_date"),
                "flow_basis": row.get("flow_basis"),
                "operating_cash_flow": ocf,
                "capex": capex,
                "cash_and_equivalents": cash,
                "revenue": _number(row.get("revenue")),
                "net_profit": _number(row.get("net_profit")),
                "assets": _number(row.get("assets")),
                "liabilities": _number(row.get("liabilities")),
                "current_liabilities": _number(row.get("current_liabilities")),
                "non_current_liabilities": _number(row.get("non_current_liabilities")),
                "debt_ratio": debt_ratio,
                "interest_bearing_debt_ratio": _number(row.get("interest_bearing_debt_ratio")),
                "total_shares": shares,
                "roe": _number(row.get("roe")),
                "capex_to_ocf": _ratio(capex, ocf),
                "capex_to_revenue": _ratio(capex, _number(row.get("revenue"))),
                "cash_change": _change(cash, prior_cash),
                "debt_ratio_change": debt_context["debt_ratio_change"],
                "share_count_change": _change(shares, prior_shares, percent=True),
                "debt_context": debt_context,
                "source_refs": [trace],
            })
        return timeline, rows

    @staticmethod
    def _detail_payload(store: TdxDataStore, symbol: str) -> tuple[dict[str, Any], str | None]:
        record = store.get_record("security_details", symbol)
        if not record:
            return {}, None
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        updated = str(payload.get("updated_at") or record.get("updated_at") or "") or None
        return payload, updated

    @staticmethod
    def _capital_points(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        invalid: list[dict[str, Any]] = []
        for index, value in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(value, dict):
                invalid.append({"source_index": index, "raw": value, "status": "UNKNOWN_RAW_FIELD"})
                continue
            event_date, total = _date_text(value.get("Date")), _number(value.get("Zgb"))
            if not event_date or total is None or total <= 0:
                invalid.append({"source_index": index, "raw": value, "status": "UNKNOWN_RAW_FIELD"})
                continue
            grouped.setdefault(event_date, []).append({"source_index": index, "total_shares": total, "raw": value})
        points: list[dict[str, Any]] = []
        for event_date in sorted(grouped):
            candidates = grouped[event_date]
            totals = {item["total_shares"] for item in candidates}
            if len(totals) != 1:
                invalid.extend({"source_index": item["source_index"], "raw": item["raw"], "status": "UNKNOWN_RAW_FIELD"} for item in candidates)
                continue
            points.append(candidates[-1])
        return points, invalid

    def _share_capital_history(
        self,
        symbol: str,
        raw: Any,
        *,
        as_of: str | None,
        detail_updated_at: str | None,
        parsed: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        points, invalid = parsed if parsed is not None else self._capital_points(raw)
        points = [item for item in points if not as_of or _date_text(item["raw"].get("Date")) <= as_of]
        events: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for point in points:
            if previous is not None and point["total_shares"] != previous["total_shares"]:
                event_date = _date_text(point["raw"].get("Date"))
                trace = self._detail_trace(
                    source_type=CAPITAL_SOURCE_TYPE,
                    symbol=symbol,
                    source_record_id=f"capital:{point['source_index']}",
                    raw=point["raw"], event_date=event_date, detail_updated_at=detail_updated_at,
                )
                events.append({
                    "event_date": event_date,
                    "total_shares_before": previous["total_shares"],
                    "total_shares_after": point["total_shares"],
                    "change_pct": _change(point["total_shares"], previous["total_shares"], percent=True),
                    "change_reason": "UNKNOWN",
                    "status": "PARTIAL",
                    "pit_status": "PIT_LIMITED",
                    "source_refs": [trace],
                })
            previous = point
        return {
            "status": "PARTIAL" if points else "UNKNOWN",
            "pit_status": "PIT_LIMITED" if points else "UNKNOWN",
            "events": events,
            "raw_unknown_fields": invalid,
            "source": "TongDaXin:get_gb_info_by_date" if points else None,
            "detail_updated_at": detail_updated_at,
        }

    @staticmethod
    def _shares_timeline(points: list[dict[str, Any]]) -> list[str]:
        return [(_date_text(item["raw"].get("Date")) or "9999-12-31") for item in points]

    @staticmethod
    def _shares_as_of(points: list[dict[str, Any]], event_date: str, timeline: list[str] | None = None) -> float | None:
        # ``_capital_points`` emits points in date order, so the newest point
        # at or before the event is the one just left of the insertion index.
        dates = timeline if timeline is not None else CapitalAllocationFactService._shares_timeline(points)
        index = bisect_right(dates, event_date)
        return points[index - 1]["total_shares"] if index else None

    @staticmethod
    def _linked_annual(annual_rows: list[dict[str, Any]], event_date: str) -> dict[str, Any] | None:
        visible = [item for item in annual_rows if str(item.get("announcement_date") or "9999-12-31") <= event_date]
        return visible[-1] if visible else None

    def _dividend_history(
        self,
        symbol: str,
        raw: Any,
        *,
        as_of: str | None,
        detail_updated_at: str | None,
        annual_rows: list[dict[str, Any]],
        capital_raw: Any,
        capital_points: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        points = capital_points if capital_points is not None else self._capital_points(capital_raw)[0]
        shares_timeline = self._shares_timeline(points)
        events: list[dict[str, Any]] = []
        raw_unknown_fields: list[dict[str, Any]] = []
        for index, value in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(value, dict):
                raw_unknown_fields.append({"source_index": index, "raw": value, "status": "UNKNOWN_RAW_FIELD"})
                continue
            event_date = _date_text(value.get("Date"))
            if not event_date or (as_of and event_date > as_of):
                continue
            # Confirmed by the existing historical-valuation implementation:
            # Type=1 with Bonus uses Bonus as cash dividend per ten shares.
            # Other types/fields are retained only as unknown raw fields.
            bonus = _number(value.get("Bonus"))
            if str(value.get("Type") or "") != "1" or bonus is None or bonus < 0:
                raw_unknown_fields.append({"source_index": index, "event_date": event_date, "raw": value, "status": "UNKNOWN_RAW_FIELD"})
                continue
            cash_per_share = bonus / 10
            shares = self._shares_as_of(points, event_date, shares_timeline)
            cash_total = cash_per_share * shares if shares is not None else None
            linked = self._linked_annual(annual_rows, event_date)
            trace = self._detail_trace(
                source_type=DIVIDEND_SOURCE_TYPE,
                symbol=symbol,
                source_record_id=f"dividend:{index}", raw=value, event_date=event_date, detail_updated_at=detail_updated_at,
            )
            payout_profit = _ratio(cash_total, _number(linked.get("net_profit")) if linked else None)
            payout_ocf = _ratio(cash_total, _number(linked.get("operating_cash_flow")) if linked else None)
            events.append({
                "event_date": event_date,
                "cash_dividend_per_ten_shares": bonus,
                "cash_dividend_per_share": cash_per_share,
                "cash_dividend_total": cash_total,
                "bonus_share": _number(value.get("ShareBonus")),
                "rights_issue_ratio": _number(value.get("Allotment")),
                "rights_issue_price": _number(value.get("AllotPrice")),
                "normalization_status": "READY",
                "pit_status": "PIT_LIMITED",
                "linked_annual_report_date": linked.get("report_date") if linked else None,
                "linked_annual_announcement_date": linked.get("announcement_date") if linked else None,
                "dividend_to_net_profit": payout_profit,
                "dividend_to_ocf": payout_ocf,
                "source_refs": [trace],
            })
        return {
            "status": "PARTIAL" if events else "UNKNOWN",
            "pit_status": "PIT_LIMITED" if events else "UNKNOWN",
            "events": events,
            "raw_unknown_fields": raw_unknown_fields,
            "source": "TongDaXin:get_divid_factors:ex_date_proxy" if events else None,
            "detail_updated_at": detail_updated_at,
        }

    @staticmethod
    def _gaps(*, has_security_detail: bool) -> list[dict[str, str]]:
        return [
            {"item": "investment_cash_flow", "status": "MISSING", "reason": "专业财务事实层尚未映射投资活动现金流。"},
            {"item": "financing_cash_flow", "status": "MISSING", "reason": "专业财务事实层尚未映射筹资活动现金流。"},
            {"item": "buyback", "status": "MISSING", "reason": "尚无回购及注销事件数据。"},
            {"item": "m_and_a", "status": "MISSING", "reason": "尚无并购、对价、商誉及投后效果事件数据。"},
            {
                "item": "equity_financing_reason",
                "status": "RAW_NOT_STRUCTURED" if has_security_detail else "NOT_COLLECTED",
                "reason": "可观察到股本变动时，当前原始股本资料没有变动原因。" if has_security_detail else "尚未缓存该公司的通达信股本详情。",
            },
            {"item": "debt_maturity", "status": "MISSING", "reason": "没有完整债务到期结构。"},
            {"item": "debt_cost", "status": "MISSING", "reason": "没有融资成本或利率的标准化历史。"},
        ]

    def _prepared_actions(self, market: str, symbol: str, as_of: str | None) -> list[dict[str, Any]]:
        """Read the optional action layer without creating its schema on GET."""
        store = CompanyActionEventStore(self.action_db_path, initialize=False)
        try:
            return store.list_events(market, symbol, as_of=as_of)
        finally:
            store.close()

    @staticmethod
    def _apply_action_reasons(capital: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        by_date = {
            str(item.get("event_date")): item for item in actions
            if item.get("event_type") == "SHARE_CAPITAL_CHANGE" and item.get("event_date")
        }
        for event in capital.get("events") or []:
            action = by_date.get(str(event.get("event_date")))
            if not action:
                continue
            if _number(action.get("shares_before")) != _number(event.get("total_shares_before")) or _number(action.get("shares_after")) != _number(event.get("total_shares_after")):
                continue
            event["change_reason"] = action.get("reason") or "UNKNOWN"
            event["reason_source_event_id"] = action.get("reason_source_event_id")
            event["source_event_id"] = action.get("id")
            event["action_pit_status"] = action.get("pit_status")

    def get_history(self, market: str, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        target = date.fromisoformat(str(as_of)[:10]).isoformat() if as_of else None
        timeline, annual_rows = self._annual_timeline(symbol, target)
        detail, detail_updated_at = self._detail_payload(self.tdx_store, symbol)
        has_detail = bool(detail)
        # Both the share-capital timeline and every dividend event resolve
        # against the same raw share-capital records; parse them once.
        parsed_capital = self._capital_points(detail.get("capital"))
        capital = self._share_capital_history(
            symbol, detail.get("capital"), as_of=target, detail_updated_at=detail_updated_at,
            parsed=parsed_capital,
        )
        dividends = self._dividend_history(
            symbol, detail.get("dividends"), as_of=target, detail_updated_at=detail_updated_at,
            annual_rows=annual_rows, capital_raw=detail.get("capital"),
            capital_points=parsed_capital[0],
        )
        actions = self._prepared_actions(normalized_market, symbol, target)
        self._apply_action_reasons(capital, actions)
        gaps = self._gaps(has_security_detail=has_detail)
        snapshots = [
            {
                "year": row.get("year"),
                "operating_cash_flow": row.get("operating_cash_flow"),
                "capex": row.get("capex"),
                "cash_balance": row.get("cash_and_equivalents"),
                "debt_context": row.get("debt_context"),
                "dividend": {
                    "events": [item for item in dividends["events"] if str(item.get("linked_annual_report_date") or "") == str(row.get("report_date") or "")],
                    "status": dividends["status"],
                },
                "share_change": row.get("share_count_change"),
                "unexplained_items": gaps,
                "allocation_completeness": "PARTIAL",
                "source_refs": row.get("source_refs"),
            }
            for row in timeline
        ]
        return {
            "company": {"market": normalized_market, "stock_code": symbol},
            "as_of": target,
            "formula_version": FACT_LAYER_VERSION,
            "read_only": True,
            "financial_timeline": {"status": "READY" if timeline else "UNKNOWN", "pit_status": "STRICT" if timeline else "UNKNOWN", "items": timeline},
            "dividend_history": dividends,
            "share_capital_history": capital,
            "company_actions": {"status": "READY" if actions else "UNKNOWN", "events": actions},
            "cash_allocation_snapshots": snapshots,
            "allocation_completeness": "PARTIAL" if timeline else "UNKNOWN",
            "pit_status": "PIT_LIMITED" if has_detail else "STRICT" if timeline else "UNKNOWN",
            "data_gaps": gaps,
            "source_traceability": {
                "financial": {"source_type": FINANCIAL_SOURCE_TYPE, "pit_status": "STRICT"},
                "dividend": {"source_type": DIVIDEND_SOURCE_TYPE, "pit_status": dividends["pit_status"]},
                "share_capital": {"source_type": CAPITAL_SOURCE_TYPE, "pit_status": capital["pit_status"]},
            },
        }


_service: CapitalAllocationFactService | None = None


def get_capital_allocation_fact_service() -> CapitalAllocationFactService:
    global _service
    if _service is None:
        _service = CapitalAllocationFactService()
    return _service
