"""早盘宏观速览测试：构建/要点判断/新闻管线/调度隔离。全假数据，不连飞书，不打外网。"""

from __future__ import annotations

import re
from datetime import datetime as dt
from zoneinfo import ZoneInfo

import src.investment_research_supervisor.morning_macro_brief as mm
import src.tdx_data.store as tdx_store_module
from src.investment_research_supervisor.morning_macro_brief import (
    build_morning_macro_brief,
    compress_title,
    draft_morning_takeaway,
)

SH = ZoneInfo("Asia/Shanghai")
FACTOR_RE = re.compile(r"\b[MA]\d{1,2}\b")
TRADING_WORDS = ("买入", "卖出", "加仓", "减仓", "追涨", "打板")


def _idx(name, symbol, close, pct):
    prev = close / (1 + pct)
    return {"name": name, "symbol": symbol, "date": "2026-09-10",
            "close": round(prev * (1 + pct), 2), "change_pct": round(pct, 4)}


def _news(title, time="07:00", url=""):
    item = {"time": time, "source": "东财快讯", "title": title}
    if url:
        item["url"] = url
    return item


# ---------------------------------------------------------------------------
# 构建与版式
# ---------------------------------------------------------------------------

def test_link_line_present_with_url():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("欧央行上调利率25个基点",
                    url="http://finance.eastmoney.com/a/20260911123.html")],
        cny_mid=6.78)
    assert "链接: http://finance.eastmoney.com/a/20260911123.html" in brief["text"]


def test_no_link_line_without_url():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("央行开展逆回购操作"), _news("某政策出台", time="06:00")],
        cny_mid=6.78)
    assert "链接:" not in brief["text"]


def test_hawk_keyword_gives_cold_stance():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("欧央行上调利率25个基点")],
        cny_mid=6.78)
    assert "【判断】偏冷" in brief["text"]
    assert "要闻出现「上调利率」" in brief["text"]
    # 要点能对应到加息
    bullet_lines = [ln for ln in brief["text"].splitlines() if ln.startswith("· ")]
    assert any("上调利率" in ln for ln in bullet_lines)


def test_dove_keyword_gives_warm_stance():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, 0.004), _idx("恒生", "^HSI", 25000, 0.008)],
        news=[_news("央行下调利率10个基点并扩大刺激")],
        cny_mid=6.78)
    assert "【判断】偏暖" in brief["text"]


def test_pending_hawk_is_neutral_not_cold():
    """「加息悬念待揭晓」事件未发生 → 中性，不得出现偏冷。"""
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("美联储加息悬念待揭晓")],
        cny_mid=6.78)
    judge = next(ln for ln in brief["text"].splitlines() if ln.startswith("【判断】"))
    assert "中性" in judge and "偏冷" not in judge


def test_pending_expectation_is_neutral():
    """「加息预期升温」同样是未落地 → 中性。"""
    out = draft_morning_takeaway([_news("加息预期升温")], [])
    assert out["stance"] == "中性"


def test_landed_rate_hike_still_cold():
    out = draft_morning_takeaway([_news("欧洲央行宣布上调利率25个基点")], [])
    assert out["stance"] == "偏冷"


def test_landed_rate_cut_still_warm():
    out = draft_morning_takeaway([_news("美联储宣布降息25个基点")], [])
    assert out["stance"] == "偏暖"


def test_pending_side_not_counted_landed_side_still_counts():
    """同一批要闻：未落地的「加息悬念」不计，已落地的「上调利率」仍判偏冷。"""
    out = draft_morning_takeaway(
        [_news("美联储加息悬念待揭晓"), _news("欧央行宣布上调利率25个基点")], [])
    assert out["stance"] == "偏冷"
    assert "上调利率" in out["reason"]


def test_empty_news_insufficient_and_no_numbers_section():
    brief = build_morning_macro_brief("2026-09-11", overseas=[], news=[], cny_mid=None)
    assert "【隔夜要闻】资料不足。" in brief["text"]
    assert "【判断】资料不足（把握低）" in brief["text"]
    assert "【要点】" not in brief["text"]
    assert "【数字对照】" not in brief["text"]


def test_shadow_adds_low_confidence():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, 0.001), _idx("恒生", "^HSI", 25000, 0.001)],
        news=[_news("某政策出台")],
        cny_mid=6.78, macro_shadow=True)
    assert "【判断】中性（把握低）" in brief["text"]


def test_neutral_reason_cites_index_counts():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006),
                  _idx("标普", "^GSPC", 6300, 0.002),
                  _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("某政策出台")],
        cny_mid=6.78)
    assert "【判断】中性" in brief["text"]
    assert "1 涨 2 跌" in brief["text"]


def test_no_trading_words_no_factor_codes():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("标普", "^GSPC", 6392.0, 0.001), _idx("纳指", "^IXIC", 21000.0, 0.0)],
        news=[{"date": "2026-09-10", "source": "政策·MIIT", "title": "某政策出台"}],
        cny_mid=6.78)
    for word in TRADING_WORDS:
        assert word not in brief["text"], word
    assert not FACTOR_RE.search(brief["text"])


def test_no_line_trimming_all_links_render():
    """不做 16 行裁剪：6 条要闻全带 url → 6 行链接全保留。"""
    overseas = [_idx("纳指", "^IXIC", 21000, -0.006),
                _idx("纳指100", "^NDX", 29000, -0.003),
                _idx("恒生", "^HSI", 25000, -0.01)]
    news = [_news(f"国内政策消息第{i}条出台", time=f"0{8 - i}:1{i}", url=f"http://e.com/a{i}")
            for i in range(1, 4)]
    news += [_news(f"海外市场消息第{i}条发布", time=f"0{6 - i}:0{i}", url=f"http://e.com/b{i}")
             for i in range(1, 4)]
    brief = build_morning_macro_brief("2026-09-11", overseas=overseas, news=news, cny_mid=6.78)
    text_lines = brief["text"].splitlines()
    assert sum(1 for ln in text_lines if ln.startswith("链接: http")) == 6
    assert "【要点】" in brief["text"] and "【判断】" in brief["text"]


def test_text_cap_10_titles_from_12_candidates():
    """12 条候选（国内 7 + 海外 5）→ text 里共 10 个标题行，国内 5、海外 5。"""
    def em_fetcher(url):
        items = [{"title": f"国内政策消息第{i}条出台", "showTime": f"2026-09-10 07:{i:02d}:00",
                  "url": ""} for i in range(7)]
        items += [{"title": f"美联储海外动态第{i}号", "showTime": f"2026-09-10 06:{i:02d}:00",
                   "url": ""} for i in range(5)]
        return _em_payload(items)

    now = dt(2026, 9, 10, 8, 0, tzinfo=SH)
    news = resolve_news("2026-09-10", research_db_path="/nonexistent.db",
                        em_fetcher=em_fetcher, rss_fetcher=lambda u: None, now=now)
    assert len(news) == 10
    brief = build_morning_macro_brief("2026-09-10", news=news, cny_mid=6.78)
    title_lines = [ln for ln in brief["text"].splitlines() if re.match(r"^\d{2}:\d{2} ", ln)]
    assert len(title_lines) == 10
    assert sum(1 for ln in title_lines if "国内政策消息" in ln) == 5
    assert sum(1 for ln in title_lines if "美联储海外动态" in ln) == 5


def test_overseas_text_has_percent():
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("标普", "^GSPC", 6392.0, 0.003), _idx("纳指", "^IXIC", 21000.0, -0.002)],
        news=[], cny_mid=6.78)
    assert "%" in brief["text"]
    assert "人民币中间价 6.7800" in brief["text"]


# ---------------------------------------------------------------------------
# 纯函数：compress_title / draft_morning_takeaway
# ---------------------------------------------------------------------------

def test_compress_title_strips_prefix_and_truncates():
    assert compress_title("【早报】油价大涨，黄金大跌") == "油价大涨，黄金大跌"
    assert compress_title("界面早报 | 新一批集采纳入23个品种") == "新一批集采纳入23个品种"
    assert len(compress_title("很" * 50)) == 30


def test_takeaway_both_hits_neutral():
    out = draft_morning_takeaway(
        [_news("欧央行上调利率"), _news("央行降息刺激经济")], [])
    assert out["stance"] == "中性"
    assert "并存" in out["reason"]


def test_takeaway_no_hits_neutral_with_index_basis():
    out = draft_morning_takeaway(
        [_news("某政策出台")],
        [_idx("纳指", "^IXIC", 100, 0.01), _idx("恒生", "^HSI", 100, -0.02)])
    assert out["stance"] == "中性"
    assert "1 涨 1 跌" in out["reason"]


def test_takeaway_empty_news_insufficient():
    out = draft_morning_takeaway([], [_idx("纳指", "^IXIC", 100, 0.05)])
    assert out == {"bullets": [], "stance": "资料不足", "reason": "隔夜要闻为空"}


# ---------------------------------------------------------------------------
# 新闻管线：东财快讯(主) > RSS > policy/公告（全 mock，不打外网）
# ---------------------------------------------------------------------------

from src.investment_research_supervisor.morning_macro_brief import (
    fetch_eastmoney_flash,
    resolve_news,
)


def _em_payload(items):
    return {"data": {"list": items}}


def test_fetch_eastmoney_flash_parses_url_and_drops_bad():
    def fetcher(url):
        assert "getNewsByColumns" in url and "column=350" in url
        return _em_payload([
            {"title": "政策A出台", "showTime": "2026-09-10 21:05:00",
             "url": "http://finance.eastmoney.com/a/1.html"},
            {"title": "相对路径补域名", "showTime": "2026-09-10 21:06:00", "url": "/a/2.html"},
            {"title": "非http丢弃链接", "showTime": "2026-09-10 21:07:00", "url": "javascript:x"},
            {"title": "坏时间", "showTime": "garbage", "url": "http://e.com/3.html"},
            {"showTime": "2026-09-10 22:00:00"},  # 无标题丢弃
        ])

    items = fetch_eastmoney_flash(fetcher=fetcher)
    assert [i["title"] for i in items] == ["政策A出台", "相对路径补域名", "非http丢弃链接"]
    assert items[0]["url"] == "http://finance.eastmoney.com/a/1.html"
    assert items[1]["url"] == "https://finance.eastmoney.com/a/2.html"
    assert items[2]["url"] == ""
    assert items[0]["source"] == "东财快讯"
    assert items[0]["published"].strftime("%H:%M") == "21:05"


def test_fetch_eastmoney_flash_network_error_returns_empty():
    def fetcher(url):
        raise OSError("network down")

    assert fetch_eastmoney_flash(fetcher=fetcher) == []


def test_resolve_news_window_filter_noise_and_sort():
    def em_fetcher(url):
        return _em_payload([
            {"title": "美联储维持利率不变", "showTime": "2026-09-10 02:00:00",
             "url": "http://finance.eastmoney.com/a/9.html"},
            {"title": "央行开展逆回购操作", "showTime": "2026-09-10 07:30:00", "url": ""},
            {"title": "某股涨停 利好", "showTime": "2026-09-10 07:40:00", "url": ""},
            {"title": "旧闻已出窗", "showTime": "2026-09-09 10:00:00", "url": ""},
        ])

    def rss_fetcher(url):
        return ("<rss><channel>"
                "<item><title>工信部发布新方案</title>"
                "<link>https://cls.cn/detail/1</link>"
                "<pubDate>Thu, 10 Sep 2026 06:00:00 +0800</pubDate></item>"
                "<item><title>无日期忽略</title></item>"
                "</channel></rss>")

    now = dt(2026, 9, 10, 8, 0, tzinfo=SH)
    news = resolve_news("2026-09-10", research_db_path="/nonexistent.db",
                        em_fetcher=em_fetcher, rss_fetcher=rss_fetcher, now=now)
    titles = [i["title"] for i in news]
    assert "央行开展逆回购操作" in titles
    assert "美联储维持利率不变" in titles
    assert "工信部发布新方案" in titles
    assert "旧闻已出窗" not in titles
    assert not any("涨停" in t for t in titles)
    # 组内时间新→旧：国内组 07:30 在 06:00 前
    domestic_times = [i["time"] for i in news if i["title"] in ("央行开展逆回购操作", "工信部发布新方案")]
    assert domestic_times == ["07:30", "06:00"]
    # url 透传
    em_item = next(i for i in news if i["title"] == "央行开展逆回购操作")
    assert em_item["time"] == "07:30" and em_item["url"] == ""
    fed_item = next(i for i in news if i["title"] == "美联储维持利率不变")
    assert fed_item["url"] == "http://finance.eastmoney.com/a/9.html"
    rss_item = next(i for i in news if i["title"] == "工信部发布新方案")
    assert rss_item["url"] == "https://cls.cn/detail/1"


def test_resolve_news_domestic_capped_at_five():
    def em_fetcher(url):
        return _em_payload([
            {"title": f"国内政策消息第{i}条", "showTime": f"2026-09-10 0{i}:00:00", "url": ""}
            for i in range(1, 8)  # 7 条国内候选
        ])

    now = dt(2026, 9, 10, 8, 0, tzinfo=SH)
    news = resolve_news("2026-09-10", research_db_path="/nonexistent.db",
                        em_fetcher=em_fetcher, rss_fetcher=lambda url: None, now=now)
    assert len(news) == 5
    assert all(i["title"].startswith("国内") for i in news)


def test_resolve_news_overseas_capped_at_five():
    def em_fetcher(url):
        return _em_payload([
            {"title": f"美联储海外动态第{i}号", "showTime": f"2026-09-10 0{i}:30:00", "url": ""}
            for i in range(1, 8)  # 7 条海外候选
        ])

    now = dt(2026, 9, 10, 8, 0, tzinfo=SH)
    news = resolve_news("2026-09-10", research_db_path="/nonexistent.db",
                        em_fetcher=em_fetcher, rss_fetcher=lambda url: None, now=now)
    assert len(news) == 5


def test_resolve_news_total_10_from_12_candidates():
    """国内 7 + 海外 5 候选 → 国内 5、海外 5，共 10 条，组内新→旧。"""
    def em_fetcher(url):
        items = [{"title": f"国内政策消息第{i}条出台", "showTime": f"2026-09-10 07:{i:02d}:00",
                  "url": ""} for i in range(7)]
        items += [{"title": f"美联储海外动态第{i}号", "showTime": f"2026-09-10 06:{i:02d}:00",
                   "url": ""} for i in range(5)]
        return _em_payload(items)

    now = dt(2026, 9, 10, 8, 0, tzinfo=SH)
    news = resolve_news("2026-09-10", research_db_path="/nonexistent.db",
                        em_fetcher=em_fetcher, rss_fetcher=lambda url: None, now=now)
    assert len(news) == 10
    domestic = [i for i in news if i["title"].startswith("国内")]
    overseas = [i for i in news if i["title"].startswith("美联储")]
    assert len(domestic) == 5 and len(overseas) == 5
    # 组内时间新→旧
    assert [i["time"] for i in domestic] == [f"07:{i:02d}" for i in (6, 5, 4, 3, 2)]
    assert [i["time"] for i in overseas] == [f"06:{i:02d}" for i in (4, 3, 2, 1, 0)]


# ---------------------------------------------------------------------------
# 发送闸（纯判定，不触库不发送）
# ---------------------------------------------------------------------------

def test_gate_empty_content_rejected():
    from src.investment_research_supervisor.morning_macro_brief import morning_gate_decision
    assert morning_gate_decision(0, 0)["status"] == "SKIPPED_EMPTY"
    assert morning_gate_decision(1, 0)["status"] == "SKIPPED_EMPTY"
    assert morning_gate_decision(3, 0)["ok"] is True
    assert morning_gate_decision(0, 2)["ok"] is True


# ---------------------------------------------------------------------------
# 【环境】一行：日报同源宏观快照（全 mock，不触库不外网）
# ---------------------------------------------------------------------------

def _patch_env(monkeypatch, payload):
    import src.investment_research_supervisor.morning_macro_brief as mm
    monkeypatch.setattr(mm, "load_macro_environment_line", lambda as_of: payload)


def test_env_line_rendered_when_snapshot_available(monkeypatch):
    _patch_env(monkeypatch, {"text": "经济和资金面没有明显方向（中性）。环境无变化，不据此调整研究名单。",
                             "snapshot_as_of": "2026-09-10", "missing": ""})
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("某政策出台")], cny_mid=6.78)
    assert "【环境】经济和资金面没有明显方向（中性）。" in brief["text"]
    # 版式顺序：【判断】→【环境】→ 中间价 →【数字对照】
    lines = brief["text"].splitlines()
    assert lines.index("【环境】经济和资金面没有明显方向（中性）。环境无变化，不据此调整研究名单。") \
        > next(i for i, ln in enumerate(lines) if ln.startswith("【判断】"))
    assert lines.index("人民币中间价 6.7800") \
        > next(i for i, ln in enumerate(lines) if ln.startswith("【环境】"))
    assert any(ln.startswith("【数字对照】") for ln in lines[lines.index("人民币中间价 6.7800") + 1:])


def test_env_line_omitted_when_snapshot_missing(monkeypatch):
    _patch_env(monkeypatch, None)
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("某政策出台")], cny_mid=6.78)
    assert "【环境】" not in brief["text"]
    brief2 = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("某政策出台")], cny_mid=6.78,
        macro_environment={"text": "  ", "snapshot_as_of": "2026-09-10", "missing": ""})
    assert "【环境】" not in brief2["text"]


def test_env_line_keeps_missing_series_half_sentence(monkeypatch):
    _patch_env(monkeypatch, {"text": "经济和资金面没有明显方向（中性）。资料还缺社融、M1，判断宜保守。",
                             "snapshot_as_of": "2026-09-10", "missing": "社融、M1"})
    brief = build_morning_macro_brief(
        "2026-09-11",
        overseas=[_idx("纳指", "^IXIC", 21000, -0.006), _idx("恒生", "^HSI", 25000, -0.01)],
        news=[_news("某政策出台")], cny_mid=6.78)
    env_line = next(ln for ln in brief["text"].splitlines() if ln.startswith("【环境】"))
    assert "社融" in env_line and "保守" in env_line


def test_gate_not_relaxed_by_env_line():
    from src.investment_research_supervisor.morning_macro_brief import morning_gate_decision
    # 环境句不参与闸判定：要闻 0 且有效外盘 <2 仍拒发空卡
    assert morning_gate_decision(0, 0)["status"] == "SKIPPED_EMPTY"
    assert morning_gate_decision(1, 0)["status"] == "SKIPPED_EMPTY"


def test_env_line_source_does_not_touch_research_stores():
    import inspect
    import src.investment_research_supervisor.morning_macro_brief as mm
    source = inspect.getsource(mm.load_macro_environment_line) + \
        inspect.getsource(mm.build_morning_macro_brief)
    for banned in ("focus_selection", "low_value_leader_pool", "level3_leaders",
                   "INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert banned not in source, banned


def test_load_macro_environment_line_shapes():
    import src.investment_research_supervisor.morning_macro_brief as mm
    ok = mm.load_macro_environment_line(
        "2026-09-11",
        summary_fn=lambda d: {"available": True, "text": "环境偏紧，重点观察更要看风险。",
                              "as_of": "2026-09-10"})
    assert ok == {"text": "环境偏紧，重点观察更要看风险。", "snapshot_as_of": "2026-09-10",
                  "missing": ""}
    assert mm.load_macro_environment_line(
        "2026-09-11", summary_fn=lambda d: {"available": False, "text": "宏观环境资料暂不可用。"}) is None
    assert mm.load_macro_environment_line(
        "2026-09-11", summary_fn=lambda d: {"available": True, "text": ""}) is None
    assert mm.load_macro_environment_line(
        "2026-09-11", summary_fn=lambda d: None) is None
    with_missing = mm.load_macro_environment_line(
        "2026-09-11",
        summary_fn=lambda d: {"available": True,
                              "text": "资料还缺PMI、社融，判断宜保守。", "as_of": "2026-09-10"})
    assert with_missing["missing"] == "PMI、社融"


# ---------------------------------------------------------------------------
# 闸门与调度隔离
# ---------------------------------------------------------------------------

def test_import_purity():
    import subprocess
    import sys
    import os
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
    from src.investment_research_supervisor.morning_macro_brief import MorningMacroScheduler
    s = MorningMacroScheduler()
    sat = dt(2026, 9, 12, 8, 0, tzinfo=SH)
    assert s.tick(sat, trading_day_check=lambda d: True)["status"] == "SKIPPED_WEEKEND"


def test_scheduler_not_trading_day():
    from src.investment_research_supervisor.morning_macro_brief import MorningMacroScheduler
    s = MorningMacroScheduler()
    r = s.tick(dt(2026, 9, 10, 8, 0, tzinfo=SH), trading_day_check=lambda d: False)
    assert r["status"] == "SKIPPED_NOT_TRADING_DAY"


def test_is_trading_day_calendar_states(monkeypatch):
    """交易日三态：日历内确认、覆盖区间内缺失=休市、超出覆盖按工作日推断。"""
    import src.tdx_data.store as tdx_store_module

    days = [f"202609{d:02d}" for d in (1, 2, 3, 7, 8)]  # 至 09-08；04(周五)缺失=节假日例

    class _FakeStore:
        def list_records(self, dataset, limit=0):
            return {"items": [{"key": d} for d in days]}

        def close(self):
            return None

    monkeypatch.setattr(tdx_store_module, "TdxDataStore", _FakeStore)

    assert mm._is_trading_day("2026-09-07") is True    # 日历内确认
    assert mm._is_trading_day("2026-09-04") is False   # 覆盖区间内缺失 = 休市（节假日）
    assert mm._is_trading_day("2026-09-05") is False   # 周六
    assert mm._is_trading_day("2026-09-14") is True    # 超出覆盖的周一 → 交易日
    assert mm._is_trading_day("2026-09-12") is False   # 超出覆盖的周六 → 休市


def test_scheduler_sends_once(tmp_path):
    from src.investment_research_supervisor.morning_macro_brief import MorningMacroScheduler
    s = MorningMacroScheduler()
    sent = []
    s.tick(dt(2026, 9, 10, 8, 0, tzinfo=SH), trading_day_check=lambda d: True,
           sender=lambda d: sent.append({"date": d, "status": "SENT"}))
    assert len(sent) == 1
    s.tick(dt(2026, 9, 10, 9, 0, tzinfo=SH), trading_day_check=lambda d: True,
           sender=lambda d: sent.append({"date": d, "status": "SENT"}))
    assert len(sent) == 1  # 重跑同日 → ALREADY_HANDLED


# ---------------------------------------------------------------------------
# 飞书通道拆分：早盘走 Hermes 凭证（ShortLived），不依赖 supervisor.enabled
# ---------------------------------------------------------------------------

def test_morning_send_uses_hermes_credentials_not_supervisor_channel(tmp_path, monkeypatch):
    """b. feishu_supervisor.enabled=False 时早盘仍经 mock Hermes sender 发送；
    c(早盘侧). 投递记录 channel=feishu_morning_macro（独立于收盘幂等键）。"""
    import src.investment_research_supervisor.morning_macro_brief as mm
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings, ShortLivedFeishuBriefSender)
    from src.investment_research_supervisor.daily_brief_store import (
        InvestmentResearchDailyBriefRepository)

    monkeypatch.setattr(
        DailyBriefNotificationSettings, "from_channels_config",
        classmethod(lambda cls: DailyBriefNotificationSettings(
            enabled=True, target_id="oc_hermes_target",
            delivery_channel="hermes_feishu_supervisor")))

    calls = []

    class _MockHermesSender(ShortLivedFeishuBriefSender):
        def send_interactive_card(self, *, target_id, card):
            calls.append({"target_id": target_id})
            return "omock"

    monkeypatch.setattr(mm, "resolve_news", lambda as_of, **kw: [
        {"time": "07:00", "source": "东财快讯", "title": "某政策出台"}])
    monkeypatch.setattr(mm, "fetch_overseas_indices", lambda as_of, **kw: [
        {"name": "纳指", "symbol": "^IXIC", "date": "2026-09-13",
         "close": 100.0, "change_pct": 0.0},
        {"name": "恒生", "symbol": "^HSI", "date": "2026-09-13",
         "close": 100.0, "change_pct": 0.0}])
    monkeypatch.setattr(mm, "load_cny_mid", lambda as_of, **kw: 6.78)

    db_path = tmp_path / "research.db"
    monkeypatch.setattr(
        "src.investment_research_supervisor.daily_brief_notification_service.InvestmentResearchDailyBriefRepository",
        lambda path=None: InvestmentResearchDailyBriefRepository(db_path))
    monkeypatch.setattr(ShortLivedFeishuBriefSender, "send_interactive_card",
                        _MockHermesSender.send_interactive_card)

    result = mm.send_morning_macro_brief("2026-09-14", research_db_path=db_path)
    assert result["status"] == "SENT", result
    assert calls and calls[0]["target_id"] == "oc_hermes_target"
    assert result["delivery"]["channel"] == "feishu_morning_macro"


def test_channel_strings_differ_between_lines():
    """c. 早盘与收盘的 delivery channel 字符串必须不同。"""
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings,
    )
    from src.investment_research_supervisor.morning_macro_brief import DELIVERY_CHANNEL

    closing = DailyBriefNotificationSettings(
        enabled=True, target_id="x", delivery_channel="hermes_feishu_supervisor")
    assert DELIVERY_CHANNEL == "feishu_morning_macro"
    assert closing.delivery_channel == "hermes_feishu_supervisor"
    assert DELIVERY_CHANNEL != closing.delivery_channel


def test_closing_settings_fall_back_to_hermes_when_supervisor_disabled(monkeypatch):
    """a. supervisor.enabled=False → 收盘 settings.delivery_channel 含 hermes。"""
    import json
    import tempfile

    import src.channels.config as channels_config
    import src.investment_research_supervisor.hermes_feishu as hermes_feishu_module
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings,
    )

    cfg = {"channels": {"feishu_supervisor": {
        "enabled": False,
        "daily_research_brief_notification": {"enabled": True, "target_id": "oc_x"},
    }}}

    class _FakeChannelsModel:
        def model_dump(self, mode=None, by_alias=False):
            return cfg["channels"]

    class _FakeConfig:
        channels = _FakeChannelsModel()

    class _FakeCreds:
        app_id = "a"
        app_secret = "s"
        domain = "feishu"
        target_id = "oc_hermes_home"

        @classmethod
        def load(cls, **kwargs):
            return _FakeCreds()

    monkeypatch.setattr(channels_config, "load_agent_config", lambda path=None: _FakeConfig())
    monkeypatch.setattr(hermes_feishu_module.HermesSupervisorFeishuCredentials,
                        "load", classmethod(lambda cls, **kw: _FakeCreds()))

    settings = DailyBriefNotificationSettings.from_channels_config()
    assert "hermes" in settings.delivery_channel
    assert settings.enabled is True
    assert settings.target_id == "oc_hermes_home"
