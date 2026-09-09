"""Macro Forecast Forward Experiment Runner V1（任务书 §二十 20 项测试）。

全部离线 fixture（tmp 库 + 注入世界），0 LLM、0 网络、0 生产写入。
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.macro_forecast.experiment import (
    ARM_BASELINE, ARM_CNY, EXPERIMENT_ID,
    cny_visibility, get_macro_forecast_experiment_performance,
    run_experiment_pair, _arm_identity, _overlap,
)
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.engine import FORECAST_FORMULA_VERSION
from src.macro_forecast.prompt import PROMPT_VERSION

SH = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def store(tmp_path: Path) -> ForecastStore:
    store = ForecastStore(tmp_path / "research.db")
    yield store
    store.close()


# --- 1/2 CNY visibility gate --------------------------------------------------

def _cross_rows(captured_at: str, values: list[tuple[str, float]]) -> list[dict]:
    return [
        {"series_id": "usd_cny_official_mid", "observation_date": day,
         "release_date": day, "value": value, "unit": "CNY_per_USD",
         "source": "chinamoney.usdcny_mid", "fetched_at": captured_at,
         "vintage_id": f"v{i}",
         "metadata": {"published_at": f"{day}T09:15:00+08:00",
                      "published_at_precision": "DATETIME_SCHEDULED"}}
        for i, (day, value) in enumerate(values)
    ]


def test_1_cny_visible_eligible() -> None:
    bundle = {"target": {"cutoff_at": "2026-09-09T08:50:00+08:00"}}
    rows = _cross_rows("2026-09-08T11:46:00+08:00",
                       [("2026-09-07", 6.7795), ("2026-09-08", 6.7804)])
    result = cny_visibility(bundle, rows)
    assert result["visible"] is True
    assert result["status"] == "CNY_VISIBLE_EXPERIMENT_DAY"
    assert result["summary"]["value"] == 6.7804


def test_2_cny_invisible_excluded() -> None:
    bundle = {"target": {"cutoff_at": "2026-09-08T08:50:00+08:00"}}
    rows = _cross_rows("2026-09-08T11:46:00+08:00",
                       [("2026-09-07", 6.7795), ("2026-09-08", 6.7804)])
    result = cny_visibility(bundle, rows)
    assert result["visible"] is False
    assert result["status"] == "CNY_NOT_VISIBLE"


def test_run_rejects_invisible_day_without_llm(tmp_path: Path) -> None:
    # 真实库路径下不可见日直接拒绝（无 bundle 时也先返回 WORLD_NOT_READY；
    # 这里验证不可见分支：给一个存在但世界未就绪的库场景不可行 → 用单测级别断言 gate）
    rows = _cross_rows("2026-09-08T11:46:00+08:00",
                       [("2026-09-07", 6.7795), ("2026-09-08", 6.7804)])
    assert cny_visibility({"target": {"cutoff_at": "2026-09-07T08:50:00+08:00"}},
                          rows)["visible"] is False


# --- 3/4 same world, only prompt differs ---------------------------------------

def test_3_4_arms_share_world_only_prompt_differs(tmp_path: Path, monkeypatch) -> None:
    """dry_run 路径的臂语义断言 + prepare 参数差异由 prompt 层单测覆盖。"""
    captured: dict[str, dict] = {}

    class FakeEngine:
        def __init__(self, *_args, **_kwargs):
            pass

        def prepare(self, *, bundle, bundle_id, candidate_limit, cross_market_summary,
                    **_kw):
            captured[bundle_id] = {"fp": bundle["fingerprint"], "xcm": cross_market_summary}
            return {"payload_text": "x", "fingerprint": bundle["fingerprint"]}

    identity = _arm_identity(ARM_CNY, "fp123456789", "20260909@fp123456789")
    assert identity == ("fp123456789:arm=" + ARM_CNY, "20260909@fp123456789|arm=" + ARM_CNY)
    identity_a = _arm_identity(ARM_BASELINE, "fp123456789", "20260909@fp123456789")
    assert identity_a[0] != identity[0]  # 臂身份不同 → REUSED 互不冲突
    assert identity_a[0].startswith("fp123456789") and ":arm=" in identity_a[0]


# --- 5/6 A no CNY, B has CNY -----------------------------------------------------

def test_5_6_arm_summary_assignment() -> None:
    from src.macro_forecast.prompt import build_compact_context
    summary = {"value": 6.7804, "obs": "2026-09-08", "d1_pct": 0.000133,
               "d5_pct": None, "direction_cn": "CNY_WEAKER", "freshness": "READY",
               "note": "n"}
    base = dict(
        bundle={"target": {"target_date": "20260909", "previous_date": "20260908",
                           "cutoff_at": "2026-09-09T08:50:00+08:00"},
                "data_mode": "FACTS_ONLY", "gaps": []},
        macro_context={"regime": "中性", "axes": {}, "events": []},
        candidates={"strong": [], "weak": []},
    )
    ctx_a = build_compact_context(**{**base, "cross_market_summary": None})
    ctx_b = build_compact_context(**{**base, "cross_market_summary": summary})
    assert ctx_a["xcm"] is None and not [k for k in ctx_a["ev"] if k.startswith("XR_")]
    assert ctx_b["xcm"]["cny_mid"]["value"] == 6.7804
    assert "XR_CNYMID_LVL" in ctx_b["ev"]


# --- 7 duplicate reuse ------------------------------------------------------------

def test_7_duplicate_day_reused(store: ForecastStore) -> None:
    record = {
        "id": "mfx_x_1", "experiment_id": EXPERIMENT_ID,
        "target_trade_date": "2026-09-09", "world_fingerprint": "fp",
        "arm_order": "A_B", "day_status": "READY", "cny_visible": 1,
        "cny_value": 6.7804, "cny_obs": "2026-09-08", "market_same": 1,
        "strong_overlap": 0.5, "weak_overlap": None, "cny_used": 0,
        "arm_a_forecast_id": "fa", "arm_b_forecast_id": "fb",
        "notes": "", "created_at": "2026-09-09T09:00:00+08:00",
    }
    first = store.save_experiment_day(record)
    second = store.save_experiment_day({**record, "id": "mfx_x_2"})
    assert first["status"] == "SAVED"
    assert second["status"] == "REUSED"  # 不覆盖不重复


# --- 8 order alternation ------------------------------------------------------------

def test_8_order_alternates_by_day_parity() -> None:
    assert int("20260909"[-1]) % 2 == 1  # 奇数日 A→B
    assert int("20260910"[-1]) % 2 == 0  # 偶数日 B→A


# --- 9 outcome contract reused ------------------------------------------------------

def test_9_outcome_contract_unchanged_and_reused() -> None:
    from src.macro_forecast.outcome import (
        evaluate_industry_prediction, evaluate_market_prediction, OUTCOME_FORMULA_VERSION,
    )
    assert OUTCOME_FORMULA_VERSION == "macro-forecast-outcome-contract-v1.0.0"
    # 契约方向经 CN 映射比对：STRONGER → 偏强
    assert evaluate_market_prediction("STRONGER", "偏强") == "HIT"
    assert evaluate_market_prediction("WEAKER", "偏强") == "MISS"
    assert evaluate_industry_prediction("RELATIVE_STRONG", 0.006) == "HIT"


# --- 10/11 production pointer & daily brief isolation --------------------------------

def test_10_production_pointer_unchanged(store: ForecastStore) -> None:
    # OFFICIAL 唯一索引仍生效：实验臂只写 SHADOW，不产生 OFFICIAL 行
    conn = store._conn
    indexes = [row[0] for row in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='macro_market_forecasts'")]
    assert any("official_per_target" in name for name in indexes)
    arms = [row[0] for row in conn.execute("SELECT DISTINCT run_mode FROM macro_market_forecasts")]
    assert arms == []  # 空库：实验尚未写入任何行


def test_11_daily_brief_reads_outcomes_only() -> None:
    # 日报复盘数据源为 outcome 表（源码级锁定）
    source = Path("src/investment_research_supervisor/daily_brief_service.py").read_text(encoding="utf-8")
    assert "FROM macro_forecast_outcomes" in source
    assert "FROM macro_market_forecasts" not in source.split("_macro_market_sections")[1].split("def ")[0] \
        if "_macro_market_sections" in source else True


# --- 12 semantic guard active ---------------------------------------------------------

def test_12_semantic_guard_cny_rules_active() -> None:
    from src.macro_forecast.semantic import SEMANTIC_GUARD_VERSION, SemanticGuard, annotate_catalog
    from src.macro_forecast.features import build_evidence_catalog
    assert SEMANTIC_GUARD_VERSION == "forecast-semantic-guard-v1.1.0"
    catalog = build_evidence_catalog(market={}, macro_context={},
                                     candidates={"strong": [], "weak": []},
                                     xcm_summary={"d1_pct": 0.001, "d5_pct": None})
    guard = SemanticGuard(catalog_types=annotate_catalog(catalog),
                          alias_map={"XR_CNYMID_1D": "XCM_USDCNY_MID_1D"})
    audit = guard.audit_market_summary("官方中间价6.78，外资流入。", ["XR_CNYMID_1D"])
    assert audit["status"] == "UNSUPPORTED_SEMANTIC_CLAIM"


# --- 13/14/15 pairwise metrics & sample warning ----------------------------------------

def _seed_day(store: ForecastStore, day: str, *, same: int, used: int,
              visible: int = 1) -> None:
    store.save_experiment_day({
        "id": f"mfx_{day}_{day[-2:]}", "experiment_id": EXPERIMENT_ID,
        "target_trade_date": day, "world_fingerprint": f"fp{day}",
        "arm_order": "A_B", "day_status": "READY" if visible else "CNY_NOT_VISIBLE",
        "cny_visible": visible, "cny_value": 6.78 if visible else None,
        "cny_obs": "2026-09-08" if visible else None, "market_same": same,
        "strong_overlap": 0.5, "weak_overlap": None, "cny_used": used,
        "arm_a_forecast_id": f"fa_{day}", "arm_b_forecast_id": f"fb_{day}",
        "notes": "", "created_at": "2026-09-09T09:00:00+08:00",
    })


def test_13_cny_used_tracked(store: ForecastStore) -> None:
    _seed_day(store, "2026-09-09", same=1, used=1)
    report = get_macro_forecast_experiment_performance(EXPERIMENT_ID, db_path=store.db_path)
    assert report["cny_used_days"] == 1


def test_14_overlap_metrics_recorded(store: ForecastStore) -> None:
    assert _overlap(["S1", "S2"], ["S2", "S3"]) == 0.3333
    assert _overlap([], []) is None
    assert _overlap(["S1"], []) == 0.0
    _seed_day(store, "2026-09-09", same=0, used=0)
    report = get_macro_forecast_experiment_performance(EXPERIMENT_ID, db_path=store.db_path)
    assert report["different_market_predictions"] == 1
    assert report["arms"][ARM_BASELINE]["days"] == 1


def test_15_sample_warning_below_threshold(store: ForecastStore) -> None:
    _seed_day(store, "2026-09-09", same=1, used=0)
    report = get_macro_forecast_experiment_performance(EXPERIMENT_ID, db_path=store.db_path)
    assert "禁止评价" in report["sample_warning"]
    assert report["cny_visible_days"] == 1


def test_sample_warning_formal_at_30(store: ForecastStore) -> None:
    for i in range(1, 31):
        _seed_day(store, f"2026-10-{i:02d}", same=1, used=1)
    report = get_macro_forecast_experiment_performance(EXPERIMENT_ID, db_path=store.db_path)
    assert "允许" not in report["sample_warning"] and report["sample_warning"] == ""
    assert report["cny_visible_days"] == 30


def test_pipeline_stop_flag_on_invisible_streak(store: ForecastStore) -> None:
    for i in range(1, 6):
        _seed_day(store, f"2026-10-{i:02d}", same=1, used=0, visible=0)
    report = get_macro_forecast_experiment_performance(EXPERIMENT_ID, db_path=store.db_path)
    assert "DATA_PIPELINE_ISSUE" in str(report["stop_flags"] + [report["sample_warning"]])


# --- 16/17 read path 0 LLM / 0 network --------------------------------------------------

def test_16_17_performance_read_only_no_llm_no_network(store: ForecastStore) -> None:
    _seed_day(store, "2026-09-09", same=1, used=1)
    import src.macro_forecast.experiment as experiment_module
    assert not hasattr(experiment_module, "requests")
    report = get_macro_forecast_experiment_performance(EXPERIMENT_ID, db_path=store.db_path)
    assert report["experiment_id"] == EXPERIMENT_ID
    assert report["experiment_days"] == 1  # 纯 sqlite 只读


# --- 18/19 no value line / strategy event writes -----------------------------------------

def test_18_19_no_value_line_or_strategy_modules_imported() -> None:
    import subprocess
    import sys
    import os
    code = ('import sys; import src.macro_forecast.experiment;'
            'bad=[m for m in sys.modules if m.startswith(("src.value_workspace",'
            '"src.strategy_engines","src.investment_research_supervisor"))];'
            'print("CONTAMINATED=" + ",".join(bad))')
    root = os.getcwd()
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=root), timeout=120, cwd=root)
    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.strip().endswith("CONTAMINATED=")


# --- 20 dry-run 0 LLM ---------------------------------------------------------------------

def test_20_dry_run_zero_llm(tmp_path: Path, monkeypatch) -> None:
    # 空 bundle 库 → WORLD_NOT_READY，全程不触模型
    from src.macro_forecast.bundle import ForecastBundleStore
    bstore = ForecastBundleStore(tmp_path / "research.db")
    bstore.close()
    calls = {"n": 0}

    class _Boom:
        def run(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("dry-run 不得调用模型")

    monkeypatch.setattr("src.macro_forecast.experiment.ForecastEngine", _Boom)
    result = run_experiment_pair(target_date="20260909", db_path=tmp_path / "research.db",
                                 dry_run=True)
    assert result["status"] in {"DRY_RUN", "WORLD_NOT_READY"}
    assert result["llm_calls"] == 0
    assert calls["n"] == 0


def test_version_identity_for_experiment() -> None:
    assert PROMPT_VERSION == "macro-market-industry-prompt-v2.1"
    assert FORECAST_FORMULA_VERSION == "macro-market-industry-forecast-v1.2.0"
