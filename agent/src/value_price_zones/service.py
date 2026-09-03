"""Deterministic, PIT-safe valuation and support/resistance research zones.

The module intentionally produces price *ranges*, never orders, targets or
position instructions.  It only reads already-published TDX and research
snapshots; no LLM, refresh or Thesis mutation is part of this service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from src.company_thesis.store import CompanyThesisRepository
from src.financial_analysis.store import FinancialAnalysisStore
from src.historical_valuation.service import HistoricalValuationService
from src.research_workspace.store import normalize_market, normalize_symbol
from src.tdx_data.store import TdxDataStore


FORMULA_VERSION = "value-price-zones-v1.0.0"


@dataclass(frozen=True)
class ZoneConfig:
    watch_discount: float = 0.10
    attractive_discount: float = 0.20
    deep_discount: float = 0.30
    overvaluation_buffer: float = 0.10
    deep_overvaluation_buffer: float = 0.30
    min_history_bars: int = 60
    pivot_window: int = 5
    cluster_tolerance: float = 0.035
    max_zones: int = 4
    min_peer_count: int = 3
    strength_touch_weight: float = 0.40
    strength_duration_weight: float = 0.20
    strength_recency_weight: float = 0.15
    strength_volume_weight: float = 0.15
    strength_reaction_weight: float = 0.10


DEFAULT_CONFIG = ZoneConfig()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _date(value: Any) -> date | None:
    text = str(value or "").replace("T", " ")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("values required")
    index = (len(ordered) - 1) * percentile
    lower, upper = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


class ValuePriceZoneService:
    """Assemble independent valuation and price-structure zones from snapshots."""

    def __init__(
        self,
        *,
        tdx_store: TdxDataStore | None = None,
        financial_store: FinancialAnalysisStore | None = None,
        thesis_repository: CompanyThesisRepository | None = None,
        config: ZoneConfig = DEFAULT_CONFIG,
        tdx_db_path: Path | None = None,
        research_db_path: Path | None = None,
        historical_valuation_service: HistoricalValuationService | None = None,
    ) -> None:
        self.tdx_store = tdx_store or TdxDataStore(tdx_db_path)
        self.financial_store = financial_store or FinancialAnalysisStore(research_db_path)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(self.financial_store.db_path)
        self.historical_valuation_service = historical_valuation_service or HistoricalValuationService(tdx_store=self.tdx_store)
        self.config = config
        self._owns = {
            "tdx": tdx_store is None,
            "financial": financial_store is None,
            "thesis": thesis_repository is None,
        }

    def close(self) -> None:
        if self._owns["tdx"]:
            self.tdx_store.close()
        if self._owns["financial"]:
            self.financial_store.close()
        if self._owns["thesis"]:
            self.thesis_repository.close()

    @staticmethod
    def _record_payload(record: dict[str, Any] | None) -> dict[str, Any]:
        return dict((record or {}).get("payload") or {})

    def _bars(self, symbol: str, as_of: date | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # Value Line's durable front-adjusted cache is the primary source.  The
        # legacy generic ``klines`` record remains a temporary read-only
        # migration fallback for companies that have not yet been backfilled.
        cached = self.tdx_store.get_adjusted_daily_bars(
            "CN", symbol, adjustment_type="front", as_of=as_of.isoformat() if as_of else None,
        )
        if cached:
            bars = [{
                "date": row["trade_date"], "open": row["open"], "high": row["high"], "low": row["low"],
                "close": row["close"], "volume": row.get("volume") or 0.0, "amount": row.get("amount") or 0.0,
            } for row in cached]
            # Coverage metadata must describe the same truncated window the
            # bars came from.  For an as-of read the persisted latest-state
            # coverage row would leak a newer last_date, so recompute it from
            # the bars visible on or before ``as_of``.
            if as_of is not None:
                coverage = self.tdx_store.adjusted_daily_bar_status_as_of(
                    "CN", symbol, adjustment_type="front", as_of=as_of.isoformat(),
                )
            else:
                coverage = self.tdx_store.adjusted_daily_bar_status("CN", symbol, adjustment_type="front")
            status = str(coverage.get("coverage_status") or "INSUFFICIENT")
            message = "" if status in {"READY", "PARTIAL"} else "历史行情不足，暂无法计算可靠支撑/压力区。"
            return bars, {
                "status": status, "message": message, "bars": len(bars), "dividend_type": "front",
                "source": "adjusted_daily_bars", "first_date": coverage.get("first_date"), "last_date": coverage.get("last_date"),
                "coverage_as_of_scoped": as_of is not None,
            }
        rows = self.tdx_store.list_records("klines", query=symbol, limit=40).get("items") or []
        selected: dict[str, Any] | None = None
        order = {"front": 0, "none": 1, "back": 2}
        for row in sorted(rows, key=lambda item: order.get(str((item.get("payload") or {}).get("dividend_type")), 9)):
            payload = self._record_payload(row)
            if payload.get("code") == symbol and payload.get("period") == "1d":
                selected = row
                break
        if not selected:
            return [], {
                "status": "MISSING",
                "message": (
                    "该公司尚无已缓存的前复权日线；系统目前仅对三级行业龙头池与低估龙头池等研究范围物化日线，"
                    "未物化的公司不计算支撑/压力区。"
                ),
                "bars": 0,
            }
        payload = self._record_payload(selected)
        data = dict(payload.get("data") or {})
        def series(field: str) -> dict[str, float]:
            output: dict[str, float] = {}
            for item in data.get(field) or []:
                if not isinstance(item, dict):
                    continue
                stamp = str(item.get("index") or "")[:10]
                value = _number(item.get(symbol))
                if stamp and value is not None:
                    output[stamp] = value
            return output
        opens, highs, lows, closes, volumes, amounts = (series(name) for name in ("Open", "High", "Low", "Close", "Volume", "Amount"))
        bars: list[dict[str, Any]] = []
        for stamp in sorted(closes):
            day = _date(stamp)
            if day is None or (as_of is not None and day > as_of):
                continue
            close = closes.get(stamp)
            high, low = highs.get(stamp), lows.get(stamp)
            if close is None or high is None or low is None:
                continue
            bars.append({"date": stamp, "open": opens.get(stamp, close), "high": high, "low": low,
                         "close": close, "volume": volumes.get(stamp, 0.0), "amount": amounts.get(stamp, 0.0)})
        status = "READY" if len(bars) >= self.config.min_history_bars else "PARTIAL" if bars else "MISSING"
        message = "" if status == "READY" else f"已缓存日线仅 {len(bars)} 根，至少需要 {self.config.min_history_bars} 根才能计算支撑/压力区。"
        return bars, {"status": status, "message": message, "bars": len(bars), "dividend_type": payload.get("dividend_type"), "source": "legacy_klines"}

    def _quote_from_record(
        self,
        record: dict[str, Any] | None,
        *,
        required_date: date | None,
        source: str,
    ) -> tuple[float | None, str | None, str | None]:
        quote = self._record_payload(record)
        quote_as_of = str(quote.get("data_as_of") or (record or {}).get("updated_at") or "").strip()
        quote_date = _date(quote_as_of)
        if required_date is not None and quote_date != required_date:
            return None, None, None
        price = _number(quote.get("price"))
        if price is None:
            return None, None, None
        return price, quote_as_of or None, source

    def _current_quote(
        self,
        symbol: str,
        as_of: date | None,
        bars: list[dict[str, Any]],
    ) -> tuple[float | None, str | None, str | None]:
        """Resolve a price without relabelling an old daily close as current.

        For an as-of analysis, a quote or daily bar is usable only when its
        own market date exactly matches the requested date.  The generic quote
        cache is checked first for current pages; dated refresh snapshots are
        then used to preserve point-in-time daily reports after the live cache
        has advanced to a newer day.
        """
        current = self.tdx_store.get_record("quotes", symbol)
        price, price_as_of, source = self._quote_from_record(
            current,
            required_date=as_of,
            source="tdx_quote_cache",
        )
        if price is not None:
            return price, price_as_of, source

        if as_of is not None:
            dated = self.tdx_store.latest_snapshot_record_for_market_date(
                "quotes",
                symbol,
                market="CN",
                market_date=as_of.isoformat(),
            )
            price, price_as_of, source = self._quote_from_record(
                dated,
                required_date=as_of,
                source="tdx_quote_snapshot",
            )
            if price is not None:
                return price, price_as_of, source

            if bars and _date(bars[-1].get("date")) == as_of:
                return float(bars[-1]["close"]), str(bars[-1]["date"]), "adjusted_daily_bar"
            return None, None, None

        # A current page prefers the most recent TDX quote.  The final daily
        # close is only a fallback when a live quote is not available.
        price, price_as_of, source = self._quote_from_record(
            current,
            required_date=None,
            source="tdx_quote_cache",
        )
        if price is not None:
            return price, price_as_of, source
        if bars:
            return float(bars[-1]["close"]), str(bars[-1]["date"]), "adjusted_daily_bar"
        return None, None, None

    def _fundamental(self, symbol: str, as_of: date | None) -> tuple[dict[str, Any], bool]:
        record = self.tdx_store.get_record("fundamentals", symbol)
        if not record:
            return {}, False
        updated = _date(record.get("updated_at"))
        if as_of is not None and (updated is None or updated > as_of):
            return {}, False
        return self._record_payload(record), True

    def _peer_multiples(self, symbol: str, level3_code: str | None, as_of: date | None) -> dict[str, Any]:
        if not level3_code:
            return {"status": "MISSING", "pe": [], "pb": [], "peer_count": 0, "pe_codes": [], "pb_codes": [], "message": "缺少三级行业归属，无法读取同业可比。"}
        members = self.tdx_store.list_records("research_terminal_industry_members", category=level3_code, limit=2_000).get("items") or []
        pe_values: list[float] = []
        pb_values: list[float] = []
        pe_codes: list[str] = []
        pb_codes: list[str] = []
        for member in members:
            code = str(self._record_payload(member).get("stock_code") or "")
            if not code or code == symbol:
                continue
            fundamental, usable = self._fundamental(code, as_of)
            if not usable:
                continue
            if (value := _number(fundamental.get("pe_ttm"))) is not None and value < 300:
                pe_values.append(value)
                pe_codes.append(code)
            if (value := _number(fundamental.get("pb_mrq"))) is not None and value < 50:
                pb_values.append(value)
                pb_codes.append(code)
        count = max(len(pe_values), len(pb_values))
        return {
            "status": "READY" if count >= self.config.min_peer_count else "PARTIAL" if count else "MISSING",
            "pe": pe_values, "pb": pb_values, "peer_count": count,
            "pe_codes": pe_codes, "pb_codes": pb_codes,
            "message": "" if count >= self.config.min_peer_count else "同三级行业可用估值样本不足，估值区间会降级。",
        }

    @staticmethod
    def _latest_annual_profit(snapshot: dict[str, Any] | None) -> float | None:
        annual = [item for item in (snapshot or {}).get("history") or [] if isinstance(item, dict) and item.get("period_type") == "annual"]
        annual.sort(key=lambda item: str(item.get("report_date") or ""), reverse=True)
        return _number((annual[0] if annual else {}).get("net_profit"))

    def _valuation(self, symbol: str, current_price: float | None, snapshot: dict[str, Any] | None,
                   fundamental: dict[str, Any], peers: dict[str, Any], historical: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        identity = dict((snapshot or {}).get("identity") or {})
        current_valuation = dict(identity.get("market_valuation") or {})
        current_pe = _number(current_valuation.get("pe")) or _number(fundamental.get("pe_ttm"))
        current_pb = _number(current_valuation.get("pb")) or _number(fundamental.get("pb_mrq"))
        methods: list[dict[str, Any]] = []
        estimates: list[float] = []
        base_forecast = (((snapshot or {}).get("forecast") or {}).get("scenarios") or {}).get("BASE") or {}
        base_rows = base_forecast.get("forecast") if isinstance(base_forecast, dict) else []
        base_profit = _number((base_rows or [{}])[0].get("net_profit")) if isinstance(base_rows, list) and base_rows else None
        annual_profit = self._latest_annual_profit(snapshot)
        pe_values = list(peers.get("pe") or [])
        if current_price and current_pe and annual_profit and base_profit and len(pe_values) >= self.config.min_peer_count:
            growth = base_profit / annual_profit
            low_multiple, mid_multiple, high_multiple = (_percentile(pe_values, p) for p in (0.25, 0.50, 0.75))
            values = [current_price * growth * multiple / current_pe for multiple in (low_multiple, mid_multiple, high_multiple)]
            estimates.extend(values)
            methods.append({"name": "预测利润 + 同三级行业 PE 可比", "kind": "PE", "status": "READY", "peer_count": len(pe_values),
                            "multiple_low": round(low_multiple, 3), "multiple_mid": round(mid_multiple, 3), "multiple_high": round(high_multiple, 3),
                            "forecast_profit": base_profit, "fair_values": [round(value, 2) for value in values]})
        pb_values = list(peers.get("pb") or [])
        if current_price and current_pb and len(pb_values) >= self.config.min_peer_count:
            low_multiple, mid_multiple, high_multiple = (_percentile(pb_values, p) for p in (0.25, 0.50, 0.75))
            values = [current_price * multiple / current_pb for multiple in (low_multiple, mid_multiple, high_multiple)]
            estimates.extend(values)
            methods.append({"name": "同三级行业 PB 可比", "kind": "PB", "status": "READY", "peer_count": len(pb_values),
                            "multiple_low": round(low_multiple, 3), "multiple_mid": round(mid_multiple, 3), "multiple_high": round(high_multiple, 3),
                            "fair_values": [round(value, 2) for value in values]})
        historical_method = {
            "name": "公司自身历史估值位置", "kind": "HISTORICAL", "status": historical.get("historical_valuation_status", "INSUFFICIENT_DATA"),
            "cheapness_percentile": historical.get("cheapness_percentile"),
            "metrics": historical.get("historical_percentiles", {}),
            "message": "历史估值位置仅用于解释当前估值的一致性，不直接改写合理价值区间。",
        }
        if historical_method["status"] != "INSUFFICIENT_DATA":
            methods.append(historical_method)
        if not estimates:
            return ({"status": "INSUFFICIENT_DATA", "fair_value_low": None, "fair_value_mid": None, "fair_value_high": None,
                     "methods": methods, "message": "缺少足够的同业可比、预测利润或当前估值数据，不能估算合理价值区间。",
                     "limitations": [] if historical_method["status"] != "INSUFFICIENT_DATA" else ["历史 PE/PB/股息率序列不足，不展示历史估值分位。"]}, [])
        low, high = min(estimates), max(estimates)
        mid = median(estimates)
        if current_price is None:
            status = "INSUFFICIENT_DATA"
        elif current_price < low * (1 - self.config.deep_discount):
            status = "DEEPLY_UNDERVALUED"
        elif current_price < low:
            status = "UNDERVALUED"
        elif current_price <= high:
            status = "FAIR"
        elif current_price > high * (1 + self.config.deep_overvaluation_buffer):
            status = "DEEPLY_OVERVALUED"
        else:
            status = "OVERVALUED"
        zones = self._valuation_zones(low, high)
        return ({"status": status, "fair_value_low": round(low, 2), "fair_value_mid": round(mid, 2), "fair_value_high": round(high, 2),
                 "methods": methods, "message": "合理价值区间仅是基于当前可比与预测假设的研究估算，不是交易指令。",
                 "limitations": [] if historical_method["status"] != "INSUFFICIENT_DATA" else ["历史 PE/PB/股息率序列不足，不展示历史估值分位。"]}, zones)

    def _valuation_zones(self, low: float, high: float) -> list[dict[str, Any]]:
        return [
            {"name": "深度低估区", "low": None, "high": round(low * (1 - self.config.deep_discount), 2), "kind": "UNDERVALUED"},
            {"name": "较高安全边际区", "low": round(low * (1 - self.config.deep_discount), 2), "high": round(low * (1 - self.config.attractive_discount), 2), "kind": "UNDERVALUED"},
            {"name": "低估关注区", "low": round(low * (1 - self.config.attractive_discount), 2), "high": round(low, 2), "kind": "UNDERVALUED"},
            {"name": "合理区", "low": round(low, 2), "high": round(high, 2), "kind": "FAIR"},
            {"name": "偏高区", "low": round(high, 2), "high": round(high * (1 + self.config.overvaluation_buffer), 2), "kind": "OVERVALUED"},
            {"name": "明显偏高区", "low": round(high * (1 + self.config.overvaluation_buffer), 2), "high": None, "kind": "OVERVALUED"},
        ]

    def _structure(self, bars: list[dict[str, Any]], current_price: float | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if len(bars) < self.config.min_history_bars or current_price is None:
            return [], []
        candidates: list[dict[str, Any]] = []
        window = self.config.pivot_window
        avg_volume = sum(float(bar.get("volume") or 0) for bar in bars) / len(bars)
        for index in range(window, len(bars) - window):
            bar, around = bars[index], bars[index - window:index + window + 1]
            low, high = float(bar["low"]), float(bar["high"])
            if low <= min(float(item["low"]) for item in around):
                rebound = max(float(item["high"]) for item in bars[index:min(len(bars), index + window + 1)]) / low - 1
                candidates.append({"price": low, "date": bar["date"], "index": index, "volume": float(bar.get("volume") or 0), "reaction": rebound, "reason": "历史重要低点"})
            if high >= max(float(item["high"]) for item in around):
                retreat = 1 - min(float(item["low"]) for item in bars[index:min(len(bars), index + window + 1)]) / high
                candidates.append({"price": high, "date": bar["date"], "index": index, "volume": float(bar.get("volume") or 0), "reaction": retreat, "reason": "历史重要高点"})
        # Long-cycle moving averages offer a second independent price anchor.
        for length in (60, 120):
            if len(bars) >= length:
                price = sum(float(item["close"]) for item in bars[-length:]) / length
                candidates.append({"price": price, "date": bars[-1]["date"], "index": len(bars) - 1, "volume": avg_volume, "reaction": 0.0, "reason": f"{length} 日均线附近"})
        # A compact volume-at-price proxy supplies a third, independent anchor.
        # It bins only the bars available on or before ``as_of``.
        price_low, price_high = min(float(item["low"]) for item in bars), max(float(item["high"]) for item in bars)
        if price_high > price_low:
            buckets: dict[int, float] = {}
            bucket_count = 16
            for index, bar in enumerate(bars):
                typical = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3
                bucket = min(bucket_count - 1, int((typical - price_low) / (price_high - price_low) * bucket_count))
                buckets[bucket] = buckets.get(bucket, 0.0) + float(bar.get("volume") or 0.0)
            for bucket, volume in sorted(buckets.items(), key=lambda item: item[1], reverse=True)[:2]:
                center = price_low + (bucket + 0.5) / bucket_count * (price_high - price_low)
                candidates.append({"price": center, "date": bars[-1]["date"], "index": len(bars) - 1, "volume": volume / max(1, len(bars)), "reaction": 0.0, "reason": "长时间成交密集区"})
        supports = self._cluster([item for item in candidates if item["price"] <= current_price * 1.03], "SUPPORT", len(bars), avg_volume)
        resistances = self._cluster([item for item in candidates if item["price"] >= current_price * 0.97], "RESISTANCE", len(bars), avg_volume)
        return supports, resistances

    def _cluster(self, candidates: list[dict[str, Any]], kind: str, total_bars: int, avg_volume: float) -> list[dict[str, Any]]:
        if not candidates:
            return []
        groups: list[list[dict[str, Any]]] = []
        for candidate in sorted(candidates, key=lambda item: float(item["price"])):
            if not groups:
                groups.append([candidate]); continue
            center = sum(float(item["price"]) for item in groups[-1]) / len(groups[-1])
            if abs(float(candidate["price"]) - center) / center <= self.config.cluster_tolerance:
                groups[-1].append(candidate)
            else:
                groups.append([candidate])
        zones: list[dict[str, Any]] = []
        for group in groups:
            touches = len(group)
            dates = sorted(str(item["date"]) for item in group)
            volumes = [float(item["volume"]) for item in group]
            reactions = [float(item["reaction"]) for item in group]
            latest_index = max(int(item.get("index") or 0) for item in group)
            # Date recency is measured against only the supplied PIT history,
            # not wall-clock time.  A point from the latest bar receives 1.0.
            recent_score = latest_index / max(1, total_bars - 1)
            score = (
                self.config.strength_touch_weight * min(1.0, touches / 4) +
                self.config.strength_duration_weight * min(1.0, len(set(dates)) / 4) +
                self.config.strength_recency_weight * recent_score +
                self.config.strength_volume_weight * min(1.0, (sum(volumes) / max(touches, 1)) / max(avg_volume, 1)) +
                self.config.strength_reaction_weight * min(1.0, (sum(reactions) / max(touches, 1)) / 0.10)
            )
            strength = "HIGH" if score >= 0.68 else "MEDIUM" if score >= 0.38 else "LOW"
            reasons = sorted({str(item["reason"]) for item in group})
            reasons.append(f"历史出现 {touches} 次")
            zones.append({"low": round(min(float(item["price"]) for item in group), 2), "high": round(max(float(item["price"]) for item in group), 2),
                          "strength": strength, "score": round(score, 3), "reasons": reasons, "touches": touches,
                          "first_seen": dates[0], "last_seen": dates[-1], "kind": kind})
        zones.sort(key=lambda item: (-(float(item["score"])), -int(item["touches"]), float(item["low"])))
        return zones[:self.config.max_zones]

    @staticmethod
    def _intersections(valuation_zones: Iterable[dict[str, Any]], structure_zones: Iterable[dict[str, Any]], *, kind: str, valuation_status: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for value_zone in valuation_zones:
            if value_zone.get("kind") != kind:
                continue
            value_low = float(value_zone.get("low") or 0.0)
            value_high = float(value_zone["high"]) if value_zone.get("high") is not None else float("inf")
            for structure in structure_zones:
                low, high = max(value_low, float(structure["low"])), min(value_high, float(structure["high"]))
                if low <= high:
                    result.append({"low": round(low, 2), "high": round(high, 2), "valuation_status": valuation_status,
                                   "support_strength": structure.get("strength"),
                                   "reasons": [f"{value_zone['name']}与历史{('支撑' if kind == 'UNDERVALUED' else '压力')}区重叠", *list(structure.get("reasons") or [])]})
        return result

    def get_price_zones(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        target = _date(as_of)
        bars, history_quality = self._bars(symbol, target)
        current_price, price_as_of, price_source = self._current_quote(symbol, target, bars)
        snapshot = self.financial_store.latest(symbol, as_of=target.isoformat() if target else None)
        fundamental, fundamentals_ready = self._fundamental(symbol, target)
        historical = self.historical_valuation_service.get_valuation_history(
            normalized_market, symbol, as_of=target.isoformat() if target else None,
        )
        identity = dict((snapshot or {}).get("identity") or {})
        peers = self._peer_multiples(symbol, identity.get("level3_code"), target)
        valuation, valuation_zones = self._valuation(symbol, current_price, snapshot, fundamental, peers, historical)
        support_zones, resistance_zones = self._structure(bars, current_price)
        thesis = self.thesis_repository.get_current_thesis(normalized_market, symbol)
        confluence = self._intersections(valuation_zones, support_zones, kind="UNDERVALUED", valuation_status=str(valuation.get("status")))
        upper = self._intersections(valuation_zones, resistance_zones, kind="OVERVALUED", valuation_status=str(valuation.get("status")))
        return {
            "stock_code": symbol, "as_of": target.isoformat() if target else price_as_of,
            "price_as_of": price_as_of, "current_price": round(current_price, 2) if current_price else None,
            "formula_version": FORMULA_VERSION, "valuation": valuation, "valuation_zones": valuation_zones,
            "support_zones": support_zones, "resistance_zones": resistance_zones,
            "confluence_zones": confluence, "upper_review_zones": upper,
            "thesis_status": thesis.get("status") if thesis else None,
            "data_quality": {
                "daily_history": history_quality,
                "historical_valuation": {
                    "status": historical["coverage"]["coverage_status"], "message": (
                        "" if historical["coverage"]["coverage_status"] != "INSUFFICIENT"
                        else "历史 PE/PB/股息率序列不足，暂时不能判断历史估值位置；该序列依赖已物化的前复权日线与估值快照，仅研究范围内公司可用。"
                    ), "coverage": historical["coverage"], "historical_valuation_status": historical["historical_valuation_status"],
                },
                "price": {
                    "status": "READY" if current_price is not None else ("STALE" if target else "MISSING"),
                    "as_of": price_as_of,
                    "source": price_source,
                    "message": (
                        "" if current_price is not None
                        else f"未找到与研究日期 {target.isoformat()} 一致的行情，未计算正式估值。"
                        if target else "缺少可用行情，未计算正式估值。"
                    ),
                },
                "current_fundamentals": "READY" if fundamentals_ready else "MISSING",
                "peer_comparables": {
                    "status": peers.get("status"), "peer_count": peers.get("peer_count"), "message": peers.get("message"),
                    "pe_peer_count": len(peers.get("pe") or []), "pb_peer_count": len(peers.get("pb") or []),
                    "pe_codes": list(peers.get("pe_codes") or []), "pb_codes": list(peers.get("pb_codes") or []),
                },
                "forecast": str((snapshot or {}).get("forecast_status") or "MISSING"), "financial_snapshot": "READY" if snapshot else "MISSING",
            },
            "historical_valuation": historical,
            "plain_summary": self._plain_summary(current_price, valuation, support_zones, confluence, upper, thesis, historical),
        }

    @staticmethod
    def _plain_summary(current_price: float | None, valuation: dict[str, Any], supports: list[dict[str, Any]], confluence: list[dict[str, Any]], upper: list[dict[str, Any]], thesis: dict[str, Any] | None, historical: dict[str, Any]) -> str:
        if current_price is None:
            return "缺少与所选数据日期一致的价格，暂时不能判断价格位置。"
        if valuation.get("status") == "INSUFFICIENT_DATA":
            return "当前价格已读取，但可比估值或预测数据不足，暂不估算合理价值区间。"
        low, high = valuation.get("fair_value_low"), valuation.get("fair_value_high")
        text = f"当前价格为 {current_price:.2f}，系统估算的合理价值区间为 {low:.2f}–{high:.2f}，当前属于{valuation.get('status')}。"
        if confluence:
            text += f"它与历史支撑的重叠关注区在 {confluence[0]['low']:.2f}–{confluence[0]['high']:.2f}，值得重点核验。"
        elif supports:
            text += f"历史主要支撑区在 {supports[0]['low']:.2f}–{supports[0]['high']:.2f}，但目前未与低估区形成重叠。"
        if upper:
            text += f"上方 {upper[0]['low']:.2f}–{upper[0]['high']:.2f} 同时接近偏高估值和历史压力，需要复核估值假设。"
        historical_state = historical.get("historical_valuation_status")
        if historical_state in {"VERY_CHEAP", "CHEAP"}:
            text += "公司自身历史估值也处于偏低位置；"
            if confluence:
                text += "价值、历史估值和价格支撑三方面出现较强重合，仍需结合基本面核验。"
        elif historical_state in {"EXPENSIVE", "VERY_EXPENSIVE"}:
            text += "公司自身历史估值处于偏高位置，应审慎解释当前估值区间。"
        if historical_state in (None, "", "INSUFFICIENT_DATA"):
            text += "历史估值分位暂缺（该公司历史估值序列未物化），暂无法对照公司自身历史估值位置。"
        if thesis and thesis.get("status") == "WEAKENING":
            text += "当前公司逻辑正在减弱，应更谨慎解释这些区间。"
        return text


_service: ValuePriceZoneService | None = None


def get_value_price_zone_service() -> ValuePriceZoneService:
    global _service
    if _service is None:
        _service = ValuePriceZoneService()
    return _service
