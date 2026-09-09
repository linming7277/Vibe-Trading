"""Next-session outlook projection: boss-safe text, honest gaps, no leakage.

边界单测：缺资金序列写「资料不足」；缺行业指数序列不回退低估池行业或
成员股合成；老板可见文案无因子编号与交易表述；SHADOW 预测必须带
「把握低·影子」；板块只能来自 sw1_index_bars 的申万一级指数日线。
"""

from __future__ import annotations

import re

from src.investment_research_supervisor.next_session_outlook import build_next_session_outlook


def _bars(days: int = 30, *, base: float = 4600.0, drift: float = -2.0) -> list[dict]:
    dates = [f"202608{day:02d}" for day in range(10, 32)] + [f"202609{day:02d}" for day in range(1, 9)]
    return [
        {"trade_date": date, "close": base + drift * index, "amount": 5.0e11 - index * 1.0e9}
        for index, date in enumerate(dates[-days:])
    ]


def _forecast(*, status: str = "SHADOW", direction: str = "WEAKER") -> dict:
    return {
        "id": "mmf_test", "status": status, "direction": direction,
        "target_trade_date": "2026-09-09",
        "model_summary": "沪深300近1/5/20日连续下跌（M1-M3），叠加信用端偏冷（A1），短期趋势仍偏弱。",
        "invalidation_conditions": [],
    }


def _sw1_table(*, codes: int = 25, end: str = "2026-09-08", days: int = 12) -> dict[str, dict]:
    """sw1_index_bars 形状的假表：code -> {name, rows:[(ISO日期, 收盘, 成交额)]}。"""
    from datetime import date as _date, timedelta

    dates = [(_date(2026, 8, 28) + timedelta(days=i)).isoformat() for i in range(days)]
    dates[-1] = end
    table: dict[str, dict] = {}
    for index in range(codes):
        code = f"881{index:03d}.SH"
        rows = []
        for i, day in enumerate(dates):
            close = 1000.0 + index * 10 + (i if index < 2 else -i)  # 前两家上行，其余下行
            rows.append((day, close, 1.0e9 + (i * 1e7 if index < 2 else -i * 1e7)))
        table[code] = {"name": f"行业{index}", "rows": rows}
    return table


def test_shadow_forecast_marked_and_no_factor_codes() -> None:
    table = _sw1_table()
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=_bars(), forecast=_forecast(),
        breadth={"advancers": 1800, "decliners": 3400, "day": "20260908"},
        sw1_bars=table,
    )
    assert "偏弱" in outlook["text"]
    assert "把握低·影子" in outlook["text"]
    assert not re.search(r"\b[MA]\d{1,2}\b", outlook["text"])
    assert not re.search(r"买入|卖出|加仓|减仓|仓位|止盈|止损|建仓", outlook["text"])
    assert "M1" in (outlook["debug"]["raw_model_summary"] or "")
    assert outlook["sectors"]["status"] == "ready"
    assert len(outlook["sectors"]["strong"]) == 3 and len(outlook["sectors"]["weak"]) == 3
    # 行业名只能是表内的 L1 名称
    names = {entry["name"] for entry in table.values()}
    assert all(item["name"] in names for item in outlook["sectors"]["strong"] + outlook["sectors"]["weak"])
    assert "相对最强" in outlook["text"] and "最弱" in outlook["text"]


def test_missing_flow_data_degrades_to_insufficient() -> None:
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=[], forecast=None, breadth={}, sw1_bars={},
    )
    assert outlook["flow"]["status"] == "资料不足"
    assert "【资金】资料不足。" in outlook["text"]


def test_missing_sw1_never_falls_back_to_pool_industries() -> None:
    """无指数日线时，低估池行业名绝不能冒充板块答案。"""
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=_bars(), forecast=_forecast(),
        breadth={"advancers": 1800, "decliners": 3400, "day": "20260908"},
        sw1_bars={},
    )
    assert outlook["sectors"]["status"] == "资料不足"
    assert "【板块】资料不足。" in outlook["text"]
    assert "申万一级行业指数日线" in outlook["missing"]
    for pool_name in ("餐饮", "旅游酒店", "白酒"):
        assert pool_name not in outlook["text"]


def test_stale_sw1_last_date_degrades_whole_section() -> None:
    table = _sw1_table(codes=10)  # 可用 9 < 20 → 资料不足
    stale = dict(table)
    stale["881000.SH"] = {
        "name": "旧行业",
        "rows": table["881000.SH"]["rows"][:-1],  # 末根日期 != P 日
    }
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=_bars(), forecast=_forecast(),
        breadth={"advancers": 1800, "decliners": 3400, "day": "20260908"},
        sw1_bars=stale,
    )
    assert outlook["sectors"]["status"] == "资料不足"
    assert "【板块】资料不足。" in outlook["text"]


def test_missing_expected_code_degrades_whole_section() -> None:
    table = _sw1_table(codes=10)  # 可用 10 < 20 → 资料不足（真实缺文件同理压低可用数）
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=_bars(), forecast=_forecast(),
        breadth={"advancers": 1800, "decliners": 3400, "day": "20260908"},
        sw1_bars=table,
    )
    assert outlook["sectors"]["status"] == "资料不足"


def test_flow_rotation_on_split_signals() -> None:
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=_bars(), forecast=_forecast(direction="NEUTRAL"),
        breadth={"advancers": 3400, "decliners": 1800, "day": "20260908"},
        sw1_bars={},
    )
    # 涨多家数占优但成交额萎缩（_bars 的 amount 递减）→ 板块轮动
    assert outlook["flow"]["status"] == "板块轮动"


def _flow_bars(ratio: float) -> list[dict]:
    """20 根恒量 + 末根=恒量×ratio，令均量比恰为 ratio。"""
    dates = [f"202608{day:02d}" for day in range(10, 32)] + [f"202609{day:02d}" for day in range(1, 5)]
    return [{"trade_date": date, "close": 4600.0, "amount": 1e11} for date in dates[:-1]] +         [{"trade_date": dates[-1], "close": 4600.0, "amount": 1e11 * ratio}]


def _flow_status(ratio: float, breadth=None) -> str:
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=_flow_bars(ratio), forecast=_forecast(),
        breadth=breadth or {}, sw1_bars={},
    )
    return outlook["flow"]["status"]


def test_volume_only_wording_bands() -> None:
    # 只有成交额：禁写 进场/离场观望
    assert _flow_status(0.65) == "成交清淡"
    assert _flow_status(0.87) == "成交平淡，方向不明"
    assert _flow_status(1.30) == "成交活跃"
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=_flow_bars(0.87), forecast=_forecast(),
        breadth={}, sw1_bars={},
    )
    assert "离场观望" not in outlook["text"] and "进场" not in outlook["text"]


def test_breadth_plus_volume_combinations() -> None:
    # 跌多明显 + 平淡 → 离场观望
    assert _flow_status(0.87, {"advancers": 1000, "decliners": 3200, "day": "20260908"}) == "离场观望"
    # 涨多明显 + 活跃 → 资金偏进场
    assert _flow_status(1.30, {"advancers": 3200, "decliners": 1000, "day": "20260908"}) == "资金偏进场"
    # 一侧明显但另一侧不配合 → 板块轮动
    assert _flow_status(0.87, {"advancers": 3200, "decliners": 1000, "day": "20260908"}) == "板块轮动"
    assert _flow_status(1.30, {"advancers": 1000, "decliners": 3200, "day": "20260908"}) == "板块轮动"
    # 无明显一边 → 方向不明
    assert _flow_status(0.87, {"advancers": 2100, "decliners": 2000, "day": "20260908"}) == "方向不明"


def test_invalidated_line_uses_p_day_close() -> None:
    bars = _bars()
    close_p = bars[-1]["close"]
    table = _sw1_table()
    outlook = build_next_session_outlook(
        "2026-09-08", index_bars=bars, forecast=_forecast(),
        breadth={"advancers": 1800, "decliners": 3400, "day": "20260908"},
        sw1_bars=table,
    )
    assert f"作废：沪深300收盘高于P日收盘价{close_p:.2f}且成交额高于P日。" in outlook["text"]
