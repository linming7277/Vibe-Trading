"""Persist and read PIT-safe TongDaXin front-adjusted daily bars.

This service is intentionally only a data layer.  It does not calculate price
zones, invoke an LLM, or change a Company Thesis.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any

from src.level3_leaders.service import Level3IndustryLeaderService, get_level3_leader_service
from src.research_workspace.store import normalize_market, normalize_symbol
from src.tdx_data.service import TdxDataService, get_tdx_service
from src.tdx_data.store import TdxDataStore


ADJUSTMENT_TYPE = "front"
SOURCE = "TongDaXin"
SOURCE_VERSION = "get_market_data/front/v1"
INITIAL_BAR_COUNT = 750
INCREMENTAL_BAR_COUNT = 30
MAX_STAGED_POOL_REFRESH = 20
DEFAULT_COMPACT_BAR_LIMIT = 126
MIN_COMPACT_BAR_LIMIT = 120
MAX_COMPACT_BAR_LIMIT = 180


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _trade_date(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


class AdjustedDailyBarService:
    """One authoritative cache for Value Line daily price research."""

    def __init__(
        self, *, tdx_store: TdxDataStore | None = None, tdx_service: TdxDataService | None = None,
        leader_service: Level3IndustryLeaderService | None = None,
    ) -> None:
        self.tdx_service = tdx_service or get_tdx_service()
        self.tdx_store = tdx_store or self.tdx_service.store
        self.leader_service = leader_service or get_level3_leader_service()
        self._owns_store = tdx_store is None and tdx_service is None

    def close(self) -> None:
        # Shared application singletons own their database connection.
        if self._owns_store:
            self.tdx_store.close()

    @staticmethod
    def _source_rows(payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
        """Normalize the field-array shape returned by TongDaXin bridge."""
        data = dict(payload.get("data") or {})

        def series(field: str) -> dict[str, float]:
            result: dict[str, float] = {}
            for raw in data.get(field) or []:
                if not isinstance(raw, dict):
                    continue
                stamp = _trade_date(raw.get("index"))
                value = _number(raw.get(symbol))
                if stamp and value is not None:
                    result[stamp] = value
            return result

        opens, highs, lows, closes, volumes, amounts = (
            series(field) for field in ("Open", "High", "Low", "Close", "Volume", "Amount")
        )
        rows: list[dict[str, Any]] = []
        for stamp, close in closes.items():
            high, low = highs.get(stamp), lows.get(stamp)
            if close <= 0 or high is None or low is None or high <= 0 or low <= 0:
                continue
            open_ = opens.get(stamp, close)
            if open_ is None or open_ <= 0:
                continue
            rows.append({
                "trade_date": stamp, "open": open_, "high": high, "low": low, "close": close,
                "volume": volumes.get(stamp), "amount": amounts.get(stamp),
            })
        return sorted(rows, key=lambda item: str(item["trade_date"]))

    @staticmethod
    def _hash(row: dict[str, Any]) -> str:
        canonical = json.dumps(
            {key: row.get(key) for key in ("trade_date", "open", "high", "low", "close", "volume", "amount")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _continuity(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """A conservative discontinuity check, not a corporate-action detector."""
        closes = [float(row["close"]) for row in rows if _number(row.get("close"))]
        jumps = [abs(closes[index] / closes[index - 1] - 1) for index in range(1, len(closes)) if closes[index - 1] > 0]
        max_jump = max(jumps) if jumps else 0.0
        # A 35% one-day move is beyond normal A-share limits and is a useful
        # smoke check for an unadjusted split/rights issue.  It does not claim
        # to prove there was no corporate action.
        return {
            "status": "PASS" if max_jump <= .35 else "REVIEW",
            "max_absolute_daily_change": round(max_jump, 6),
            "threshold": .35,
            "bar_count": len(closes),
        }

    def get_daily_bars(self, market: str, stock_code: str, *, as_of: str | None = None) -> list[dict[str, Any]]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        return self.tdx_store.get_adjusted_daily_bars(
            normalized_market, symbol, adjustment_type=ADJUSTMENT_TYPE, as_of=as_of,
        )

    def compact_daily_bars(
        self, market: str, stock_code: str, *, as_of: str | None = None, limit: int = DEFAULT_COMPACT_BAR_LIMIT,
    ) -> dict[str, Any]:
        """Return a bounded, PIT-safe front-adjusted series for a Quick View.

        This is intentionally a cache read only.  It does not ask TongDaXin for
        missing rows and cannot update coverage or any other database state.
        """
        if limit < MIN_COMPACT_BAR_LIMIT or limit > MAX_COMPACT_BAR_LIMIT:
            raise ValueError(f"compact daily bar limit must be {MIN_COMPACT_BAR_LIMIT}–{MAX_COMPACT_BAR_LIMIT}")
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        normalized_as_of = None
        if as_of:
            try:
                normalized_as_of = date.fromisoformat(str(as_of)[:10]).isoformat()
            except ValueError as exc:
                raise ValueError("as_of must be an ISO date") from exc

        visible_rows = self.get_daily_bars(normalized_market, symbol, as_of=normalized_as_of)
        status = self.tdx_store.adjusted_daily_bar_status(
            normalized_market, symbol, adjustment_type=ADJUSTMENT_TYPE,
        )
        bars = [{
            "date": row["trade_date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row.get("volume"),
        } for row in visible_rows[-limit:]]
        visible_count = len(visible_rows)
        coverage_status = "READY" if visible_count >= 250 else "PARTIAL" if visible_count >= 60 else "INSUFFICIENT"
        return {
            "market": normalized_market,
            "stock_code": symbol,
            "adjustment_type": ADJUSTMENT_TYPE,
            "bars": bars,
            "bar_count": visible_count,
            "returned_bar_count": len(bars),
            "coverage_status": coverage_status,
            "data_as_of": bars[-1]["date"] if bars else None,
            "requested_as_of": normalized_as_of,
            "source": status.get("source"),
            "source_version": status.get("source_version"),
            "fetched_at": status.get("fetched_at"),
        }

    def status(self, market: str, stock_code: str) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        result = self.tdx_store.adjusted_daily_bar_status(normalized_market, symbol, adjustment_type=ADJUSTMENT_TYPE)
        return {**result, "continuity": self._continuity(self.get_daily_bars(normalized_market, symbol))}

    def refresh_company(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Initial 750-bar fetch or bounded post-last-date incremental fetch."""
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        before = self.tdx_store.adjusted_daily_bar_status(normalized_market, symbol, adjustment_type=ADJUSTMENT_TYPE)
        last_date = before.get("last_date")
        initial = not last_date
        start_time = ""
        if last_date:
            # Include the last cached day in the request.  The database's
            # unique key makes overlap harmless and permits a source correction.
            start_time = str(last_date).replace("-", "")
        end_time = str(as_of or "").replace("-", "")
        try:
            payload = self.tdx_service.fetch_kline(
                symbol, period="1d", count=INITIAL_BAR_COUNT if initial else INCREMENTAL_BAR_COUNT,
                dividend_type=ADJUSTMENT_TYPE, start_time=start_time, end_time=end_time,
            )
            if str(payload.get("dividend_type")) != ADJUSTMENT_TYPE:
                raise ValueError("tdx_source_did_not_confirm_front_adjustment")
            raw_rows = self._source_rows(payload, symbol)
            if as_of:
                raw_rows = [row for row in raw_rows if str(row["trade_date"]) <= str(as_of)[:10]]
            now = datetime.now(timezone.utc).isoformat()
            saved_rows = [{
                "market": normalized_market, "stock_code": symbol, **row,
                "adjustment_type": ADJUSTMENT_TYPE, "source": SOURCE, "source_version": SOURCE_VERSION,
                "fetched_at": now, "source_hash": self._hash(row),
            } for row in raw_rows]
            changed = self.tdx_store.upsert_adjusted_daily_bars(saved_rows)
            all_rows = self.get_daily_bars(normalized_market, symbol)
            source_hash = hashlib.sha256("".join(row["source_hash"] for row in saved_rows).encode("utf-8")).hexdigest() if saved_rows else ""
            coverage = self.tdx_store.refresh_adjusted_daily_bar_coverage(
                normalized_market, symbol, adjustment_type=ADJUSTMENT_TYPE, source=SOURCE,
                source_version=SOURCE_VERSION, fetched_at=now, source_hash=source_hash,
                error="" if raw_rows else "tdx_returned_no_usable_daily_bars",
            )
            return {
                **coverage, "initial_backfill": initial, "fetched_count": len(raw_rows), "changed_count": changed,
                "continuity": self._continuity(all_rows), "as_of": as_of,
            }
        except Exception as exc:
            coverage = self.tdx_store.refresh_adjusted_daily_bar_coverage(
                normalized_market, symbol, adjustment_type=ADJUSTMENT_TYPE, source=SOURCE,
                source_version=SOURCE_VERSION, error=f"{type(exc).__name__}: {exc}",
            )
            return {**coverage, "initial_backfill": initial, "fetched_count": 0, "changed_count": 0,
                    "continuity": self._continuity(self.get_daily_bars(normalized_market, symbol)), "as_of": as_of}

    def current_l3_symbols(self) -> list[str]:
        _pool, symbols = self.current_l3_pool()
        return symbols

    def current_l3_pool(self) -> tuple[dict[str, Any], list[str]]:
        pool = self.leader_service.ensure_current_pool()
        allowed = {"ACTIVE", "NEW", "REENTERED"}
        symbols = sorted({
            str(row.get("stock_code") or "").upper() for row in pool.get("members") or []
            if str(row.get("lifecycle_status") or "") in allowed and str(row.get("stock_code") or "")
        })
        return pool, symbols

    def refresh_current_l3_daily_bars(self, *, limit: int, as_of: str | None = None) -> dict[str, Any]:
        """Explicit staged refresh; full-pool rollout is intentionally blocked in V1."""
        if limit < 1 or limit > MAX_STAGED_POOL_REFRESH:
            raise ValueError(f"staged current-pool refresh limit must be 1–{MAX_STAGED_POOL_REFRESH}")
        symbols = self.current_l3_symbols()[:limit]
        items = [self.refresh_company("CN", symbol, as_of=as_of) for symbol in symbols]
        ready = sum(item["coverage_status"] == "READY" for item in items)
        return {
            "scope": "CURRENT_L3_LEADER_POOL_STAGED", "requested": limit, "processed": len(items),
            "ready": ready, "partial": sum(item["coverage_status"] == "PARTIAL" for item in items),
            "insufficient": sum(item["coverage_status"] == "INSUFFICIENT" for item in items),
            "failed": sum(bool(item.get("error")) for item in items), "items": items,
        }

    @staticmethod
    def _error_parts(error: Any) -> tuple[str, str]:
        text = str(error or "").strip()
        if not text:
            return "", ""
        kind, separator, summary = text.partition(":")
        return (kind or "RefreshError")[:100], (summary.strip() if separator else text)[:1000]

    def backfill_current_l3_pool(
        self, *, as_of: str | None = None, batch_size: int = 20, resume_run_id: str | None = None,
        offset: int = 0, max_batches: int | None = None, retry_failed: bool = False,
        throttle_seconds: float = .15,
    ) -> dict[str, Any]:
        """Backfill the frozen current-pool snapshot sequentially and resumably.

        The default processes all remaining batches.  ``max_batches`` is for a
        controlled operator run; a later call with ``resume_run_id`` continues
        only PENDING items, while ``retry_failed`` puts failed items back into
        the pending queue.  No scheduler calls this method.
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
            run = self.tdx_store.adjusted_daily_bar_backfill_run(resume_run_id)
            if not run:
                raise KeyError(f"daily bar backfill run not found: {resume_run_id}")
            run_id = resume_run_id
            target_as_of = str(run["target_as_of"])
            if as_of and str(as_of)[:10] != target_as_of:
                raise ValueError("resume as_of must match the existing run target_as_of")
            if retry_failed:
                self.tdx_store.reset_adjusted_daily_bar_backfill_failures(run_id)
            # Progress state is authoritative on resume.  Offset is accepted
            # only for an operator choosing where to inspect/process next.
            start_offset = offset
        else:
            pool, symbols = self.current_l3_pool()
            target_as_of = str(as_of or pool.get("as_of") or "")[:10]
            try:
                date.fromisoformat(target_as_of)
            except ValueError as exc:
                raise ValueError("current L3 pool has no valid as_of date") from exc
            run_id = f"l3bars_{uuid.uuid4().hex[:16]}"
            self.tdx_store.create_adjusted_daily_bar_backfill_run(
                run_id=run_id, pool_id=str(pool.get("id") or ""), market="CN", target_as_of=target_as_of,
                batch_size=batch_size, symbols=symbols,
            )
            start_offset = offset

        batches = 0
        while max_batches is None or batches < max_batches:
            pending = self.tdx_store.adjusted_daily_bar_backfill_items(
                run_id, statuses=("PENDING",), offset=start_offset, limit=batch_size,
            )
            if not pending:
                # A bounded offset can leave earlier PENDING items.  A normal
                # resume starts at zero and will pick them up.
                break
            for item in pending:
                symbol = str(item["stock_code"])
                existing = self.tdx_store.adjusted_daily_bar_status("CN", symbol, adjustment_type=ADJUSTMENT_TYPE)
                if (
                    existing.get("coverage_status") == "READY"
                    and int(existing.get("bar_count") or 0) >= INITIAL_BAR_COUNT
                    and str(existing.get("last_date") or "") >= target_as_of
                ):
                    self.tdx_store.update_adjusted_daily_bar_backfill_item(
                        run_id, symbol, status="SKIPPED", bar_count=int(existing.get("bar_count") or 0),
                        coverage_status="READY", increment_attempts=False,
                    )
                    continue
                result = self.refresh_company("CN", symbol, as_of=target_as_of)
                error_type, error_summary = self._error_parts(result.get("error"))
                outcome = "FAILED" if error_type else str(result.get("coverage_status") or "INSUFFICIENT")
                self.tdx_store.update_adjusted_daily_bar_backfill_item(
                    run_id, symbol, status=outcome, bar_count=int(result.get("bar_count") or 0),
                    coverage_status=str(result.get("coverage_status") or "INSUFFICIENT"),
                    error_type=error_type, error_summary=error_summary,
                )
                if throttle_seconds:
                    time.sleep(throttle_seconds)
            batches += 1
            start_offset = 0  # subsequent batches always take the next pending company
            self.tdx_store.finalize_adjusted_daily_bar_backfill_run(run_id)

        summary = self.tdx_store.finalize_adjusted_daily_bar_backfill_run(run_id)
        return {
            **summary, "scope": "CURRENT_L3_LEADER_POOL", "run_id": run_id,
            "target_as_of": target_as_of, "batch_size": batch_size,
            "resume_supported": True, "retry_supported": True,
        }


_service: AdjustedDailyBarService | None = None


def get_adjusted_daily_bar_service() -> AdjustedDailyBarService:
    global _service
    if _service is None:
        _service = AdjustedDailyBarService()
    return _service
