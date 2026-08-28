"""Build and interpret point-in-time company valuation history.

The service only reconstructs data from dated TongDaXin sources.  It does not
make investment recommendations, change forecasts, or mutate Company Thesis.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.adjusted_daily_bars.service import _number, _trade_date
from src.level3_leaders.service import Level3IndustryLeaderService, get_level3_leader_service
from src.research_workspace.store import normalize_market, normalize_symbol
from src.tdx_data.financial_history import FinancialHistoryService
from src.tdx_data.service import TdxDataService, get_tdx_service
from src.tdx_data.store import TdxDataStore


SOURCE_TYPE = "PIT_REBUILT_FROM_TDX"
PRICE_SOURCE_ID = "TongDaXin:get_market_data:1d:none"
CAPITAL_SOURCE_ID = "TongDaXin:get_gb_info_by_date"
DIVIDEND_SOURCE_ID = "TongDaXin:get_divid_factors:ex_date_proxy"
YEARS = 5
INITIAL_PRICE_COUNT = 1300
INCREMENTAL_PRICE_COUNT = 30
MIN_RELIABLE_OBSERVATIONS = 250
READY_OBSERVATIONS = 750
WINSOR_LOW = .01
WINSOR_HIGH = .99


def _safe(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and math.isfinite(number) else None


def _date(value: Any) -> str | None:
    raw = str(value or "").replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile needs values")
    position = (len(ordered) - 1) * q
    left, right = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def _coverage_status(pe_count: int, pb_count: int) -> str:
    count = max(pe_count, pb_count)
    if count >= READY_OBSERVATIONS:
        return "READY"
    if count >= MIN_RELIABLE_OBSERVATIONS:
        return "PARTIAL"
    return "INSUFFICIENT"


class HistoricalValuationService:
    def __init__(
        self, *, tdx_store: TdxDataStore | None = None, tdx_service: TdxDataService | None = None,
        financial_history: FinancialHistoryService | None = None, leader_service: Level3IndustryLeaderService | None = None,
    ) -> None:
        self.tdx_service = tdx_service or get_tdx_service()
        self.tdx_store = tdx_store or self.tdx_service.store
        self.financial_history = financial_history or FinancialHistoryService(self.tdx_store, self.tdx_service.client)
        self.leader_service = leader_service or get_level3_leader_service()

    @staticmethod
    def _price_rows(payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
        data = dict(payload.get("data") or {})
        fields: dict[str, dict[str, float]] = {}
        for field in ("Close",):
            values: dict[str, float] = {}
            for item in data.get(field) or []:
                if not isinstance(item, dict):
                    continue
                stamp, value = _trade_date(item.get("index")), _safe(item.get(symbol))
                if stamp and value is not None and value > 0:
                    values[stamp] = value
            fields[field] = values
        return [{"trade_date": stamp, "close": close} for stamp, close in sorted(fields["Close"].items())]

    @staticmethod
    def _capital_rows(raw: Any) -> tuple[list[str], list[float]]:
        values: dict[str, float] = {}
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            stamp, shares = _date(item.get("Date")), _safe(item.get("Zgb"))
            if stamp and shares is not None and shares > 0:
                values[stamp] = shares
        dates = sorted(values)
        return dates, [values[item] for item in dates]

    @staticmethod
    def _shares_as_of(dates: list[str], shares: list[float], trade_date: str) -> float | None:
        index = bisect.bisect_right(dates, trade_date) - 1
        return shares[index] if index >= 0 else None

    @staticmethod
    def _dividends(raw: Any) -> list[dict[str, Any]]:
        values = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            stamp, bonus = _trade_date(item.get("Date")), _safe(item.get("Bonus"))
            # TDX's cash Bonus is per ten shares.  Ex-right date is used as a
            # conservative visibility boundary because this endpoint does not
            # expose the board/announcement date.
            if stamp and bonus is not None and bonus > 0 and str(item.get("Type") or "") == "1":
                values.append({"date": stamp, "cash_per_share": bonus / 10})
        return sorted(values, key=lambda item: item["date"])

    @staticmethod
    def _visible_financials(rows: list[dict[str, Any]], trade_date: str) -> list[dict[str, Any]]:
        by_period: dict[str, dict[str, Any]] = {}
        for item in rows:
            announced = str(item.get("announcement_date") or "")
            period = str(item.get("report_date") or "")
            if not announced or not period or announced > trade_date:
                continue
            prior = by_period.get(period)
            if prior is None or announced >= str(prior.get("announcement_date") or ""):
                by_period[period] = item
        return [by_period[key] for key in sorted(by_period)]

    @staticmethod
    def _ttm_and_book(visible: list[dict[str, Any]]) -> tuple[float | None, float | None, str | None, str]:
        if not visible:
            return None, None, None, ""
        latest = visible[-1]
        periods = visible[-4:]
        profit_values = [_safe(item.get("net_profit")) for item in periods]
        # The professional-finance dataset has been verified as single-quarter
        # flow values.  Use exactly four latest visible fiscal periods only.
        ttm = sum(profit_values) if len(periods) == 4 and all(item is not None for item in profit_values) else None
        book = _safe(latest.get("parent_equity")) or _safe(latest.get("equity"))
        announced = str(latest.get("announcement_date") or "") or None
        source_id = f"tdx-financial:{latest.get('report_date')}:{announced or ''}:latest4q"
        return ttm, book, announced, source_id

    @staticmethod
    def _dividend_yield(dividends: list[dict[str, Any]], trade_date: str, close: float) -> float | None:
        start = (date.fromisoformat(trade_date) - timedelta(days=365)).isoformat()
        cash = sum(float(item["cash_per_share"]) for item in dividends if start < item["date"] <= trade_date)
        return cash / close * 100 if cash > 0 and close > 0 else None

    @staticmethod
    def _hash(row: dict[str, Any]) -> str:
        value = {key: row.get(key) for key in (
            "trade_date", "close", "pe_ttm", "pb_mrq", "dividend_yield", "market_cap",
            "financial_data_as_of", "financial_source_id", "price_source_id",
        )}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def refresh_company(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        target = date.fromisoformat(str(as_of)[:10]).isoformat()
        existing = self.tdx_store.historical_valuation_coverage(normalized_market, symbol)
        full = not existing.get("first_date") or str(existing.get("first_date")) > (date.fromisoformat(target) - timedelta(days=YEARS * 365 + 45)).isoformat()
        start_time = "" if full else str(existing.get("last_date") or "").replace("-", "")
        try:
            price_payload = self.tdx_service.fetch_kline(
                symbol, period="1d", count=INITIAL_PRICE_COUNT if full else INCREMENTAL_PRICE_COUNT,
                dividend_type="none", start_time=start_time, end_time=target.replace("-", ""),
            )
            if str(price_payload.get("dividend_type")) != "none":
                raise ValueError("tdx_source_did_not_confirm_unadjusted_price")
            prices = self._price_rows(price_payload, symbol)
            prices = [row for row in prices if row["trade_date"] <= target]
            capital_raw = self.tdx_service.client.call(
                "get_gb_info_by_date", symbol, start_date=(date.fromisoformat(target) - timedelta(days=YEARS * 365 + 90)).strftime("%Y%m%d"), end_date=target.replace("-", ""),
            ) or []
            share_dates, shares = self._capital_rows(capital_raw)
            dividends = self._dividends(self.tdx_service.client.call(
                "get_divid_factors", symbol, start_time=(date.fromisoformat(target) - timedelta(days=YEARS * 365 + 370)).strftime("%Y%m%d"), end_time=target.replace("-", ""),
            ) or [])
            financials = self.financial_history.query(symbol, as_of=target).get("items") or []
            now = datetime.now(timezone.utc).isoformat()
            records: list[dict[str, Any]] = []
            for price in prices:
                trade_date, close = str(price["trade_date"]), float(price["close"])
                visible = self._visible_financials(financials, trade_date)
                ttm, book, announcement, financial_source_id = self._ttm_and_book(visible)
                share_count = self._shares_as_of(share_dates, shares, trade_date)
                market_cap = close * share_count if share_count else None
                pe = market_cap / ttm if market_cap is not None and ttm is not None and ttm > 0 else None
                pb = market_cap / book if market_cap is not None and book is not None and book > 0 else None
                dividend_yield = self._dividend_yield(dividends, trade_date, close)
                quality = "READY" if pe is not None and pb is not None else "PARTIAL" if pe is not None or pb is not None else "PRICE_ONLY"
                record = {
                    "market": normalized_market, "stock_code": symbol, "trade_date": trade_date, "close": close,
                    "pe_ttm": pe, "pb_mrq": pb, "dividend_yield": dividend_yield, "market_cap": market_cap,
                    "financial_data_as_of": announcement, "financial_source_id": financial_source_id,
                    "price_source_id": PRICE_SOURCE_ID, "source_type": SOURCE_TYPE, "quality_status": quality,
                    "created_at": now,
                }
                record["source_hash"] = self._hash(record)
                records.append(record)
            changed = self.tdx_store.upsert_historical_valuation_series(records)
            coverage = self.tdx_store.refresh_historical_valuation_coverage(normalized_market, symbol)
            return {**coverage, "stock_code": symbol, "as_of": target, "fetched_prices": len(prices), "changed_count": changed,
                    "source_type": SOURCE_TYPE, "price_source_id": PRICE_SOURCE_ID, "capital_source_id": CAPITAL_SOURCE_ID,
                    "dividend_source_id": DIVIDEND_SOURCE_ID}
        except Exception as exc:
            coverage = self.tdx_store.refresh_historical_valuation_coverage(
                normalized_market, symbol, last_error=f"{type(exc).__name__}: {exc}",
            )
            return {**coverage, "stock_code": symbol, "as_of": target, "fetched_prices": 0, "changed_count": 0,
                    "source_type": SOURCE_TYPE}

    @staticmethod
    def _metric_percentile(rows: list[dict[str, Any]], key: str, *, inverse: bool) -> dict[str, Any]:
        values = [float(row[key]) for row in rows if _safe(row.get(key)) is not None and float(row[key]) > 0]
        if len(values) < MIN_RELIABLE_OBSERVATIONS:
            return {"status": "INSUFFICIENT_DATA", "count": len(values), "current": None, "percentile": None,
                    "cheapness_percentile": None, "direction": "higher_is_cheaper" if inverse else "lower_is_cheaper",
                    "plain": "历史估值数据不足，暂时不能判断。"}
        current = values[-1]
        low, high = _quantile(values, WINSOR_LOW), _quantile(values, WINSOR_HIGH)
        clipped = [min(high, max(low, value)) for value in values]
        current_clipped = min(high, max(low, current))
        less = sum(value < current_clipped for value in clipped)
        equal = sum(value == current_clipped for value in clipped)
        percentile = (less + equal * .5) / len(clipped) * 100
        cheapness = 100 - percentile if inverse else percentile
        if cheapness <= 10:
            state = "VERY_CHEAP"
        elif cheapness <= 30:
            state = "CHEAP"
        elif cheapness < 70:
            state = "NORMAL"
        elif cheapness < 90:
            state = "EXPENSIVE"
        else:
            state = "VERY_EXPENSIVE"
        wording = (
            f"当前{key}处于历史较低位置，约只有 {percentile:.0f}% 的时间更低。"
            if not inverse else f"当前{key}处于历史较高位置，约只有 {100 - percentile:.0f}% 的时间更高。"
        )
        return {
            "status": "READY", "count": len(values), "current": round(current, 4), "percentile": round(percentile, 2),
            "cheapness_percentile": round(cheapness, 2), "state": state,
            "direction": "higher_is_cheaper" if inverse else "lower_is_cheaper",
            "winsorized": {"low_quantile": WINSOR_LOW, "high_quantile": WINSOR_HIGH, "low": round(low, 4), "high": round(high, 4)},
            "plain": wording,
        }

    def get_valuation_history(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        rows = self.tdx_store.get_historical_valuation_series(normalized_market, symbol, as_of=as_of)
        metrics = {
            "pe_ttm": self._metric_percentile(rows, "pe_ttm", inverse=False),
            "pb_mrq": self._metric_percentile(rows, "pb_mrq", inverse=False),
            "dividend_yield": self._metric_percentile(rows, "dividend_yield", inverse=True),
        }
        cheapness = [item["cheapness_percentile"] for item in metrics.values() if item.get("cheapness_percentile") is not None]
        average = sum(cheapness) / len(cheapness) if cheapness else None
        if average is None:
            state = "INSUFFICIENT_DATA"
        elif average <= 10:
            state = "VERY_CHEAP"
        elif average <= 30:
            state = "CHEAP"
        elif average < 70:
            state = "NORMAL"
        elif average < 90:
            state = "EXPENSIVE"
        else:
            state = "VERY_EXPENSIVE"
        pe_count = sum(1 for row in rows if _safe(row.get("pe_ttm")) and float(row["pe_ttm"]) > 0)
        pb_count = sum(1 for row in rows if _safe(row.get("pb_mrq")) and float(row["pb_mrq"]) > 0)
        dy_count = sum(1 for row in rows if _safe(row.get("dividend_yield")) and float(row["dividend_yield"]) > 0)
        coverage = {
            "first_date": rows[0]["trade_date"] if rows else None, "last_date": rows[-1]["trade_date"] if rows else None,
            "pe_count": pe_count, "pb_count": pb_count, "dividend_yield_count": dy_count,
            "coverage_status": _coverage_status(pe_count, pb_count), "minimum_reliable_observations": MIN_RELIABLE_OBSERVATIONS,
        }
        return {
            "market": normalized_market, "stock_code": symbol, "as_of": str(as_of)[:10] if as_of else (rows[-1]["trade_date"] if rows else None),
            "current": rows[-1] if rows else None, "historical_percentiles": metrics,
            "historical_valuation_status": state, "cheapness_percentile": round(average, 2) if average is not None else None,
            "coverage": coverage,
            "series_summary": {"observations": len(rows), "source_type": SOURCE_TYPE, "price_source_id": PRICE_SOURCE_ID,
                               "outlier_rule": "PE/PB/股息率原始值完整保存；分位统计按 1%–99% Winsor 化。"},
        }

    def refresh_current_l3(self, *, limit: int, as_of: str | None = None) -> dict[str, Any]:
        if limit < 1 or limit > 20:
            raise ValueError("staged current L3 valuation refresh limit must be 1–20")
        pool = self.leader_service.ensure_current_pool()
        target = str(as_of or pool.get("as_of") or "")[:10]
        allowed = {"ACTIVE", "NEW", "REENTERED"}
        symbols = sorted({str(item.get("stock_code") or "") for item in pool.get("members") or [] if str(item.get("lifecycle_status")) in allowed})[:limit]
        items = [self.refresh_company("CN", symbol, as_of=target) for symbol in symbols]
        return {
            "scope": "CURRENT_L3_LEADER_POOL_STAGED", "target_as_of": target, "processed": len(items),
            "ready": sum(item["coverage_status"] == "READY" for item in items),
            "partial": sum(item["coverage_status"] == "PARTIAL" for item in items),
            "insufficient": sum(item["coverage_status"] == "INSUFFICIENT" for item in items),
            "failed": sum(bool(item.get("last_error")) for item in items), "items": items,
        }

    def current_l3_pool(self) -> tuple[dict[str, Any], list[str]]:
        pool = self.leader_service.ensure_current_pool()
        allowed = {"ACTIVE", "NEW", "REENTERED"}
        symbols = sorted({
            str(item.get("stock_code") or "").upper()
            for item in pool.get("members") or []
            if str(item.get("lifecycle_status") or "") in allowed and str(item.get("stock_code") or "")
        })
        return pool, symbols

    @staticmethod
    def _error_parts(error: Any) -> tuple[str, str]:
        text = str(error or "").strip()
        if not text or text == "not_cached":
            return "", ""
        kind, separator, summary = text.partition(":")
        return (kind or "RefreshError")[:100], (summary.strip() if separator else text)[:1000]

    def backfill_current_l3_pool(
        self, *, as_of: str | None = None, batch_size: int = 20, resume_run_id: str | None = None,
        offset: int = 0, max_batches: int | None = None, retry_failed: bool = False,
        throttle_seconds: float = .15,
    ) -> dict[str, Any]:
        """Build the Current L3 historical-valuation cache in resumable batches.

        This is an explicit operator action. It uses the existing PIT rebuild
        unchanged and never schedules five-year history reconstruction.
        """
        if batch_size < 1 or batch_size > 50:
            raise ValueError("batch_size must be 1–50")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if max_batches is not None and max_batches < 1:
            raise ValueError("max_batches must be >= 1")
        if throttle_seconds < 0 or throttle_seconds > 5:
            raise ValueError("throttle_seconds must be between 0 and 5")

        if resume_run_id:
            run = self.tdx_store.historical_valuation_backfill_run(resume_run_id)
            if not run:
                raise KeyError(f"historical valuation backfill run not found: {resume_run_id}")
            run_id = resume_run_id
            target_as_of = str(run["target_as_of"])
            if as_of and str(as_of)[:10] != target_as_of:
                raise ValueError("resume as_of must match the existing run target_as_of")
            if retry_failed:
                self.tdx_store.reset_historical_valuation_backfill_failures(run_id)
            start_offset = offset
        else:
            pool, symbols = self.current_l3_pool()
            target_as_of = str(as_of or pool.get("as_of") or "")[:10]
            try:
                target_date = date.fromisoformat(target_as_of)
            except ValueError as exc:
                raise ValueError("current L3 pool has no valid as_of date") from exc
            run_id = f"l3valuation_{uuid.uuid4().hex[:16]}"
            self.tdx_store.create_historical_valuation_backfill_run(
                run_id=run_id, pool_id=str(pool.get("id") or ""), market="CN", target_as_of=target_as_of,
                batch_size=batch_size, symbols=symbols,
            )
            start_offset = offset

        batches = 0
        while max_batches is None or batches < max_batches:
            pending = self.tdx_store.historical_valuation_backfill_items(
                run_id, statuses=("PENDING",), offset=start_offset, limit=batch_size,
            )
            if not pending:
                break
            for item in pending:
                symbol = str(item["stock_code"])
                existing = self.tdx_store.historical_valuation_coverage("CN", symbol)
                if (
                    existing.get("coverage_status") == "READY"
                    and str(existing.get("last_date") or "") >= target_as_of
                ):
                    self.tdx_store.update_historical_valuation_backfill_item(
                        run_id, symbol, status="SKIPPED", pe_count=int(existing.get("pe_count") or 0),
                        pb_count=int(existing.get("pb_count") or 0),
                        dividend_yield_count=int(existing.get("dividend_yield_count") or 0),
                        coverage_status="READY", increment_attempts=False,
                    )
                    continue
                result = self.refresh_company("CN", symbol, as_of=target_as_of)
                error_type, error_summary = self._error_parts(result.get("last_error"))
                outcome = "FAILED" if error_type else str(result.get("coverage_status") or "INSUFFICIENT")
                self.tdx_store.update_historical_valuation_backfill_item(
                    run_id, symbol, status=outcome, pe_count=int(result.get("pe_count") or 0),
                    pb_count=int(result.get("pb_count") or 0),
                    dividend_yield_count=int(result.get("dividend_yield_count") or 0),
                    coverage_status=str(result.get("coverage_status") or "INSUFFICIENT"),
                    error_type=error_type, error_summary=error_summary,
                )
                if throttle_seconds:
                    time.sleep(throttle_seconds)
            batches += 1
            start_offset = 0
            self.tdx_store.finalize_historical_valuation_backfill_run(run_id)

        summary = self.tdx_store.finalize_historical_valuation_backfill_run(run_id)
        completed = [item for item in summary["items"] if item["status"] in {"READY", "PARTIAL", "SKIPPED"}]
        by_history = sorted(completed, key=lambda item: max(int(item["pe_count"] or 0), int(item["pb_count"] or 0)))
        summary["data_quality"] = {
            "shortest_history": [
                {"stock_code": item["stock_code"], "pe_count": item["pe_count"], "pb_count": item["pb_count"],
                 "dividend_yield_count": item["dividend_yield_count"], "coverage_status": item["coverage_status"]}
                for item in by_history[:10]
            ],
            "no_pe": [item["stock_code"] for item in summary["items"] if int(item["pe_count"] or 0) == 0],
            "no_pb": [item["stock_code"] for item in summary["items"] if int(item["pb_count"] or 0) == 0],
            "no_dividend_yield": [
                item["stock_code"] for item in summary["items"] if int(item["dividend_yield_count"] or 0) == 0
            ],
        }
        return {
            **summary, "scope": "CURRENT_L3_LEADER_POOL", "run_id": run_id, "target_as_of": target_as_of,
            "batch_size": batch_size, "resume_supported": True, "retry_supported": True,
        }


_service: HistoricalValuationService | None = None


def get_historical_valuation_service() -> HistoricalValuationService:
    global _service
    if _service is None:
        _service = HistoricalValuationService()
    return _service
