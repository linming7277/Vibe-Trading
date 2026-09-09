"""Forward-only A/B experiment runner（xcm-cny-forward-v1，任务书 V1）。

Arm A = BASELINE_V2（prompt v2 语义：无 xcm）；Arm B = CNY_V21（prompt v2.1：
仅 xcm.cny_mid）。同一 bundle 世界、同一模型配置，唯一差异 = CNY 官方中间价
证据。SHADOW-only；双臂复用既有 outcome contract（automation 的 outcome
扫描会评价所有未评价 forecast 行）。

资格与身份：
- CNY_VISIBLE_EXPERIMENT_DAY：cutoff 前真实可见（published_at/captured_at 双门槛）；
  不可见 → CNY_NOT_VISIBLE，该日不进入效果比较。
- 世界未就绪（FACTS_ONLY / benchmark 未 READY）→ WORLD_NOT_READY，0 LLM。
- 臂身份 = input_fingerprint 后缀 ":arm=<ARM>" + input_bundle_id 后缀
  "|arm=<ARM>"；REUSED 按臂独立判定，不覆盖、不重调模型。
- 顺序交替（§五）：目标日奇数 A→B，偶数 B→A（避免 provider 顺序偏差）。
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.macro_forecast.bars import ForecastBarStore
from src.macro_forecast.engine import ForecastEngine
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.forecast_service import (
    load_macro_context, resolve_model_config, FORECAST_MODEL_ROLE,
)
from src.macro_forecast.prompt import build_cny_mid_summary
from src.macro_forecast.registry import InstrumentRegistryStore, PRIMARY_BENCHMARK, REFERENCE_INDEXES
from src.macro_forecast.service import load_industry_rows, load_macro_series_rows
from src.macro_data.features import build_cross_market_context

logger = logging.getLogger(__name__)

EXPERIMENT_ID = "xcm-cny-forward-v1"
ARM_BASELINE = "BASELINE_V2"
ARM_CNY = "CNY_V21"
EXPERIMENT_ARMS = (ARM_BASELINE, ARM_CNY)

STATUS_CNY_NOT_VISIBLE = "CNY_NOT_VISIBLE"
STATUS_WORLD_NOT_READY = "WORLD_NOT_READY"
STATUS_READY = "READY"

MIN_SAMPLE_DAYS = 20
FORMAL_SAMPLE_DAYS = 30
DATA_PIPELINE_STOP_DAYS = 5


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def cny_visibility(bundle: dict[str, Any], series_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """ cutoff 下 CNY 官方中间价是否真实可见（§二 资格门槛）。"""
    cutoff = ((bundle.get("target") or {}).get("cutoff_at") or "")
    summary = None
    if cutoff:
        context = build_cross_market_context(
            series_rows, cutoff=datetime.fromisoformat(cutoff))
        summary = build_cny_mid_summary(context)
    if summary is None:
        return {"visible": False, "status": STATUS_CNY_NOT_VISIBLE, "summary": None}
    return {"visible": True, "status": "CNY_VISIBLE_EXPERIMENT_DAY", "summary": summary}


def world_readiness(bundle: dict[str, Any]) -> tuple[bool, str]:
    """确定性弃权规避：世界必须已就绪（非 FACTS_ONLY 且基准 READY）。"""
    if str(bundle.get("data_mode")) == "FACTS_ONLY":
        return False, "bundle data_mode=FACTS_ONLY（基准/宏观输入未就绪）"
    benchmark = ((bundle.get("market") or {}).get("benchmark") or {})
    if benchmark.get("status") != "READY":
        return False, f"benchmark status={benchmark.get('status')}"
    return True, "READY"


def _arm_identity(arm: str, base_fingerprint: str, base_bundle_id: str) -> tuple[str, str]:
    return f"{base_fingerprint}:arm={arm}", f"{base_bundle_id}|arm={arm}"


def _overlap(predicted: list[str], other: list[str]) -> float | None:
    if not predicted and not other:
        return None
    if not predicted or not other:
        return 0.0
    return round(len(set(predicted) & set(other)) / len(set(predicted) | set(other)), 4)


def run_experiment_pair(*, target_date: str | None = None, db_path: Path | None = None,
                        dry_run: bool = False, model_config: dict[str, Any] | None = None,
                        force: bool = False) -> dict[str, Any]:
    """对一个 target 跑一次 A/B（或 dry-run）。每日预算 = 2 logical calls。"""
    from src.macro_forecast.bundle import ForecastBundleStore
    from src.macro_forecast.forecast_service import load_trading_days

    if db_path is None:
        from src.config.paths import get_runtime_root
        db_path = get_runtime_root() / "research.db"
    research_db = db_path

    days = load_trading_days()
    if target_date is None:
        from datetime import datetime as _dt
        from src.macro_forecast.contracts import next_trading_day as _next
        target_date = _next(_dt.now().strftime("%Y%m%d"), days) or ""
        if not target_date:
            return {"status": "CALENDAR_UNAVAILABLE",
                    "reason": "交易日历未覆盖当前日之后（离线日历止于最近已归档日）；"
                              "请显式传 --target 或在 EOD 后运行",
                    "target": "", "llm_calls": 0}
    target_date = str(target_date).replace("-", "")

    store = ForecastBundleStore(research_db)
    try:
        row = store.latest_for_target(target_date)
    finally:
        store.close()
    if not row:
        return {"status": STATUS_WORLD_NOT_READY, "reason": f"目标 {target_date} 无已冻结输入包",
                "target": target_date, "llm_calls": 0}
    bundle = row["payload"]
    ready, ready_reason = world_readiness(bundle)
    series_rows = load_macro_series_rows(research_db)
    visibility = cny_visibility(bundle, series_rows)

    if dry_run:
        return {
            "status": "DRY_RUN", "experiment_id": EXPERIMENT_ID, "target": target_date,
            "cny_visibility": visibility["status"],
            "cny_summary": visibility["summary"],
            "world_ready": ready, "world_reason": ready_reason,
            "world_fingerprint": bundle.get("fingerprint"),
            "arm_a": {"arm": ARM_BASELINE, "would_call": bool(ready and visibility["visible"] or force and ready),
                      "prompt_semantics": "v2（无 xcm）"},
            "arm_b": {"arm": ARM_CNY, "would_call": bool(ready and visibility["visible"]),
                      "prompt_semantics": "v2.1（xcm.cny_mid）"},
            "llm_calls": 0,
        }

    if not visibility["visible"]:
        return {"status": STATUS_CNY_NOT_VISIBLE, "target": target_date,
                "reason": "cutoff 前 CNY 官方中间价不可见，该日不进入 A/B 比较", "llm_calls": 0}
    if not ready:
        return {"status": STATUS_WORLD_NOT_READY, "target": target_date,
                "reason": ready_reason, "llm_calls": 0}

    summary = visibility["summary"]
    previous_date = str((bundle.get("target") or {}).get("previous_date") or "").replace("-", "")
    base_fp = str(bundle.get("fingerprint"))
    base_bundle_id = f"{target_date}@{base_fp[-12:]}"
    if model_config is None:
        model_config = resolve_model_config(role=FORECAST_MODEL_ROLE, db_path=research_db)
    if model_config is None:
        return {"status": "MODEL_CONFIG_UNAVAILABLE", "target": target_date, "llm_calls": 0}
    model_config = {**model_config, "extra_body": {"thinking": {"type": "disabled"}}}

    engine = ForecastEngine(ForecastStore(research_db))
    registry = InstrumentRegistryStore(research_db)
    bar_store = ForecastBarStore(research_db)
    day_parity = int(target_date[-1]) % 2  # 1=奇数日 A→B；0=偶数日 B→A
    arm_order = "A_B" if day_parity == 1 else "B_A"
    results: dict[str, Any] = {}
    try:
        industry_rows = load_industry_rows(registry)
        codes = ([PRIMARY_BENCHMARK] + [c for c, _ in REFERENCE_INDEXES]
                 + [r["code"] for r in industry_rows])
        bars_map = bar_store.read_bars(codes, end_date=previous_date or "99999999", count=320)
        macro_context = load_macro_context(previous_date, db_path=research_db)
        world_kw = dict(bars_map=bars_map, industry_rows=industry_rows,
                        macro_context=macro_context)
        preps = {}
        for arm in EXPERIMENT_ARMS:
            fp, bundle_id = _arm_identity(arm, base_fp, base_bundle_id)
            preps[arm] = engine.prepare(
                bundle={**bundle, "fingerprint": fp}, bundle_id=bundle_id,
                candidate_limit=None,
                cross_market_summary=(summary if arm == ARM_CNY else None),
                **world_kw,
            ), fp
        order = [ARM_BASELINE, ARM_CNY] if day_parity == 1 else [ARM_CNY, ARM_BASELINE]
        runs: dict[str, dict[str, Any]] = {}
        for arm in order:
            prep, fp = preps[arm]
            runs[arm] = engine.run(prep=prep, bundle={**bundle, "fingerprint": fp},
                                   run_mode="SHADOW", model_config=model_config,
                                   macro_summary=macro_context)
        for arm in EXPERIMENT_ARMS:
            run = runs[arm]
            payload = run.get("payload") or {}
            market = payload.get("market") or {}
            industries = payload.get("industries") or {}
            entries = (industries.get("relative_strong") or []) + (industries.get("relative_weak") or [])
            all_keys = list(market.get("evidence_keys") or []) + [
                k for item in entries for k in (item.get("evidence_keys") or [])]
            results[arm] = {
                "forecast_id": run.get("id"),
                "status": run.get("status"),
                "market_direction": run.get("direction"),
                "strong": [i.get("id") for i in (industries.get("relative_strong") or [])],
                "weak": [i.get("id") for i in (industries.get("relative_weak") or [])],
                "cny_used": any(k.startswith("XR_CNYMID") for k in all_keys),
                "validation_status": (run.get("validation") or {}).get("status"),
                "latency_ms": (run.get("row") or {}).get("model_latency_ms")
                if isinstance(run.get("row"), dict) else None,
                "model": model_config.get("model"),
            }
        direction_a = results[ARM_BASELINE]["market_direction"]
        direction_b = results[ARM_CNY]["market_direction"]
        market_same = (direction_a is not None and direction_a == direction_b)
        pair_record = {
            "id": f"mfx_{target_date}_{uuid.uuid4().hex[:10]}",
            "experiment_id": EXPERIMENT_ID,
            "target_trade_date": f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}",
            "world_fingerprint": base_fp,
            "arm_order": arm_order,
            "day_status": STATUS_READY,
            "cny_visible": 1,
            "cny_value": summary.get("value"),
            "cny_obs": summary.get("obs"),
            "market_same": 1 if market_same else 0,
            "strong_overlap": _overlap(results[ARM_BASELINE]["strong"], results[ARM_CNY]["strong"]),
            "weak_overlap": _overlap(results[ARM_BASELINE]["weak"], results[ARM_CNY]["weak"]),
            "cny_used": 1 if results[ARM_CNY]["cny_used"] else 0,
            "arm_a_forecast_id": results[ARM_BASELINE]["forecast_id"],
            "arm_b_forecast_id": results[ARM_CNY]["forecast_id"],
            "notes": f"order={arm_order}; direction_a={direction_a}; direction_b={direction_b}",
            "created_at": _now(),
        }
        fstore = ForecastStore(research_db)
        try:
            fstore.save_experiment_day(pair_record)
        finally:
            fstore.close()
        llm_calls = sum(int(runs[arm].get("logical_calls") or 0) for arm in EXPERIMENT_ARMS)
        return {
            "status": "PAIRED", "experiment_id": EXPERIMENT_ID, "target": target_date,
            "arm_order": arm_order, "cny": {"value": summary.get("value"), "obs": summary.get("obs")},
            "market_same": market_same,
            "arms": results,
            "llm_calls": llm_calls,
        }
    finally:
        engine.store.close()
        registry.close()
        bar_store.close()


def get_macro_forecast_experiment_performance(experiment_id: str = EXPERIMENT_ID,
                                              *, db_path: Path | None = None) -> dict[str, Any]:
    """只读实验聚合（§十八/§十九）：0 LLM、0 网络。"""
    if db_path is None:
        from src.config.paths import get_runtime_root
        db_path = get_runtime_root() / "research.db"
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        days = [dict(row) for row in conn.execute(
            "SELECT * FROM macro_forecast_experiment_days WHERE experiment_id=? ORDER BY target_trade_date",
            (experiment_id,)).fetchall()]
        paired = [day for day in days if day["day_status"] == STATUS_READY and day["cny_visible"]]
        arms: dict[str, dict[str, Any]] = {}
        for arm, column in ((ARM_BASELINE, "arm_a_forecast_id"), (ARM_CNY, "arm_b_forecast_id")):
            ids = [day[column] for day in paired if day[column]]
            hits = abstains = invalid = total = 0
            industry_hits = strong_hits = weak_hits = strong_n = weak_n = 0
            for forecast_id in ids:
                forecast = conn.execute(
                    "SELECT status, market_direction FROM macro_market_forecasts WHERE id=?",
                    (forecast_id,)).fetchone()
                if not forecast:
                    continue
                total += 1
                if forecast["status"] == "ABSTAINED":
                    abstains += 1
                if forecast["status"] == "INVALID_OUTPUT":
                    invalid += 1
                outcome = conn.execute(
                    "SELECT market_evaluation, industry_results_json FROM macro_forecast_outcomes WHERE forecast_id=?",
                    (forecast_id,)).fetchone()
                if not outcome:
                    continue
                if outcome["market_evaluation"] == "HIT":
                    hits += 1
                try:
                    import json as _json
                    for item in _json.loads(outcome["industry_results_json"] or "[]"):
                        if item.get("evaluation") not in ("HIT", "MISS"):
                            continue
                        if item.get("evaluation") == "HIT":
                            industry_hits += 1
                        if item.get("side") == "RELATIVE_STRONG":
                            strong_n += 1
                            strong_hits += 1 if item["evaluation"] == "HIT" else 0
                        else:
                            weak_n += 1
                            weak_hits += 1 if item["evaluation"] == "HIT" else 0
                except (TypeError, ValueError):
                    continue
            arms[arm] = {
                "days": len(ids), "evaluated_forecasts": total, "market_hits": hits,
                "industry_hits": industry_hits, "strong_hits": strong_hits, "strong_n": strong_n,
                "weak_hits": weak_hits, "weak_n": weak_n,
                "abstain": abstains, "semantic_invalid": invalid,
            }
        visible_days = sum(1 for day in days if day["cny_visible"])
        invisible_streak = 0
        for day in reversed(days):
            if day["cny_visible"]:
                break
            invisible_streak += 1
        warnings: list[str] = []
        n = len(paired)
        if n < MIN_SAMPLE_DAYS:
            warnings.append(
                f"样本 {n} < {MIN_SAMPLE_DAYS}：禁止评价 CNY 对准确率的影响，仅显示样本量与原始成绩")
        elif n < FORMAL_SAMPLE_DAYS:
            warnings.append(f"样本 {n} 处于 {MIN_SAMPLE_DAYS}-{FORMAL_SAMPLE_DAYS - 1}：仅初步观察")
        if invisible_streak >= DATA_PIPELINE_STOP_DAYS:
            warnings.append(f"连续 {invisible_streak} 个交易日 CNY 不可见 → DATA_PIPELINE_ISSUE")
        invalid_a = arms[ARM_BASELINE]["semantic_invalid"]
        invalid_b = arms[ARM_CNY]["semantic_invalid"]
        if invalid_b > invalid_a and invalid_b >= 3:
            warnings.append("B 臂 semantic failure 明显高于 A → EXPERIMENT_QUALITY_ISSUE")
        return {
            "experiment_id": experiment_id,
            "experiment_days": len(days),
            "cny_visible_days": visible_days,
            "arms": arms,
            "different_market_predictions": sum(1 for day in paired if not day["market_same"]),
            "cny_used_days": sum(1 for day in paired if day["cny_used"]),
            "sample_warning": "；".join(warnings) if warnings else "",
            "stop_flags": ([("DATA_PIPELINE_ISSUE" if invisible_streak >= DATA_PIPELINE_STOP_DAYS else None),
                            ("EXPERIMENT_QUALITY_ISSUE" if (invalid_b > invalid_a and invalid_b >= 3) else None)]),
        }
    finally:
        conn.close()
