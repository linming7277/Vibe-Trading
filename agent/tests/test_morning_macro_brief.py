"""早盘宏观速览测试：构建/文案/调度隔离。全假数据，不连飞书，不打外网。"""

from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo

from src.investment_research_supervisor.morning_macro_brief import (
    build_morning_macro_brief,
    render_feishu_card,
)

SH = ZoneInfo("Asia/Shanghai")
FACTOR_RE = re.compile(r"\b[MA]\d{1,2}\b")
TRADING_WORDS = ("买入", "卖出", "加仓", "减仓", "追涨", "打板")


def _idx(name, symbol, close, pct):
    prev = close / (1 + pct)
    return {"name": name, "symbol": symbol, "date": "2026-09-09",
            "close": round(prev * (1 + pct), 2), "change_pct": round(pct, 4)}


def test_overseas_text_has_percent():
    brief = build_morning_macro_brief(
        "2026-09-10",
        overseas=[_idx("标普", "^GSPC", 6392.0, 0.003), _idx("纳指", "^IXIC", 21000.0, -0.002)],
        news=[], cny_mid=6.78)
    assert "%" in brief["text"]
    assert "人民币中间价 6.7800" in brief["text"]


def test_overseas_empty():
    brief = build_morning_macro_brief("2026-09-10", overseas=[], news=[], cny_mid=None)
    assert "资料不足" in brief["text"]


def test_zero_news_omits_section():
    brief = build_morning_macro_brief("2026-09-10", overseas=[], news=[], cny_mid=6.78)
    assert "【国内要闻】" not in brief["text"]


def test_no_trading_words_no_factor_codes():
    brief = build_morning_macro_brief(
        "2026-09-10",
        overseas=[_idx("标普", "^GSPC", 6392.0, 0.001), _idx("纳指", "^IXIC", 21000.0, 0.0)],
        news=[{"title": "某政策出台", "source": "政策·MIIT", "date": "2026-09-09"}],
        cny_mid=6.78)
    for word in TRADING_WORDS:
        assert word not in brief["text"], word
    assert not FACTOR_RE.search(brief["text"])


def test_env_sentiment():
    warm = build_morning_macro_brief(
        "2026-09-10",
        overseas=[_idx("标普", "^GSPC", 100, 0.006), _idx("纳指", "^IXIC", 100, 0.004)],
        news=[], cny_mid=6.78)
    assert "偏暖" in warm["text"]


def test_import_purity():
    import subprocess, sys, os
    code = (
        "import sys; import src.investment_research_supervisor.morning_macro_brief;"
        "bad=[m for m in sys.modules if m.startswith(('src.focus_selection',"
        "'src.low_value_leader_pool','src.level3_leaders'))];"
        "print('C=' + ','.join(bad))"
    )
    root = os.getcwd()
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=root), timeout=120, cwd=root)
    assert r.returncode == 0, r.stderr[-500:]


def test_gate_default_off(monkeypatch):
    import src.investment_research_supervisor.morning_macro_brief as mm
    monkeypatch.delenv("HZ_MORNING_MACRO", raising=False)
    mm._scheduler = None
    mm.start_morning_macro_scheduler()
    s = mm._scheduler
    assert s is not None and (s._thread is None or not s._thread.is_alive())
    mm._scheduler = None


def test_gate_env_on_starts_thread(monkeypatch):
    import src.investment_research_supervisor.morning_macro_brief as mm
    monkeypatch.setenv("HZ_MORNING_MACRO", "on")
    mm._scheduler = None
    mm.start_morning_macro_scheduler()
    s = mm._scheduler
    assert s is not None and s._thread and s._thread.is_alive()
    s.stop()
    mm._scheduler = None


def test_scheduler_weekend_skipped():
    from datetime import datetime as dt
    from src.investment_research_supervisor.morning_macro_brief import MorningMacroScheduler
    SH = ZoneInfo("Asia/Shanghai")
    s = MorningMacroScheduler()
    sat = dt(2026, 9, 12, 8, 0, tzinfo=SH)
    assert s.tick(sat, trading_day_check=lambda d: True)["status"] == "SKIPPED_WEEKEND"


def test_scheduler_not_trading_day():
    from datetime import datetime as dt
    from src.investment_research_supervisor.morning_macro_brief import MorningMacroScheduler
    SH = ZoneInfo("Asia/Shanghai")
    s = MorningMacroScheduler()
    r = s.tick(dt(2026, 9, 10, 8, 0, tzinfo=SH), trading_day_check=lambda d: False)
    assert r["status"] == "SKIPPED_NOT_TRADING_DAY"


def test_scheduler_sends_once(tmp_path):
    from datetime import datetime as dt
    from src.investment_research_supervisor.morning_macro_brief import MorningMacroScheduler
    SH = ZoneInfo("Asia/Shanghai")
    s = MorningMacroScheduler()
    sent = []
    s.tick(dt(2026, 9, 10, 8, 0, tzinfo=SH), trading_day_check=lambda d: True,
           sender=lambda d: sent.append({"date": d, "status": "SENT"}))
    assert len(sent) == 1
    s.tick(dt(2026, 9, 10, 9, 0, tzinfo=SH), trading_day_check=lambda d: True,
           sender=lambda d: sent.append({"date": d, "status": "SENT"}))
    assert len(sent) == 1  # 重跑同日 → ALREADY_HANDLED
