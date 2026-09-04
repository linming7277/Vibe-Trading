"""Macro→sector projection V1 (read-only, no new data sources)."""

from __future__ import annotations

from unittest.mock import patch

from src.value_strategy.macro_sector_projection import get_macro_sector_projection


_BANNED = ("买入", "卖出", "买点", "卖点", "建仓", "加仓", "减仓", "止盈", "止损", "下单", "目标价", "开仓", "平仓", "建议买入")


def test_projection_returns_stable_fields_with_live_data() -> None:
    result = get_macro_sector_projection()
    assert result["formula_version"].startswith("macro-sector-projection-v")
    assert "available" in result
    assert "macro" in result or result["available"] is False


def test_projection_axes_labels_are_chinese() -> None:
    result = get_macro_sector_projection()
    if not result.get("available"):
        return  # 无宏观快照时跳过（环境限制）
    labels = {axis["label"] for axis in result["macro"]["axes"]}
    assert "经济增长" in labels and "流动性" in labels
    for axis in result["macro"]["axes"]:
        assert axis["state"] in {"偏暖", "偏冷", "中性", "资料不足"}


def test_projection_no_trading_language_in_visible_strings() -> None:
    import json

    result = get_macro_sector_projection()
    text = json.dumps(result, ensure_ascii=False)
    for term in _BANNED:
        assert term not in text, f"禁词：{term}"


def test_projection_macro_unavailable_returns_reason() -> None:
    # Patch build_snapshot (primary) and macro (fallback) both to fail.
    with patch("src.strategy_engines.macro_data.MacroDataService") as mock_data_svc, \
         patch("src.strategy_engines.value_line.ValueLineService") as mock_svc:
        mock_data_svc.side_effect = RuntimeError("no data")
        mock_svc.return_value.macro.return_value = None
        result = get_macro_sector_projection()
    assert result["available"] is False
    assert result["reason"]


def test_projection_includes_data_quality(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.macro_line.freshness.check_macro_source_freshness",
        lambda **kwargs: {"status": "PARTIAL", "reason": "缺 2 条序列", "missing_series_labels": ["社融增量", "美元/人民币"]},
    )
    result = get_macro_sector_projection()
    if not result.get("available"):
        return
    assert result["data_quality"]["status"] == "PARTIAL"


def test_daily_brief_macro_environment_text_is_fail_soft() -> None:
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    # 正常路径：宏观可读或不可读都不抛异常
    text = InvestmentResearchDailyBriefService._macro_environment_text()
    assert isinstance(text, dict)
    assert "text" in text
    assert isinstance(text["text"], str)
    for term in _BANNED:
        assert term not in text["text"], f"禁词：{term}"


def test_daily_brief_card_renders_macro_environment_before_price_conditions() -> None:
    from src.investment_research_supervisor.daily_brief_notification_service import build_daily_brief_card

    brief = {
        "brief_payload": {
            "research_as_of": "2026-09-03",
            "executive_watchlist": [],
            "low_value_leader_bitable_url": "",
        },
        "macro_environment": {"available": True, "text": "当前宏观环境：中性（经济和资金面没有明显方向）。流动性 偏暖。"},
        "price_condition_digest": {"as_of": "2026-09-03", "empty": True, "lines": [], "omitted_count": 0, "disclaimer": "x"},
    }
    card = build_daily_brief_card(brief)
    contents = [el.get("content") or "" for el in card["elements"] if el.get("tag") == "markdown"]
    macro_idx = next((i for i, t in enumerate(contents) if "当前研究环境" in t), None)
    price_idx = next((i for i, t in enumerate(contents) if "今日价格条件" in t), None)
    assert macro_idx is not None, "宏观环境块必须在卡片中"
    assert price_idx is not None, "价格条件块必须在卡片中"
    assert macro_idx < price_idx, "宏观环境在价格条件之前"


def test_daily_brief_card_omits_macro_block_when_unavailable() -> None:
    from src.investment_research_supervisor.daily_brief_notification_service import build_daily_brief_card

    brief = {
        "brief_payload": {"research_as_of": "2026-09-03", "executive_watchlist": [], "low_value_leader_bitable_url": ""},
        "macro_environment": {"available": False, "text": "宏观环境资料暂不可用。"},
    }
    card = build_daily_brief_card(brief)
    contents = [el.get("content") or "" for el in card["elements"] if el.get("tag") == "markdown"]
    assert not any("当前研究环境" in t for t in contents), "宏观不可用时整块省略"
