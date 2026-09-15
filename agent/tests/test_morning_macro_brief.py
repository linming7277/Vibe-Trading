"""早盘宏观速览 V4 测试：国内 6+海外 6、解读、可能相关 L1、闸与调度。
全 mock，不打外网、不连飞书、零 LLM。"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timezone, timedelta
from datetime import datetime as dt
from zoneinfo import ZoneInfo

import src.investment_research_supervisor.morning_macro_brief as mm
import src.tdx_data.store as tdx_store_module
from src.investment_research_supervisor.morning_macro_brief import (
    build_morning_macro_brief,
    compress_title,
    draft_morning_takeaway,
    fetch_eastmoney_flash,
    resolve_news,
)

SH = ZoneInfo("Asia/Shanghai")
FORBIDDEN = ("买入", "卖出", "加仓", "减仓", "止盈", "止损")


def _news(title, time="07:00"):
    return {"time": time, "source": "东财快讯", "title": title}


def _idx(name, symbol, close, pct):
    prev = close / (1 + pct)
    return {"name": name, "symbol": symbol, "date": "2026-09-12",
            "close": round(prev * (1 + pct), 2), "change_pct": round(pct, 4)}


def _em_payload(items):
    return {"data": {"list": items}}


def _fixture_14():
    """≥14 条夹具：纯日历、秋招×2、相似重复、国内外政策/行业/外盘。"""
    return [
        _news("新华财经早报：9月15日", "07:50"),
        _news("南财投资日历（9月14日）", "07:51"),
        _news("某券商秋招启动 面向2026届毕业生", "07:52"),
        _news("某银行秋招公告发布", "07:53"),
        _news("国常会部署进一步完善算力基础设施建设", "07:55"),
        _news("电子信息制造业发展“十五五”规划发布", "07:56"),
        _news("8月经济数据将发布 媒体聚焦核心议题", "07:57"),
        _news("央行开展逆回购操作 净投放加码", "07:58"),
        _news("光通信企业发布新一代数据中心互联方案", "07:59"),
        _news("存储芯片现货价格连续上调", "08:00"),
        _news("美股三大指数收跌 避险情绪升温", "06:30"),
        _news("国际油价大涨 布伦特原油突破前高", "06:20"),
        _news("国际金价再创阶段新高 黄金避险需求升温", "06:10"),
        _news("美债收益率上行 市场等待联储决议", "06:00"),
    ]




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


def test_compress_title_strips_prefix_and_truncates():
    assert compress_title("【早报】油价大涨，黄金大跌") == "油价大涨，黄金大跌"
    assert compress_title("界面早报 | 新一批集采纳入23个品种") == "新一批集采纳入23个品种"
    assert len(compress_title("很" * 50)) == 30


def test_pending_hawk_is_neutral_not_cold():
    """「加息悬念待揭晓」事件未发生 → 中性，不得出现偏冷。"""
    brief = build_morning_macro_brief(
        "2026-09-11",
        news=[_news("美联储加息悬念待揭晓")],
        macro_shadow=False)
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


def test_takeaway_both_hits_neutral():
    out = draft_morning_takeaway(
        [_news("欧央行上调利率"), _news("央行降息刺激经济")], [])
    assert out["stance"] == "中性"
    assert "并存" in out["reason"]


def test_takeaway_empty_news_insufficient():
    out = draft_morning_takeaway([], [_idx("纳指", "^IXIC", 100, 0.05)])
    assert out == {"bullets": [], "stance": "资料不足", "reason": "隔夜要闻为空"}


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

    monkeypatch.setattr(mm, "_load_flash_items", lambda as_of, **kw: [
        {"time": "07:00", "source": "东财快讯", "title": "某政策出台",
         "url": "http://e.com/policy", "published_at": "2026-09-14T23:00:00+08:00"}])

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


# ---------------------------------------------------------------------------
# V4 版式：国内 6 + 海外 6、过滤、解读、可能相关
# ---------------------------------------------------------------------------

def test_fixture_caps_and_filters():
    news = _fixture_14()
    domestic, overseas = mm._split_and_cap(list(news))
    assert len(domestic) <= 6 and len(overseas) <= 6
    assert len(domestic) + len(overseas) <= 12
    titles = [i["title"] for i in domestic + overseas]
    assert not any("秋招" in t for t in titles)
    assert not any("新华财经早报" in t for t in titles)
    assert not any("南财投资日历" in t for t in titles)
    assert any("规划" in t for t in titles)
    assert any("光通信" in t for t in titles)
    assert any("存储" in t for t in titles)


def test_text_has_no_market_noise_and_no_url():
    brief = build_morning_macro_brief("2026-09-15", news=_fixture_14(), macro_shadow=False)
    text = brief["text"]
    for banned in ("纳指", "数字对照", "中间价", "26253", "http", "【环境】", "【要点】"):
        assert banned not in text, banned


def test_possible_industries_contains_sw_l1():
    brief = build_morning_macro_brief("2026-09-15", news=_fixture_14(), macro_shadow=False)
    assert "可能相关：" in brief["text"]
    assert "以上不改今天 Focus 名单。" in brief["text"]
    line = next(ln for ln in brief["text"].splitlines() if ln.startswith("可能相关："))
    names = line[len("可能相关："):].split("。")[0].split("、")
    assert 1 <= len(names) <= 4
    assert all(n in mm._SW_L1_WHITELIST for n in names)
    assert ("通信" in names) or ("电子" in names)


def test_only_soft_ad_news_build_insufficient_and_gate_zero(tmp_path, monkeypatch):
    """只有秋招 → build 资料不足；send → SKIPPED_EMPTY 且网关 0 次。"""
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings, ShortLivedFeishuBriefSender)
    from src.investment_research_supervisor.daily_brief_store import (
        InvestmentResearchDailyBriefRepository)

    news = [_news("某券商秋招启动", "07:00"), _news("某银行秋招公告", "07:05")]
    brief = build_morning_macro_brief("2026-09-15", news=list(news), macro_shadow=False)
    assert brief["text"].startswith("【隔夜要闻】资料不足。")
    assert "【判断】资料不足" in brief["text"]

    monkeypatch.setattr(DailyBriefNotificationSettings, "from_channels_config",
                        classmethod(lambda cls: DailyBriefNotificationSettings(
                            enabled=True, target_id="oc_hermes_target",
                            delivery_channel="hermes_feishu_supervisor")))
    monkeypatch.setattr(mm, "_load_flash_items", lambda as_of, **kw: [
        {"time": "07:50", "title": "某券商秋招启动", "url": "http://e.com/x",
         "source": "东财快讯"},
        {"time": "07:05", "title": "某银行秋招公告发布", "url": "http://e.com/y",
         "source": "东财快讯"}])
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


def test_gate_allows_news_without_overseas(tmp_path, monkeypatch):
    """有要闻、无外盘指数 → 闸放行（指数不参与）。"""
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings, ShortLivedFeishuBriefSender)
    from src.investment_research_supervisor.daily_brief_store import (
        InvestmentResearchDailyBriefRepository)
    from src.investment_research_supervisor import morning_macro_brief as mm

    monkeypatch.setattr(mm, "_load_flash_items", lambda as_of, **kw: [
        {"time": "07:50", "title": "国内某重要政策出台", "url": "http://e.com/x",
         "source": "东财快讯"}])
    monkeypatch.setattr(DailyBriefNotificationSettings, "from_channels_config",
                        classmethod(lambda cls: DailyBriefNotificationSettings(
                            enabled=True, target_id="oc_t",
                            delivery_channel="hermes_feishu_supervisor")))
    monkeypatch.setattr(
        "src.investment_research_supervisor.daily_brief_notification_service.InvestmentResearchDailyBriefRepository",
        lambda path=None: InvestmentResearchDailyBriefRepository(tmp_path / "r.db"))
    sent = []

    class _Spy(ShortLivedFeishuBriefSender):
        def send_interactive_card(self, *, target_id, card):
            sent.append(target_id)
            return "omock"

    monkeypatch.setattr(ShortLivedFeishuBriefSender, "send_interactive_card",
                        _Spy.send_interactive_card)
    result = mm.send_morning_macro_brief("2026-09-15", research_db_path=tmp_path / "r.db")
    assert result["status"] == "SENT"
    assert sent == ["oc_t"]


def test_brief_text_no_trading_words():
    brief = build_morning_macro_brief("2026-09-15", news=_fixture_14(), macro_shadow=False)
    for word in FORBIDDEN:
        assert word not in brief["text"], word


def test_module_ast_does_not_import_research_stores():
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


def test_resolve_news_end_to_end_filter_and_split(monkeypatch):
    items = [
        {"title": "国常会部署政策落地", "source": "东财快讯",
         "url": "http://e.com/1", "published": datetime(2026, 9, 15, 7, 55, tzinfo=SH)},
        {"title": "新华财经早报：9月15日", "source": "东财快讯",
         "url": "http://e.com/2", "published": datetime(2026, 9, 15, 7, 50, tzinfo=SH)},
        {"title": "某券商秋招启动", "source": "东财快讯",
         "url": "http://e.com/3", "published": datetime(2026, 9, 15, 7, 45, tzinfo=SH)},
        {"title": "美股三大指数收跌", "source": "东财快讯",
         "url": "http://e.com/4", "published": datetime(2026, 9, 15, 6, 30, tzinfo=SH)},
    ]
    monkeypatch.setattr(mm, "fetch_eastmoney_flash",
                        lambda fetcher=None, max_items=20: items)
    monkeypatch.setattr(mm, "fetch_rss_items", lambda ws, we, fetcher=None: [])
    monkeypatch.setattr(mm, "load_policy_items", lambda as_of, **kw: [])
    monkeypatch.setattr(mm, "load_company_announcements", lambda as_of, **kw: [])
    now = dt(2026, 9, 15, 8, 0, tzinfo=SH)
    news = mm.resolve_news("2026-09-15", rss_fetcher=lambda u: None, now=now)
    titles = [i["title"] for i in news]
    assert not any("秋招" in t or "新华财经早报" in t for t in titles)
    assert len([t for t in titles if "美股" in t]) == 1


# ---------------------------------------------------------------------------
# 同事件去重收紧：房价三句合一，不同事件不误伤
# ---------------------------------------------------------------------------

def test_same_event_house_price_titles_merged():
    """房价三句只留最具体的一条；国标/十五五/美股不受影响。"""
    items = [
        _news("8月70城房价出炉：15城环比上涨", "07:00"),
        _news("国家统计局城市司高级统计师杨彩芳解读2026年8月份商品住宅销售价格变动情况统计数据", "09:48"),
        _news("70城最新房价出炉", "09:32"),
        _news("互联网租赁自行车国家标准发布", "09:16"),
        _news("电子信息制造业发展“十五五”规划发布", "09:02"),
        _news("美股光通信存储芯片板块大跌", "06:30"),
    ]
    out = mm._filter_news(list(items))
    titles = [i["title"] for i in out]
    house = [t for t in titles if "房价" in t or "商品住宅" in t or "70城" in t]
    assert len(house) == 1  # 房价类只出现 1 条
    assert house[0].startswith("国家统计局")  # 留最长（最具体）
    assert any("互联网租赁自行车国家标准" in t for t in titles)
    assert any("十五五" in t for t in titles)
    assert any("光通信" in t and "存储" in t for t in titles)


def test_build_with_house_price_triplet_keeps_one():
    """整卡版：三条房价经 build 过滤后只出现一次。"""
    brief = build_morning_macro_brief("2026-09-15", news=[
        _news("8月70城房价出炉：15城环比上涨", "07:00"),
        _news("国家统计局城市司高级统计师杨彩芳解读2026年8月份商品住宅销售价格变动情况统计数据", "09:48"),
        _news("70城最新房价出炉", "09:32"),
        _news("互联网租赁自行车国家标准发布", "09:16"),
    ], macro_shadow=False)
    count = sum(1 for ln in brief["text"].splitlines()
                if "房价" in ln or "商品住宅" in ln or "城市司" in ln)
    assert count == 1
