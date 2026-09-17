"""早盘快讯缓存：ingest 去重、窗口过滤、调度窗口、构建读表带链接。全 mock，零 LLM。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import pytest

import src.investment_research_supervisor.morning_macro_brief as mm
from src.cio_report.service import CioReportService
from src.cio_report.store import CioReportStore

SH = ZoneInfo("Asia/Shanghai")
CODE = "003012.SZ"


def _sh(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=SH)


def _ingest_rows(rows, tmp_path):
    """按 (published datetime, title, url) 批量入库，返回 ingest 结果。"""
    def fetcher(url):
        raise OSError("no network in tests")

    class _FakeEM:
        @staticmethod
        def fetch(fetcher=None, max_items=50):
            return [{"title": t, "source": "东财快讯",
                     "published": pub.astimezone(SH), "url": u}
                    for pub, t, u in rows]

    import src.investment_research_supervisor.morning_macro_brief as mm
    monkey_prev = mm.fetch_eastmoney_flash
    monkey_prev_holder = mm
    # 直接以参数注入：复用 ingest 的真实写库逻辑
    result = mm.ingest_morning_flash(
        now=rows[-1][0] if rows else None,
        research_db_path=tmp_path / "research.db",
        em_fetcher=lambda u: {"data": {"list": [
            {"title": t, "showTime": pub.strftime("%Y-%m-%d %H:%M:%S"), "url": u_}
            for pub, t, u_ in rows]}} if rows else {"data": {"list": []}},
        rss_fetcher=lambda w1, w2: [])
    return result


# ---------------------------------------------------------------------------
# 1. ingest：只入库带 url 的条；同 url 重跑 0 新增
# ---------------------------------------------------------------------------

def test_ingest_keeps_only_url_rows_and_dedups(tmp_path, monkeypatch):
    import src.investment_research_supervisor.morning_macro_brief as mm

    rows = [(_sh(2026, 9, 15, 7, 50), "带链接的头条", "http://e.com/1"),
            (_sh(2026, 9, 15, 7, 45), "带链接的其二", "http://e.com/2"),
            (_sh(2026, 9, 15, 7, 40), "没有链接的条目", "")]

    def fake_em(fetcher=None, max_items=50):
        return [{"title": t, "source": "东财快讯",
                 "published": pub, "url": u} for pub, t, u in rows]

    def fake_rss(ws, we, fetcher=None):
        return []

    monkeypatch.setattr(mm, "fetch_eastmoney_flash", fake_em)
    monkeypatch.setattr(mm, "fetch_rss_items", fake_rss)

    result = mm.ingest_morning_flash(now=_sh(2026, 9, 15, 8, 0),
                                     research_db_path=tmp_path / "research.db")
    assert result["fetched"] == 3
    assert result["inserted"] == 2      # 无 url 条不写表
    assert result["skipped"] == 1

    conn = mm._flash_conn(tmp_path / "research.db")
    count = conn.execute("SELECT COUNT(*) FROM morning_flash_items").fetchone()[0]
    assert count == 2
    conn.close()

    # 同 url 重跑 → 0 新增
    result2 = mm.ingest_morning_flash(now=_sh(2026, 9, 15, 8, 5),
                                      research_db_path=tmp_path / "research.db")
    assert result2["inserted"] == 0


# ---------------------------------------------------------------------------
# 2. 窗口：published_at ∈ (上一交易日 15:00, 当日 08:00]
# ---------------------------------------------------------------------------

def test_window_filter(tmp_path, monkeypatch):
    db = tmp_path / "research.db"
    rows = [
        (_sh(2026, 9, 14, 16, 0), "昨16:00 在窗", "http://e.com/a"),
        (_sh(2026, 9, 15, 7, 50), "今07:50 在窗", "http://e.com/b"),
        (_sh(2026, 9, 15, 10, 11), "今10:11 出窗", "http://e.com/c"),
        (_sh(2026, 9, 14, 14, 50), "昨14:50 出窗", "http://e.com/d"),
    ]
    for pub, t, u in rows:
        mm.ingest_morning_flash.__wrapped__ if False else None
        # 直接写表（绕过 ingest 的 now 语义），保持窗口断言聚焦读侧
        conn = mm._flash_conn(db)
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO morning_flash_items"
                "(source, title, url, published_at, region, raw_key, fetched_at, as_of_date)"
                " VALUES('东财快讯',?,?,?,NULL,?,?,?)",
                (t, u, pub.isoformat(), f"{t}|{u}", pub.isoformat(), pub.date().isoformat()))
        conn.close()

    items = mm._load_flash_items("2026-09-15", research_db_path=db)
    titles = [i["title"] for i in items]
    assert "昨16:00 在窗" in titles
    assert "今07:50 在窗" in titles
    assert "今10:11 出窗" not in titles
    assert "昨14:50 出窗" not in titles


# ---------------------------------------------------------------------------
# 3. 构建读表：链接行出现、无行情噪声；空窗 → 资料不足 + SKIPPED_EMPTY
# ---------------------------------------------------------------------------

def test_build_reads_cache_and_shows_links(tmp_path, monkeypatch):
    import src.investment_research_supervisor.morning_macro_brief as mm

    db = tmp_path / "research.db"
    conn = mm._flash_conn(db)
    with conn:
        for t, u in (("今07:50 在窗头条", "http://e.com/b"), ("今07:45 在窗其二", "http://e.com/a")):
            conn.execute(
                "INSERT OR IGNORE INTO morning_flash_items"
                "(source, title, url, published_at, region, raw_key, fetched_at, as_of_date)"
                " VALUES('东财快讯',?,?,?,NULL,?,?,?)",
                (t, u, "2026-09-15T07:45:00+08:00", f"{t}|{u}", "2026-09-15T08:00:00+08:00",
                 "2026-09-15"))
    conn.close()

    monkeypatch.setattr(mm, "_load_flash_items",
                        lambda as_of, **kw: [
                            {"time": "07:50", "title": "今07:50 在窗头条",
                             "url": "http://e.com/b", "source": "东财快讯"},
                            {"time": "07:45", "title": "今07:45 在窗其二",
                             "url": "http://e.com/a", "source": "东财快讯"}])
    brief = mm.build_morning_macro_brief("2026-09-15", macro_shadow=False)
    text = brief["text"]
    assert text.count("http://e.com/") == 2
    assert "纳指" not in text and "数字对照" not in text and "中间价" not in text


def test_empty_window_insufficient_and_gate_zero(tmp_path, monkeypatch):
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings, ShortLivedFeishuBriefSender)
    from src.investment_research_supervisor.daily_brief_store import (
        InvestmentResearchDailyBriefRepository)
    import src.investment_research_supervisor.morning_macro_brief as mm

    monkeypatch.setattr(mm, "_load_flash_items", lambda as_of, **kw: [])
    monkeypatch.setattr(DailyBriefNotificationSettings, "from_channels_config",
                        classmethod(lambda cls: DailyBriefNotificationSettings(
                            enabled=True, target_id="oc_t",
                            delivery_channel="hermes_feishu_supervisor")))
    monkeypatch.setattr(
        "src.investment_research_supervisor.daily_brief_notification_service.InvestmentResearchDailyBriefRepository",
        lambda path=None: InvestmentResearchDailyBriefRepository(tmp_path / "r.db"))
    calls = []

    class _Spy(ShortLivedFeishuBriefSender):
        def send_interactive_card(self, *, target_id, card):
            calls.append(target_id)
            return "omock"

    monkeypatch.setattr(ShortLivedFeishuBriefSender, "send_interactive_card",
                        _Spy.send_interactive_card)
    result = mm.send_morning_macro_brief("2026-09-15", research_db_path=tmp_path / "r.db")
    assert result["status"] == "SKIPPED_EMPTY"
    assert calls == []


# ---------------------------------------------------------------------------
# 4. 调度：窗口与 10 分钟间隔
# ---------------------------------------------------------------------------

def test_collect_window_matrix():
    assert mm.in_collect_window(_sh(2026, 9, 14, 15, 6)) is True    # 周一 15:06
    assert mm.in_collect_window(_sh(2026, 9, 14, 14, 0)) is False   # 周一 14:00
    assert mm.in_collect_window(_sh(2026, 9, 15, 7, 0)) is True     # 周二 07:00
    assert mm.in_collect_window(_sh(2026, 9, 15, 9, 0)) is False    # 周二 09:00
    assert mm.in_collect_window(_sh(2026, 9, 13, 12, 0)) is True    # 周日全天
    assert mm.in_collect_window(_sh(2026, 9, 12, 3, 0)) is True     # 周六凌晨


def test_scheduler_ten_minute_gap(tmp_path, monkeypatch):
    import src.investment_research_supervisor.morning_macro_brief as mm

    calls = []
    monkeypatch.setattr(mm, "ingest_morning_flash",
                        lambda now=None, **kw: calls.append(now) or {"fetched": 0})
    sched = mm.MorningFlashScheduler()

    t1 = _sh(2026, 9, 14, 15, 10)
    assert sched.should_collect(t1) is True
    sched.tick(now=t1)
    assert len(calls) == 1

    t2 = t1 + timedelta(minutes=5)
    assert sched.should_collect(t2) is False    # 距上次 <10 分钟
    sched.tick(now=t2)
    assert len(calls) == 1

    t3 = t1 + timedelta(minutes=11)
    assert sched.should_collect(t3) is True
    sched.tick(now=t3)
    assert len(calls) == 2


def test_outside_window_no_ingest(tmp_path, monkeypatch):
    import src.investment_research_supervisor.morning_macro_brief as mm

    calls = []
    monkeypatch.setattr(mm, "ingest_morning_flash",
                        lambda now=None, **kw: calls.append(now) or {"fetched": 0})
    sched = mm.MorningFlashScheduler()
    assert sched.should_collect(_sh(2026, 9, 14, 14, 0)) is False
    sched.tick(now=_sh(2026, 9, 14, 14, 0))
    assert calls == []


# ---------------------------------------------------------------------------
# 5. AST：收集器/构建器不引用研究名单模块
# ---------------------------------------------------------------------------

def test_flash_module_ast_no_research_stores():
    import ast

    import src.investment_research_supervisor.morning_macro_brief as mm

    source = open(mm.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in ("src.focus_selection", "src.low_value_leader_pool", "src.level3_leaders"):
        assert not any(m == banned or m.startswith(banned + ".") for m in imported), banned


from datetime import timedelta as _td  # noqa: E402


# ---------------------------------------------------------------------------
# 5. recent_flash_news：宏观总览页「最近 N 天新闻」
# ---------------------------------------------------------------------------

def _insert_flash(tmp_path, rows):
    """rows: (published_at, title, url)；直接写缓存表（模拟已入库）。"""
    conn = mm._flash_conn(tmp_path / "research.db")
    try:
        for pub, title, url in rows:
            conn.execute(
                "INSERT INTO morning_flash_items(source,title,url,published_at,raw_key,fetched_at,as_of_date)"
                " VALUES('东财快讯',?,?,?,?,'','2026-09-16')",
                (title, url, pub, f"东财快讯|{url}"))
        conn.commit()
    finally:
        conn.close()


def test_recent_news_groups_by_day_partitions_and_keeps_window(tmp_path):
    now = _sh(2026, 9, 16, 9, 0)
    _insert_flash(tmp_path, [
        ("2026-09-16 07:50", "国家统计局发布8月份经济数据", "http://e.com/1"),
        ("2026-09-16 07:40", "美联储官员释放鹰派信号", "http://e.com/2"),
        ("2026-09-15 16:10", "两市成交额突破1.5万亿元", "http://e.com/3"),
        ("2026-09-14 09:00", "欧股收盘普跌", "http://e.com/4"),
        ("2026-09-13 09:00", "窗口外的旧新闻不该出现", "http://e.com/5"),
    ])
    # 日期级（无发布时间）条目：按抓取时间归到 09-15。
    conn = mm._flash_conn(tmp_path / "research.db")
    try:
        conn.execute(
            "INSERT INTO morning_flash_items(source,title,url,published_at,raw_key,fetched_at,as_of_date)"
            " VALUES('东财快讯','日期级条目也保留','http://e.com/6','','东财快讯|http://e.com/6','2026-09-15T08:00:00','2026-09-15')")
        conn.commit()
    finally:
        conn.close()
    result = mm.recent_flash_news(days=3, research_db_path=tmp_path / "research.db", now=now)
    days = {d["date"]: d for d in result["days"]}
    assert set(days) == {"2026-09-16", "2026-09-15", "2026-09-14"}
    # 国内强制词优先；默认落国内；海外词归海外。
    assert [i["title"] for i in days["2026-09-16"]["domestic"]] == ["国家统计局发布8月份经济数据"]
    assert [i["title"] for i in days["2026-09-16"]["overseas"]] == ["美联储官员释放鹰派信号"]
    assert "两市成交额突破1.5万亿元" in [i["title"] for i in days["2026-09-15"]["domestic"]]
    # 日期级（无发布时间）条目按抓取时间归日，time 为空。
    day15 = [i for i in days["2026-09-15"]["domestic"] if i["title"] == "日期级条目也保留"]
    assert day15 and day15[0]["time"] == ""
    assert result["total"] == 5


def test_recent_news_drops_promo_calendar_and_cross_day_duplicates(tmp_path):
    now = _sh(2026, 9, 16, 9, 0)
    _insert_flash(tmp_path, [
        ("2026-09-15 10:00", "美联储宣布维持利率不变", "http://e.com/old"),
        ("2026-09-16 07:30", "600519.SH 涨停建议关注", "http://e.com/promo"),
        ("2026-09-16 07:20", "新华财经早报：9月16日", "http://e.com/calendar"),
        ("2026-09-16 07:10", "美联储宣布维持利率不变", "http://e.com/new"),
    ])
    result = mm.recent_flash_news(days=3, research_db_path=tmp_path / "research.db", now=now)
    days = {d["date"]: d for d in result["days"]}
    # 推广与纯日历占位被丢弃；同事件跨天只留最新一条。
    overseas_today = [i["title"] for i in days["2026-09-16"]["overseas"]]
    assert overseas_today == ["美联储宣布维持利率不变"]
    assert all(i["title"] != "美联储宣布维持利率不变" for i in days["2026-09-15"]["overseas"])
    assert result["total"] == 1


def test_recent_news_respects_per_group_cap(tmp_path):
    now = _sh(2026, 9, 16, 9, 0)
    _insert_flash(tmp_path, [
        (f"2026-09-16 0{i}:10", f"国内政策动向通报之{i}", f"http://e.com/{i}") for i in range(1, 5)
    ])
    result = mm.recent_flash_news(days=1, per_group_cap=2, research_db_path=tmp_path / "research.db", now=now)
    assert len(result["days"][0]["domestic"]) == 2
