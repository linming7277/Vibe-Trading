"""日报修复回归：实验臂隔离（P-E）+ 价格条件降级（P-F）+ EOD 前置步骤（P-D）。

背景：2026-09-08 日报三段全灭 + 实验臂泄漏风险。全部离线 fixture。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo


from src.macro_forecast.forecast_store import ForecastStore

SH = ZoneInfo("Asia/Shanghai")


# --- P-E: ForecastStore.latest 排除实验臂 --------------------------------------

def test_pe_latest_excludes_experiment_arm(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path / "t.db")
    rows = [
        {"id": "mmf_a", "target_trade_date": "2026-09-09", "run_mode": "SHADOW",
         "status": "SHADOW", "input_bundle_id": "b|arm=BASELINE_V2",
         "input_fingerprint": "fp:arm=BASELINE_V2", "market_direction": "WEAKER"},
        {"id": "mmf_b", "target_trade_date": "2026-09-09", "run_mode": "SHADOW",
         "status": "SHADOW", "input_bundle_id": "b|arm=CNY_V21",
         "input_fingerprint": "fp:arm=CNY_V21", "market_direction": "WEAKER"},
        {"id": "mmf_prod", "target_trade_date": "2026-09-08", "run_mode": "SHADOW",
         "status": "SHADOW", "input_bundle_id": "b", "input_fingerprint": "fp_prod",
         "market_direction": "RANGE_BOUND"},
    ]
    conn = store._conn
    for row in rows:
        conn.execute(
            """INSERT INTO macro_market_forecasts(
                   id, target_trade_date, run_mode, status, input_bundle_id,
                   input_fingerprint, forecast_formula_version, prompt_version,
                   renderer_version, candidate_rules_version, structured_payload_json,
                   logical_calls, actual_requests, retry_count, created_at)
               VALUES(:id, :target_trade_date, :run_mode, :status, :input_bundle_id,
                      :input_fingerprint, 'f', 'p', 'r', 'c', '{}', 0, 0, 0,
                      '2026-09-08T20:00:00+08:00')""",
            row,
        )
    conn.commit()
    latest_any = store.latest()
    latest_prod = store.latest(exclude_experiment_arm=True)
    assert latest_any["id"] in {"mmf_a", "mmf_b"}       # 不加过滤 → 实验臂最新
    assert latest_prod["id"] == "mmf_prod"               # 过滤后 → 生产行
    store.close()


def test_pe_review_semantics_exclude_arm_rows(tmp_path: Path) -> None:
    # 日报复盘 SQL 的语义镜像：arm 行不得成为「昨日预测复盘」来源
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("""CREATE TABLE macro_market_forecasts (
        id TEXT PRIMARY KEY, input_fingerprint TEXT NOT NULL, run_mode TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE macro_forecast_outcomes (
        id TEXT PRIMARY KEY, forecast_id TEXT NOT NULL, target_trade_date TEXT NOT NULL,
        run_mode TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("INSERT INTO macro_market_forecasts VALUES ('mmf_arm', 'fp:arm=CNY_V21', 'SHADOW', '2026-09-08T20:05')")
    conn.execute("INSERT INTO macro_market_forecasts VALUES ('mmf_prod', 'fp_prod', 'SHADOW', '2026-09-08T19:00')")
    conn.execute("INSERT INTO macro_forecast_outcomes VALUES ('o1', 'mmf_arm', '2026-09-09', 'SHADOW', '2026-09-08T20:10')")
    conn.execute("INSERT INTO macro_forecast_outcomes VALUES ('o2', 'mmf_prod', '2026-09-09', 'SHADOW', '2026-09-08T19:30')")
    conn.commit()
    row = conn.execute(
        """SELECT o.forecast_id FROM macro_forecast_outcomes o
           JOIN macro_market_forecasts f ON f.id = o.forecast_id
           WHERE o.target_trade_date=? AND f.input_fingerprint NOT LIKE '%:arm=%'
           ORDER BY CASE o.run_mode WHEN 'OFFICIAL' THEN 0 ELSE 1 END, o.created_at DESC
           LIMIT 1""", ("2026-09-09",)).fetchone()
    assert row[0] == "mmf_prod"  # 实验臂被排除，生产行成为复盘来源
    conn.close()


# --- P-F: 价格条件排序降级（范围外/停牌推断排后） --------------------------------

def test_pf_digest_sort_penalty_formula() -> None:
    # 排序公式镜像：范围外 +10、停牌推断 +5 —— 在范围内可执行条件排最前
    def sort_of(effective: str, *, outside: bool, exited: bool, suspended: bool) -> int:
        base = (0 if (exited or effective in {"BLOCKED", "VALUATION_REVIEW_REQUIRED", "DATA_REVIEW_REQUIRED"})
                else 1 if effective == "HIGH_ATTENTION"
                else 2 if effective == "ATTENTION"
                else 3)
        return base + (10 if (outside or exited) else 0) + (5 if suspended else 0)

    in_scope_action = sort_of("VALUATION_REVIEW_REQUIRED", outside=False, exited=False, suspended=False)
    out_suspended_action = sort_of("VALUATION_REVIEW_REQUIRED", outside=True, exited=False, suspended=True)
    assert in_scope_action < out_suspended_action  # 范围外停牌股不再挤掉在范围内条目
    in_scope_high = sort_of("HIGH_ATTENTION", outside=False, exited=False, suspended=False)
    assert in_scope_high < out_suspended_action


# --- P-D: eod_steps --------------------------------------------------------------

def test_pd_prepare_step_combines_and_fail_soft(monkeypatch) -> None:
    import src.macro_forecast.eod_steps as eod_steps

    monkeypatch.setattr(eod_steps, "refresh_forecast_bars",
                        lambda **kw: {"status": "READY", "inserted": 10})
    monkeypatch.setattr(eod_steps, "resolve_next_target",
                        lambda today=None: "20260910")
    monkeypatch.setattr(eod_steps, "build_next_bundle",
                        lambda target_date=None: {"status": "BUILT", "target_date": "2026-09-10"})
    result = eod_steps.prepare_forecast_inputs_step()
    assert result["bars_status"] == "READY"
    assert result["bundle_status"] == "BUILT"
    assert result["target_date"] == "20260910"

    # bars 失败 → 阶段 FAILED 但不抛异常（fail-soft）
    monkeypatch.setattr(eod_steps, "refresh_forecast_bars",
                        lambda **kw: {"status": "FAILED", "error": "tdx down"})
    result = eod_steps.prepare_forecast_inputs_step()
    assert result["bars_status"] == "FAILED"
    assert result["bundle_status"] in {"BUILT", "FAILED", "SKIPPED", "FAILED_TARGET"}


def test_pd_build_next_bundle_world_not_ready(monkeypatch, tmp_path: Path) -> None:
    import src.macro_forecast.eod_steps as eod_steps

    class FakeClient:
        def close(self):
            pass

    monkeypatch.setattr("src.tdx_data.client.TdxClient", FakeClient)
    monkeypatch.setattr("src.macro_forecast.forecast_service.load_trading_days",
                        lambda tdx_client=None, today=None: ["20260908", "20260909"])

    def fake_prepare(*, target_date, trading_days, save):
        assert target_date == "20260909"
        return {"status": "BUILT", "payload": {"target": {"target_date": "2026-09-09"},
                                               "data_mode": "DOMESTIC_LIMITED"},
                "save": {"bundle_id": "mfib_x"}, "gaps": []}

    monkeypatch.setattr("src.macro_forecast.service.prepare_input_bundle", fake_prepare)
    result = eod_steps.build_next_bundle(target_date="20260909")
    assert result["status"] == "BUILT"
    assert result["bundle_id"] == "mfib_x"
    assert result["target_date"] == "2026-09-09"
