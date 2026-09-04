"""Macro Line V1: events, diff, refresh, Daily Brief integration, pool invariant."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.macro_line.events import MacroEventStore, event_to_chinese

_BANNED = ("买入", "卖出", "买点", "卖点", "建仓", "加仓", "减仓", "止盈", "止损", "下单", "目标价", "开仓", "平仓", "推荐买入")


def _snapshot(as_of: str, regime: str = "中性", *, growth=None, inflation=None, liquidity=65, credit=50, fc=65):
    return {
        "as_of": as_of, "regime": regime, "score": 50, "status": "partial",
        "axes": {"growth": growth, "inflation": inflation, "liquidity": liquidity, "credit": credit, "financial_conditions": fc},
        "states": {}, "coverage": 0.8, "series_count": 15, "series_total": 19,
    }


@pytest.fixture()
def store(tmp_path: Path) -> MacroEventStore:
    s = MacroEventStore(tmp_path / "test.db")
    # macro_snapshots is owned by ValueDataStore; create a minimal version for tests
    with s._conn:
        s._conn.executescript("""
            CREATE TABLE IF NOT EXISTS macro_snapshots (
                id TEXT PRIMARY KEY, as_of TEXT NOT NULL, formula_version TEXT,
                regime TEXT, score REAL, coverage REAL, confidence TEXT, status TEXT,
                axes_json TEXT DEFAULT '{}', states_json TEXT DEFAULT '{}',
                missing_fields_json TEXT DEFAULT '[]', sources_json TEXT DEFAULT '[]',
                provenance_key TEXT UNIQUE, created_at TEXT
            );
        """)
    return s


def test_no_change_no_event(store: MacroEventStore) -> None:
    prev = _snapshot("2026-09-02")
    cur = _snapshot("2026-09-03")
    # Seed previous snapshot
    store._conn.execute(
        "INSERT INTO macro_snapshots (id, as_of, formula_version, regime, score, coverage, confidence, status, axes_json, states_json, missing_fields_json, sources_json, provenance_key, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap1", "2026-09-02", "v2", prev["regime"], prev["score"], prev["coverage"], "MEDIUM", "partial",
         json.dumps(prev["axes"]), "{}", "[]", "[]", "pk1", "2026-09-02T10:00:00"),
    )
    store._conn.commit()
    events = store.diff_and_record(cur)
    assert events == []


def test_regime_change_creates_event(store: MacroEventStore) -> None:
    store._conn.execute(
        "INSERT INTO macro_snapshots (id, as_of, formula_version, regime, score, coverage, confidence, status, axes_json, states_json, missing_fields_json, sources_json, provenance_key, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap1", "2026-09-02", "v2", "扩张", 70, 0.8, "MEDIUM", "ready",
         json.dumps({"growth":70,"inflation":40,"liquidity":70,"credit":60,"financial_conditions":70}), "{}", "[]", "[]", "pk1", "2026-09-02T10:00:00"),
    )
    store._conn.commit()
    cur = _snapshot("2026-09-03", regime="收缩", growth=30, inflation=70, liquidity=30, credit=30, fc=30)
    events = store.diff_and_record(cur)
    types = {e["event_type"] for e in events}
    assert "MACRO_REGIME_CHANGED" in types


def test_axis_tier_change_creates_event(store: MacroEventStore) -> None:
    store._conn.execute(
        "INSERT INTO macro_snapshots (id, as_of, formula_version, regime, score, coverage, confidence, status, axes_json, states_json, missing_fields_json, sources_json, provenance_key, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap1", "2026-09-02", "v2", "中性", 50, 0.8, "MEDIUM", "partial",
         json.dumps({"growth":50,"inflation":50,"liquidity":65,"credit":50,"financial_conditions":65}), "{}", "[]", "[]", "pk1", "2026-09-02T10:00:00"),
    )
    store._conn.commit()
    cur = _snapshot("2026-09-03", growth=30, inflation=50, credit=50, fc=65)  # growth 65→30: 中性→偏冷, others unchanged
    events = store.diff_and_record(cur)
    axis_events = [e for e in events if e["event_type"] == "MACRO_AXIS_CHANGED"]
    assert any(e["axis_key"] == "growth" and e["from_value"] == "中性" and e["to_value"] == "偏冷" for e in axis_events)


def test_same_tier_score_wiggle_no_event(store: MacroEventStore) -> None:
    store._conn.execute(
        "INSERT INTO macro_snapshots (id, as_of, formula_version, regime, score, coverage, confidence, status, axes_json, states_json, missing_fields_json, sources_json, provenance_key, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap1", "2026-09-02", "v2", "中性", 50, 0.8, "MEDIUM", "partial",
         json.dumps({"growth":55,"inflation":50,"liquidity":65,"credit":50,"financial_conditions":65}), "{}", "[]", "[]", "pk1", "2026-09-02T10:00:00"),
    )
    store._conn.commit()
    cur = _snapshot("2026-09-03", growth=58, inflation=50, credit=50, fc=65)  # 55→58 both 中性, no tier cross
    events = store.diff_and_record(cur)
    assert events == []


def test_none_to_value_is_event(store: MacroEventStore) -> None:
    store._conn.execute(
        "INSERT INTO macro_snapshots (id, as_of, formula_version, regime, score, coverage, confidence, status, axes_json, states_json, missing_fields_json, sources_json, provenance_key, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap1", "2026-09-02", "v2", "数据不足", None, 0.4, "LOW", "partial",
         json.dumps({"growth":None,"inflation":None,"liquidity":67,"credit":None,"financial_conditions":68}), "{}", "[]", "[]", "pk1", "2026-09-02T10:00:00"),
    )
    store._conn.commit()
    cur = _snapshot("2026-09-03", growth=50, inflation=45)  # None → 中性
    events = store.diff_and_record(cur)
    axis_events = [e for e in events if e["event_type"] == "MACRO_AXIS_CHANGED"]
    assert any(e["axis_key"] == "growth" and e["from_value"] == "资料不足" for e in axis_events)


def test_events_are_daily_digest_not_immediate(store: MacroEventStore) -> None:
    store._conn.execute(
        "INSERT INTO macro_snapshots (id, as_of, formula_version, regime, score, coverage, confidence, status, axes_json, states_json, missing_fields_json, sources_json, provenance_key, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap1", "2026-09-02", "v2", "扩张", 70, 0.8, "MEDIUM", "ready",
         json.dumps({"growth":70,"inflation":40,"liquidity":70,"credit":60,"financial_conditions":70}), "{}", "[]", "[]", "pk1", "2026-09-02T10:00:00"),
    )
    store._conn.commit()
    events = store.diff_and_record(_snapshot("2026-09-03", regime="收缩", growth=30, inflation=70, liquidity=30, credit=30, fc=30))
    assert all(e["delivery_mode"] == "DAILY_DIGEST" for e in events)


def test_macro_events_not_in_strategy_events_table(store: MacroEventStore) -> None:
    store._conn.execute(
        "INSERT INTO macro_snapshots (id, as_of, formula_version, regime, score, coverage, confidence, status, axes_json, states_json, missing_fields_json, sources_json, provenance_key, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap1", "2026-09-02", "v2", "扩张", 70, 0.8, "MEDIUM", "ready", "{}", "{}", "[]", "[]", "pk1", "2026-09-02T10:00:00"),
    )
    store._conn.commit()
    store.diff_and_record(_snapshot("2026-09-03", regime="收缩"))
    # Verify events went to macro_events, not value_strategy_state_events
    assert store._conn.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] > 0
    store.close()


def test_event_chinese_text_no_trading_words(store: MacroEventStore) -> None:
    event = {
        "event_type": "MACRO_AXIS_CHANGED", "axis_key": "liquidity",
        "from_value": "偏暖", "to_value": "中性",
    }
    text = event_to_chinese(event)
    for banned in _BANNED:
        assert banned not in text


def test_events_for_summary_includes_same_day_open_events(store: MacroEventStore) -> None:
    store._insert("2026-09-04", "MACRO_AXIS_CHANGED", "liquidity", "偏暖", "中性")
    store._insert("2026-09-03", "MACRO_AXIS_CHANGED", "growth", "中性", "偏冷")
    with store._conn:
        store._conn.execute(
            "UPDATE macro_events SET status='CLOSED' WHERE research_as_of='2026-09-03'",
        )
        store._conn.commit()
    events = store.events_for_summary("2026-09-04")
    assert len(events) == 1
    assert events[0]["axis_key"] == "liquidity"


def test_get_macro_line_summary_uses_same_day_events(monkeypatch) -> None:
    from src.macro_line import refresh as refresh_mod

    class FakeStore:
        def events_for_summary(self, research_as_of: str):
            return [{
                "event_type": "MACRO_AXIS_CHANGED", "axis_key": "credit",
                "from_value": "中性", "to_value": "偏冷",
            }]

        def close(self) -> None:
            return None

    class FakeDataService:
        def __init__(self, store=None) -> None:
            pass

        def build_snapshot(self, as_of: str) -> dict:
            return {
                "as_of": as_of, "regime": "中性",
                "states": {"credit": "恶化"},
                "axes": {"growth": 50, "inflation": 50, "liquidity": 50, "credit": 30, "financial_conditions": 65},
            }

    monkeypatch.setattr(refresh_mod, "MacroEventStore", lambda: FakeStore())
    monkeypatch.setattr(
        "src.strategy_engines.macro_data.MacroDataService",
        FakeDataService,
    )
    monkeypatch.setattr(
        "src.strategy_engines.value_data_store.ValueDataStore",
        lambda: type("VS", (), {"close": staticmethod(lambda: None)})(),
    )
    monkeypatch.setattr(
        "src.macro_line.freshness.check_macro_source_freshness",
        lambda **kwargs: {"missing_series_labels": ["社融增量"]},
    )
    summary = refresh_mod.get_macro_line_summary("2026-09-04")
    assert summary["changed"] is True
    assert "经济和资金面没有明显方向" in summary["text"]
    assert "信用偏冷" in summary["text"]
    assert "金融条件偏暖" in summary["text"]
    assert "恶化" not in summary["text"]
    assert "今日变化" in summary["text"]
    assert any("信用" in item for item in summary["changes"])
    assert any("由「" in item and "」变为「" in item for item in summary["changes"])


def test_compose_macro_owner_text_no_change_is_still_specific() -> None:
    from src.macro_line.refresh import compose_macro_owner_text

    text = compose_macro_owner_text(
        regime="中性",
        axes={"growth": 50, "inflation": 50, "liquidity": 50, "credit": 30, "financial_conditions": 65},
        change_texts=[],
        missing_labels=["社融增量"],
    )
    assert text.startswith("经济和资金面没有明显方向（中性）")
    assert "信用偏冷" in text and "金融条件偏暖" in text
    assert "恶化" not in text and "改善" not in text
    assert "资料还缺社融增量" in text
    assert "环境无变化" in text
    assert "不据此调整研究名单" in text


def test_brief_macro_environment_includes_from_to_when_changed(monkeypatch) -> None:
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    monkeypatch.setattr(
        "src.macro_line.get_macro_line_summary",
        lambda _as_of: {
            "available": True,
            "text": "当前宏观环境：中性。流动性由「偏暖」变为「中性」。",
            "regime": "中性",
            "regime_label": "经济和资金面没有明显方向",
            "changed": True,
            "changes": ["流动性由「偏暖」变为「中性」"],
            "as_of": "2026-09-04",
        },
    )
    env = InvestmentResearchDailyBriefService._macro_environment_text("2026-09-04")
    assert env["changed"] is True
    assert env["changes"]
    assert "由「偏暖」变为「中性」" in env["changes"][0]


def test_daily_brief_macro_text_fail_soft() -> None:
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    env = InvestmentResearchDailyBriefService._macro_environment_text()
    assert isinstance(env, dict)
    assert "text" in env and "changed" in env and "changes" in env
    for banned in _BANNED:
        assert banned not in env["text"], f"禁词：{banned}"


def test_brief_payload_persists_macro_environment(tmp_path: Path) -> None:
    from src.investment_research_supervisor.daily_brief_store import InvestmentResearchDailyBriefRepository

    macro_env = {
        "available": True,
        "text": "当前宏观环境：中性。环境无变化。",
        "regime": "中性",
        "changed": False,
        "changes": [],
        "as_of": "2026-09-04",
    }
    repo = InvestmentResearchDailyBriefRepository(tmp_path / "brief.db")
    try:
        saved = repo.save_ready({
            "research_as_of": "2026-09-04",
            "low_value_active_count": 201,
            "enter_count": 0,
            "exit_count": 0,
            "priority_companies": [],
            "risk_summary": {},
            "thesis_changes": [],
            "financial_changes": [],
            "data_gaps": [],
            "brief_payload": {"text": "brief", "macro_environment": macro_env},
            "formula_version": "daily-brief-v27",
        })
        assert saved["brief_payload"]["macro_environment"]["available"] is True
        assert "环境无变化" in saved["brief_payload"]["macro_environment"]["text"]
    finally:
        repo.close()


def test_pool_active_count_unchanged_by_macro_refresh(monkeypatch) -> None:
    """SPEC §1.4: refresh must not mutate the low-value pool."""
    import src.macro_line.refresh as refresh_mod

    class FakeDataService:
        def build_snapshot(self, as_of: str) -> dict:
            return {
                "as_of": as_of, "regime": "中性", "score": 50, "status": "partial",
                "axes": {"growth": 50, "inflation": 50, "liquidity": 50, "credit": 50, "financial_conditions": 50},
                "states": {}, "coverage": 1.0, "series_count": 17, "series_total": 19,
                "confidence": "MEDIUM", "missing_fields": [], "sources": [],
                "provenance_key": f"pk-{as_of}", "created_at": "2026-09-04T00:00:00",
            }

    class FakeValueStore:
        def save_macro_snapshot(self, snapshot: dict) -> dict:
            return snapshot

        def close(self) -> None:
            return None

    class FakeEventStore:
        def diff_and_record(self, snapshot: dict) -> list:
            return []

        def close(self) -> None:
            return None

    monkeypatch.setattr(refresh_mod, "MacroEventStore", lambda: FakeEventStore())
    monkeypatch.setattr("src.strategy_engines.macro_data.MacroDataService", lambda store=None: FakeDataService())
    monkeypatch.setattr("src.strategy_engines.value_data_store.ValueDataStore", lambda: FakeValueStore())
    monkeypatch.setattr(refresh_mod, "_maybe_ingest_missing_series", lambda as_of: {"ingested": False})
    monkeypatch.setattr(
        "src.macro_line.freshness.check_macro_source_freshness",
        lambda **kwargs: {"status": "PARTIAL", "series_count": 17, "series_total": 19},
    )

    result = refresh_mod.refresh_macro_line("2026-09-04")
    assert result["status"] == "OK"
    assert result["freshness"]["status"] == "PARTIAL"


def test_check_macro_source_freshness_ready(monkeypatch) -> None:
    from src.macro_line.freshness import _EXPECTED_SERIES, check_macro_source_freshness

    class FakeStore:
        def macro_series_as_of(self, as_of: str) -> list[dict]:
            return [{"series_id": sid, "value": 1.0} for sid in _EXPECTED_SERIES]

        def get_macro_snapshot(self, as_of: str) -> dict:
            return {
                "as_of": as_of,
                "status": "partial",
                "axes": {"growth": 50, "inflation": 50, "liquidity": 50, "credit": 50, "financial_conditions": 50},
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr("src.macro_line.freshness.ValueDataStore", lambda: FakeStore())
    result = check_macro_source_freshness(as_of="2026-09-04")
    assert result["status"] == "READY"
    assert result["missing_series"] == []


def test_check_macro_source_freshness_partial_when_optional_missing(monkeypatch) -> None:
    from src.macro_line.freshness import _EXPECTED_SERIES, check_macro_source_freshness

    present = [sid for sid in _EXPECTED_SERIES if sid not in {"social_financing_increment", "usd_cny"}]

    class FakeStore:
        def macro_series_as_of(self, as_of: str) -> list[dict]:
            return [{"series_id": sid, "value": 1.0} for sid in present]

        def get_macro_snapshot(self, as_of: str) -> dict:
            return {
                "as_of": as_of,
                "status": "partial",
                "axes": {"growth": 50, "inflation": 50, "liquidity": 50, "credit": 50, "financial_conditions": 50},
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr("src.macro_line.freshness.ValueDataStore", lambda: FakeStore())
    result = check_macro_source_freshness(as_of="2026-09-04")
    assert result["status"] == "PARTIAL"
    assert "social_financing_increment" in result["missing_series"]
    assert "usd_cny" in result["missing_series"]


def test_macro_refresh_failure_is_fail_soft_for_eod(monkeypatch) -> None:
    """A7 / E4: macro refresh failure must not block EOD COMPLETED."""
    from datetime import datetime
    from unittest.mock import MagicMock

    from src.value_workspace.automation import SHANGHAI, ValueResearchScheduler

    scheduler = ValueResearchScheduler()
    pool = {
        "id": "pool-test",
        "as_of": "2026-09-03",
        "research_states": [{"lifecycle_status": "ACTIVE", "research_status": "READY"}],
    }
    fake_store = MagicMock()
    fake_store.acquire_automation_lock.return_value = True
    fake_store.pool_for_as_of.return_value = pool
    fake_store.update_automation.return_value = {}

    tdx = MagicMock()
    tdx.latest_qualified_close_snapshot.return_value = (True, "", {"market_date": "2026-09-03"})
    monkeypatch.setattr("src.value_workspace.automation.get_tdx_service", lambda: tdx)
    monkeypatch.setattr(scheduler, "_financial_ready", lambda _pool: True)
    monkeypatch.setattr(
        scheduler,
        "_ensure_low_value_daily_bars",
        lambda *_args, **_kwargs: {"missing": 0, "failed": [], "remaining": 0, "active_members": 1},
    )
    monkeypatch.setattr(scheduler, "_pool_stage_ready", lambda *_args, **_kwargs: True)

    notify_service = MagicMock()
    notify_service.prepare_activation.return_value = None
    notify_service.notify.return_value = {"status": "READY"}
    monkeypatch.setattr(
        "src.low_value_leader_notifications.get_low_value_leader_notification_service",
        lambda: notify_service,
    )
    monkeypatch.setattr(
        "src.low_value_leader_pool.get_low_value_leader_pool_service",
        lambda: MagicMock(),
    )

    risk_service = MagicMock()
    risk_service.coverage_for_active_pool.return_value = {"complete": True}
    monkeypatch.setattr(
        "src.low_value_risk_snapshot.get_low_value_pool_risk_snapshot_service",
        lambda: risk_service,
    )
    monkeypatch.setattr(
        "src.risk_research_preparation.schedule_current_low_value_preparation",
        lambda **_kwargs: False,
    )

    event_service = MagicMock()
    event_service.evaluate_universe.return_value = {"status": "COMPLETED", "created_events": 0}
    event_service.repository = MagicMock()
    monkeypatch.setattr(
        "src.value_strategy.get_value_strategy_event_service",
        lambda *_args, **_kwargs: event_service,
    )
    monkeypatch.setattr(
        "src.value_strategy.ValueStrategyEventNotificationService",
        lambda *_args, **_kwargs: MagicMock(deliver_immediate=MagicMock(return_value={"status": "DISABLED"})),
    )

    def _macro_boom(_as_of: str) -> dict:
        raise RuntimeError("macro ingest down")

    monkeypatch.setattr("src.macro_line.refresh_macro_line", _macro_boom)

    brief_service = MagicMock()
    brief_service.build.return_value = MagicMock(status="READY", reused=True)
    monkeypatch.setattr(
        "src.investment_research_supervisor.get_investment_research_daily_brief_service",
        lambda: brief_service,
    )
    monkeypatch.setattr(
        "src.investment_research_supervisor.get_daily_brief_bitable_publisher",
        lambda: MagicMock(publish=MagicMock(return_value={"status": "SKIPPED"})),
    )
    monkeypatch.setattr(
        "src.investment_research_supervisor.get_daily_brief_notification_service",
        lambda: MagicMock(notify=MagicMock(return_value={"status": "READY"})),
    )

    class _EventStore:
        def close_events(self, _as_of: str) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("src.macro_line.events.MacroEventStore", _EventStore)

    result = scheduler._run_latest_completed(
        fake_store,
        automation={"enabled": True, "max_retries": 3, "retry_minutes": 20},
        local=datetime(2026, 9, 3, 17, 0, tzinfo=SHANGHAI),
        allow_refresh_request=False,
    )

    assert result["status"] == "COMPLETED"
    assert result["stages"]["MACRO_LINE_READY"] == "FAILED"
    assert result["stages"]["DAILY_RESEARCH_BRIEF_READY"] in {"READY", "REUSED"}
    brief_service.build.assert_called_once_with(research_as_of="2026-09-03")
    completed_updates = [call.kwargs for call in fake_store.update_automation.call_args_list if call.kwargs.get("last_status") == "completed"]
    assert completed_updates
    assert "MACRO_LINE_READY=FAILED" in completed_updates[-1]["last_error"]

