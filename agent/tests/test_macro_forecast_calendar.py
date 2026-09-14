"""宏观预测交易日历测试：日历只到已实现日时，按惯例投影「下一交易日」。

全部纯函数/monkeypatch，不打 TDX 接口、不触库、不调模型。
"""

from __future__ import annotations

import src.tdx_data.client as tdx_client_module
from src.macro_forecast.contracts import (
    project_future_trading_days,
    projected_next_trading_day,
)
from src.macro_forecast.eod_steps import resolve_next_target

# 2026-09-11 是周五；09-12/13 周末；下一交易日 09-14（周一）。
CAL_THRU_0911 = [f"202609{d:02d}" for d in range(1, 12)]


def _patch_calendar(monkeypatch, days):
    import src.macro_forecast.forecast_service as fs

    monkeypatch.setattr(fs, "load_trading_days", lambda **kwargs: list(days))

    class _FakeClient:
        def call(self, *a, **kw):
            return []

        def close(self):
            return None

    monkeypatch.setattr(tdx_client_module, "TdxClient", _FakeClient)


def test_projector_skips_weekend_from_friday():
    out = project_future_trading_days(CAL_THRU_0911, after="20260911")
    assert out[0] == "20260914"
    assert "20260912" not in out and "20260913" not in out
    # 10 个自然日窗口（09-12..09-21）内的交易日
    assert out == ["20260914", "20260915", "20260916", "20260917", "20260918", "20260921"]


def test_projector_never_returns_anchor_or_earlier():
    out = project_future_trading_days(CAL_THRU_0911, after="20260911")
    assert all(d > "20260911" for d in out)
    assert "20260911" not in out


def test_projector_respects_holiday_table():
    out = project_future_trading_days(CAL_THRU_0911, after="20260911",
                                      holidays={"20260914"})
    assert out[0] == "20260915"


def test_projected_next_prefers_confirmed_calendar():
    # 日历本身含未来日 → 直接用日历，不投影
    days = CAL_THRU_0911 + ["20260914"]
    assert projected_next_trading_day("20260911", days) == "20260914"


def test_projected_next_falls_back_when_calendar_ends_today():
    assert projected_next_trading_day("20260911", CAL_THRU_0911) == "20260914"


def test_resolve_next_target_mock_calendar_ends_20260911(monkeypatch):
    _patch_calendar(monkeypatch, CAL_THRU_0911)
    assert resolve_next_target(today="20260911") == "20260914"


def test_resolve_next_target_friday_skips_weekend(monkeypatch):
    _patch_calendar(monkeypatch, CAL_THRU_0911)
    got = resolve_next_target(today="20260911")
    assert got not in {"20260911", "20260912", "20260913"}


def test_resolve_next_target_calendar_with_future_uses_calendar(monkeypatch):
    _patch_calendar(monkeypatch, CAL_THRU_0911 + ["20260915", "20260916"])
    # 日历已含未来日（15/16，无 14）→ 严格取日历内下一日，不投影出 14
    assert resolve_next_target(today="20260911") == "20260915"
