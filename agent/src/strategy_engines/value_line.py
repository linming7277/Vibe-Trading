"""Cached data pipeline for Macro -> 881 industry -> Leader Value Line V2."""

from __future__ import annotations

import math
import statistics
import threading
import uuid
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Callable

import pandas as pd

from src.tdx_data.financial_history import FinancialHistoryService, cagr
from src.tdx_data.service import get_tdx_service
from src.tdx_data.store import TdxDataStore, utc_now

from .common.normalization import cross_sectional_percentiles
from .common.provenance import stable_fingerprint
from .common.scoring import weighted_score
from .macro_data import MacroDataService
from .policy_data import PolicyDataService
from .value.leader_score_v2 import FORMULA_VERSION as LEADER_VERSION, WEIGHTS as LEADER_WEIGHTS, calculate as leader_calculate
from .value.macro_sector_v2 import FORMULA_VERSION as MATRIX_VERSION, describe as macro_sector_profile
from .value.sector_score_v2 import CONTEXT_FIELDS as SECTOR_CONTEXT_FIELDS, FORMULA_VERSION as SECTOR_VERSION, WEIGHTS as SECTOR_WEIGHTS, calculate as sector_calculate
from .value_data_store import ValueDataStore, now
from .value_market_history import BENCHMARK, ValueMarketHistoryService


MODULE_ORDER = ("financial_history", "market_history", "macro", "policy", "scores")
MODULE_LABELS = {
    "financial_history": "专业财务", "market_history": "历史行情",
    "macro": "宏观", "policy": "政策", "scores": "评分",
}
SECTOR_DATASET = "value_sector_scores_v2"
LEADER_DATASET = "value_leader_scores_v2"
# The candidate-track choice controls pool capacity, not a per-track quota.
# For example, the top 20 tracks produce one global top-100 leader pool.
TOTAL_LEADER_POOL_PER_TRACK = 5
INDUSTRY_DATASET = "value_industries"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _median(values: list[float | None], *, positive: bool = False) -> float | None:
    clean = [float(value) for value in values if value is not None and (not positive or value > 0)]
    return statistics.median(clean) if clean else None


def _mean(values: list[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def _return(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return None
    return (values[-1] / values[-periods - 1] - 1) * 100


def _volatility(values: list[float], periods: int) -> float | None:
    sample = values[-(periods + 1):]
    if len(sample) < periods + 1:
        return None
    returns = [(sample[index] / sample[index - 1] - 1) for index in range(1, len(sample)) if sample[index - 1] > 0]
    return statistics.pstdev(returns) * math.sqrt(252) * 100 if len(returns) >= 2 else None


def _max_drawdown(values: list[float], periods: int = 60) -> float | None:
    sample = values[-periods:]
    if len(sample) < 2:
        return None
    peak, worst = sample[0], 0.0
    for value in sample:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return abs(worst) * 100


def _confidence(coverage: float) -> str:
    return "HIGH" if coverage >= .85 else "MEDIUM" if coverage >= .60 else "LOW"


def _component_details(raw: dict[str, Any], normalized: dict[str, float | None], weights: dict[str, float], result: Any) -> list[dict[str, Any]]:
    return [{
        "name": key, "raw_value": raw.get(key), "normalized_value": normalized.get(key),
        "weight": weight, "contribution": (
            round(float(normalized[key]) * result.used_weights.get(key, 0), 4)
            if normalized.get(key) is not None and result.used_weights else None
        ),
    } for key, weight in weights.items()]


class ValueLineService:
    def __init__(
        self,
        *,
        cache: TdxDataStore | None = None,
        data_store: ValueDataStore | None = None,
    ) -> None:
        self.cache = cache or TdxDataStore()
        self.data_store = data_store or ValueDataStore()
        self.cache.ensure_modules(MODULE_ORDER)
        self._lock = threading.RLock()
        self._active_job: str | None = None
        # Background threads cannot survive an API-process restart.  Persist a
        # terminal state instead of leaving the UI spinning forever.
        for job in self.data_store.recent_jobs(limit=20):
            if job.get("status") in {"queued", "running"}:
                errors = list(job.get("errors") or [])
                errors.append({"module": str(job.get("current_module") or ""), "error": "service_restarted_before_completion"})
                self.data_store.update_job(
                    str(job["id"]), status="failed", current_module="", errors=errors, completed_at=now(),
                )
        for state in self.cache.module_states():
            if state.get("module") in MODULE_ORDER and state.get("status") == "running":
                self.cache.set_module_state(
                    str(state["module"]), status="failed", message="服务重启，未覆盖上次成功缓存",
                    error="service_restarted_before_completion", updated_at=utc_now(),
                )

    def close(self) -> None:
        self.data_store.close()

    def industries(self, *, live_if_missing: bool = False) -> list[dict[str, str]]:
        cached = self.cache.list_records(INDUSTRY_DATASET, limit=500)["items"]
        if cached:
            return [{"code": row["payload"]["code"], "name": row["payload"]["name"]} for row in cached]
        tdx = get_tdx_service()
        rows = tdx.store.list_records("sectors", limit=1000)["items"]
        industries = [
            {"code": str(row["key"]), "name": str(row.get("name") or row["payload"].get("name") or row["key"])}
            for row in rows if str(row["key"]).startswith("881") and str(row["key"]).endswith(".SH")
        ]
        if not industries and live_if_missing:
            live = tdx.client.call("get_sector_list", list_type=1) or []
            industries = [
                {"code": str(row.get("Code")), "name": str(row.get("Name") or row.get("Code"))}
                for row in live if str(row.get("Code") or "").startswith("881") and str(row.get("Code") or "").endswith(".SH")
            ]
        industries.sort(key=lambda item: item["code"])
        if industries:
            self.cache.replace_dataset(INDUSTRY_DATASET, [
                {"key": row["code"], "name": row["name"], "payload": row} for row in industries
            ])
        return industries

    def snapshot_memberships(self, as_of: str) -> dict[str, Any]:
        # The dedicated TDX sector refresh already persists every sector and
        # constituent locally.  Reuse that authoritative TDX snapshot before
        # calling the broker bridge again: a full live 881 scan is serial and
        # can block the whole value refresh when the desktop client is busy.
        cached_members = self.cache.list_records("sector_members", limit=100_000)["items"]
        cached_rows: list[dict[str, str]] = []
        for item in cached_members:
            payload = item.get("payload") or {}
            sector_code = str(payload.get("sector_code") or item.get("category") or "")
            symbol = str(payload.get("code") or "")
            if not (sector_code.startswith("881") and sector_code.endswith(".SH") and symbol):
                continue
            cached_rows.append({
                "sector_code": sector_code,
                "sector_name": str(payload.get("sector_name") or ""),
                "symbol": symbol,
                "source": "TongDaXin cached 881 second-level industry",
            })
        if cached_rows:
            count = self.data_store.replace_membership_snapshot(as_of, cached_rows)
            return {
                "status": "ready", "industries": len({row["sector_code"] for row in cached_rows}),
                "memberships": count, "as_of": as_of, "source": "tdx_cache",
            }

        industries = self.industries(live_if_missing=True)
        if not industries:
            raise RuntimeError("tdx_881_industries_unavailable")
        client = get_tdx_service().client
        rows: list[dict[str, str]] = []
        for industry in industries:
            members = client.call("get_stock_list_in_sector", industry["code"], list_type=1) or []
            for item in members:
                symbol = str(item.get("Code") if isinstance(item, dict) else item)
                if symbol:
                    rows.append({
                        "sector_code": industry["code"], "sector_name": industry["name"],
                        "symbol": symbol, "source": "TongDaXin 881 second-level industry",
                    })
        if not rows:
            raise RuntimeError("tdx_industry_memberships_unavailable")
        count = self.data_store.replace_membership_snapshot(as_of, rows)
        return {
            "status": "ready", "industries": len(industries), "memberships": count,
            "as_of": as_of, "source": "tdx_live",
        }

    def refresh_financial_history(self, progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        tdx = get_tdx_service()
        securities = tdx.store.list_records("securities", limit=10_000)["items"]
        symbols = [str(row["key"]) for row in securities]
        if not symbols:
            symbols = [str(row.get("Code")) for row in (tdx.client.call("get_stock_list", "5", list_type=1) or []) if row.get("Code")]
        return FinancialHistoryService(self.cache, tdx.client).collect(symbols, progress=progress)

    def refresh_market_history(self, as_of: str, progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        membership = self.snapshot_memberships(as_of)
        snapshot = self.data_store.memberships_as_of(as_of)
        symbols = sorted({row["symbol"] for row in snapshot["items"]})
        result = ValueMarketHistoryService(client=get_tdx_service().client).refresh(symbols, as_of=as_of, progress=progress)
        return {**result, "membership": membership}

    def refresh_macro(self, as_of: str) -> dict[str, Any]:
        service = MacroDataService(self.data_store)
        return service.refresh(as_of)

    def refresh_policy(self) -> dict[str, Any]:
        return PolicyDataService(self.data_store).refresh(self.industries(live_if_missing=True))

    def refresh_scores(self, as_of: str) -> dict[str, Any]:
        sectors, leaders, quality = self._calculate_scores(as_of)
        self.cache.upsert_records(SECTOR_DATASET, [
            {"key": f"{as_of}:{row['sector_code']}", "category": as_of, "name": row["sector_name"], "payload": row}
            for row in sectors
        ])
        self.cache.upsert_records(LEADER_DATASET, [
            {"key": f"{as_of}:{row['sector_code']}:{row['symbol']}", "category": f"{as_of}:{row['sector_code']}", "name": row["name"], "payload": row}
            for row in leaders
        ])
        return {"status": "ready" if any(row["score"] is not None for row in sectors) else "partial", "sectors": len(sectors), "leaders": len(leaders), "quality": quality}

    def _load_financials(self, as_of: str) -> dict[str, list[dict[str, Any]]]:
        rows = self.cache.list_records("financial_history", limit=250_000)["items"]
        latest_by_report: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for item in rows:
            row = item["payload"]
            if str(row.get("announcement_date") or "9999-12-31") <= as_of:
                symbol = str(row.get("symbol") or item.get("category") or "")
                report_date = str(row.get("report_date") or "")
                previous = latest_by_report[symbol].get(report_date)
                if not previous or str(row.get("announcement_date") or "") > str(previous.get("announcement_date") or ""):
                    latest_by_report[symbol][report_date] = row
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for symbol, reports in latest_by_report.items():
            result[symbol] = list(reports.values())
        for values in result.values():
            values.sort(key=lambda row: (row["report_date"], row["announcement_date"]))
        return result

    def _calculate_scores(self, as_of: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        membership = self.data_store.memberships_as_of(as_of)
        if membership["status"] != "ready":
            raise RuntimeError("membership_history_unavailable")
        macro = self.data_store.get_macro_snapshot(as_of)
        history = ValueMarketHistoryService().read(as_of)
        if history.empty:
            raise RuntimeError("market_history_unavailable")
        fundamentals = {row["key"]: row["payload"] for row in self.cache.list_records("fundamentals", limit=10_000)["items"]}
        quotes = {row["key"]: row["payload"] for row in self.cache.list_records("quotes", limit=10_000)["items"]}
        financials = self._load_financials(as_of)
        member_map: dict[str, list[str]] = defaultdict(list)
        names: dict[str, str] = {}
        for row in membership["items"]:
            member_map[row["sector_code"]].append(row["symbol"])
            names[row["sector_code"]] = row["sector_name"]
        price_map: dict[str, pd.DataFrame] = {
            str(symbol): frame.sort_values("trade_date") for symbol, frame in history.groupby("symbol")
        }
        benchmark_frame = price_map.get(BENCHMARK)
        benchmark_closes = list(benchmark_frame["close"].astype(float)) if benchmark_frame is not None else []
        benchmark_returns = {period: _return(benchmark_closes, period) for period in (5, 20, 60)}
        sector_raw: list[dict[str, Any]] = []
        sector_meta: list[dict[str, Any]] = []
        policy_service = PolicyDataService(self.data_store)
        market_latest_amount = 0.0
        for symbol, frame in price_map.items():
            if symbol != BENCHMARK and not frame.empty:
                market_latest_amount += float(frame.iloc[-1].get("amount") or 0)
        for sector_code in sorted(member_map):
            members = sorted(set(member_map[sector_code]))
            returns: dict[int, list[float]] = {5: [], 20: [], 60: []}
            latest_amounts: list[float] = []
            amount_accel_5: list[float] = []
            amount_accel_20: list[float] = []
            vols20: list[float] = []
            vols60: list[float] = []
            drawdowns: list[float] = []
            member_20d: list[float] = []
            active = 0
            for symbol in members:
                frame = price_map.get(symbol)
                if frame is None or frame.empty:
                    continue
                closes = list(frame["close"].astype(float))
                amounts = [float(value or 0) for value in frame["amount"].tolist()]
                symbol_returns: dict[int, float | None] = {}
                for period in returns:
                    value = _return(closes, period)
                    symbol_returns[period] = value
                    if value is not None:
                        returns[period].append(value)
                if symbol_returns[20] is not None:
                    member_20d.append(float(symbol_returns[20]))
                latest_amounts.append(amounts[-1] if amounts else 0)
                if len(amounts) >= 10 and _mean(amounts[-10:-5]) not in {None, 0}:
                    amount_accel_5.append((_mean(amounts[-5:]) or 0) / (_mean(amounts[-10:-5]) or 1))
                if len(amounts) >= 40 and _mean(amounts[-40:-20]) not in {None, 0}:
                    amount_accel_20.append((_mean(amounts[-20:]) or 0) / (_mean(amounts[-40:-20]) or 1))
                if len(amounts) >= 20 and amounts[-1] > statistics.median(amounts[-20:]):
                    active += 1
                if (value := _volatility(closes, 20)) is not None:
                    vols20.append(value)
                if (value := _volatility(closes, 60)) is not None:
                    vols60.append(value)
                if (value := _max_drawdown(closes)) is not None:
                    drawdowns.append(value)
            latest_annual = []
            for symbol in members:
                annual = [row for row in financials.get(symbol, []) if row.get("period_type") == "annual"]
                if annual:
                    latest_annual.append(annual[-1])
            revenue_yoy = [row.get("revenue_yoy") for row in latest_annual]
            profit_yoy = [row.get("net_profit_yoy") for row in latest_annual]
            roe_improved = []
            for symbol in members:
                annual = [row for row in financials.get(symbol, []) if row.get("period_type") == "annual"]
                if len(annual) >= 2 and annual[-1].get("roe") is not None and annual[-2].get("roe") is not None:
                    roe_improved.append(annual[-1]["roe"] > annual[-2]["roe"])
            pe = [fundamentals.get(symbol, {}).get("pe_ttm") for symbol in members]
            pb = [fundamentals.get(symbol, {}).get("pb_mrq") for symbol in members]
            dy = [fundamentals.get(symbol, {}).get("dividend_yield") for symbol in members]
            policy = policy_service.policy_fit(sector_code, as_of)
            macro_profile = macro_sector_profile(names[sector_code], dict(macro.get("axes") or {})) if macro else None
            raw = {
                "relative_5d": (_median(returns[5]) - benchmark_returns[5]) if _median(returns[5]) is not None and benchmark_returns[5] is not None else None,
                "relative_20d": (_median(returns[20]) - benchmark_returns[20]) if _median(returns[20]) is not None and benchmark_returns[20] is not None else None,
                "relative_60d": (_median(returns[60]) - benchmark_returns[60]) if _median(returns[60]) is not None and benchmark_returns[60] is not None else None,
                "up_breadth": sum(value > 0 for value in member_20d) / len(member_20d) * 100 if member_20d else None,
                "revenue_yoy_median": _median(revenue_yoy), "profit_yoy_median": _median(profit_yoy),
                "positive_profit_growth": sum((value or 0) > 0 for value in profit_yoy if value is not None) / sum(value is not None for value in profit_yoy) * 100 if any(value is not None for value in profit_yoy) else None,
                "roe_improvement": sum(roe_improved) / len(roe_improved) * 100 if roe_improved else None,
                "pe_median": _median(pe, positive=True), "pb_median": _median(pb, positive=True), "dividend_yield_median": _median(dy),
                "turnover_share": sum(latest_amounts) / market_latest_amount * 100 if market_latest_amount else None,
                "volume_acceleration": _mean([_mean(amount_accel_5), _mean(amount_accel_20)]),
                "active_breadth": active / len(members) * 100 if members else None,
                "volatility_20d": _median(vols20), "volatility_60d": _median(vols60),
                "max_drawdown": _median(drawdowns), "return_dispersion": statistics.pstdev(member_20d) if len(member_20d) >= 2 else None,
            }
            sector_raw.append(raw)
            sector_meta.append({
                "sector_code": sector_code, "sector_name": names[sector_code], "members": members,
                "member_coverage": len({symbol for symbol in members if symbol in price_map}) / len(members) if members else 0,
                "macro_fit": macro_profile["score"] if macro_profile else None,
                "macro_group": macro_profile["group"] if macro_profile else "unavailable",
                "macro_group_name": macro_profile["group_name"] if macro_profile else "不可用",
                "macro_exposure": macro_profile["exposure"] if macro_profile else {},
                "macro_stance": macro_profile["stance"] if macro_profile else "unavailable",
                "macro_drivers": macro_profile["drivers"] if macro_profile else [],
                "macro_matrix_explicit": bool(macro_profile and macro_profile["explicit"]),
                "policy_fit": policy["score"], "policy_events": policy["events"],
            })
        directions = {
            "relative_5d": True, "relative_20d": True, "relative_60d": True, "up_breadth": True,
            "revenue_yoy_median": True, "profit_yoy_median": True, "positive_profit_growth": True, "roe_improvement": True,
            "pe_median": False, "pb_median": False, "dividend_yield_median": True,
            "turnover_share": True, "volume_acceleration": True, "active_breadth": True,
            "volatility_20d": False, "volatility_60d": False, "max_drawdown": False, "return_dispersion": False,
        }
        normalized = cross_sectional_percentiles(sector_raw, directions)
        component_specs = {
            "momentum": ({"relative_5d": .20, "relative_20d": .30, "relative_60d": .30, "up_breadth": .20}),
            "earnings_momentum": ({"revenue_yoy_median": .30, "profit_yoy_median": .30, "positive_profit_growth": .25, "roe_improvement": .15}),
            "valuation": ({"pe_median": .40, "pb_median": .30, "dividend_yield_median": .30}),
            "capital_flow_proxy": ({"turnover_share": .40, "volume_acceleration": .40, "active_breadth": .20}),
            "risk_quality": ({"volatility_20d": .30, "volatility_60d": .25, "max_drawdown": .30, "return_dispersion": .15}),
        }
        sector_results: list[dict[str, Any]] = []
        for index, meta in enumerate(sector_meta):
            components = {name: weighted_score(normalized[index], weights, minimum_coverage=.50).score for name, weights in component_specs.items()}
            result = sector_calculate(components)
            effective_score = result.score if meta["member_coverage"] >= .80 else None
            effective_status = result.status if meta["member_coverage"] >= .80 else "insufficient_data"
            effective_coverage = min(result.coverage, meta["member_coverage"])
            missing = [key for key in SECTOR_WEIGHTS if components.get(key) is None]
            provenance = stable_fingerprint({
                "as_of": as_of, "sector": meta["sector_code"], "raw": sector_raw[index], "components": components,
                "formula": SECTOR_VERSION, "matrix": MATRIX_VERSION, "membership_as_of": membership["as_of"],
            })
            sector_results.append({
                **meta, "as_of": as_of, "score": effective_score, "base_score": effective_score,
                "coverage": effective_coverage, "confidence": _confidence(effective_coverage), "status": effective_status,
                "raw_features": sector_raw[index], "normalized_features": normalized[index], "component_scores": components,
                "components": _component_details(components, components, SECTOR_WEIGHTS, result),
                "missing_fields": missing, "formula_version": SECTOR_VERSION, "matrix_version": MATRIX_VERSION,
                "ranking_basis": list(SECTOR_WEIGHTS),
                "context_fields": {
                    "macro_fit": meta["macro_fit"],
                    "policy_fit": meta["policy_fit"],
                    "available": [name for name in SECTOR_CONTEXT_FIELDS if meta.get(name) is not None],
                    "missing": [name for name in SECTOR_CONTEXT_FIELDS if meta.get(name) is None],
                },
                "data_as_of": as_of, "sources": ["TongDaXin", "AKShare", "国家统计局", "中国人民银行"],
                "provenance_key": provenance,
            })
        sector_results.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["sector_code"]))
        for rank, row in enumerate(sector_results, 1):
            row["rank"] = rank
        macro_order = sorted(
            sector_results,
            key=lambda row: (row["macro_fit"] is None, -(row["macro_fit"] or 0), row["sector_code"]),
        )
        macro_ranks = {row["sector_code"]: rank for rank, row in enumerate(macro_order, 1)}
        for row in sector_results:
            row["macro_rank"] = macro_ranks[row["sector_code"]]
        leaders: list[dict[str, Any]] = []
        leader_coverages: list[float] = []
        missing_reasons: Counter[str] = Counter()
        for sector in sector_results:
            rows = self._leader_rows(
                sector["sector_code"], sector["sector_name"], sector["members"], as_of,
                financials, fundamentals, quotes,
            )
            leaders.extend(rows)
            for row in rows:
                leader_coverages.append(row["coverage"])
                missing_reasons.update(row["missing_fields"])
        quality = {
            "researchable": len(leader_coverages),
            "coverage_ge_085": sum(value >= .85 for value in leader_coverages) / len(leader_coverages) if leader_coverages else 0,
            "target_met": bool(leader_coverages) and sum(value >= .85 for value in leader_coverages) / len(leader_coverages) >= .85,
            "missing_reason_distribution": dict(missing_reasons),
        }
        return sector_results, leaders, quality

    def _leader_rows(
        self,
        sector_code: str,
        sector_name: str,
        members: list[str],
        as_of: str,
        financials: dict[str, list[dict[str, Any]]],
        fundamentals: dict[str, dict[str, Any]],
        quotes: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        identities: list[dict[str, str]] = []
        statuses: list[dict[str, Any]] = []
        for symbol in members:
            fundamental = fundamentals.get(symbol, {})
            name = str(fundamental.get("name") or quotes.get(symbol, {}).get("name") or symbol)
            if "ST" in name.upper() or "退" in name:
                continue
            annual = [row for row in financials.get(symbol, []) if row.get("period_type") == "annual"]
            latest = annual[-1] if annual else {}
            revenue_cagr = cagr([(row["report_date"], row.get("revenue")) for row in annual])
            profit_cagr = cagr([(row["report_date"], row.get("net_profit")) for row in annual])
            rev_growth = [row.get("revenue_yoy") for row in annual if row.get("revenue_yoy") is not None]
            profit_growth = [row.get("net_profit_yoy") for row in annual if row.get("net_profit_yoy") is not None]
            ocf = [row.get("operating_cash_flow") for row in annual if row.get("operating_cash_flow") is not None]
            shareholders = [row.get("shareholders") for row in annual if row.get("shareholders") not in {None, 0}]
            net_profit = latest.get("net_profit")
            revenue = latest.get("revenue")
            raw = {
                "market_cap": fundamental.get("market_cap_100m"), "revenue": revenue, "net_profit": net_profit,
                "roe": latest.get("roe"), "gross_margin": latest.get("gross_margin"), "net_margin": latest.get("net_margin"),
                "revenue_cagr": revenue_cagr["value"], "profit_cagr": profit_cagr["value"],
                "growth_consistency": _mean([
                    sum(value > 0 for value in rev_growth) / len(rev_growth) * 100 if rev_growth else None,
                    sum(value > 0 for value in profit_growth) / len(profit_growth) * 100 if profit_growth else None,
                ]),
                "growth_low_volatility": -_mean([
                    statistics.pstdev(rev_growth) if len(rev_growth) >= 2 else None,
                    statistics.pstdev(profit_growth) if len(profit_growth) >= 2 else None,
                ]) if _mean([
                    statistics.pstdev(rev_growth) if len(rev_growth) >= 2 else None,
                    statistics.pstdev(profit_growth) if len(profit_growth) >= 2 else None,
                ]) is not None else None,
                "cash_conversion": (latest.get("operating_cash_flow") / net_profit * 100) if latest.get("operating_cash_flow") is not None and net_profit not in {None, 0} else None,
                "ocf_margin": (latest.get("operating_cash_flow") / revenue * 100) if latest.get("operating_cash_flow") is not None and revenue not in {None, 0} else None,
                "positive_ocf_years": sum(value > 0 for value in ocf) / len(ocf) * 100 if ocf else None,
                "ocf_trend": ((ocf[-1] / ocf[-4]) ** (1 / 3) - 1) * 100 if len(ocf) >= 4 and ocf[-1] > 0 and ocf[-4] > 0 else None,
                "pe": fundamental.get("pe_ttm") if _number(fundamental.get("pe_ttm")) and float(fundamental["pe_ttm"]) > 0 else None,
                "pb": fundamental.get("pb_mrq") if _number(fundamental.get("pb_mrq")) and float(fundamental["pb_mrq"]) > 0 else None,
                "dividend_yield": fundamental.get("dividend_yield"),
                "debt_safety": -float(latest["debt_ratio"]) if latest.get("debt_ratio") is not None else None,
                "shareholder_stability": -abs((shareholders[-1] / shareholders[-2] - 1) * 100) if len(shareholders) >= 2 else None,
                "low_beta": -abs(float(fundamental["beta"])) if fundamental.get("beta") is not None else None,
            }
            candidates.append(raw)
            identities.append({"symbol": symbol, "name": name})
            statuses.append({"revenue_cagr": revenue_cagr["status"], "profit_cagr": profit_cagr["status"]})
        if not candidates:
            return []
        directions = {key: key not in {"pe", "pb"} for key in candidates[0]}
        directions["pe"], directions["pb"] = False, False
        normalized = cross_sectional_percentiles(candidates, directions)
        specs = {
            "industry_position": {"market_cap": .40, "revenue": .30, "net_profit": .30},
            "profitability": {"roe": .40, "gross_margin": .30, "net_margin": .30},
            "growth_stability": {"revenue_cagr": .30, "profit_cagr": .30, "growth_consistency": .20, "growth_low_volatility": .20},
            "cash_flow": {"cash_conversion": .30, "ocf_margin": .30, "positive_ocf_years": .20, "ocf_trend": .20},
            "valuation": {"pe": .40, "pb": .30, "dividend_yield": .30},
            "governance_risk": {"debt_safety": .40, "shareholder_stability": .30, "low_beta": .30},
        }
        result_rows = []
        for index, identity in enumerate(identities):
            components = {name: weighted_score(normalized[index], weights, minimum_coverage=.50).score for name, weights in specs.items()}
            score = leader_calculate(components)
            missing = [key for key in LEADER_WEIGHTS if components.get(key) is None]
            provenance = stable_fingerprint({
                "as_of": as_of, "sector": sector_code, "symbol": identity["symbol"],
                "raw": candidates[index], "components": components, "formula": LEADER_VERSION,
            })
            result_rows.append({
                **identity, "sector_code": sector_code, "sector_name": sector_name,
                "score": score.score, "base_score": score.score, "coverage": score.coverage,
                "confidence": _confidence(score.coverage), "status": score.status,
                "raw_features": candidates[index], "normalized_features": normalized[index],
                "component_scores": components, "components": _component_details(components, components, LEADER_WEIGHTS, score),
                "missing_fields": missing, "growth_status": statuses[index],
                "formula_version": LEADER_VERSION, "data_as_of": as_of,
                "sources": ["TongDaXin professional finance", "TongDaXin quote/fundamental"],
                "provenance_key": provenance,
            })
        result_rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["symbol"]))
        for rank, row in enumerate(result_rows, 1):
            row["rank"] = rank
        return result_rows

    def start_refresh(self, modules: list[str], as_of: str) -> dict[str, Any]:
        date.fromisoformat(as_of)
        requested = list(MODULE_ORDER) if not modules or modules == ["all"] else [module for module in MODULE_ORDER if module in set(modules)]
        unknown = sorted(set(modules) - set(MODULE_ORDER) - {"all"})
        if unknown or not requested:
            raise ValueError(f"unknown value modules: {unknown}")
        with self._lock:
            if self._active_job:
                active = self.data_store.get_job(self._active_job)
                if active and active["status"] in {"queued", "running"}:
                    raise RuntimeError(f"value refresh already running: {self._active_job}")
            job_id = f"value_{uuid.uuid4().hex[:16]}"
            self.data_store.create_job(job_id, requested, as_of)
            self._active_job = job_id
            threading.Thread(target=self._run_job, args=(job_id, requested, as_of), daemon=True, name="value-line-refresh").start()
            return self.data_store.get_job(job_id) or {"id": job_id, "status": "queued"}

    def _run_job(self, job_id: str, modules: list[str], as_of: str) -> None:
        results: dict[str, Any] = {}
        errors: list[dict[str, str]] = []
        self.data_store.update_job(job_id, status="running", started_at=now())
        for index, module in enumerate(modules):
            self.data_store.update_job(job_id, current_module=module, progress=index, results=results, errors=errors)
            self.cache.set_module_state(
                module, status="running", progress=0, total=0, started_at=utc_now(),
                message=f"正在更新{MODULE_LABELS[module]}", error="",
            )

            def progress(done: int, total: int, message: str) -> None:
                self.cache.set_module_state(module, progress=done, total=total, message=message)

            try:
                if module == "financial_history":
                    value = self.refresh_financial_history(progress)
                elif module == "market_history":
                    value = self.refresh_market_history(as_of, progress)
                elif module == "macro":
                    value = self.refresh_macro(as_of)
                elif module == "policy":
                    value = self.refresh_policy()
                else:
                    value = self.refresh_scores(as_of)
                    # Persist the refreshed V2 snapshot for the research workbench.
                    # The bridge is intentionally part of the score refresh so the
                    # sector page and downstream research never drift apart.
                    from src.value_workspace.service import ValueWorkspaceService
                    workspace = ValueWorkspaceService()
                    try:
                        snapshot = workspace.materialize_v2_snapshot(as_of=as_of, force_refresh=True)
                        value["workbench_run_id"] = (snapshot.get("run") or {}).get("id")
                    finally:
                        workspace.close()
                results[module] = value
                state = "partial" if value.get("status") == "partial" else "ready"
                self.cache.set_module_state(
                    module, status=state, progress=1, total=1,
                    item_count=int(
                        value.get("item_count") or value.get("rows") or value.get("series_rows")
                        or value.get("sectors") or value.get("events") or 0
                    ),
                    message=f"{MODULE_LABELS[module]}更新完成", metadata_json=value,
                    updated_at=utc_now(), last_success_at=utc_now(), error="",
                )
                if state == "partial":
                    errors.append({"module": module, "error": "partial"})
            except Exception as exc:
                errors.append({"module": module, "error": str(exc)})
                self.cache.set_module_state(
                    module, status="failed", message="更新失败，已保留上次成功缓存",
                    error=str(exc), updated_at=utc_now(),
                )
        status = "partial" if errors and results else "failed" if errors else "completed"
        self.data_store.update_job(
            job_id, status=status, current_module="", progress=len(modules), results=results,
            errors=errors, completed_at=now(),
        )
        with self._lock:
            if self._active_job == job_id:
                self._active_job = None

    def status(self) -> dict[str, Any]:
        states = {row["module"]: row for row in self.cache.module_states()}
        package = FinancialHistoryService(self.cache, get_tdx_service().client).package_status()
        modules = [{"code": code, "label": MODULE_LABELS[code], **states.get(code, {})} for code in MODULE_ORDER]
        latest_scores = self.cache.list_records(SECTOR_DATASET, limit=100_000)["items"]
        latest_score_as_of = max(
            (str(row["payload"].get("as_of") or "") for row in latest_scores), default="",
        ) or None
        return {
            "professional_finance": package, "modules": modules,
            "recent_jobs": self.data_store.recent_jobs(), "latest_score_as_of": latest_score_as_of,
            "schedule_template": {
                "name": "价值线工作日收盘后更新", "cron": "0 17 * * 1-5",
                "timezone": "Asia/Shanghai", "modules": list(MODULE_ORDER), "enabled": False,
            },
        }

    def macro(self, as_of: str | None = None) -> dict[str, Any] | None:
        return self.data_store.get_macro_snapshot(as_of)

    def policies(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        return self.data_store.policies(status, limit)

    def sectors(self, as_of: str | None = None, *, status: str | None = None, query: str = "") -> dict[str, Any]:
        target = as_of or self.status().get("latest_score_as_of")
        if not target:
            return {"as_of": as_of, "items": [], "total": 0}
        items = [row["payload"] for row in self.cache.list_records(SECTOR_DATASET, category=target, limit=500)["items"]]
        if status:
            items = [row for row in items if row.get("status") == status]
        if query:
            needle = query.lower()
            items = [row for row in items if needle in str(row.get("sector_name") or "").lower() or needle in str(row.get("sector_code") or "").lower()]
        items.sort(key=lambda row: (row.get("score") is None, -(row.get("score") or 0), row.get("sector_code") or ""))
        return {"as_of": target, "items": items, "total": len(items), "formula_version": SECTOR_VERSION}

    def leaders(
        self,
        sector_code: str | None = None,
        as_of: str | None = None,
        candidate_track_limit: int | None = None,
    ) -> dict[str, Any]:
        target = as_of or self.status().get("latest_score_as_of")
        if not target:
            return {"as_of": as_of, "sector_code": sector_code, "items": [], "total": 0}
        category = f"{target}:{sector_code}" if sector_code else None
        # Keep the current snapshot intact when this table contains multiple
        # daily runs.  Filtering a 10k page after reading it can otherwise
        # silently omit older rows from today's complete leader pool.
        cached = self.cache.list_records(LEADER_DATASET, category=category, limit=100_000)["items"]
        items = [
            row["payload"] for row in cached
            if str(row["payload"].get("as_of") or row["payload"].get("data_as_of") or "") == target
        ]
        if sector_code:
            items = [
                row for row in items
                if row.get("score") is not None and int(row.get("rank") or 1) <= TOTAL_LEADER_POOL_PER_TRACK
            ]
            items.sort(key=lambda row: (row.get("rank") or 10_000, -(row.get("score") or 0), row.get("symbol") or ""))
        else:
            sector_ranks = {
                str(row["payload"].get("sector_code") or ""): int(row["payload"].get("rank") or 10_000)
                for row in self.cache.list_records(SECTOR_DATASET, category=target, limit=500)["items"]
            }
            # A total leader pool is not a second copy of the A-share universe.
            # Candidate-track count determines its capacity, but does not give
            # every candidate track a five-company quota.  All scored members
            # of the selected tracks compete on one leader-score ranking.
            items = [
                {**row, "candidate_sector_rank": sector_ranks.get(str(row.get("sector_code") or ""), 10_000)}
                for row in items
                if row.get("score") is not None
                and (candidate_track_limit is None or sector_ranks.get(str(row.get("sector_code") or ""), 10_000) <= candidate_track_limit)
            ]
            # This is a cross-track research queue.  Sector rank is context
            # and a stable tie-breaker; it is never a primary ordering rule.
            items.sort(key=lambda row: (
                row.get("score") is None, -(row.get("score") or 0), -float(row.get("coverage") or 0),
                row["candidate_sector_rank"], row.get("rank") or 10_000, row.get("symbol") or "",
            ))
            # Be defensive if a future membership source assigns a company to
            # more than one track: retain its highest-priority appearance only.
            unique_items: list[dict[str, Any]] = []
            seen_symbols: set[str] = set()
            for row in items:
                symbol = str(row.get("symbol") or "")
                if symbol and symbol in seen_symbols:
                    continue
                if symbol:
                    seen_symbols.add(symbol)
                unique_items.append(row)
            pool_capacity = candidate_track_limit * TOTAL_LEADER_POOL_PER_TRACK if candidate_track_limit else None
            items = unique_items[:pool_capacity] if pool_capacity else unique_items
        return {
            "as_of": target, "sector_code": sector_code, "items": items, "total": len(items),
            "formula_version": LEADER_VERSION,
            "pool_rule": {
                "leaders_per_candidate_track": TOTAL_LEADER_POOL_PER_TRACK,
                "pool_capacity": candidate_track_limit * TOTAL_LEADER_POOL_PER_TRACK if candidate_track_limit else None,
                "candidate_track_limit": candidate_track_limit,
                "scored_only": True,
                "deduplicated_by_symbol": True,
                "ordering": "leader_score_desc_global_capacity",
            },
        }


_service: ValueLineService | None = None


def get_value_line_service() -> ValueLineService:
    global _service
    if _service is None:
        _service = ValueLineService()
    return _service
