"""China macro ingestion and point-in-time feature construction for Value V2."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .common.normalization import percentile
from .common.provenance import stable_fingerprint
from .value.macro_regime_v2 import AXES, FORMULA_VERSION, calculate
from .value_data_store import ValueDataStore, now


NBS_URL = "https://www.stats.gov.cn/sj/zxfb/"
PBOC_URL = "https://www.pbc.gov.cn/diaochatongjisi/116219/index.html"
CFETS_URL = "https://www.shibor.org/"

# function, date column, output column, id, axis, higher axis score, unit,
# actual official institution, URL, and whether observation date is also the
# public release date (official daily series only).
SERIES_SPECS = (
    ("macro_china_pmi", "月份", "制造业-指数", "pmi_manufacturing", "growth", True, "index", "国家统计局", NBS_URL, False),
    ("macro_china_gyzjz", "月份", "同比增长", "industrial_output_yoy", "growth", True, "%", "国家统计局", NBS_URL, False),
    ("macro_china_consumer_goods_retail", "月份", "同比增长", "retail_sales_yoy", "growth", True, "%", "国家统计局", NBS_URL, False),
    ("macro_china_gdzctz", "月份", "同比增长", "fixed_asset_investment_yoy", "growth", True, "%", "国家统计局", NBS_URL, False),
    ("macro_china_gdp", "季度", "国内生产总值-同比增长", "gdp_yoy", "growth", True, "%", "国家统计局", NBS_URL, False),
    ("macro_china_exports_yoy", "日期", "今值", "exports_yoy", "growth", True, "%", "海关总署", "http://www.customs.gov.cn/customs/302249/zfxxgk/2799825/index.html", False),
    ("macro_china_cpi", "月份", "全国-同比增长", "cpi_yoy", "inflation", True, "%", "国家统计局", NBS_URL, False),
    ("macro_china_ppi", "月份", "当月同比增长", "ppi_yoy", "inflation", True, "%", "国家统计局", NBS_URL, False),
    ("macro_china_money_supply", "月份", "货币(M1)-同比增长", "m1_yoy", "liquidity", True, "%", "中国人民银行", PBOC_URL, False),
    ("macro_china_money_supply", "月份", "货币和准货币(M2)-同比增长", "m2_yoy", "liquidity", True, "%", "中国人民银行", PBOC_URL, False),
    ("macro_china_lpr", "TRADE_DATE", "LPR1Y", "lpr_1y", "liquidity", False, "%", "全国银行间同业拆借中心", CFETS_URL, True),
    ("macro_china_shibor_all", "日期", "3M-定价", "shibor_3m", "liquidity", False, "%", "全国银行间同业拆借中心", CFETS_URL, True),
    ("macro_china_new_financial_credit", "月份", "当月-同比增长", "new_rmb_loans_yoy", "credit", True, "%", "中国人民银行", PBOC_URL, False),
    ("macro_china_shrzgm", "月份", "社会融资规模增量", "social_financing_increment", "credit", True, "亿元", "中国人民银行", PBOC_URL, False),
    ("macro_china_lpr", "TRADE_DATE", "LPR5Y", "lpr_5y", "financial_conditions", False, "%", "全国银行间同业拆借中心", CFETS_URL, True),
    ("macro_china_shibor_all", "日期", "O/N-定价", "shibor_overnight", "financial_conditions", False, "%", "全国银行间同业拆借中心", CFETS_URL, True),
    ("macro_china_rmb", "日期", "美元/人民币_中间价", "usd_cny", "financial_conditions", False, "CNY/USD", "中国人民银行", PBOC_URL, True),
)
MARKET_SERIES_SPECS = {
    "csi_all_share_risk_appetite": {"axis": "financial_conditions", "higher_good": True},
    "a_share_breadth_20d": {"axis": "financial_conditions", "higher_good": True},
}


def _observation_date(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    raw = str(value or "").strip()
    match = re.search(r"(\d{4})年(\d{1,2})月", raw)
    if match:
        year, month = map(int, match.groups())
        return date(year, month, 1).isoformat()
    match = re.search(r"(\d{4})年第(\d)季度", raw)
    if match:
        year, quarter = map(int, match.groups())
        return date(year, quarter * 3, 1).isoformat()
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _confidence(coverage: float) -> str:
    return "HIGH" if coverage >= .85 else "MEDIUM" if coverage >= .60 else "LOW"


class MacroDataService:
    def __init__(self, store: ValueDataStore | None = None, provider: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self.store = store or ValueDataStore()
        self.provider = provider or self._fetch_akshare

    @staticmethod
    def _fetch_akshare() -> list[dict[str, Any]]:
        import akshare as ak

        fetched_at = now()
        frames: dict[str, Any] = {}
        records: list[dict[str, Any]] = []
        for function_name, date_column, value_column, series_id, axis, higher_good, unit, source, url, daily_release in SERIES_SPECS:
            try:
                if function_name not in frames:
                    frames[function_name] = getattr(ak, function_name)()
                frame = frames[function_name]
                if date_column not in frame.columns or value_column not in frame.columns:
                    continue
                for raw in frame[[date_column, value_column]].to_dict("records"):
                    observation = _observation_date(raw.get(date_column))
                    value = _finite(raw.get(value_column))
                    if not observation or value is None:
                        continue
                    # For official daily rates the observation is public that
                    # day.  Monthly/quarterly history without verified calendars
                    # is usable only from the first observed crawl onward.
                    release = observation if daily_release else fetched_at[:10]
                    vintage = hashlib.sha256(f"{series_id}:{observation}:{release}:{value}".encode()).hexdigest()[:16]
                    records.append({
                        "series_id": series_id, "axis": axis, "higher_good": higher_good,
                        "observation_date": observation, "release_date": release,
                        "vintage_id": vintage, "value": value, "unit": unit, "source": source,
                        "source_url": url, "release_status": "official_daily" if daily_release else "first_observed_only",
                        "fetched_at": fetched_at, "metadata": {"adapter": "AKShare", "function": function_name},
                    })
            except Exception as exc:
                records.append({"error": str(exc), "series_id": series_id, "axis": axis, "source": source})
        records.extend(MacroDataService._fetch_pboc_social_financing())
        records.extend(MacroDataService._fetch_cfets_usd_cny())
        return records

    @staticmethod
    def _fetch_pboc_social_financing() -> list[dict[str, Any]]:
        """Read the PBOC social-financing series through its licensed mirror."""
        import os

        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token == "your-tushare-token":
            return [{"error": "TUSHARE_TOKEN is required for the PBOC social-financing mirror", "series_id": "social_financing_increment", "axis": "credit", "source": "PBOC"}]
        try:
            import tushare as ts

            frame = ts.pro_api(token).sf_month(start_m="201901", end_m=date.today().strftime("%Y%m"))
            fetched_at = now()
            records = []
            for raw in frame.to_dict("records"):
                month, value = str(raw.get("month") or ""), _finite(raw.get("inc_month"))
                if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", month) or value is None:
                    continue
                observation = f"{month[:4]}-{month[4:]}-01"
                records.append({
                    "series_id": "social_financing_increment", "axis": "credit", "higher_good": True,
                    "observation_date": observation, "release_date": fetched_at[:10],
                    "vintage_id": hashlib.sha256(f"sf:{month}:{value}:{fetched_at[:10]}".encode()).hexdigest()[:16],
                    "value": value, "unit": "CNY 100m", "source": "PBOC",
                    "source_url": PBOC_URL, "release_status": "first_observed_only", "fetched_at": fetched_at,
                    "metadata": {"adapter": "Tushare sf_month", "source_of_record": "PBOC"},
                })
            return records or [{"error": "PBOC social-financing mirror returned no rows", "series_id": "social_financing_increment", "axis": "credit", "source": "PBOC"}]
        except Exception as exc:
            return [{"error": str(exc), "series_id": "social_financing_increment", "axis": "credit", "source": "PBOC"}]

    @staticmethod
    def _fetch_cfets_usd_cny() -> list[dict[str, Any]]:
        """Read the official CFETS USD/CNY central-parity history."""
        import httpx

        source_url = "https://www.chinamoney.com.cn/dqs/rest/cm-u-pt/CcprHis"
        end_date = date.today()
        try:
            with httpx.Client(timeout=20, follow_redirects=True, trust_env=False, headers={"User-Agent": "Mozilla/5.0 hzstock-value-research", "Referer": "https://www.chinamoney.com.cn/"}) as client:
                response = client.get(source_url, params={"startDate": (end_date - timedelta(days=365 * 6)).isoformat(), "endDate": end_date.isoformat(), "currencyPair": "USD/CNY", "pageNum": 1, "pageSize": 5000})
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return [{"error": str(exc), "series_id": "usd_cny", "axis": "financial_conditions", "source": "CFETS"}]
        candidates = payload.get("records") or payload.get("data") or payload.get("result") or []
        if isinstance(candidates, dict):
            candidates = candidates.get("records") or candidates.get("data") or []
        fetched_at, records = now(), []
        for raw in candidates if isinstance(candidates, list) else []:
            if not isinstance(raw, dict):
                continue
            raw_date = str(raw.get("date") or raw.get("tradeDate") or raw.get("publishDate") or "")[:10]
            value = _finite(raw.get("price") or raw.get("rate") or raw.get("middlePrice") or raw.get("parity"))
            try:
                observation = date.fromisoformat(raw_date).isoformat()
            except ValueError:
                continue
            if value is None or value <= 0:
                continue
            records.append({
                "series_id": "usd_cny", "axis": "financial_conditions", "higher_good": False,
                "observation_date": observation, "release_date": observation,
                "vintage_id": hashlib.sha256(f"usd_cny:{observation}:{value}".encode()).hexdigest()[:16],
                "value": value, "unit": "CNY/USD", "source": "CFETS",
                "source_url": source_url, "release_status": "official_daily", "fetched_at": fetched_at,
                "metadata": {"adapter": "CFETS ChinaMoney central parity", "currency_pair": "USD/CNY"},
            })
        return records or [{"error": "CFETS central-parity endpoint returned no USD/CNY rows", "series_id": "usd_cny", "axis": "financial_conditions", "source": "CFETS"}]

    def refresh(self, as_of: str) -> dict[str, Any]:
        date.fromisoformat(as_of)
        fetched = self.provider()
        fetched.extend(self._cached_market_features(as_of))
        valid = [row for row in fetched if row.get("observation_date") and row.get("release_date")]
        errors = [row for row in fetched if row.get("error")]
        if not valid:
            raise RuntimeError("macro_sources_unavailable")
        self.store.replace_macro_series({row["series_id"] for row in valid}, valid)
        snapshot = self.build_snapshot(as_of)
        return {"status": "partial" if errors else snapshot["status"], "series_rows": len(valid), "errors": errors, "snapshot": snapshot}

    @staticmethod
    def _cached_market_features(as_of: str) -> list[dict[str, Any]]:
        """Build risk-appetite inputs from the existing close-of-day cache."""
        from .value_market_history import BENCHMARK, ValueMarketHistoryService

        frame = ValueMarketHistoryService().read(as_of)
        if frame.empty:
            return []
        returns: dict[str, float] = {}
        for symbol, group in frame.groupby("symbol"):
            closes = [float(value) for value in group.sort_values("trade_date")["close"].tolist() if _finite(value) is not None]
            if len(closes) >= 21 and closes[-21] > 0:
                returns[str(symbol)] = (closes[-1] / closes[-21] - 1) * 100
        if not returns:
            return []
        market_returns = [value for symbol, value in returns.items() if symbol != BENCHMARK]
        values = {
            "csi_all_share_risk_appetite": returns.get(BENCHMARK),
            "a_share_breadth_20d": sum(value > 0 for value in market_returns) / len(market_returns) * 100 if market_returns else None,
        }
        fetched_at = now()
        records = []
        for series_id, value in values.items():
            if value is None:
                continue
            records.append({
                "series_id": series_id, "axis": "financial_conditions", "higher_good": True,
                "observation_date": as_of, "release_date": as_of,
                "vintage_id": hashlib.sha256(f"{series_id}:{as_of}:{value}".encode()).hexdigest()[:16],
                "value": value, "unit": "%", "source": "通达信/AKShare行情缓存",
                "source_url": "", "release_status": "cached_market_close", "fetched_at": fetched_at,
                "metadata": {"window": "20D", "point_in_time": True},
            })
        return records

    @staticmethod
    def _cached_market_features(as_of: str) -> list[dict[str, Any]]:
        """Build a PIT 20-day risk-appetite history from the TDX close cache."""
        from .value_market_history import BENCHMARK, ValueMarketHistoryService

        frame = ValueMarketHistoryService().read(as_of)
        if frame.empty:
            return []
        frame = frame.copy()
        frame["trade_date"] = frame["trade_date"].dt.date
        frame["close"] = frame["close"].map(_finite)
        closes = frame.dropna(subset=["close"]).pivot_table(
            index="trade_date", columns="symbol", values="close", aggfunc="last",
        ).sort_index()
        if len(closes.index) < 21:
            return []
        fetched_at = now()
        records = []
        for index in range(20, len(closes.index)):
            observation = closes.index[index].isoformat()
            if observation > as_of:
                continue
            returns = ((closes.iloc[index] / closes.iloc[index - 20] - 1) * 100).replace(
                [math.inf, -math.inf], float("nan"),
            ).dropna()
            market_returns = returns.drop(labels=[BENCHMARK], errors="ignore")
            values = {
                "csi_all_share_risk_appetite": _finite(returns.get(BENCHMARK)),
                "a_share_breadth_20d": float((market_returns > 0).mean() * 100) if not market_returns.empty else None,
            }
            for series_id, value in values.items():
                if value is None:
                    continue
                records.append({
                    "series_id": series_id, "axis": "financial_conditions", "higher_good": True,
                    "observation_date": observation, "release_date": observation,
                    "vintage_id": hashlib.sha256(f"{series_id}:{observation}:{value}".encode()).hexdigest()[:16],
                    "value": value, "unit": "%", "source": "TongDaXin market-history cache",
                    "source_url": "", "release_status": "cached_market_close", "fetched_at": fetched_at,
                    "metadata": {"window": "20D", "point_in_time": True, "benchmark": BENCHMARK},
                })
        return records

    def build_snapshot(self, as_of: str) -> dict[str, Any]:
        rows = self.store.macro_series_as_of(as_of)
        specs = {
            **{item[3]: {"axis": item[4], "higher_good": item[5]} for item in SERIES_SPECS},
            **MARKET_SERIES_SPECS,
        }
        by_series: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            by_series.setdefault(row["series_id"], {})[row["observation_date"]] = row
        metric_scores: dict[str, float | None] = {}
        metric_details: dict[str, Any] = {}
        cutoff = date.fromisoformat(as_of) - timedelta(days=365 * 5 + 2)
        for series_id, observations in by_series.items():
            spec = specs.get(series_id)
            if not spec:
                continue
            ordered = [row for _, row in sorted(observations.items()) if row.get("value") is not None and row["observation_date"] >= cutoff.isoformat()]
            if len(ordered) < 3:
                metric_scores[series_id] = None
                continue
            values = [float(row["value"]) for row in ordered]
            level = sum(item <= values[-1] for item in values) / len(values) * 100
            if not spec["higher_good"]:
                level = 100 - level
            scale = max(1e-9, percentile([abs(value) for value in values], .50))
            recent_delta = values[-1] - values[-3]
            direction = max(0.0, min(100.0, 50 + recent_delta / scale * 25))
            if not spec["higher_good"]:
                direction = 100 - direction
            prior_delta = values[-3] - values[-5] if len(values) >= 5 else 0.0
            acceleration = max(0.0, min(100.0, 50 + (recent_delta - prior_delta) / scale * 20))
            if not spec["higher_good"]:
                acceleration = 100 - acceleration
            metric_scores[series_id] = round(level * .5 + direction * .3 + acceleration * .2, 4)
            metric_details[series_id] = {
                "latest": values[-1], "observation_date": ordered[-1]["observation_date"],
                "release_date": ordered[-1]["release_date"], "level": round(level, 4),
                "direction": round(direction, 4), "acceleration": round(acceleration, 4),
                "source": ordered[-1]["source"], "release_status": ordered[-1]["release_status"],
            }
        axes: dict[str, float | None] = {}
        sources: list[str] = []
        for axis in AXES:
            values = [metric_scores[series_id] for series_id, spec in specs.items() if spec["axis"] == axis and metric_scores.get(series_id) is not None]
            axes[axis] = round(sum(values) / len(values), 4) if values else None
        for row in rows:
            source = str(row.get("source") or "")
            if source and source not in sources:
                sources.append(source)
        result = calculate(axes)
        missing_axes = [axis for axis in AXES if axes.get(axis) is None]
        expected_series = sorted(specs)
        usable_series = sorted(series_id for series_id in expected_series if metric_scores.get(series_id) is not None)
        missing_series = sorted(set(expected_series) - set(usable_series))
        series_total = len(expected_series)
        series_count = len(usable_series)
        series_coverage = series_count / series_total if series_total else 0.0
        verified_statuses = {"official", "official_daily", "official_verified", "cached_market_close"}
        verified_count = sum(
            metric_details.get(series_id, {}).get("release_status") in verified_statuses
            for series_id in usable_series
        )
        first_observed_count = sum(
            metric_details.get(series_id, {}).get("release_status") == "first_observed_only"
            for series_id in usable_series
        )
        release_verified_coverage = verified_count / series_count if series_count else 0.0
        axis_coverage = float(result["coverage"])
        if series_coverage >= .95 and release_verified_coverage >= .80:
            confidence = "HIGH"
        elif series_coverage >= .75:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        missing_fields = [*missing_axes, *missing_series]
        quality = {
            "axis_coverage": axis_coverage,
            "series_coverage": round(series_coverage, 6),
            "series_count": series_count,
            "series_total": series_total,
            "release_verified_coverage": round(release_verified_coverage, 6),
            "first_observed_count": first_observed_count,
            "missing_series": missing_series,
        }
        provenance = stable_fingerprint({
            "as_of": as_of, "axes": axes, "details": metric_details,
            "quality": quality, "formula": FORMULA_VERSION,
        })
        snapshot = {
            "id": f"macro_{uuid.uuid4().hex[:16]}", "as_of": as_of, "formula_version": FORMULA_VERSION,
            "regime": result["regime"], "score": result["score"], "coverage": axis_coverage,
            "confidence": confidence,
            "status": "partial" if missing_series and result["status"] == "ready" else result["status"],
            "axes": axes, "states": result["states"], "missing_fields": missing_fields,
            "sources": sources, "details": metric_details, "provenance_key": provenance, "created_at": now(),
            **quality,
        }
        saved = self.store.save_macro_snapshot(snapshot)
        saved["details"] = metric_details
        return saved

    def get(self, as_of: str | None = None) -> dict[str, Any] | None:
        return self.store.get_macro_snapshot(as_of)
