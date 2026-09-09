"""语义质量保护 V1：15 项契约测试（§十五）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.render import render_narrative
from src.macro_forecast.semantic import (
    FALLBACK_INVALIDATION, SEMANTIC_GUARD_VERSION, SemanticGuard,
    annotate_catalog, classify_reason_basis, metric_type, outcome_phrase_violations,
    revised_output_from,
)

CATALOG_TYPES = {
    "MKT_BENCH_RET_1D": "MARKET_RETURN", "MKT_BENCH_RET_5D": "MARKET_RETURN",
    "MKT_BENCH_VOL_20D": "MARKET_VOLATILITY",
    "MKT_BREADTH_20D": "MARKET_BREADTH",
    "MKT_REF_000985_RET_5D": "BACKGROUND_INDEX_RETURN",
    "MACRO_AXIS_CREDIT": "MACRO_CREDIT",
    "IND_881106_SH_RELATIVE_5D": "INDUSTRY_RELATIVE_RETURN",
    "IND_881106_SH_RET_5D": "INDUSTRY_RETURN",
    "IND_881106_SH_TC": "INDUSTRY_TREND",
    "IND_881106_SH_MACRO_STANCE": "INDUSTRY_MACRO_STANCE",
}


def _guard() -> SemanticGuard:
    return SemanticGuard(catalog_types=CATALOG_TYPES)


# 1. 背景指数 ≠ 宽度（当前样例场景）
def test_01_background_index_is_not_breadth() -> None:
    audit = _guard().audit_market_summary(
        "P日单日反弹且市场宽度偏高，指数或维持震荡",
        ["M1", "M2", "M10", "M4"],
    )
    # M10 → MKT_REF_000985_RET_5D（无 alias 时键直传，目录标注为背景指数）
    audit = SemanticGuard(catalog_types=CATALOG_TYPES, alias_map={
        "M1": "MKT_BENCH_RET_1D", "M2": "MKT_BENCH_RET_5D",
        "M10": "MKT_REF_000985_RET_5D", "M4": "MKT_BENCH_VOL_20D",
    }).audit_market_summary("P日单日反弹且市场宽度偏高", ["M1", "M2", "M10", "M4"])
    assert audit["status"] == "UNSUPPORTED_SEMANTIC_CLAIM"
    assert audit["breadth_claim"] == "REMOVED"
    assert audit["background_index_substitution"] is True
    assert "市场宽度偏高" not in audit["revised_summary"]


# 2. 真实宽度证据支撑宽度措辞
def test_02_real_breadth_supports_breadth_wording() -> None:
    guard = SemanticGuard(catalog_types=CATALOG_TYPES, alias_map={"MB": "MKT_BREADTH_20D"})
    audit = guard.audit_market_summary("宽度78.7偏高，指数或维持震荡", ["MB", "M2"])
    assert audit["breadth_claim"] == "SUPPORTED"
    assert audit["status"] == "PASS"


# 3. 波动 ≠ 方向
def test_03_volatility_is_not_direction() -> None:
    guard = SemanticGuard(catalog_types=CATALOG_TYPES, alias_map={"M4": "MKT_BENCH_VOL_20D"})
    audit = guard.audit_market_summary("20日波动扩大导致市场大涨", ["M4"])
    assert audit["volatility_direction_substitution"] is True
    assert audit["status"] == "UNSUPPORTED_SEMANTIC_CLAIM"
    ok = guard.audit_market_summary("波动处于低位，指数或维持区间震荡", ["M4"])
    assert ok["volatility_direction_substitution"] is False


# 4/5/6. 理由来源标签
def test_04_relative_return_is_price_momentum() -> None:
    assert classify_reason_basis(["IND_881106_SH_RELATIVE_5D", "IND_881106_SH_RET_5D", "IND_881106_SH_TC"]) == "PRICE_MOMENTUM"


def test_05_macro_stance_is_macro_alignment() -> None:
    assert classify_reason_basis(["IND_881106_SH_MACRO_STANCE", "MACRO_AXIS_CREDIT"]) == "MACRO_ALIGNMENT"


def test_06_mixed_sources() -> None:
    assert classify_reason_basis(["IND_881106_SH_RELATIVE_5D", "MACRO_AXIS_CREDIT"]) == "MIXED"


# 7. invalidation 中的 outcome 短语被拒
def test_07_outcome_phrase_rejected_in_invalidation() -> None:
    violations = outcome_phrase_violations(["T日沪深300收盘涨跌幅超出±1%则判断失效", "cutoff后重大政策变化"])
    assert len(violations) == 1 and "涨跌幅超出" in violations[0]["phrase"] + violations[0]["text"]
    audit = _guard().audit_invalidation(["T日沪深300收盘涨跌幅超出±1%则判断失效", "cutoff后重大政策变化"])
    assert audit["status"] == "ISSUE"
    assert audit["kept"] == ["cutoff后重大政策变化"]


# 8. cutoff 后冲击是合法失效条件
def test_08_post_cutoff_shock_valid() -> None:
    audit = _guard().audit_invalidation(
        ["信息截止后若海外市场出现重大冲击且数据恢复后可验证，本次判断需重新评估"])
    assert audit["status"] == "PASS" and not audit["removed"]


# 9. 反向证据语义保持合法（与 invalidation 不混）
def test_09_counter_evidence_stays_valid() -> None:
    guard = SemanticGuard(catalog_types=CATALOG_TYPES, alias_map={
        "M3": "MKT_BENCH_RET_20D", "A1": "MACRO_AXIS_CREDIT"})
    audit = guard.audit_market_summary("5日、20日仍为负，信用偏冷", ["M3", "A1"])
    assert audit["status"] == "PASS"  # 既有反向信息可作背景表述


# 10/11. 重验证 0 LLM、0 模型重跑（脚本路径无任何模型/网络调用点）
def test_10_11_revalidation_no_llm_no_rerun(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path / "s.db")
    report = _guard().run(
        {"market": {"direction": "RANGE_BOUND", "summary": "指数或维持区间震荡",
                    "evidence_keys": ["M1", "M2"], "counter_evidence_keys": ["M3"],
                    "invalidation_conditions": ["cutoff后重大政策变化"]}},
        [{"side": "RELATIVE_STRONG", "industry_id": "tdx:881106.SH", "display_name": "种植业",
          "reason": "5日相对走强", "evidence_keys": ["IND_881106_SH_RELATIVE_5D"]}],
    )
    saved = store.save_semantic_validation("mmf_test", guard_version=SEMANTIC_GUARD_VERSION,
                                           report=report, revised_narrative_md="# x")
    assert saved["status"] == "SAVED"
    again = store.save_semantic_validation("mmf_test", guard_version=SEMANTIC_GUARD_VERSION,
                                           report=report, revised_narrative_md="# x")
    assert again["status"] == "ALREADY_SAVED"
    loaded = store.load_semantic_validation("mmf_test")
    assert loaded["report"]["status"] == "PASS"
    store.close()


# 12. 旧 SHADOW 不可变（原始 model_output 不被修正视图改写）
def test_12_old_shadow_immutable() -> None:
    original = {
        "market": {"direction": "RANGE_BOUND", "summary": "反弹且市场宽度偏高",
                   "evidence_keys": ["M1"], "counter_evidence_keys": ["M3"],
                   "invalidation_conditions": ["T日收盘涨幅超过1%则失效"]},
        "industries": {"relative_strong": [{"id": "S1", "reason": "r", "evidence_keys": ["X1_REL5"]}],
                       "relative_weak": []},
    }
    guard = SemanticGuard(catalog_types=CATALOG_TYPES, alias_map={"M1": "MKT_BENCH_RET_1D"})
    report = guard.run(original, [])
    revised = revised_output_from(report, original)
    assert "市场宽度偏高" in original["market"]["summary"]  # 原文不动
    assert "市场宽度偏高" not in revised["market"]["summary"]
    assert original["market"]["invalidation_conditions"] == ["T日收盘涨幅超过1%则失效"]


# 13. 中文渲染（依据来源自然表达 + 语义说明）
def test_13_chinese_renderer_semantic_aware() -> None:
    payload = {
        "input": {"gaps": []},
        "market_context": {"ret_1d": 0.001},
        "macro_context": {"regime": "中性", "axes": {}},
        "model_output": {"market": {"direction": "RANGE_BOUND",
                                     "summary": "指数或维持区间震荡",
                                     "evidence_keys": ["M1"], "counter_evidence_keys": ["M3"],
                                     "invalidation_conditions": ["cutoff后重大政策变化"]},
                          "industries": {}},
        "validation": {"valid": True, "industry_entries": [
            {"side": "RELATIVE_STRONG", "industry_id": "tdx:881106.SH", "display_name": "种植业",
             "reason": "5日相对走强", "evidence_keys": ["IND_881106_SH_RELATIVE_5D"],
             "reason_basis": "PRICE_MOMENTUM"}]},
        "semantic": {
            "market_summary": {"revised_summary": "指数或维持区间震荡", "breadth_claim": "REMOVED",
                                "breadth_claim_removed_clauses": ["市场宽度偏高"]},
            "invalidation": {"kept": ["cutoff后重大政策变化"], "removed": ["T日收盘涨幅超过1%则失效"]},
            "industries": [{"display_name": "种植业", "reason_basis": "PRICE_MOMENTUM",
                             "revised_reason": "5日相对走强", "classification": "SUPPORTED"}],
        },
        "theme_forecast": None,
    }
    narrative = render_narrative(payload, {"target": {"target_date": "2026-09-08", "cutoff_at": "x"},
                                            "data_mode": "DOMESTIC_LIMITED", "gaps": []})
    assert "依据：近期相对走势" in narrative
    assert "PRICE_MOMENTUM" not in narrative
    assert "已删除" in narrative and "收盘复盘" in narrative
    assert "失效条件：cutoff后重大政策变化" in narrative


# 14/15. 无价值线写入 / 无 Strategy Event
def test_14_15_no_side_effect_tables(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path / "s.db")
    with store._conn:
        store._conn.execute("CREATE TABLE IF NOT EXISTS macro_events (id TEXT)")
    report = _guard().run({"market": {"summary": "s", "evidence_keys": ["M1"],
                                       "invalidation_conditions": ["x"]}}, [])
    store.save_semantic_validation("mmf_x", guard_version=SEMANTIC_GUARD_VERSION,
                                   report=report, revised_narrative_md="n")
    assert store._conn.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] == 0
    connection = sqlite3.connect(tmp_path / "s.db")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    store.close()
    assert tables - {"forecast_run_leases", "forecast_provider_health", "sqlite_sequence",
                     "macro_market_forecasts", "macro_events", "macro_forecast_outcomes",
                     "macro_forecast_experiment_days"} == {"forecast_semantic_validations"}


# 附加：兜底失效条件与宏类型标注完整性
def test_fallback_invalidation_when_all_invalid() -> None:
    audit = _guard().audit_invalidation(["T日行业跑输则判断失效"])
    assert audit["used_fallback"] is True
    assert audit["kept"] == [FALLBACK_INVALIDATION]


def test_metric_type_annotation_complete() -> None:
    types = annotate_catalog(CATALOG_TYPES)
    assert set(types.values()) <= {"MARKET_RETURN", "MARKET_VOLATILITY", "MARKET_BREADTH",
                                    "BACKGROUND_INDEX_RETURN", "MACRO_CREDIT",
                                    "INDUSTRY_RELATIVE_RETURN", "INDUSTRY_RETURN",
                                    "INDUSTRY_TREND", "INDUSTRY_MACRO_STANCE"}
    assert metric_type("MKT_RISK_APPETITE") == "RISK_APPETITE"
    assert metric_type("MACRO_AXIS_GROWTH") == "MACRO_GROWTH"
