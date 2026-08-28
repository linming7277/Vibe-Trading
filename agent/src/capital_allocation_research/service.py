"""Deterministic capital-allocation research built solely from fact-layer data.

The service intentionally does not assign a score, judge management, emit risk
events, update Thesis/Moat/Leader data, or express trading semantics.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.capital_allocation_facts import CapitalAllocationFactService, get_capital_allocation_fact_service
from src.research_workspace.store import normalize_market, normalize_symbol


FORMULA_VERSION = "capital-allocation-research-v1.0.0"
DIMENSIONS = ("reinvestment", "dividend", "debt_management", "equity_dilution", "cash_management", "buyback", "m_and_a")


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(current: Any, previous: Any) -> float | None:
    value, base = _number(current), _number(previous)
    if value is None or base is None or base <= 0:
        return None
    return round((value / base - 1) * 100, 4)


def _delta(current: Any, previous: Any) -> float | None:
    value, base = _number(current), _number(previous)
    return round(value - base, 4) if value is not None and base is not None else None


def _source_refs(*items: dict[str, Any] | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        for ref in (item or {}).get("source_refs") or []:
            if not isinstance(ref, dict):
                continue
            key = str(ref.get("source_record_id") or ref)
            if key not in seen:
                seen.add(key)
                refs.append(deepcopy(ref))
    return refs


def _dimension(
    *,
    status: str,
    direction: str,
    observation: str,
    fact_refs: list[dict[str, Any]] | None = None,
    comparison_window: dict[str, Any] | None = None,
    data_gaps: list[str] | None = None,
    pit_status: str = "STRICT",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "direction": direction,
        "observation": observation,
        "fact_refs": fact_refs or [],
        "comparison_window": comparison_window or {},
        "data_gaps": data_gaps or [],
        "pit_status": pit_status,
        **extra,
    }


class CapitalAllocationResearchService:
    """Generate an explanatory projection from an existing fact service."""

    def __init__(self, *, fact_service: CapitalAllocationFactService | None = None) -> None:
        self.fact_service = fact_service or get_capital_allocation_fact_service()

    @staticmethod
    def _timeline(facts: dict[str, Any]) -> list[dict[str, Any]]:
        rows = list(((facts.get("financial_timeline") or {}).get("items") or []))
        return sorted((row for row in rows if isinstance(row, dict)), key=lambda row: str(row.get("report_date") or ""))

    def _reinvestment(self, timeline: list[dict[str, Any]]) -> dict[str, Any]:
        valid = [row for row in timeline if _number(row.get("capex")) is not None]
        if len(valid) < 2:
            return _dimension(
                status="UNKNOWN", direction="UNKNOWN", observation="年度资本开支历史不足，暂不能观察再投资节奏。",
                data_gaps=["INSUFFICIENT_CAPEX_HISTORY"], signal="INSUFFICIENT_HISTORY",
            )
        observations: list[dict[str, Any]] = []
        signals: list[str] = []
        for index, row in enumerate(valid):
            prior = valid[index - 1] if index else None
            later = [candidate for candidate in timeline if str(candidate.get("report_date") or "") > str(row.get("report_date") or "")][:2]
            capex_change = _pct(row.get("capex"), prior.get("capex")) if prior else None
            outcome_rows = []
            for candidate in later:
                revenue_change = _pct(candidate.get("revenue"), row.get("revenue"))
                profit_change = _pct(candidate.get("net_profit"), row.get("net_profit"))
                ocf_change = _pct(candidate.get("operating_cash_flow"), row.get("operating_cash_flow"))
                positive = sum(value is not None and value > 0 for value in (revenue_change, profit_change, ocf_change))
                negative = sum(value is not None and value < 0 for value in (revenue_change, profit_change, ocf_change))
                outcome_rows.append({
                    "outcome_report_date": candidate.get("report_date"),
                    "revenue_change_pct": revenue_change,
                    "net_profit_change_pct": profit_change,
                    "operating_cash_flow_change_pct": ocf_change,
                    "source_refs": _source_refs(candidate),
                    "positive_metric_count": positive,
                    "negative_metric_count": negative,
                })
            if not later:
                signal = "OUTCOME_NOT_YET_OBSERVABLE"
            elif capex_change is None or capex_change < 10:
                signal = "MIXED_SIGNAL"
            else:
                positive = max((item["positive_metric_count"] for item in outcome_rows), default=0)
                negative = max((item["negative_metric_count"] for item in outcome_rows), default=0)
                signal = "EFFECTIVE_SIGNAL" if positive >= 2 else "PRESSURE_SIGNAL" if negative >= 2 else "MIXED_SIGNAL"
            if signal in {"EFFECTIVE_SIGNAL", "MIXED_SIGNAL", "PRESSURE_SIGNAL"}:
                signals.append(signal)
            observations.append({
                "capex_report_date": row.get("report_date"),
                "capex": row.get("capex"),
                "capex_change_pct": capex_change,
                "capex_to_ocf": row.get("capex_to_ocf"),
                "capex_to_revenue": row.get("capex_to_revenue"),
                "outcomes": outcome_rows,
                "signal": signal,
                "comparison_window": {
                    "capital_period": row.get("report_date"),
                    "outcome_periods": [item.get("outcome_report_date") for item in outcome_rows],
                },
                "source_refs": _source_refs(row, *(later or [])),
            })
        effective, pressure = signals.count("EFFECTIVE_SIGNAL"), signals.count("PRESSURE_SIGNAL")
        observable = len(signals)
        if observable == 0:
            status, direction, signal, text = "PARTIAL", "UNKNOWN", "INSUFFICIENT_HISTORY", "已有资本开支记录，但最新投入尚无后续年度结果可观察。"
        elif effective > pressure and effective > 0:
            status, direction, signal, text = "SUPPORTED", "POSITIVE", "EFFECTIVE_SIGNAL", "部分资本开支增加后的后续年度，收入、利润或经营现金流出现同步改善迹象。"
        elif pressure > effective and pressure > 0:
            status, direction, signal, text = "SUPPORTED", "CAUTION", "PRESSURE_SIGNAL", "部分资本开支增加后的可见后续年度，多个经营结果指标未同步改善，需继续观察。"
        else:
            status, direction, signal, text = "PARTIAL", "NEUTRAL", "MIXED_SIGNAL", "资本开支与后续经营结果的可见信号不一致，当前不足以形成单一方向判断。"
        return _dimension(
            status=status, direction=direction, observation=text, signal=signal, observations=observations,
            fact_refs=_source_refs(*timeline),
            comparison_window={"method": "Capex period T is compared only with annual T+1/T+2 outcomes", "annual_points": len(timeline)},
            data_gaps=["NO_ROIC", "NO_PROJECT_LEVEL_CAPEX", "LATEST_OUTCOME_MAY_NOT_BE_OBSERVABLE"],
        )

    def _dividend(self, facts: dict[str, Any]) -> dict[str, Any]:
        source = facts.get("dividend_history") or {}
        events = [item for item in source.get("events") or [] if isinstance(item, dict) and item.get("normalization_status") == "READY"]
        years = sorted({str(item.get("event_date") or "")[:4] for item in events if str(item.get("event_date") or "")[:4].isdigit()})
        longest, run, previous = 0, 0, None
        for year in [int(value) for value in years]:
            run = run + 1 if previous is not None and year == previous + 1 else 1
            longest = max(longest, run)
            previous = year
        matched = [item for item in events if (item.get("dividend_to_net_profit") or {}).get("value") is not None or (item.get("dividend_to_ocf") or {}).get("value") is not None]
        exceeds = [item for item in matched if any(
            _number((item.get(key) or {}).get("value")) is not None and _number((item.get(key) or {}).get("value")) > 120
            for key in ("dividend_to_net_profit", "dividend_to_ocf")
        )]
        pit_status = str(source.get("pit_status") or "UNKNOWN")
        if not events:
            return _dimension(
                status="UNKNOWN", direction="UNKNOWN", observation="当前缓存中没有可可靠标准化的现金分红事实。",
                data_gaps=["DIVIDEND_DATA_UNAVAILABLE"], pit_status=pit_status, events=[],
            )
        if exceeds:
            status, direction, text = "SUPPORTED" if len(matched) >= 2 else "PARTIAL", "CAUTION", "部分已连接年度的分红金额高于对应净利润或经营现金流，需要核验年度口径和持续性。"
        elif longest >= 3 and len(matched) >= 2:
            status, direction, text = "SUPPORTED", "POSITIVE", "过去保存的数据中存在连续现金分红，且可连接的利润/经营现金流匹配记录未显示明显超出。"
        else:
            status, direction, text = "PARTIAL", "NEUTRAL", "存在已确认现金分红，但连续性或财务匹配数据仍有限。"
        return _dimension(
            status=status, direction=direction, observation=text, events=events,
            continuity={"calendar_years": years, "longest_consecutive_years": longest, "event_count": len(events)},
            matching={"matched_event_count": len(matched), "above_profit_or_ocf_count": len(exceeds)},
            fact_refs=[ref for item in events for ref in item.get("source_refs") or []],
            comparison_window={"linkage": "event date -> latest annual report visible at that date"},
            data_gaps=["DIVIDEND_PIT_LIMITED", "NO_DECLARATION_DATE"], pit_status=pit_status,
        )

    def _debt_management(self, timeline: list[dict[str, Any]]) -> dict[str, Any]:
        usable = [row for row in timeline if _number(row.get("liabilities")) is not None]
        if len(usable) < 2:
            return _dimension(status="UNKNOWN", direction="UNKNOWN", observation="负债历史不足，暂不能观察债务变化。", data_gaps=["INSUFFICIENT_DEBT_HISTORY"])
        first, latest = usable[0], usable[-1]
        liability_change = _pct(latest.get("liabilities"), first.get("liabilities"))
        debt_ratio_change = _delta(latest.get("debt_ratio"), first.get("debt_ratio"))
        interest_change = _delta(latest.get("interest_bearing_debt_ratio"), first.get("interest_bearing_debt_ratio"))
        ocf_change = _pct(latest.get("operating_cash_flow"), first.get("operating_cash_flow"))
        if debt_ratio_change is not None and debt_ratio_change >= 5 and (ocf_change is None or ocf_change <= 0):
            direction, text = "CAUTION", "负债率上升，且经营现金流未同步改善；当前只能提示继续核验，无法判断债务成本或到期压力。"
        elif debt_ratio_change is not None and debt_ratio_change <= -5:
            direction, text = "POSITIVE", "可见年度的负债率下降；当前资料不包含债务成本和到期结构。"
        else:
            direction, text = "NEUTRAL", "可见年度的债务规模或负债率存在变化，但尚未形成明显单一趋势。"
        status = "SUPPORTED" if len(usable) >= 3 and _number(latest.get("debt_ratio")) is not None else "PARTIAL"
        return _dimension(
            status=status, direction=direction, observation=text,
            trend={"liabilities_change_pct": liability_change, "debt_ratio_change_pp": debt_ratio_change, "interest_bearing_debt_ratio_change_pp": interest_change, "operating_cash_flow_change_pct": ocf_change, "cash_change": _pct(latest.get("cash_and_equivalents"), first.get("cash_and_equivalents"))},
            fact_refs=_source_refs(first, latest), comparison_window={"from_report_date": first.get("report_date"), "to_report_date": latest.get("report_date")},
            data_gaps=["DEBT_MATURITY_MISSING", "DEBT_COST_MISSING", "NO_BORROWING_REPAYMENT_CASH_FLOW"],
        )

    def _equity_dilution(self, facts: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[str, Any]:
        usable = [row for row in timeline if _number(row.get("total_shares")) is not None]
        capital = facts.get("share_capital_history") or {}
        events = list(capital.get("events") or [])
        if len(usable) < 2:
            return _dimension(status="UNKNOWN", direction="UNKNOWN", observation="年度总股本历史不足，暂不能观察股本变化。", data_gaps=["INSUFFICIENT_SHARE_HISTORY"], pit_status=str(capital.get("pit_status") or "UNKNOWN"))
        changes = [_pct(usable[index].get("total_shares"), usable[index - 1].get("total_shares")) for index in range(1, len(usable))]
        positive_changes = [value for value in changes if value is not None and value > 0.5]
        total_change = _pct(usable[-1].get("total_shares"), usable[0].get("total_shares"))
        if len(positive_changes) >= 2 and (total_change or 0) > 2:
            direction, text = "CAUTION", "年度总股本在多个可见年度增加，需要进一步核验各次变动的原因。"
        else:
            direction, text = "NEUTRAL", "可见年度的总股本存在变动或基本稳定；当前没有变动原因证据。"
        return _dimension(
            status="SUPPORTED" if len(usable) >= 3 else "PARTIAL", direction=direction, observation=text,
            trend={"total_share_change_pct": total_change, "annual_increase_count": len(positive_changes), "annual_observation_count": len(usable), "event_count": len(events), "change_reason": "UNKNOWN"},
            fact_refs=_source_refs(usable[0], usable[-1]),
            comparison_window={"from_report_date": usable[0].get("report_date"), "to_report_date": usable[-1].get("report_date")},
            data_gaps=["EQUITY_FINANCING_REASON_UNKNOWN"], pit_status=str(capital.get("pit_status") or "UNKNOWN"), share_capital_events=events,
        )

    def _cash_management(self, timeline: list[dict[str, Any]]) -> dict[str, Any]:
        usable = [row for row in timeline if _number(row.get("cash_and_equivalents")) is not None]
        if len(usable) < 2:
            return _dimension(status="UNKNOWN", direction="UNKNOWN", observation="现金余额历史不足，暂不能观察现金变化背景。", data_gaps=["INSUFFICIENT_CASH_HISTORY"])
        first, latest = usable[0], usable[-1]
        cash_change = _pct(latest.get("cash_and_equivalents"), first.get("cash_and_equivalents"))
        ocf_change = _pct(latest.get("operating_cash_flow"), first.get("operating_cash_flow"))
        capex_change = _pct(latest.get("capex"), first.get("capex"))
        debt_ratio = _number(latest.get("debt_ratio"))
        cash = _number(latest.get("cash_and_equivalents"))
        liabilities = _number(latest.get("liabilities"))
        high_cash_high_debt = bool(cash is not None and liabilities is not None and cash > 0 and debt_ratio is not None and debt_ratio >= 60)
        if cash_change is not None and cash_change <= -20 and (ocf_change is None or ocf_change < 0):
            direction, text = "CAUTION", "现金余额下降且经营现金流未同步改善；当前系统无法识别具体现金用途。"
        elif high_cash_high_debt:
            direction, text = "CAUTION", "当前同时存在现金余额和较高负债率，需结合融资成本、到期结构和现金用途进一步核验。"
        elif cash_change is not None and cash_change > 0:
            direction, text = "NEUTRAL", "现金余额在可见年度增加；当前系统尚不能判断其具体使用效率。"
        else:
            direction, text = "NEUTRAL", "现金余额存在变化，需要结合尚未接入的投资和融资现金流解释。"
        return _dimension(
            status="SUPPORTED" if len(usable) >= 3 else "PARTIAL", direction=direction, observation=text,
            context={"cash_change_pct": cash_change, "operating_cash_flow_change_pct": ocf_change, "capex_change_pct": capex_change, "latest_cash": cash, "latest_liabilities": liabilities, "latest_debt_ratio": debt_ratio, "high_cash_high_debt": high_cash_high_debt},
            fact_refs=_source_refs(first, latest), comparison_window={"from_report_date": first.get("report_date"), "to_report_date": latest.get("report_date")},
            data_gaps=["INVESTMENT_CASH_FLOW_MISSING", "FINANCING_CASH_FLOW_MISSING", "CASH_USE_MISSING", "DEBT_COST_MISSING"],
        )

    @staticmethod
    def _unknown_dimension(code: str, gap: str, text: str) -> dict[str, Any]:
        return _dimension(status="UNKNOWN", direction="UNKNOWN", observation=text, data_gaps=[gap], code=code)

    @staticmethod
    def _summary(dimensions: dict[str, dict[str, Any]]) -> str:
        clauses: list[str] = []
        reinvestment = dimensions["reinvestment"]
        if reinvestment["status"] != "UNKNOWN":
            clauses.append("已基于年度资本开支和后续经营数据观察再投资节奏")
        dividend = dimensions["dividend"]
        if dividend["status"] != "UNKNOWN":
            clauses.append("已整理已确认的现金分红及其与利润、经营现金流的连接")
        debt = dimensions["debt_management"]
        if debt["status"] != "UNKNOWN":
            clauses.append("已记录债务、现金和经营现金流的变化背景")
        equity = dimensions["equity_dilution"]
        if equity["status"] != "UNKNOWN":
            clauses.append("已观察年度总股本变化，但原因仍未知")
        beginning = "当前资料" + ("；".join(clauses) if clauses else "不足以形成资本配置研究")
        return beginning + "。由于缺少回购、并购、完整投资/融资现金流、股本变动原因及债务成本数据，尚不能形成完整资本去向闭环。"

    def get_research(self, market: str, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        facts = self.fact_service.get_history(normalized_market, symbol, as_of)
        timeline = self._timeline(facts)
        dimensions = {
            "reinvestment": self._reinvestment(timeline),
            "dividend": self._dividend(facts),
            "debt_management": self._debt_management(timeline),
            "equity_dilution": self._equity_dilution(facts, timeline),
            "cash_management": self._cash_management(timeline),
            "buyback": self._unknown_dimension("BUYBACK", "BUYBACK_DATA_MISSING", "当前没有回购或注销事件数据，不能由股本变化推断回购。"),
            "m_and_a": self._unknown_dimension("M_AND_A", "M_AND_A_DATA_MISSING", "当前没有并购、对价、商誉或投后结果事实，不能从财务波动推断并购。"),
        }
        strengths = [
            {"dimension": code, "observation": value["observation"], "direction": value["direction"]}
            for code, value in dimensions.items() if value["status"] == "SUPPORTED" and value["direction"] == "POSITIVE"
        ]
        cautions = [
            {"dimension": code, "observation": value["observation"], "direction": value["direction"]}
            for code, value in dimensions.items() if value["direction"] == "CAUTION"
        ]
        gaps = list(facts.get("data_gaps") or [])
        for value in dimensions.values():
            gaps.extend({"item": gap, "status": "MISSING", "reason": "研究维度所需事实尚未具备。"} for gap in value.get("data_gaps") or [] if gap not in {item.get("item") for item in gaps if isinstance(item, dict)})
        statuses = [value["status"] for value in dimensions.values() if value["status"] != "UNKNOWN"]
        return {
            "company": {"market": normalized_market, "stock_code": symbol},
            "research_as_of": facts.get("as_of"),
            "fact_layer_as_of": facts.get("as_of"),
            "status": "READY" if len(statuses) >= 3 else "PARTIAL" if statuses else "UNKNOWN",
            "dimensions": dimensions,
            "strengths": strengths,
            "cautions": cautions,
            "data_gaps": gaps,
            "capital_allocation_summary": self._summary(dimensions),
            "formula_version": FORMULA_VERSION,
            "fact_layer_formula_version": facts.get("formula_version"),
            "read_only": True,
            "pit_status": facts.get("pit_status"),
        }


_service: CapitalAllocationResearchService | None = None


def get_capital_allocation_research_service() -> CapitalAllocationResearchService:
    global _service
    if _service is None:
        _service = CapitalAllocationResearchService()
    return _service
