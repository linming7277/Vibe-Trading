"""Macro overview endpoint composition (projection + series trends + forecast)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.value_strategy.macro_overview import get_macro_overview

_BANNED = ("买入", "卖出", "建仓", "加仓", "减仓", "止损", "下单", "目标价", "建议")


def _fake_projection() -> dict:
    return {
        "as_of": "2026-09-15", "available": True,
        "macro": {"regime": "中性", "regime_label": "环境中性", "score": 52.1, "coverage": 1.0, "axes": []},
        "data_quality": {"status": "READY", "reason": "ok"},
        "policy_sectors": [], "l3_research_sectors": [],
    }


def _fake_rows() -> list[dict]:
    return [
        {"series_id": "us_treasury_10y", "observation_date": "2026-09-14", "value": 4.05, "fetched_at": "2026-09-15T07:35:00"},
        {"series_id": "us_treasury_10y", "observation_date": "2026-09-11", "value": 3.98, "fetched_at": "2026-09-12T07:35:00"},
        {"series_id": "shibor_overnight", "observation_date": "2026-09-14", "value": 1.52, "fetched_at": "2026-09-15T07:35:00"},
    ]


def _patch_store(rows: list[dict]):
    store_cls = MagicMock()
    store_cls.return_value.read_rows.return_value = rows
    return patch("src.macro_data.store.CrossMarketStore", store_cls), store_cls


def test_overview_composes_projection_series_and_trend() -> None:
    store_patch, store_cls = _patch_store(_fake_rows())
    with patch("src.value_strategy.macro_sector_projection.get_macro_sector_projection", _fake_projection), \
         store_patch, store_cls, \
         patch("src.value_strategy.macro_overview._snapshot_axes_trend", lambda _db: {"credit": {"score": 34.1, "state": "恶化", "prev": {"score": 36.0, "state": "中性"}}}), \
         patch("src.value_strategy.macro_overview._latest_forecast", lambda _db: {"direction": "RANGE_BOUND", "direction_cn": "震荡", "narrative_md": "# 前瞻", "run_mode": "SHADOW", "status": "SHADOW", "target_trade_date": "2026-09-16", "published_at": None, "created_at": "2026-09-15"}):
        result = get_macro_overview()

    assert result["projection"]["available"] is True
    by_id = {item["series_id"]: item for item in result["series"]}
    # 跨市场序列：页面中文名 + 单位 + 较前值变化 + 迷你走势。
    treasury = by_id["us_treasury_10y"]
    assert treasury["label"] == "美债10年期收益率"
    assert treasury["unit"] == "percent"
    assert treasury["value"] == 4.05
    assert treasury["prev_value"] == 3.98
    assert treasury["change"] == 0.07
    assert len(treasury["sparkline"]) == 2
    assert treasury["status"] == "READY"
    # shibor 不在 macro_data 新鲜度规则表：兜底规则，不得让接口失败。
    assert by_id["shibor_overnight"]["status"] in {"READY", "STALE"}
    # 未落库序列必须显式 MISSING，不得编造。
    assert by_id["wti_spot"]["status"] == "MISSING" and by_id["wti_spot"]["value"] is None
    # 环境轴前值透传 + 前瞻透传。
    assert result["axes_trend"]["credit"]["prev"]["score"] == 36.0
    assert result["forecast"]["direction_cn"] == "震荡"
    assert result["forecast"]["run_mode"] == "SHADOW"
    # 国内读数组存在且覆盖既有序列。
    assert {item["series_id"] for item in result["domestic_series"]} >= {"cpi_yoy", "lpr_1y", "a_share_breadth_20d"}
    # 读侧只读：store 用完必须关闭。
    assert store_cls.return_value.close.called


def test_overview_without_forecast_keeps_none() -> None:
    store_patch, _ = _patch_store([])
    with patch("src.value_strategy.macro_sector_projection.get_macro_sector_projection", _fake_projection), \
         store_patch, \
         patch("src.value_strategy.macro_overview._snapshot_axes_trend", lambda _db: {}), \
         patch("src.value_strategy.macro_overview._latest_forecast", lambda _db: None):
        result = get_macro_overview()
    assert result["forecast"] is None
    assert result["axes_trend"] == {}


def test_overview_stale_series_reports_lag(monkeypatch) -> None:
    import src.value_strategy.macro_overview as module

    monkeypatch.setattr(module, "OVERVIEW_SERIES", ("wti_spot",))
    store_patch, store_cls = _patch_store([
        {"series_id": "wti_spot", "observation_date": "2026-08-15", "value": 63.1, "fetched_at": "2026-08-20T14:31:00"},
    ])
    with patch("src.value_strategy.macro_sector_projection.get_macro_sector_projection", _fake_projection), \
         store_patch, \
         patch("src.value_strategy.macro_overview._snapshot_axes_trend", lambda _db: {}), \
         patch("src.value_strategy.macro_overview._latest_forecast", lambda _db: None):
        result = module.get_macro_overview()

    entry = result["series"][0]
    assert entry["label"] == "WTI 原油现货"
    assert entry["status"] == "STALE"
    assert entry["lag_days"] is not None and entry["lag_days"] > 0
    assert store_cls.return_value.close.called


def test_overview_carries_no_trading_language() -> None:
    import json

    store_patch, _ = _patch_store([])
    with patch("src.value_strategy.macro_sector_projection.get_macro_sector_projection", _fake_projection), \
         store_patch, \
         patch("src.value_strategy.macro_overview._snapshot_axes_trend", lambda _db: {}), \
         patch("src.value_strategy.macro_overview._latest_forecast", lambda _db: None):
        text = json.dumps(get_macro_overview(), ensure_ascii=False)
    for term in _BANNED:
        assert term not in text, f"禁词：{term}"


def test_overview_uses_frozen_registry_labels_for_registered_series() -> None:
    import src.value_strategy.macro_overview as module
    from src.macro_forecast.registry import MACRO_SERIES_CATALOG

    catalog = {str(item["series_id"]): item for item in MACRO_SERIES_CATALOG}
    overridden = set(module._SERIES_LABELS)
    store_patch, _ = _patch_store([])
    with patch("src.value_strategy.macro_sector_projection.get_macro_sector_projection", _fake_projection), \
         store_patch, \
         patch("src.value_strategy.macro_overview._snapshot_axes_trend", lambda _db: {}), \
         patch("src.value_strategy.macro_overview._latest_forecast", lambda _db: None):
        result = get_macro_overview()
    by_id = {item["series_id"]: item for item in [*result["domestic_series"], *result["series"]]}
    # 注册表 name_zh 是标签单一来源；仅页面覆盖表（老板可读性）可以优先于它。
    for series_id, meta in catalog.items():
        if series_id in by_id and series_id not in overridden:
            assert by_id[series_id]["label"] == meta["name_zh"]
    assert by_id["shibor_overnight"]["label"].startswith("银行间隔夜拆借利率")
