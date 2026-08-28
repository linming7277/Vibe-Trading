"""Cached inputs and deterministic company scoring used by Value Line L3."""

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
from .value.leader_score_v2 import (
    DIMENSION_METRIC_WEIGHTS,
    FORMULA_VERSION as LEADER_VERSION,
    METRIC_DEFINITIONS,
    WEIGHTS as LEADER_WEIGHTS,
    calculate as leader_calculate,
)
from .value_data_store import ValueDataStore, now
from .value_market_history import BENCHMARK, ValueMarketHistoryService


MODULE_ORDER = ("financial_history", "market_history", "macro", "policy")
MODULE_LABELS = {
    "financial_history": "专业财务", "market_history": "历史行情",
    "macro": "宏观", "policy": "政策",
}
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

    def _leader_rows(
        self,
        sector_code: str,
        sector_name: str,
        members: list[str],
        as_of: str,
        financials: dict[str, list[dict[str, Any]]],
        fundamentals: dict[str, dict[str, Any]],
        quotes: dict[str, dict[str, Any]],
        market_context: dict[str, Any],
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
        directions = {
            key: bool(METRIC_DEFINITIONS[key]["higher_is_better"])
            for key in candidates[0]
        }
        normalized = cross_sectional_percentiles(candidates, directions)
        result_rows = []
        for index, identity in enumerate(identities):
            components = {
                name: weighted_score(normalized[index], weights, minimum_coverage=.50).score
                for name, weights in DIMENSION_METRIC_WEIGHTS.items()
            }
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
                "formula_version": LEADER_VERSION, "as_of": as_of,
                "requested_as_of": as_of, "market_data_as_of": market_context["market_data_as_of"],
                "financial_data_as_of": as_of, "market_data_status": market_context["market_data_status"],
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
                    raise ValueError(f"unsupported value input module: {module}")
                results[module] = value
                state = "partial" if value.get("status") == "partial" else "ready"
                self.cache.set_module_state(
                    module, status=state, progress=1, total=1,
                    item_count=int(
                        value.get("item_count") or value.get("rows") or value.get("series_rows")
                        or value.get("events") or 0
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
        return {
            "professional_finance": package, "modules": modules,
            "recent_jobs": self.data_store.recent_jobs(),
            "schedule_template": {
                "name": "价值线工作日收盘后更新", "cron": "0 17 * * 1-5",
                "timezone": "Asia/Shanghai", "modules": list(MODULE_ORDER), "enabled": False,
            },
        }

    def macro(self, as_of: str | None = None) -> dict[str, Any] | None:
        return self.data_store.get_macro_snapshot(as_of)

    def policies(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        return self.data_store.policies(status, limit)


_service: ValueLineService | None = None


def get_value_line_service() -> ValueLineService:
    global _service
    if _service is None:
        _service = ValueLineService()
    return _service
