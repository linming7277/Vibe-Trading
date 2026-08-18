"""TDX-first, AKShare-fallback A-share daily history for Value V2."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import duckdb
import pandas as pd

from src.strategy_engines.history import HistoricalFeatureStore
from src.tdx_data.client import TdxClient


DATASET = "value_ohlcv"
MIN_COVERAGE = .90
# Sector V1 treats market-wide price/volume/amount data as one atomic daily
# snapshot.  A mostly-zero volume day is not a valid fallback input for the
# Capital Flow component, even when close prices themselves are present.
MIN_MARKET_VOLUME_COVERAGE = .95
MIN_MARKET_AMOUNT_COVERAGE = .95
BENCHMARK = "000985.SH"
# TDX stores the CSI All Share local day file under its Shenzhen namespace
# (vipdoc/sz/lday/sz000985.day), while the public canonical code is .SH.
TDX_BENCHMARK_ALIAS = "000985.SZ"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class ValueMarketHistoryService:
    def __init__(self, store: HistoricalFeatureStore | None = None, client: TdxClient | None = None) -> None:
        self.store = store or HistoricalFeatureStore()
        self.client = client or TdxClient()

    @staticmethod
    def _tdx_rows(payload: dict[str, Any], symbols: list[str]) -> list[dict[str, Any]]:
        fields = {key.lower(): rows for key, rows in payload.items() if isinstance(rows, list)}
        by_date: dict[str, dict[str, dict[str, Any]]] = {}
        for field, rows in fields.items():
            for raw in rows:
                timestamp = str(raw.get("index") or "")[:10]
                if len(timestamp) != 10:
                    continue
                for symbol in symbols:
                    value = _number(raw.get(symbol))
                    if value is not None:
                        by_date.setdefault(timestamp, {}).setdefault(symbol, {})[field] = value
        result = []
        for trade_date, securities in by_date.items():
            for symbol, values in securities.items():
                close = values.get("close")
                if close is None or close <= 0:
                    continue
                result.append({
                    "trade_date": trade_date, "symbol": symbol,
                    "open": values.get("open"), "high": values.get("high"),
                    "low": values.get("low"), "close": close,
                    "volume": values.get("volume"),
                    # The sampled TQ daily Amount field is ten-thousand yuan.
                    "amount": values.get("amount") * 10_000 if values.get("amount") is not None else None,
                    "source": "TongDaXin", "adjustment": "qfq",
                })
        return result

    @staticmethod
    def _akshare_rows(symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        import akshare as ak

        bare = symbol.split(".")[0]
        if symbol == BENCHMARK:
            frame = ak.index_zh_a_hist(
                symbol=bare, period="daily", start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
        else:
            frame = ak.stock_zh_a_hist(
                symbol=bare, period="daily", start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""), adjust="qfq",
            )
        records = []
        for raw in frame.to_dict("records"):
            close = _number(raw.get("收盘"))
            if close is None or close <= 0:
                continue
            records.append({
                "trade_date": str(raw.get("日期"))[:10], "symbol": symbol,
                "open": _number(raw.get("开盘")), "high": _number(raw.get("最高")),
                "low": _number(raw.get("最低")), "close": close,
                # AKShare Eastmoney daily volume is lots; warehouse unit is shares.
                "volume": (_number(raw.get("成交量")) or 0) * 100,
                "amount": _number(raw.get("成交额")), "source": "AKShare/Eastmoney", "adjustment": "qfq",
            })
        return records

    def _retain_failed_symbols(self, trade_date: str, daily: pd.DataFrame) -> pd.DataFrame:
        """Merge rows not refreshed today from the prior atomic partition."""
        target = self.store.root / "cn" / DATASET / f"date={trade_date}" / "part-000.parquet"
        if not target.exists():
            return daily
        connection = duckdb.connect()
        try:
            existing = connection.execute("SELECT * FROM read_parquet(?)", [str(target)]).fetch_df()
        finally:
            connection.close()
        if existing.empty or "symbol" not in existing:
            return daily
        refreshed = set(daily["symbol"].astype(str))
        retained = existing[~existing["symbol"].astype(str).isin(refreshed)].drop(columns=["index"], errors="ignore")
        return pd.concat([daily, retained], ignore_index=True, sort=False) if not retained.empty else daily

    def refresh(
        self,
        symbols: list[str],
        *,
        as_of: str,
        count: int = 250,
        # The TongDaXin bridge accepts at most 100 symbols reliably.  Larger
        # requests can return a syntactically valid payload that silently omits
        # roughly half the requested securities, which is unsafe for a daily
        # cross-sectional value snapshot.
        batch_size: int = 100,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        date.fromisoformat(as_of)
        universe = sorted(set(symbols))
        requested = universe + ([BENCHMARK] if BENCHMARK not in universe else [])
        rows: list[dict[str, Any]] = []
        for offset in range(0, len(requested), batch_size):
            batch = requested[offset:offset + batch_size]
            tdx_batch = [TDX_BENCHMARK_ALIAS if symbol == BENCHMARK else symbol for symbol in batch]
            payload = self.client.call(
                "get_market_data", field_list=["open", "high", "low", "close", "volume", "amount"],
                stock_list=tdx_batch, period="1d", end_time=as_of.replace("-", ""), count=count,
                dividend_type="front", fill_data=False,
            ) or {}
            batch_rows = self._tdx_rows(payload, tdx_batch)
            for row in batch_rows:
                if row["symbol"] == TDX_BENCHMARK_ALIAS:
                    row["symbol"] = BENCHMARK
            rows.extend(batch_rows)
            if progress:
                progress(min(offset + len(batch), len(requested)), len(requested), "读取通达信前复权日线")
        # A symbol may have old history in the response but still lack the
        # requested day's close.  Only today's rows count as coverage here.
        covered_today = {
            str(row["symbol"]) for row in rows
            if str(row.get("trade_date") or "") == as_of
        }
        missing = [symbol for symbol in requested if symbol not in covered_today]
        # TongDaXin can keep an exchange's local daily-K cache one trading day
        # behind even when its quote service is connected.  Ask the terminal to
        # refresh just the missing symbols, then re-read them before reaching
        # for the network fallback.  This keeps the Value Line TDX-first and
        # avoids treating a local cache lag as unavailable market data.
        if missing:
            # TDX refresh_kline is reliable with a small list but can block in
            # the vendor DLL for minutes when sent 100 symbols at once.
            refresh_batch_size = min(batch_size, 20)
            for offset in range(0, len(missing), refresh_batch_size):
                batch = missing[offset:offset + refresh_batch_size]
                self.client.call("refresh_kline", stock_list=batch, period="1d")
            for offset in range(0, len(missing), batch_size):
                batch = missing[offset:offset + batch_size]
                payload = self.client.call(
                    "get_market_data", field_list=["open", "high", "low", "close", "volume", "amount"],
                    stock_list=batch, period="1d", end_time=as_of.replace("-", ""), count=count,
                    dividend_type="front", fill_data=False,
                ) or {}
                batch_rows = self._tdx_rows(payload, batch)
                rows.extend(batch_rows)
                covered_today.update(
                    str(row["symbol"]) for row in batch_rows
                    if str(row.get("trade_date") or "") == as_of
                )
            missing = [symbol for symbol in requested if symbol not in covered_today]
        start = (date.fromisoformat(as_of) - timedelta(days=800)).isoformat()
        fallback_errors: list[str] = []
        if missing:
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="value-akshare") as executor:
                futures = {executor.submit(self._akshare_rows, symbol, start, as_of): symbol for symbol in missing}
                for index, future in enumerate(as_completed(futures), 1):
                    symbol = futures[future]
                    try:
                        values = future.result()
                        rows.extend(values)
                        if any(str(row.get("trade_date") or "") == as_of for row in values):
                            covered_today.add(symbol)
                    except Exception as exc:
                        fallback_errors.append(f"{symbol}:{exc}")
                    if progress and (index == 1 or index % 25 == 0 or index == len(missing)):
                        progress(len(requested) + index, len(requested) + len(missing), "AKShare 补齐缺失日线")
        coverage = len(covered_today.intersection(universe)) / len(universe) if universe else 0.0
        if universe and coverage < MIN_COVERAGE:
            return {
                "status": "partial", "coverage": coverage, "covered": len(covered_today.intersection(universe)),
                "total": len(universe), "written": 0, "errors": fallback_errors[:100],
                "message": "有效证券覆盖低于90%，未生成正式Sector行情快照，旧缓存已保留",
            }
        frame = pd.DataFrame(rows).drop_duplicates(["trade_date", "symbol"], keep="last")
        requested_daily = frame[frame["trade_date"].astype(str) == as_of] if not frame.empty else pd.DataFrame()
        requested_market_data_coverage = self.market_data_completeness(requested_daily, universe)
        if requested_market_data_coverage["status"] != "COMPLETE":
            return {
                "status": "partial", "coverage": coverage, "covered": len(covered_today.intersection(universe)),
                "total": len(universe), "written": 0, "errors": fallback_errors[:100],
                "message": "收盘、成交量或成交额覆盖不足，未生成正式Sector行情快照，旧缓存已保留",
                "requested_as_of": as_of, "market_data_status": requested_market_data_coverage["status"],
                "requested_market_data_coverage": requested_market_data_coverage,
            }
        records = []
        available_at = datetime.now(timezone.utc).isoformat()
        for trade_date, daily in frame.groupby("trade_date", sort=True):
            payload = daily.drop(columns=["trade_date"])
            payload = self._retain_failed_symbols(str(trade_date), payload)
            records.append(self.store.write_partition(
                market="CN", dataset=DATASET, data_as_of=str(trade_date), frame=payload,
                provider="TongDaXin+AKShare", available_at=available_at, coverage=coverage,
                metadata={"adjustment": "qfq", "volume_unit": "share", "amount_unit": "CNY"},
            ))
        return {
            "status": "partial" if fallback_errors else "ready", "coverage": coverage,
            "covered": len(covered_today.intersection(universe)), "total": len(universe),
            "written": len(records), "rows": len(frame), "errors": fallback_errors[:100],
            "requested_as_of": as_of, "market_data_status": requested_market_data_coverage["status"],
            "requested_market_data_coverage": requested_market_data_coverage,
        }

    def read(self, as_of: str) -> pd.DataFrame:
        frame = self.store.read_as_of(market="CN", dataset=DATASET, as_of=as_of)
        if frame.empty:
            return frame
        if "trade_date" not in frame.columns:
            frame["trade_date"] = frame["data_as_of"]
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
        return frame.dropna(subset=["trade_date", "symbol", "close"])

    @staticmethod
    def market_data_completeness(daily: pd.DataFrame, symbols: list[str]) -> dict[str, Any]:
        """Measure whether a daily cross-section is safe for market factors.

        Coverage deliberately uses the requested stock universe as its
        denominator.  Missing values and explicit zero values therefore both
        prevent a date from being treated as a complete market snapshot.
        """
        universe = sorted(set(str(symbol) for symbol in symbols))
        snapshot = daily.drop_duplicates("symbol", keep="last").copy() if not daily.empty else daily.copy()
        if not snapshot.empty:
            snapshot["symbol"] = snapshot["symbol"].astype(str)
            snapshot = snapshot.set_index("symbol", drop=False)

        def stats(field: str) -> tuple[int, int, int]:
            if snapshot.empty or field not in snapshot.columns:
                return 0, 0, len(universe)
            values = pd.to_numeric(snapshot.reindex(universe)[field], errors="coerce")
            valid = int((values > 0).sum())
            zeros = int((values == 0).sum())
            missing = int(values.isna().sum())
            return valid, zeros, missing

        valid_close, zero_close, missing_close = stats("close")
        valid_amount, zero_amount, missing_amount = stats("amount")
        valid_volume, zero_volume, missing_volume = stats("volume")
        total = len(universe)
        amount_coverage = valid_amount / total if total else 0.0
        volume_coverage = valid_volume / total if total else 0.0
        return {
            "total_symbols": total,
            "valid_close_count": valid_close,
            "valid_amount_count": valid_amount,
            "valid_volume_count": valid_volume,
            "zero_close_count": zero_close,
            "zero_amount_count": zero_amount,
            "zero_volume_count": zero_volume,
            "missing_close_count": missing_close,
            "missing_amount_count": missing_amount,
            "missing_volume_count": missing_volume,
            "close_coverage": valid_close / total if total else 0.0,
            "amount_coverage": amount_coverage,
            "volume_coverage": volume_coverage,
            "status": "COMPLETE" if amount_coverage >= MIN_MARKET_AMOUNT_COVERAGE and volume_coverage >= MIN_MARKET_VOLUME_COVERAGE else "INCOMPLETE",
        }

    def resolve_complete_market_snapshot(
        self,
        *,
        history: pd.DataFrame,
        symbols: list[str],
        requested_as_of: str,
    ) -> dict[str, Any]:
        """Select one complete market date, with an explicit dated fallback."""
        requested = pd.Timestamp(requested_as_of).normalize()
        candidates = history.copy()
        if candidates.empty:
            raise RuntimeError("market_history_unavailable")
        candidates["trade_date"] = pd.to_datetime(candidates["trade_date"], errors="coerce").dt.normalize()
        candidates = candidates.dropna(subset=["trade_date"])
        candidates = candidates[candidates["trade_date"] <= requested]
        if candidates.empty:
            raise RuntimeError("market_history_unavailable_before_requested_as_of")
        requested_daily = candidates[candidates["trade_date"] == requested]
        requested_coverage = self.market_data_completeness(requested_daily, symbols)
        for trade_date in sorted(candidates["trade_date"].unique(), reverse=True):
            daily = candidates[candidates["trade_date"] == trade_date]
            coverage = self.market_data_completeness(daily, symbols)
            if coverage["status"] == "COMPLETE":
                market_data_as_of = pd.Timestamp(trade_date).date().isoformat()
                return {
                    "requested_as_of": requested_as_of,
                    "market_data_as_of": market_data_as_of,
                    "market_data_status": "COMPLETE" if market_data_as_of == requested_as_of else "INCOMPLETE_FALLBACK",
                    "market_data_coverage": coverage,
                    "requested_market_data_coverage": requested_coverage,
                }
        raise RuntimeError("market_history_incomplete_no_complete_snapshot")

    def read_symbols(self, symbols: list[str], *, as_of: str, count: int = 260) -> dict[str, list[dict[str, Any]]]:
        """Read a small symbol slice without materializing the full market warehouse."""
        requested = sorted(set(symbols))
        if not requested:
            return {}
        paths = sorted({
            item["partition_path"] for item in self.store.catalog(market="CN", dataset=DATASET)
            if str(item.get("data_as_of") or "") <= as_of
        })
        if not paths:
            return {symbol: [] for symbol in requested}
        placeholders = ",".join("?" for _ in requested)
        connection = duckdb.connect()
        try:
            frame = connection.execute(
                f"""SELECT * EXCLUDE(row_number) FROM (
                        SELECT *,ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY data_as_of DESC) AS row_number
                        FROM read_parquet(?) WHERE symbol IN ({placeholders})
                    ) WHERE row_number<=? ORDER BY symbol,data_as_of""",  # noqa: S608
                [paths, *requested, max(20, min(int(count), 500))],
            ).fetch_df()
        finally:
            connection.close()
        result = {symbol: [] for symbol in requested}
        for raw in frame.to_dict("records"):
            result.setdefault(str(raw.get("symbol") or ""), []).append(raw)
        return result
