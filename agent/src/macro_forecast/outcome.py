"""收盘复盘与结果评价契约 V1（macro-forecast-outcome-contract-v1.0.0）。

本契约在读取任何 target_trade_date 实际收盘结果之前冻结（见
docs/macro-outlook/macro-forecast-outcome-contract-v1.md 的冻结时间戳）。
第一批真实评价完成前不得修改区间或语义；未来调整须新版本号并保留原成绩。

固定口径：
- 基准：沪深300 价格指数 000300.SH（大盘与行业相对基准一致）；
- R_market = T 正式收盘 / P 正式收盘 - 1，只取 forecast_index_bars，
  不用盘中 quote、不用后复权个股价格；
- 大盘三分类（Evaluation Threshold V1，工程初值，非历史优化参数）：
  偏强 R > +0.50%；震荡 -0.50% <= R <= +0.50%（边界值属震荡）；偏弱 R < -0.50%；
- 行业 RR = R_industry - R_market；看强 RR>0 命中，看弱 RR<0 命中；
  |RR| < 1e-10 视为 0 → 未命中（相同不构成"相对更强/更弱"）；
- 行业自身涨跌不改变判定（只看相对基准）；
- 状态：HIT / MISS / ABSTAINED / NOT_EVALUABLE / PENDING（无 PARTIAL_HIT）；
- MODEL_FAILED / INVALID_OUTPUT → NOT_EVALUABLE（不进预测命中率分母，
  进系统可用率分母）；ABSTAINED 不进命中率分母、不算错误；
- invalidation 不自动取消成绩：V1 记 invalidation_observed=unknown，
  原预测照常评价；
- 评价读取 structured forecast payload，不从中文 narrative 解析；
- 全程 0 LLM / 0 网络，只读已持久化的预测与 K 线。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

OUTCOME_FORMULA_VERSION = "macro-forecast-outcome-contract-v1.0.0"
MARKET_EVALUATION_VERSION = "market-direction-evaluation-v1.0.0"
# §五/§六：±0.50% 为 V1 工程评价阈值（边界值属震荡），非优化参数；禁止回溯调参。
MARKET_BAND = 0.005
# §十四：浮点零容差。
RR_ZERO_TOLERANCE = 1e-10

BENCHMARK_SYMBOL = "000300.SH"

MARKET_STRONGER = "偏强"
MARKET_RANGE = "震荡"
MARKET_WEAKER = "偏弱"

MARKET_CLASS_BY_DIRECTION = {"STRONGER": MARKET_STRONGER, "RANGE_BOUND": MARKET_RANGE,
                             "WEAKER": MARKET_WEAKER, "ABSTAIN": "暂不判断"}

OUTCOME_HIT = "HIT"
OUTCOME_MISS = "MISS"
OUTCOME_ABSTAINED = "ABSTAINED"
OUTCOME_NOT_EVALUABLE = "NOT_EVALUABLE"
OUTCOME_PENDING = "PENDING"

# §64：老板端中文标签。
OUTCOME_CN = {
    OUTCOME_HIT: "命中", OUTCOME_MISS: "未命中", OUTCOME_ABSTAINED: "暂不判断",
    OUTCOME_NOT_EVALUABLE: "数据不足无法评价", OUTCOME_PENDING: "尚未到复盘时间",
}

# 契约冻结时间（先于任何 target 实际结果的读取；由文档同步记录）。
CONTRACT_FROZEN_AT = "2026-09-07T15:31:00+08:00"


def classify_market_return(r_market: float) -> str:
    """±0.50% 边界属震荡：+0.500000% → 震荡；> +0.500000% → 偏强。"""
    if r_market > MARKET_BAND:
        return MARKET_STRONGER
    if r_market < -MARKET_BAND:
        return MARKET_WEAKER
    return MARKET_RANGE


def evaluate_market_prediction(predicted: str | None, actual_class: str) -> str:
    if predicted in (None, "", "ABSTAIN"):
        return OUTCOME_ABSTAINED
    predicted_cn = MARKET_CLASS_BY_DIRECTION.get(predicted, predicted)
    return OUTCOME_HIT if predicted_cn == actual_class else OUTCOME_MISS


def evaluate_industry_prediction(side: str, rr: float) -> str:
    """相对基准语义：看强 RR>0 命中；看弱 RR<0 命中；|RR|<1e-10 视为 0 → 未命中。"""
    if abs(rr) < RR_ZERO_TOLERANCE:
        return OUTCOME_MISS
    if side == "RELATIVE_STRONG":
        return OUTCOME_HIT if rr > 0 else OUTCOME_MISS
    if side == "RELATIVE_WEAK":
        return OUTCOME_HIT if rr < 0 else OUTCOME_MISS
    return OUTCOME_NOT_EVALUABLE


def _bars_close(db_path: Path, code: str, trade_date: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT close, source, first_observed_date, pit_status, fetched_at "
            "FROM forecast_index_bars WHERE code=? AND trade_date=?",
            (code, trade_date),
        ).fetchone()
        if not row or row["close"] is None:
            return None
        return {
            "close": float(row["close"]), "source": row["source"],
            "first_observed_date": row["first_observed_date"],
            "pit_status": row["pit_status"], "fetched_at": row["fetched_at"],
            "bar_ref": f"forecast_index_bars[{code},{trade_date}]",
        }
    finally:
        conn.close()


def evaluate_forecast_outcome(
    forecast_id: str,
    *,
    as_of: str | None = None,
    research_db: Any = None,
) -> dict[str, Any]:
    """纯确定性评价一条预测（0 LLM / 0 网络；只读留档与 K 线表）。

    返回 status：EVALUATED（含 HIT/MISS/ABSTAINED 结论）、PENDING（T 行情未就）、
    NOT_EVALUABLE（预测无效或 P 行情缺失）、REUSED（已有正式评价）。
    """
    from src.macro_forecast.forecast_store import ForecastStore

    db_path = Path(research_db or (get_runtime_root() / "research.db"))

    store = ForecastStore(db_path)
    try:
        existing = store.load_outcome(forecast_id, outcome_formula_version=OUTCOME_FORMULA_VERSION)
        if existing:
            return {"status": "REUSED", "outcome": existing, "written": 0}
    finally:
        store.close()

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, target_trade_date, run_mode, status, input_fingerprint, market_direction, structured_payload_json "
            "FROM macro_market_forecasts WHERE id=?",
            (forecast_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"status": "NOT_EVALUABLE", "reason": f"预测留档不存在：{forecast_id}"}

    payload = json.loads(row["structured_payload_json"] or "{}")
    model_output = payload.get("model_output") or {}
    validation_entries = (payload.get("validation") or {}).get("industry_entries") or []
    target = str(row["target_trade_date"]).replace("-", "")

    # P 日来自该预测当时冻结的输入包（按 input_fingerprint 关联），不得事后改写。
    bundle_conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        bundle_row = bundle_conn.execute(
            "SELECT payload_json FROM forecast_input_bundles WHERE fingerprint=?",
            (row["input_fingerprint"],),
        ).fetchone()
    finally:
        bundle_conn.close()
    if not bundle_row:
        return {"status": "NOT_EVALUABLE",
                "reason": f"输入包缺失（fingerprint={row['input_fingerprint']}），P 无法确定"}
    bundle_payload = json.loads(bundle_row[0] or "{}")
    previous_date = str((bundle_payload.get("target") or {}).get("previous_date") or "").replace("-", "")

    base = {
        "forecast_id": forecast_id,
        "target_trade_date": f"{target[:4]}-{target[4:6]}-{target[6:8]}",
        "run_mode": row["run_mode"],
        "forecast_status": row["status"],
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "outcome_formula_version": OUTCOME_FORMULA_VERSION,
        "market_evaluation_version": MARKET_EVALUATION_VERSION,
        "market_band": MARKET_BAND,
        "invalidation_observed": "unknown",  # §23：V1 不自动取消成绩
        "source_as_of": as_of or datetime.now(timezone.utc).isoformat(),
    }

    # §十：无效预测 → NOT_EVALUABLE（进系统可用率分母，不进命中率分母）
    if row["status"] in ("MODEL_FAILED", "INVALID_OUTPUT"):
        return {"status": "NOT_EVALUABLE",
                "outcome": {**base, "market_evaluation": OUTCOME_NOT_EVALUABLE,
                            "not_evaluable_reason": f"forecast_status={row['status']}"},
                "written": 0}

    # §二十：行情门
    p_benchmark = _bars_close(db_path, BENCHMARK_SYMBOL, previous_date) if previous_date else None
    if p_benchmark is None:
        return {"status": "NOT_EVALUABLE",
                "outcome": {**base, "market_evaluation": OUTCOME_NOT_EVALUABLE,
                            "not_evaluable_reason": "基准 P 日收盘缺失"},
                "written": 0}
    t_benchmark = _bars_close(db_path, BENCHMARK_SYMBOL, target)
    if t_benchmark is None:
        return {"status": "PENDING",
                "outcome": {**base, "market_evaluation": OUTCOME_PENDING,
                            "pending_reason": "T 日正式收盘尚未入库（不用盘中数据）"},
                "written": 0}

    r_market = t_benchmark["close"] / p_benchmark["close"] - 1
    actual_class = classify_market_return(r_market)
    predicted_direction = row["market_direction"]
    abstained = bool(model_output.get("abstain")) or predicted_direction == "ABSTAIN"
    market_evaluation = (OUTCOME_ABSTAINED if abstained
                         else evaluate_market_prediction(predicted_direction, actual_class))

    # §十二-十五：行业相对评价（读取结构化条目，非 narrative）
    # §20/§22：任一预测行业 T 价未齐 → 整体 PENDING（不部分评价）；行业 P 缺 → NOT_EVALUABLE。
    codes_predicted = [str(entry.get("industry_id") or "").replace("tdx:", "")
                       for entry in validation_entries]
    for code in codes_predicted:
        if _bars_close(db_path, code, target) is None:
            return {"status": "PENDING",
                    "outcome": {**base, "market_evaluation": OUTCOME_PENDING,
                                "pending_reason": f"行业 {code} T 日正式收盘尚未入库"},
                    "written": 0}
    for code in codes_predicted:
        if _bars_close(db_path, code, previous_date) is None:
            return {"status": "NOT_EVALUABLE",
                    "outcome": {**base, "market_evaluation": OUTCOME_NOT_EVALUABLE,
                                "not_evaluable_reason": f"行业 {code} P 日收盘缺失"},
                    "written": 0}
    industry_results: list[dict[str, Any]] = []
    alias_map = (payload.get("input") or {}).get("alias_map") or {}
    catalog = (payload.get("input") or {}).get("evidence_catalog") or {}
    for entry in validation_entries:
        code = str(entry.get("industry_id") or "").replace("tdx:", "")
        side = entry.get("side") or ""
        t_bar = _bars_close(db_path, code, target)
        p_bar = _bars_close(db_path, code, previous_date)
        if t_bar is None or p_bar is None:
            industry_results.append({
                "industry_id": entry.get("industry_id"), "display_name": entry.get("display_name"),
                "side": side, "evaluation": OUTCOME_PENDING,
                "r_industry": None, "rr": None,
                "reason": "T/P 行情不齐", "reason_basis": entry.get("reason_basis"),
            })
            continue
        r_industry = t_bar["close"] / p_bar["close"] - 1
        rr = r_industry - r_market
        industry_results.append({
            "industry_id": entry.get("industry_id"), "display_name": entry.get("display_name"),
            "side": side,
            "evaluation": evaluate_industry_prediction(side, rr),
            "r_industry": round(r_industry, 12), "rr": round(rr, 12),
            "absolute_note": ("行业自身下跌但相对跑赢仍计命中" if r_industry < 0 and rr > 0 and side == "RELATIVE_STRONG"
                              else ("行业自身上涨但相对跑输仍计未命中" if r_industry > 0 and rr < 0 and side == "RELATIVE_STRONG" else "")),
            "reason_basis": entry.get("reason_basis"),
        })

    strong = [item for item in industry_results if item["side"] == "RELATIVE_STRONG"]
    weak = [item for item in industry_results if item["side"] == "RELATIVE_WEAK"]
    strong_evaluable = [item for item in strong if item["evaluation"] in (OUTCOME_HIT, OUTCOME_MISS)]
    weak_evaluable = [item for item in weak if item["evaluation"] in (OUTCOME_HIT, OUTCOME_MISS)]

    outcome = {
        **base,
        "previous_trade_date": f"{previous_date[:4]}-{previous_date[4:6]}-{previous_date[6:8]}",
        "previous_close": p_benchmark["close"], "actual_close": t_benchmark["close"],
        "actual_return": round(r_market, 12), "actual_market_class": actual_class,
        "predicted_market_direction": predicted_direction,
        "market_evaluation": market_evaluation,
        "industry_results": industry_results,
        "strong_count": len(strong), "strong_hits": sum(1 for i in strong_evaluable if i["evaluation"] == OUTCOME_HIT),
        "weak_count": len(weak), "weak_hits": sum(1 for i in weak_evaluable if i["evaluation"] == OUTCOME_HIT),
        "strong_evaluable_count": len(strong_evaluable), "weak_evaluable_count": len(weak_evaluable),
        "industry_hit_rate": (
            round(sum(1 for i in (*strong_evaluable, *weak_evaluable) if i["evaluation"] == OUTCOME_HIT)
                  / len((*strong_evaluable, *weak_evaluable)), 6)
            if (*strong_evaluable, *weak_evaluable) else None),
        "strong_hit_rate": (round(sum(1 for i in strong_evaluable if i["evaluation"] == OUTCOME_HIT) / len(strong_evaluable), 6)
                            if strong_evaluable else None),
        "weak_hit_rate": (round(sum(1 for i in weak_evaluable if i["evaluation"] == OUTCOME_HIT) / len(weak_evaluable), 6)
                          if weak_evaluable else None),
        "actual_source_refs": {"benchmark": {"p": p_benchmark, "t": t_benchmark},
                                "industry_codes": sorted({str(i["industry_id"]) for i in industry_results}),
                                "bars_table": "forecast_index_bars",
                                "input_fingerprint": row["input_fingerprint"]},
        "evidence_snapshot": {"alias_map_keys": len(alias_map), "catalog_keys": len(catalog)},
    }
    return {"status": "EVALUATED" if market_evaluation in (OUTCOME_HIT, OUTCOME_MISS, OUTCOME_ABSTAINED)
            else "NOT_EVALUABLE", "outcome": outcome, "written": 0}


def persist_outcome(evaluation: dict[str, Any], *, research_db: Any = None) -> dict[str, Any]:
    """幂等保存正式评价（UNIQUE forecast_id + outcome_formula_version）。"""
    from src.macro_forecast.forecast_store import ForecastStore

    store = ForecastStore(research_db or (get_runtime_root() / "research.db"))
    try:
        if evaluation.get("status") not in {"EVALUATED", "NOT_EVALUABLE", "PENDING"}:
            return {"status": evaluation.get("status"), "written": 0}
        return store.save_outcome(evaluation["outcome"])
    finally:
        store.close()
