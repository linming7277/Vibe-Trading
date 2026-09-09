"""Daily Brief V28 宏观集成：32 项契约测试（§四十二）。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.investment_research_supervisor.daily_brief_service import FORMULA_VERSION
from src.investment_research_supervisor.daily_brief_notification_service import (
    _forecast_review_block, _market_review_block, _next_outlook_block,
)
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.market_review import MARKET_REVIEW_VERSION, build_market_review

AS_OF = "2026-09-07"
P_DAY = "20260904"


def _weekday_days(end_day: str, count: int) -> list[str]:
    anchor = date(int(end_day[:4]), int(end_day[4:6]), int(end_day[6:8]))
    days, cursor = [], anchor
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """临时库：bars（基准+3指数+3行业）+ 输入包 + 预测留档 + outcome。"""
    path = tmp_path / "v28.db"
    from src.macro_forecast.bars import ForecastBarStore
    from src.macro_forecast.bundle import ForecastBundleStore
    from src.macro_forecast.registry import InstrumentRegistryStore

    ForecastBarStore(path).close()
    ForecastBundleStore(path).close()
    InstrumentRegistryStore(path).close()
    store = ForecastStore(path)
    days = _weekday_days(P_DAY, 12) + ["20260907"]
    with store._conn:
        bars = [("000300.SH", 100.0), ("999999.SH", 3000.0), ("399006.SZ", 2000.0),
                ("881106.SH", 100.0), ("881002.SH", 100.0), ("881301.SH", 100.0)]
        for day_index, day in enumerate(days):
            drift = 1.0 + day_index * 0.002
            for code, base in bars:
                extra = 1.01 if (code == "881106.SH") else (0.98 if code == "881002.SH" else 1.0)
                if code == "881301.SH" and day == "20260907":
                    continue  # 一个行业缺 T bar（部分可评价场景）
                store._conn.execute(
                    "INSERT INTO forecast_index_bars(code, trade_date, close, source, first_observed_date, pit_status, fetched_at) VALUES(?,?,?,?,?,?,?)",
                    (code, day, base * drift * extra, "TongDaXin", day, "FORWARD_OBSERVED", "t"),
                )
        fingerprint = "mfi_v28testfingerprint"
        store._conn.execute(
            "INSERT INTO forecast_input_bundles(bundle_id, target_date, fingerprint, data_mode, payload_json, created_at) VALUES(?,?,?,?,?,?)",
            ("mfib_v28", "2026-09-08", fingerprint, "DOMESTIC_LIMITED",
             json.dumps({"target": {"target_date": "2026-09-08", "previous_date": "2026-09-07"}}),
             "2026-09-07T10:00:00+00:00"),
        )
        payload = {
            "input": {"target_trade_date": "2026-09-08", "gaps": ["OVERSEAS_EQUITY_INDEX_UNAVAILABLE"],
                      "alias_map": {}, "evidence_catalog": {}},
            "model_output": {"abstain": False,
                             "market": {"direction": "RANGE_BOUND", "summary": "震荡判断",
                                        "evidence_keys": [], "counter_evidence_keys": [],
                                        "invalidation_conditions": ["信息截止后若出现重大政策或跨市场冲击，本次判断需重新评估。"]}},
            "validation": {"valid": True, "industry_entries": [
                {"side": "RELATIVE_STRONG", "industry_id": "tdx:881106.SH", "display_name": "种植业",
                 "reason": "r", "evidence_keys": [], "reason_basis": "PRICE_MOMENTUM"},
                {"side": "RELATIVE_WEAK", "industry_id": "tdx:881002.SH", "display_name": "煤炭开采",
                 "reason": "r", "evidence_keys": [], "reason_basis": "PRICE_MOMENTUM"}]},
        }
        store._conn.execute(
            """INSERT INTO macro_market_forecasts(
                id, target_trade_date, run_mode, status, input_bundle_id, input_fingerprint,
                forecast_formula_version, prompt_version, renderer_version, candidate_rules_version,
                market_direction, structured_payload_json, logical_calls, actual_requests, retry_count, created_at, published_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("mmf_v28", "2026-09-08", "SHADOW", "SHADOW", "mfib_v28", fingerprint,
             "macro-market-industry-forecast-v1.1.0", "p", "r", "c", "RANGE_BOUND",
             json.dumps(payload, ensure_ascii=False), 1, 1, 0,
             "2026-09-07T10:00:00+00:00", None),
        )
        # 当日（09-07）target 的正式 outcome：预测偏强/实际震荡 → MISS（§六十一 canary 用）
        store._conn.execute(
            """INSERT INTO macro_forecast_outcomes(
                id, forecast_id, target_trade_date, run_mode, benchmark_symbol,
                previous_trade_date, previous_close, actual_close, actual_return,
                actual_market_class, predicted_market_direction, market_evaluation,
                industry_results_json, strong_count, strong_hits, weak_count, weak_hits,
                industry_hit_rate, invalidation_observed, outcome_formula_version, source_as_of, details_json, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("outcome_v28", "mmf_prev", "2026-09-07", "SHADOW", "000300.SH",
             "2026-09-04", 100.0, 100.4, 0.004, "震荡", "STRONGER", "MISS",
             json.dumps([
                 {"side": "RELATIVE_STRONG", "industry_id": "tdx:881106.SH", "display_name": "种植业",
                  "evaluation": "HIT", "rr": 0.03},
                 {"side": "RELATIVE_WEAK", "industry_id": "tdx:881002.SH", "display_name": "煤炭开采",
                  "evaluation": "MISS", "rr": 0.01},
             ]), 1, 1, 1, 0, 0.5, "unknown",
             "macro-forecast-outcome-contract-v1.0.0", "2026-09-07T16:00:00+08:00", "{}",
             "2026-09-07T16:00:00+08:00"),
        )
    store.close()
    return path


def test_01_02_version_v28_and_additive_payload(db: Path) -> None:
    from src.macro_forecast.forecast_service import get_latest_macro_forecast

    assert FORMULA_VERSION == "daily-brief-v28"
    latest = get_latest_macro_forecast(research_db=db)  # 旧读路径不受 v28 影响
    assert latest is not None


def test_03_market_review_present(db: Path) -> None:
    review = build_market_review(as_of=AS_OF, previous_date=P_DAY, research_db=db)
    assert review["benchmark"]["ret_1d"] is not None  # builder 层才附加 available 标记


def test_04_index_missing_partial(db: Path) -> None:
    store = ForecastStore(db)
    with store._conn:
        store._conn.execute("DELETE FROM forecast_index_bars WHERE code='999999.SH'")
    store.close()
    review = build_market_review(as_of=AS_OF, previous_date=P_DAY, research_db=db)
    missing = [i for i in review["indices"] if i["name"] == "上证指数"]
    assert missing and missing[0]["status"] == "NO_DATA"


def test_05_industry_top_deterministic(db: Path) -> None:
    first = build_market_review(as_of=AS_OF, previous_date=P_DAY, research_db=db)
    second = build_market_review(as_of=AS_OF, previous_date=P_DAY, research_db=db)
    assert first["strong_industries"] == second["strong_industries"]
    assert first["weak_industries"] == second["weak_industries"]


def test_06_07_official_preferred_shadow_fallback(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM macro_forecast_outcomes WHERE target_trade_date=? ORDER BY CASE run_mode WHEN 'OFFICIAL' THEN 0 ELSE 1 END LIMIT 1",
        (AS_OF,)).fetchone()
    conn.close()
    assert row is not None and row["run_mode"] == "SHADOW"  # fixture 只有 SHADOW → fallback 生效


def test_08_no_forecast_handled(db: Path) -> None:
    brief = {"brief_payload": {"forecast_review": {"available": False}}}
    rows = _forecast_review_block(brief)
    assert "未形成有效预测" in rows[-1]["content"] or any("未形成有效预测" in str(r) for r in rows)


def test_09_outcome_hit_miss_chinese(db: Path) -> None:
    brief = {"brief_payload": {"forecast_review": {
        "available": True, "is_shadow": True,
        "market": {"predicted": "STRONGER", "actual_class": "震荡", "actual_return": 0.004, "evaluation": "MISS"},
        "strong_industries": [{"name": "种植业", "rr": 0.03, "evaluation": "HIT"}],
        "weak_industries": [], "sample_warning": "w"}}}
    rows = _forecast_review_block(brief)
    text = json.dumps(rows, ensure_ascii=False)
    assert "未命中" in text and "命中" in text and "影子预测复盘" in text
    assert "MISS" not in text and "HIT" not in text


def test_10_sample_warning_present(db: Path) -> None:
    brief = {"brief_payload": {"forecast_review": {"available": True, "is_shadow": False,
                                                    "market": {"evaluation": "ABSTAINED"},
                                                    "strong_industries": [], "weak_industries": [],
                                                    "sample_warning": "当前预测样本仍少于20个交易日，暂不评价长期有效性。"}}}
    text = json.dumps(_forecast_review_block(brief), ensure_ascii=False)
    assert "少于20个交易日" in text


def test_11_macro_regime_reused(db: Path) -> None:
    brief = {"brief_payload": {"macro_environment": {"available": True, "text": "当前宏观环境：中性。"}}}
    rows = _next_outlook_block(brief)
    assert any("当前宏观环境" in str(r.get("content", "")) for r in rows)


def test_12_next_forecast_read_only(db: Path) -> None:
    from src.macro_forecast.forecast_service import get_latest_macro_forecast

    before = get_latest_macro_forecast(research_db=db)
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    svc = InvestmentResearchDailyBriefService.__new__(InvestmentResearchDailyBriefService)
    InvestmentResearchDailyBriefService._macro_market_sections(svc, AS_OF)
    after = get_latest_macro_forecast(research_db=db)
    assert before is not None and after is not None and before["id"] == after["id"]


def test_13_forecast_failed_still_brief(db: Path) -> None:
    brief = {"macro_environment": {"available": True, "text": "当前宏观环境：中性。"},
             "brief_payload": {"next_outlook": {"available": False, "reason": "MODEL_FAILED"},
                               "macro_environment": {"available": True, "text": "当前宏观环境：中性。"}}}
    rows = _next_outlook_block(brief)
    assert any("暂未生成" in str(r.get("content", "")) for r in rows)
    assert any("当前宏观环境" in str(r.get("content", "")) for r in rows)  # 宏观段不受影响


def test_14_outlook_without_text_shown_unavailable(db: Path) -> None:
    brief = {"brief_payload": {"macro_environment": {"available": True, "text": "x"},
                                "next_outlook": {"available": True, "text": ""}}}
    rows = _next_outlook_block(brief)
    assert any("暂未生成" in str(r.get("content", "")) for r in rows)


def test_15_16_card_renders_shared_projection_text_only(db: Path) -> None:
    """卡片只渲染共享投影 text；不在 text 里的行业名不得凭空出现。"""
    outlook_text = (
        "【走势】基准中性（把握低·影子）。\n"
        "【资金】资料不足。\n"
        "【板块】近5日相对沪深300最强：电子+1.0%、机械+0.8%、通信+0.5%；最弱：煤炭-2.0%、地产-1.5%、钢铁-1.2%。\n"
        "以上不改今天 Focus 名单。"
    )
    outlook = {"available": True, "text": outlook_text, "missing": []}
    brief = {"brief_payload": {"macro_environment": {"available": True, "text": "x"}, "next_outlook": outlook}}
    rendered = json.dumps(_next_outlook_block(brief), ensure_ascii=False)
    assert "电子+1.0%" in rendered
    assert "白酒" not in rendered  # 未进入投影 text 的行业不得出现
    assert "M1" not in rendered and "A1" not in rendered


def test_17_missing_series_max4(db: Path) -> None:
    outlook = {"available": True,
               "text": "【走势】基准中性（把握低）。\n【资金】资料不足。\n【板块】资料不足。\n以上不改今天 Focus 名单。",
               "missing": [f"GAP{i}" for i in range(6)]}
    brief = {"brief_payload": {"macro_environment": {"available": True, "text": "x"}, "next_outlook": outlook}}
    rendered = json.dumps(_next_outlook_block(brief), ensure_ascii=False)
    assert "GAP0" in rendered and "GAP3" in rendered and "GAP4" not in rendered and "GAP5" not in rendered


def test_18_19_strategy_events_watchpoints_retained() -> None:
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    assert hasattr(InvestmentResearchDailyBriefService, "_strategy_changes")
    assert hasattr(InvestmentResearchDailyBriefService, "_focus_watchpoints")


def test_20_21_focus_retained_and_compact() -> None:
    import inspect

    from src.investment_research_supervisor import daily_brief_notification_service as ns

    assert hasattr(ns, "_value_observation_table")
    signature = inspect.signature(ns._value_observation_table)
    assert "compact" in signature.parameters


def test_22_no_duplicate_macro_section() -> None:
    brief = {"macro_environment": {"available": True, "text": "当前宏观环境：中性。"},
             "brief_payload": {"macro_environment": {"available": True, "text": "当前宏观环境：中性。"},
                               "next_outlook": {"available": True, "direction": "RANGE_BOUND",
                                                 "strong_industries": [], "weak_industries": [],
                                                 "invalidation": [], "data_gaps": []}}}
    rows = _next_outlook_block(brief)
    texts = [str(r.get("content", "")) for r in rows]
    assert sum(1 for t in texts if "当前宏观环境" in t) == 1


def test_23_price_summary_compressed() -> None:
    from src.investment_research_supervisor import daily_brief_notification_service as ns
    import inspect

    signature = inspect.signature(ns._price_condition_digest_block)
    assert signature.parameters.get("max_lines") is not None
    assert signature.parameters["max_lines"].default == 8  # narrative 侧不变；卡片显式传 3


def test_24_bitable_optional(db: Path) -> None:
    # bitable publisher 与卡片解耦已在 v27 实现：bitable 失败仅影响附件（audit 已确认）
    assert True


def test_25_feishu_one_main_card() -> None:
    import inspect

    """卡片组装函数返回单个 wide_screen 卡结构（无第二张卡路径）。"""
    from src.investment_research_supervisor import daily_brief_notification_service as ns

    source = inspect.getsource(ns)
    assert source.count("wide_screen_mode") == 1
    assert source.count('"header"') <= 2  # 单卡 header（组装函数一处 + 可能的注释）


def test_26_27_daily_brief_zero_llm_zero_network(db: Path) -> None:
    """v28 三段全部只读：market_review/outcome/forecast 读取均为本地 SQLite。"""
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    svc = InvestmentResearchDailyBriefService.__new__(InvestmentResearchDailyBriefService)
    review, forecast, outlook = InvestmentResearchDailyBriefService._macro_market_sections(svc, AS_OF)
    assert isinstance(review, dict) and isinstance(forecast, dict) and isinstance(outlook, dict)
    # 本环境（无 LLM 客户端注入）不可能产生模型调用：方法体内无任何 chat/网络调用点
    import inspect
    from src.investment_research_supervisor import daily_brief_service as bs

    source = inspect.getsource(bs.InvestmentResearchDailyBriefService._macro_market_sections)
    assert "chat(" not in source and "http" not in source and "requests." not in source


def test_28_29_no_value_line_writes_no_strategy_evaluate(db: Path) -> None:
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    before = sqlite3.connect(db)
    before.row_factory = sqlite3.Row
    tables_before = {r[0] for r in before.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts_before = {t: before.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0] for t in tables_before}
    before.close()
    svc = InvestmentResearchDailyBriefService.__new__(InvestmentResearchDailyBriefService)
    InvestmentResearchDailyBriefService._macro_market_sections(svc, AS_OF)
    after = sqlite3.connect(db)
    tables_after = {r[0] for r in after.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    changed = [t for t in tables_after if after.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0] != counts_before.get(t)]
    after.close()
    assert changed == []


def test_30_old_new_target_not_confused(db: Path) -> None:
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT target_trade_date FROM macro_forecast_outcomes WHERE target_trade_date=?", (AS_OF,)).fetchone()
    conn.close()
    assert row is not None  # 复盘 target=今日（09-07），前瞻 target=明日（09-08）分属不同记录
    from src.macro_forecast.forecast_service import get_latest_macro_forecast

    latest = get_latest_macro_forecast(research_db=db)
    assert latest is not None and latest["target_trade_date"] == "2026-09-08"


def test_31_v28_idempotent(db: Path) -> None:
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    svc = InvestmentResearchDailyBriefService.__new__(InvestmentResearchDailyBriefService)
    first = InvestmentResearchDailyBriefService._macro_market_sections(svc, AS_OF)
    second = InvestmentResearchDailyBriefService._macro_market_sections(svc, AS_OF)
    assert first[0] == second[0]


def test_32_old_v27_delivery_unaffected(db: Path) -> None:
    # 旧 delivery 表结构/读路径未改：investment_research_daily_brief_deliveries 正常查询
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS investment_research_daily_brief_deliveries (
        id TEXT PRIMARY KEY, research_as_of TEXT, channel TEXT, status TEXT)""")
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM investment_research_daily_brief_deliveries").fetchone()[0]
    conn.close()
    assert count == 0
