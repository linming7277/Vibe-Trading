"""预测输入包（PredictionInputBundle）：冻结、指纹、不可变留档（主规格 §6.7 §8.2）。

一次冻结包含：时间契约、宏观事实（按 C 可见性选择）、市场/行业特征（截至 P）、
冻结的行业宇宙与映射版本、传导矩阵版本（HYPOTHESIS 标记）、缺口清单、评价阈值
与全部版本号。同内容同指纹幂等；内容变更产生新指纹新版本；不覆盖旧输入。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.macro_forecast.contracts import (
    FactVisibility, INPUT_BUNDLE_VERSION, MIN_BENCHMARK_CLOSINGS, MIN_INDUSTRY_CLOSINGS, MIN_MACRO_GROUPS,
    NEUTRAL_BAND, PIT_CONTRACT_VERSION, RELATIVE_BAND, TIME_CONTRACT_VERSION,
    canonical_json, comparable_periods, direction_with_guards,
    fingerprint, require_aware, visibility_decision,
)
from src.macro_forecast.registry import DIRECTION_MACRO_GROUPS

BUNDLE_KIND_LIVE_FORWARD_INPUTS = "LIVE_FORWARD_INPUTS"
DATA_MODE_FULL = "FULL"
DATA_MODE_DOMESTIC_LIMITED = "DOMESTIC_LIMITED"
DATA_MODE_FACTS_ONLY = "FACTS_ONLY"

TRANSMISSION_MATRIX_VERSION = "value-macro-sector-v2.1.0"
TRANSMISSION_MATRIX_STATUS = "HYPOTHESIS"  # §8.3：专家配置，不是已验证因果事实

GAP_OVERSEAS = "OVERSEAS_EQUITY_INDEX_UNAVAILABLE"
GAP_FX = "USDCNY_STALE_SINCE_2021_05"
GAP_SOCIAL_FINANCING = "SOCIAL_FINANCING_MISSING"
GAP_P_CLOSE_PENDING = "P_DAY_CLOSE_PENDING"
GAP_SCHEDULE_NO_SOURCE = "SCHEDULE_NO_RELIABLE_SOURCE"


def _fmt_day(value: str) -> str:
    text = str(value or "").replace("-", "")[:8]
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else ""


# ---------------------------------------------------------------------------
# 市场特征（§8.2：只用截至 P 的数据；历史不足不补 0）
# ---------------------------------------------------------------------------

def _daily_returns(closes: list[float]) -> list[float]:
    return [
        round(closes[index] / closes[index - 1] - 1, 12)
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]


def compute_entity_features(
    bars: list[dict[str, Any]], *, expected_last_date: str, min_closings: int,
) -> dict[str, Any]:
    """1日/5日收益、20日波动、量额相对20日均值；要求最后一根 K 线恰为 P。"""
    ordered = sorted(
        (row for row in bars if row.get("close") is not None and float(row["close"]) > 0),
        key=lambda row: str(row.get("trade_date") or ""),
    )
    last_date = str(ordered[-1]["trade_date"]) if ordered else ""
    if last_date != str(expected_last_date).replace("-", ""):
        return {"status": "EXCLUDED_NO_P_BAR", "last_bar_date": _fmt_day(last_date),
                "expected_p": _fmt_day(expected_last_date), "closings": len(ordered)}
    if len(ordered) < max(min_closings, 2):
        return {"status": "EXCLUDED_INSUFFICIENT_HISTORY", "last_bar_date": _fmt_day(last_date),
                "closings": len(ordered), "required": max(min_closings, 2)}
    closes = [float(row["close"]) for row in ordered]
    returns = _daily_returns(closes)
    window20 = returns[-20:]
    variance = (
        sum((item - sum(window20) / len(window20)) ** 2 for item in window20) / (len(window20) - 1)
        if len(window20) >= 2 else None
    )
    amounts = [float(row["amount"]) for row in ordered[-20:] if row.get("amount") not in (None, 0)]
    amount_ratio = None
    if amounts and len(amounts) >= 6 and ordered[-1].get("amount"):
        base = sum(amounts[:-1]) / (len(amounts) - 1)
        if base and base > 0:
            amount_ratio = round(amounts[-1] / base, 6)
    return {
        "status": "READY",
        "last_bar_date": _fmt_day(last_date),
        "closings": len(ordered),
        "close": closes[-1],
        "ret_1d": returns[-1] if returns else None,
        "ret_5d": (round(closes[-1] / closes[-6] - 1, 12) if len(closes) >= 6 else None),
        "vol_20d": round(variance ** 0.5, 12) if variance is not None else None,
        "amount_ratio_vs_20d_mean": amount_ratio,
    }


def compute_market_section(
    bars_map: dict[str, list[dict[str, Any]]], *, benchmark_code: str, previous_date: str,
) -> dict[str, Any]:
    benchmark_bars = bars_map.get(benchmark_code) or []
    benchmark = compute_entity_features(benchmark_bars, expected_last_date=previous_date,
                                        min_closings=MIN_BENCHMARK_CLOSINGS)
    market = {
        "benchmark_code": benchmark_code,
        "benchmark": benchmark,
        "reference": {},
        "benchmark_ready": benchmark.get("status") == "READY",
    }
    for code, bars in sorted(bars_map.items()):
        if code == benchmark_code:
            continue
        market["reference"][code] = compute_entity_features(
            bars, expected_last_date=previous_date, min_closings=MIN_BENCHMARK_CLOSINGS,
        )
    return market


def compute_industry_section(
    *, bars_map: dict[str, list[dict[str, Any]]], industry_rows: list[dict[str, Any]],
    benchmark_code: str, previous_date: str,
) -> dict[str, Any]:
    benchmark = compute_entity_features(bars_map.get(benchmark_code) or [],
                                        expected_last_date=previous_date,
                                        min_closings=MIN_BENCHMARK_CLOSINGS)
    benchmark_ret_1d = (benchmark.get("ret_1d") if benchmark.get("status") == "READY" else None)
    benchmark_ret_5d = (benchmark.get("ret_5d") if benchmark.get("status") == "READY" else None)
    entries: list[dict[str, Any]] = []
    ready = 0
    for row in industry_rows:
        code = str(row.get("code") or "")
        features = compute_entity_features(bars_map.get(code) or [], expected_last_date=previous_date,
                                           min_closings=MIN_INDUSTRY_CLOSINGS)
        entry: dict[str, Any] = {
            "code": code, "name_zh": row.get("name_zh"), "matrix_key": row.get("name_zh"),
            "matrix_linked": bool(row.get("matrix_linked")), **features,
        }
        if features.get("status") == "READY":
            ready += 1
            entry["excess_ret_1d"] = (
                round(features["ret_1d"] - benchmark_ret_1d, 12)
                if benchmark_ret_1d is not None and features.get("ret_1d") is not None else None
            )
            entry["excess_ret_5d"] = (
                round(features["ret_5d"] - benchmark_ret_5d, 12)
                if benchmark_ret_5d is not None and features.get("ret_5d") is not None else None
            )
        entries.append(entry)
    return {
        "entries": entries,
        "ready_count": ready,
        "total": len(entries),
        "matrix_version": TRANSMISSION_MATRIX_VERSION,
        "matrix_status": TRANSMISSION_MATRIX_STATUS,
        "benchmark_for_excess": benchmark_code,
    }


# ---------------------------------------------------------------------------
# 宏观事实选择（§6.2；从 macro_series 原始行选取 C 可见版本）
# ---------------------------------------------------------------------------

def select_macro_facts(
    series_rows: list[dict[str, Any]], *, cutoff: datetime,
) -> list[dict[str, Any]]:
    """每个序列取 C 可见的最新 observation，以及同口径上一期做比较。

    series_rows 需含 series_id / observation_date / release_date / value / unit /
    fetched_at / vintage_id。选择层执行 §6.2：captured<=C 且（有日期级发布时）
    发布日末 <=C 的保守边界；无可证边界的不进事实层。
    """
    require_aware(cutoff, "cutoff")
    by_series: dict[str, list[dict[str, Any]]] = {}
    for raw in series_rows:
        series_id = str(raw.get("series_id") or "")
        day = str(raw.get("observation_date") or "").replace("-", "")[:8]
        if series_id and day:
            by_series.setdefault(series_id, []).append(raw)
    facts: list[dict[str, Any]] = []
    for series_id, rows in sorted(by_series.items()):
        rows.sort(key=lambda row: str(row.get("observation_date") or ""))
        candidates: list[tuple[FactVisibility, dict[str, Any]]] = []
        for row in rows:
            visibility = visibility_decision(
                cutoff=cutoff,
                release_time_precision="DATE",
                source_released_at=row.get("release_date"),
                first_seen_at=row.get("fetched_at"),
                content_hash=row.get("vintage_id"),
            )
            if visibility.usable:
                candidates.append((visibility, row))
        if not candidates:
            continue
        visibility, latest = candidates[-1]
        previous = next(
            (row for _, row in reversed(candidates[:-1])
             if str(row.get("observation_date")) != str(latest.get("observation_date"))),
            None,
        )
        unit = str(latest.get("unit") or "")
        fact: dict[str, Any] = {
            "series_id": series_id,
            "observation_date": _fmt_day(str(latest.get("observation_date"))),
            "value": latest.get("value"),
            "unit": unit,
            "pit_status": visibility.pit_status,
            "visible_at": visibility.visible_at.isoformat() if visibility.visible_at else None,
            "revision_id": latest.get("vintage_id"),
            "source": latest.get("source"),
        }
        if previous is not None:
            ok, _ = comparable_periods(
                {"unit": latest.get("unit"), "frequency": "", "seasonal_adjustment": ""},
                {"unit": previous.get("unit"), "frequency": "", "seasonal_adjustment": ""},
            )
            guarded = direction_with_guards(latest.get("value"), previous.get("value"))
            fact.update({
                "comparison_value": previous.get("value"),
                "comparison_period": _fmt_day(str(previous.get("observation_date"))),
                "comparison_basis": "同序列上一可见观测（同单位）" if ok else "口径不一致，仅供参考",
                "direction": guarded["direction"],
                "delta": guarded["delta"],
                "negative_base_guard": guarded["negative_base_guard"],
            })
        else:
            fact.update({"comparison_value": None, "comparison_basis": "无更早可见观测"})
        facts.append(fact)
    return facts


def macro_group_coverage(facts: list[dict[str, Any]], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    groups_with_fact: set[str] = set()
    by_id = {fact["series_id"]: fact for fact in facts}
    for entry in catalog:
        if entry["series_id"] in by_id and by_id[entry["series_id"]].get("value") is not None:
            groups_with_fact.add(entry["group"])
    direction_groups = sorted(groups_with_fact & set(DIRECTION_MACRO_GROUPS))
    return {
        "groups_with_usable_fact": sorted(groups_with_fact),
        "direction_groups_covered": direction_groups,
        "direction_groups_required": MIN_MACRO_GROUPS,
        "direction_input_ok": len(direction_groups) >= MIN_MACRO_GROUPS,
    }


# ---------------------------------------------------------------------------
# 输入包组装与数据模式判定
# ---------------------------------------------------------------------------

def decide_data_mode(*, market: dict[str, Any], macro_coverage: dict[str, Any],
                     cross_market_available: bool) -> tuple[str, list[str]]:
    gaps: list[str] = []
    if not cross_market_available:
        gaps.append(GAP_OVERSEAS)
    if not market.get("benchmark_ready"):
        gaps.append("BENCHMARK_P_CLOSE_NOT_READY")
    if not macro_coverage["direction_input_ok"]:
        gaps.append("MACRO_GROUPS_BELOW_MINIMUM")
    mode = DATA_MODE_FACTS_ONLY
    if market.get("benchmark_ready") and macro_coverage["direction_input_ok"]:
        mode = DATA_MODE_DOMESTIC_LIMITED if gaps else DATA_MODE_FULL
    return mode, gaps


def build_bundle_payload(
    *,
    timeline: Any,
    bars_map: dict[str, list[dict[str, Any]]],
    series_rows: list[dict[str, Any]],
    industry_rows: list[dict[str, Any]],
    benchmark_code: str,
    macro_catalog: list[dict[str, Any]],
    registry_version: str,
    bars_as_of: str,
    p_close_pending: bool = False,
    cross_market_available: bool = False,
    extra_gaps: list[str] | None = None,
    cross_market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market = compute_market_section(bars_map, benchmark_code=benchmark_code,
                                    previous_date=timeline.previous_date)
    industries = compute_industry_section(
        bars_map=bars_map, industry_rows=industry_rows,
        benchmark_code=benchmark_code, previous_date=timeline.previous_date,
    )
    # M1-B：跨市场序列只进入新增的 cross_market_context 字段，
    # 既有 CN 宏观事实段（macro_facts）保持原 schema、原序列集合不变。
    from src.macro_data.identity import MACRO_SERIES_IDS

    domestic_rows = [row for row in series_rows
                     if str(row.get("series_id") or "") not in MACRO_SERIES_IDS]
    facts = select_macro_facts(domestic_rows, cutoff=timeline.cutoff_at)
    coverage = macro_group_coverage(facts, macro_catalog)
    # M1-B：官方中间价新鲜可见时消解 USDCNY_STALE 缺口；其余缺口逻辑不变。
    from src.macro_data.features import fx_official_mid_ready

    if fx_official_mid_ready(cross_market_context):
        gaps = [gap for gap in (GAP_SOCIAL_FINANCING, GAP_SCHEDULE_NO_SOURCE)]
    else:
        gaps = [GAP_FX, GAP_SOCIAL_FINANCING, GAP_SCHEDULE_NO_SOURCE]
    mode, decided_gaps = decide_data_mode(market=market, macro_coverage=coverage,
                                          cross_market_available=cross_market_available)
    gaps.extend(decided_gaps)
    if p_close_pending:
        gaps.append(GAP_P_CLOSE_PENDING)
    gaps.extend(extra_gaps or [])
    payload = {
        "input_bundle_version": INPUT_BUNDLE_VERSION,
        "time_contract_version": TIME_CONTRACT_VERSION,
        "pit_contract_version": PIT_CONTRACT_VERSION,
        "registry_version": registry_version,
        "transmission_matrix_version": TRANSMISSION_MATRIX_VERSION,
        "transmission_matrix_status": TRANSMISSION_MATRIX_STATUS,
        "kind": BUNDLE_KIND_LIVE_FORWARD_INPUTS,
        "target": {
            "target_date": _fmt_day(timeline.target_date),
            "previous_date": _fmt_day(timeline.previous_date),
            "cutoff_at": timeline.cutoff_at.isoformat(),
            "publish_deadline": timeline.publish_deadline.isoformat(),
            "calendar_status": timeline.calendar_status,
            "notes": list(timeline.notes),
        },
        "data_mode": mode,
        "bars_as_of": _fmt_day(bars_as_of),
        "market": market,
        "industries": industries,
        "macro_facts": facts,
        "macro_coverage": coverage,
        "macro_series_catalog": macro_catalog,
        "cross_market_context": cross_market_context or {
            "status": "UNAVAILABLE", "reason": "NOT_PROVIDED",
        },
        "schedule": {
            "status": "NO_RELIABLE_SOURCE",
            "entries": [],
            "note": "无官方发布日程来源；不输出精确时刻，待接入可信日程源后按 §6.2 例外类型登记",
        },
        "gaps": sorted(set(gaps)),
        "evaluation": {
            "neutral_band": NEUTRAL_BAND,
            "relative_band": RELATIVE_BAND,
            "band_basis": "本版固定评价区间（主规格 §7.1/§7.3），配置变更只对未来生效",
        },
    }
    payload["fingerprint"] = fingerprint(payload)
    return payload


class ForecastBundleStore:
    """research.db forecast_input_bundles：不可变输入包（同指纹幂等）。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS forecast_input_bundles (
                    bundle_id TEXT PRIMARY KEY,
                    target_date TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    data_mode TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (target_date, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_forecast_bundles_target
                    ON forecast_input_bundles(target_date DESC, created_at DESC);
            """)

    def close(self) -> None:
        self._conn.close()

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        """幂等保存：同 (target_date, fingerprint) 已存在 → 返回既有 bundle_id。"""
        target = _fmt_day(payload.get("target", {}).get("target_date") or "")
        mark = str(payload.get("fingerprint") or "")
        if not target or not mark:
            raise ValueError("payload 缺少 target_date 或 fingerprint")
        existing = self._conn.execute(
            "SELECT bundle_id, created_at FROM forecast_input_bundles WHERE target_date=? AND fingerprint=?",
            (target, mark),
        ).fetchone()
        if existing:
            return {"status": "ALREADY_SAVED", "bundle_id": existing["bundle_id"],
                    "fingerprint": mark, "written": 0}
        bundle_id = f"mfib_{target.replace('-', '')}_{mark[-12:]}"
        created = datetime.now().astimezone().isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO forecast_input_bundles(bundle_id, target_date, fingerprint, data_mode, payload_json, created_at) VALUES(?,?,?,?,?,?)",
                (bundle_id, target, mark, str(payload.get("data_mode")),
                 canonical_json(payload), created),
            )
        return {"status": "SAVED", "bundle_id": bundle_id, "fingerprint": mark, "written": 1}

    def load(self, bundle_id: str) -> dict[str, Any] | None:
        import json as _json

        row = self._conn.execute(
            "SELECT payload_json FROM forecast_input_bundles WHERE bundle_id=?", (bundle_id,),
        ).fetchone()
        return _json.loads(row["payload_json"]) if row else None

    def latest_for_target(self, target_date: str) -> dict[str, Any] | None:
        import json as _json

        row = self._conn.execute(
            "SELECT bundle_id, payload_json FROM forecast_input_bundles WHERE target_date=? ORDER BY created_at DESC LIMIT 1",
            (_fmt_day(target_date),),
        ).fetchone()
        if not row:
            return None
        payload = _json.loads(row["payload_json"])
        return {"bundle_id": row["bundle_id"], "fingerprint": payload.get("fingerprint"), "payload": payload}

    def data_version(self) -> int:
        row = self._conn.execute("PRAGMA data_version").fetchone()
        return int(row[0]) if row else 0


class StabilityGuard:
    """§6.7：读前后校验各库修订号；并发变化重取一次，仍不稳定放弃保存。"""

    def __init__(self, connections: dict[str, sqlite3.Connection]) -> None:
        self._connections = connections
        self._initial = {name: _data_version(conn) for name, conn in connections.items()}

    def stable(self) -> tuple[bool, dict[str, int]]:
        current = {name: _data_version(conn) for name, conn in self._connections.items()}
        return current == self._initial, {"initial": self._initial, "final": current}


def _data_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA data_version").fetchone()
    return int(row[0]) if row else 0
