"""早盘宏观速览 V3（东财快讯为主）：工作日 08:00 Asia/Shanghai。

新闻三级源：东财快讯(主) > RSSHub 财联社/金十(备) > policy_events(补)。
独立于收盘日报。发送闸默认 off（HZ_MORNING_MACRO）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

SHANGHAI = timezone(timedelta(hours=8))
MORNING_SEND_TIME = time(8, 0, 0)
BRIEF_TYPE = "morning_macro"
DELIVERY_CHANNEL = "feishu_morning_macro"
WINDOW_HOURS = 17
_NEWS_DOMESTIC_MAX = 6
_NEWS_OVERSEAS_MAX = 6
_PRIORITY_KWS = ("政策", "监管", "出台", "发布", "改革", "决议", "加息", "降息", "集采",
                 "规划", "数据", "发布会", "净投放", "美联储", "欧央行", "油价", "黄金",
                 "原油", "房价", "芯片", "存储", "光通信", "信贷", "社融")
_RSS_TIMEOUT_S = 8
_TRADING_TERMS = ("买入", "卖出", "加仓", "减仓", "追涨", "打板")
_RSSHUB_BASE = os.environ.get("HZ_RSSHUB_BASE", "https://rsshub.app")
_RSS_FEEDS = (("财联社", _RSSHUB_BASE + "/cls/telegraph"), ("金十", _RSSHUB_BASE + "/jin10"))


def _now():
    return datetime.now(SHANGHAI).isoformat()


def _assert_boss_safe(text):
    if any(t in text for t in _TRADING_TERMS):
        raise ValueError("morning macro text must not contain trading language")
    return text


def _compact(day):
    return str(day or "").replace("-", "")[:8]


def _shift_days(day, delta):
    from datetime import date as d, timedelta as td
    return (d(int(day[:4]), int(day[5:7]), int(day[8:10])) + td(days=delta)).isoformat()


def morning_macro_enabled():
    return os.environ.get("HZ_MORNING_MACRO", "off").strip().lower() == "on"


# ---------------------------------------------------------------------------
# 新闻源 1：东财快讯（主源）
# ---------------------------------------------------------------------------

_EM_NEWS_URL = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
_EM_NEWS_FIELDS = "code,showTime,title,mediaName,summary,url,uniqueUrl"


def _default_em_fetcher(url):
    import time as _time
    import urllib.request
    from urllib.parse import urlencode
    full = url + "&" + urlencode({"req_trace": str(int(_time.time() * 1000))})
    req = urllib.request.Request(full, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _abs_http_url(raw):
    """只认 http(s) 绝对地址；相对路径补东财域名，补不出返回空。"""
    raw = str(raw or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return "https://finance.eastmoney.com" + raw
    return ""


def fetch_eastmoney_flash(*, fetcher=None, max_items=20):
    """东财快讯（column=350 财经快讯）：返回 {title, source, published, url} 列表，新→旧。

    published 为北京时间 datetime；解析失败的条目丢弃。url 仅留 http(s) 地址，无则空串。
    """
    query = {"client": "web", "biz": "web_724", "column": "350", "order": "1",
             "needInteractData": "0", "page_index": "1", "page_size": str(max_items),
             "fields": _EM_NEWS_FIELDS}
    from urllib.parse import urlencode
    url = _EM_NEWS_URL + "?" + urlencode(query)
    if fetcher is None:
        fetcher = _default_em_fetcher
    try:
        payload = fetcher(url)
    except Exception:
        logger.warning("eastmoney flash fetch failed", exc_info=True)
        return []
    if not payload or not isinstance(payload, dict):
        return []
    raw_list = (payload.get("data") or {}).get("list") or []
    out = []
    for item in raw_list:
        title = str(item.get("title") or "").strip()
        if not title:
            summary = str(item.get("summary") or "").strip()
            title = summary.split("】", 1)[-1].strip()[:60] if summary else ""
        if not title:
            continue
        published = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                published = datetime.strptime(
                    str(item.get("showTime") or "").strip(), fmt).replace(tzinfo=SHANGHAI)
                break
            except ValueError:
                continue
        if published is None:
            continue
        link = _abs_http_url(item.get("url")) or _abs_http_url(item.get("uniqueUrl"))
        out.append({"title": title, "source": "东财快讯", "published": published, "url": link})
    return out[:max_items]


# ---------------------------------------------------------------------------
# 新闻源 2：RSSHub 财联社/金十（备源，失败 → 空）
# ---------------------------------------------------------------------------

def fetch_rss_items(window_start, window_end, *, fetcher=None):
    if fetcher is None:
        def fetcher(url):
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "vibe-trading-morning/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.read().decode("utf-8", errors="replace") if resp.status == 200 else None
    items = []
    for source_name, url in _RSS_FEEDS:
        try:
            raw = fetcher(url)
            if not raw:
                continue
            root = ElementTree.fromstring(raw)
            for item in root.iter("item"):
                title_el = item.find("title")
                pub_el = item.find("pubDate")
                if title_el is None or not (title_el.text or "").strip():
                    continue
                title = " ".join(str(title_el.text).split())
                published = None
                if pub_el is not None and (pub_el.text or "").strip():
                    try:
                        published = parsedate_to_datetime(pub_el.text).astimezone(SHANGHAI)
                    except Exception:
                        published = None
                if published is None:
                    continue
                if not (window_start <= published <= window_end):
                    continue
                link_el = item.find("link")
                link = _abs_http_url(link_el.text if link_el is not None else None)
                items.append({"title": title, "source": source_name,
                              "published": published, "url": link})
        except Exception:
            logger.warning("rss source failed: %s", source_name)
    return items


# ---------------------------------------------------------------------------
# 新闻源 3：policy_events（政策表补 2 条）
# ---------------------------------------------------------------------------

def load_policy_items(as_of_date, *, research_db_path=None):
    try:
        if research_db_path is None:
            from src.config.paths import get_runtime_root
            research_db_path = get_runtime_root() / "research.db"
        conn = sqlite3.connect(f"file:{Path(str(research_db_path)).as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT title, source, published_at FROM policy_events "
                "WHERE published_at IN (?,?) AND title IS NOT NULL",
                (_shift_days(as_of_date, -1), as_of_date)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [{"title": str(r["title"]), "source": f"政策·{r['source']}",
             "date": str(r["published_at"])[:10]} for r in rows]


def load_company_announcements(as_of_date, *, research_db_path=None):
    """公司公告（定增除外——定增走事件表）。"""
    try:
        if research_db_path is None:
            from src.config.paths import get_runtime_root
            research_db_path = get_runtime_root() / "research.db"
        conn = sqlite3.connect(f"file:{Path(str(research_db_path)).as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT title, announcement_date FROM company_action_events "
                "WHERE announcement_date IN (?,?) AND title IS NOT NULL "
                "AND event_type != 'PRIVATE_PLACEMENT'",
                (_shift_days(as_of_date, -1), as_of_date)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [{"title": str(r["title"]), "source": "公司公告",
             "date": str(r["announcement_date"])[:10]} for r in rows]


def load_cny_mid(as_of_date, *, research_db_path=None):
    try:
        if research_db_path is None:
            from src.config.paths import get_runtime_root
            research_db_path = get_runtime_root() / "research.db"
        conn = sqlite3.connect(f"file:{Path(str(research_db_path)).as_posix()}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT value FROM macro_series WHERE series_id='usd_cny_official_mid' "
            "AND value IS NOT NULL AND observation_date<=? ORDER BY observation_date DESC LIMIT 1",
            (as_of_date,)).fetchone()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 【环境】一行：日报同源宏观快照（只读，不 refresh、不作为筛选条件）
# ---------------------------------------------------------------------------

_MISSING_RE = re.compile(r"资料还缺([^，。]+)，判断宜保守")


def _default_macro_summary(as_of_date):
    """与收盘日报「当前研究环境」同源：InvestmentResearchDailyBriefService._macro_environment_text。"""
    from src.investment_research_supervisor.daily_brief_service import (
        InvestmentResearchDailyBriefService)
    return InvestmentResearchDailyBriefService._macro_environment_text(as_of_date)


def load_macro_environment_line(as_of_date, *, summary_fn=None):
    """只读已落库宏观快照的一句话环境，返回 {text, snapshot_as_of, missing} 或 None。

    as-of 查询天然取 ≤as_of 的最新观察值（当日未采集不空段）；available=False
    或句子为空 → None，整段【环境】省略，绝不编「中性」。缺数半句
    「资料还缺…，判断宜保守。」已由快照文案自带，渲染原句不加工。
    """
    try:
        fn = summary_fn or _default_macro_summary
        summary = dict(fn(as_of_date) or {})
    except Exception:
        logger.warning("macro environment line unavailable for %s", as_of_date, exc_info=True)
        return None
    if not summary.get("available"):
        return None
    text = str(summary.get("text") or "").strip()
    if not text:
        return None
    matched = _MISSING_RE.search(text)
    return {"text": text, "snapshot_as_of": str(summary.get("as_of") or as_of_date),
            "missing": matched.group(1).strip() if matched else ""}


# ---------------------------------------------------------------------------
# 外盘（数字对照）：主源=ds 扩展行情；Yahoo 兜底
# ---------------------------------------------------------------------------

_OVERSEAS_ORDER = ("标普", "纳指", "道指", "恒生", "纳指100")
_SYMBOL_BY_FILE = {"12#A_IXIC.day": "^IXIC", "12#A_NDX.day": "^NDX", "27#HSI.day": "^HSI"}


def symbol_of(filename):
    return {"12#A_IXIC.day": "^IXIC", "12#A_NDX.day": "^NDX", "27#HSI.day": "^HSI"}.get(filename, filename)


def _default_yahoo_fetcher(symbol, as_of_date):
    try:
        from backtest.loaders.yahoo_loader import DataLoader
        loader = DataLoader()
        frames = loader.fetch([symbol], _shift_days(as_of_date, -10), as_of_date)
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            return []
        return [{"date": str(i)[:10], "close": float(r["close"])}
                for i, r in frame.iterrows() if r.get("close") is not None]
    except Exception:
        return []


def fetch_overseas_indices(as_of_date, *, tdx_home=None, yahoo_fetcher=None):
    from src.tdx_data.day_file import default_tdx_home, read_ds_lday
    cutoff = _compact(as_of_date)
    out = {}
    ds_home = Path(tdx_home) if tdx_home else default_tdx_home()
    for name, filename in (("纳指", "12#A_IXIC.day"), ("纳指100", "12#A_NDX.day"), ("恒生", "27#HSI.day")):
        path = ds_home / "vipdoc" / "ds" / "lday" / filename
        bars = [b for b in read_ds_lday(str(path)) if _compact(b["date"]) < cutoff]
        if len(bars) < 2 or bars[-1]["close"] is None:
            continue
        last, prev = bars[-1], bars[-2]
        if prev["close"] <= 0:
            continue
        out[name] = {"name": name, "symbol": symbol_of(filename), "date": last["date"],
                     "close": float(last["close"]),
                     "change_pct": round(float(last["close"]) / float(prev["close"]) - 1, 6),
                     "source": "tdx_ds"}
    if yahoo_fetcher is None:
        yahoo_fetcher = _default_yahoo_fetcher
    for name, symbol in (("^GSPC", "标普"), ("^DJI", "道指")):
        if name in out:
            continue
        try:
            bars = [b for b in yahoo_fetcher(symbol, as_of_date) if _compact(b.get("date")) <= cutoff]
        except Exception:
            bars = []
        if len(bars) < 2:
            continue
        last, prev = bars[-1], bars[-2]
        if prev["close"] <= 0:
            continue
        out[name] = {"name": name, "symbol": symbol, "date": last["date"],
                     "close": float(last["close"]),
                     "change_pct": round(float(last["close"]) / float(prev["close"]) - 1, 6),
                     "source": "yahoo"}
    return [out[n] for n in _OVERSEAS_ORDER if n in out]


# ---------------------------------------------------------------------------
# 新闻过滤 + 分类
# ---------------------------------------------------------------------------

_DROP_PATTERNS = ("涨停", "龙虎榜", "打板", "广告", "加微信", "定增", "增发",
                  "PRIVATE_PLACEMENT", "PRIVATE PLACEMENT")
_OVERSEAS_KW = ("美股", "欧股", "港股", "恒生", "纳指", "标普", "道指", "日经",
                "原油", "黄金", "美债", "美元", "联储", "欧央行", "鲍威尔",
                "美联储", "油价", "美股期货")

_CALENDAR_PREFIX_RE = re.compile(r"^(新华财经早报|南财投资日历|财经早报|早报)")
_CALENDAR_ONLY_RE = re.compile(r"^(新华财经早报|南财投资日历|财经早报|早报)[：:\s|]*\d{1,2}月\d{1,2}日?[）)]?$")
_DOMESTIC_FORCE_KWS = ("国家统计局", "统计局", "海关总署", "央行", "财政部",
                       "发改委", "国常会", "两部门", "新华社")
_SOFT_AD_KWS = ("招聘", "秋招", "校招", "宣讲", "展台", "人才画像")
_EVENT_HINT_KWS = ("出台", "发布", "改革", "决议", "加息", "降息", "集采", "规划", "房价",
                   "CPI", "PMI", "净投放", "监管", "政策", "数据", "上调", "下调", "公布",
                   "印发", "会议", "实施", "签署", "获批", "开工", "投产", "外资", "出口")

_SW_L1_WHITELIST = (
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器",
    "食品饮料", "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输",
    "房地产", "商贸零售", "社会服务", "银行", "非银金融", "综合", "建筑材料",
    "建筑装饰", "电力设备", "机械设备", "国防军工", "计算机", "传媒", "通信",
    "煤炭", "石油石化", "环保", "美容护理",
)
_KEYWORD_L1_MAP = (
    ("光通信", "通信"), ("光纤", "通信"), ("运营商", "通信"), ("5G", "通信"),
    ("存储", "电子"), ("半导体", "电子"), ("芯片", "电子"), ("面板", "电子"),
    ("电子", "电子"),
    ("银行", "银行"), ("信贷", "银行"),
    ("原油", "石油石化"), ("油价", "石油石化"), ("天然气", "石油石化"),
    ("黄金", "有色金属"), ("铜价", "有色金属"), ("稀土", "有色金属"),
    ("地产", "房地产"), ("房价", "房地产"),
    ("医保", "医药生物"), ("医药", "医药生物"), ("创新药", "医药生物"),
    ("煤炭", "煤炭"),
    ("汽车", "汽车"), ("车企", "汽车"),
    ("食品", "食品饮料"), ("白酒", "食品饮料"),
    ("钢铁", "钢铁"),
    ("电力", "公用事业"), ("电网", "公用事业"),
    ("券商", "非银金融"), ("保险", "非银金融"),
    ("基建", "建筑装饰"), ("铁路投资", "交通运输"),
    ("军工", "国防军工"), ("国防", "国防军工"),
    ("传媒", "传媒"), ("游戏", "传媒"),
    ("算力", "计算机"), ("软件", "计算机"), ("人工智能", "计算机"),
    ("机器人", "机械设备"),
    ("化工", "基础化工"),
    ("家电", "家用电器"),
    ("种业", "农林牧渔"), ("粮食", "农林牧渔"),
    ("光伏", "电力设备"), ("储能", "电力设备"), ("锂电", "电力设备"),
    ("纺织", "纺织服饰"),
)

# 解读规则：标题命中关键词 → 固定解读句（换说法，不复读标题；纯规则无 LLM）。
_INTERPRET_RULES = (
    ("国常会", "政策由国常会定调，重点看后续部委落地节奏。"),
    ("算力", "算力基础设施获政策加持，产业链需求侧信号明确。"),
    ("数据中心", "算力基础设施获政策加持，产业链需求侧信号明确。"),
    ("美联储", "海外利率路径仍有不确定性，外部流动性变量需要跟踪。"),
    ("加息", "海外利率路径仍有不确定性，外部流动性变量需要跟踪。"),
    ("降息", "海外利率转向将改善外部流动性环境，需持续跟踪。"),
    ("房价", "地产量价数据仍是内需侧最重要的观察变量之一。"),
    ("集采", "支付端政策变化会直接影响相关公司的盈利结构。"),
    ("医保", "支付端政策变化会直接影响相关公司的盈利结构。"),
    ("油价", "能源价格波动值得关注对成本端与通胀的传导。"),
    ("原油", "能源价格波动值得关注对成本端与通胀的传导。"),
    ("黄金", "避险资产价格波动反映全球风险偏好的变化。"),
    ("信贷", "信用扩张节奏是资金面最核心的观察项。"),
    ("社融", "信用扩张节奏是资金面最核心的观察项。"),
    ("存储", "存储涨价周期对电子产业链盈利有直接传导。"),
    ("芯片", "半导体国产化与需求回暖是产业链的主要变量。"),
    ("光通信", "AI 算力建设拉动光通信需求，行业景气度值得跟踪。"),
    ("资本市场", "资本市场制度改革影响研究范围的估值与流动性环境。"),
    ("改革", "改革举措落地节奏决定相关行业的边际变化。"),
    ("发布会", "官方发布会的口径通常给出后续政策的方向锚。"),
)

# 强辨识主题组：同组词命中即视为同一事件（组词只收强辨识事件词）。
_TOPIC_TERM_GROUPS = (
    ("房价", ("70城", "房价", "商品住宅", "住宅销售", "城市司")),
    ("算力国常会", ("国常会", "算力基础设施")),
    ("光通信数据中心", ("光通信", "数据中心")),
    ("存储芯片", ("存储", "芯片", "半导体")),
    ("美联储利率", ("美联储", "联储", "利率决议", "降息", "加息")),
    ("黄金", ("黄金",)),
    ("原油油价", ("原油", "油价", "布伦特")),
    ("集采", ("集采",)),
    ("国标自行车", ("租赁自行车", "国家标准")),
    ("十五五规划", ("十五五",)),
)
_TITLE_STOPWORDS = ("最新", "出炉", "解读", "高级统计师", "统计师", "今日", "昨日")


def _normalize_title(title):
    """规范化：去早报前缀/日期/停用词/标点，用于同事件比较。"""
    t = _BULLET_PREFIX_RE.sub("", title.strip())
    t = re.sub(r"\d{4}年\d{1,2}月[份]?", "", t)
    t = re.sub(r"\d{1,2}月\d{1,2}日", "", t)
    t = re.sub(r"\d{6,}", "", t)
    for word in _TITLE_STOPWORDS:
        t = t.replace(word, "")
    return re.sub(r"[：:，,。、（）()\[\]|｜\s\-—？?！!]+", "", t)


def _title_topics(title):
    topics = set()
    for name, terms in _TOPIC_TERM_GROUPS:
        if any(term in title for term in terms):
            topics.add(name)
    return topics


def _is_overseas_news(title):
    return any(kw in title for kw in _OVERSEAS_KW)


def _is_stock_promo(title):
    return bool(re.search(r"\d{6}", title)) and any(
        w in title for w in ("利好", "涨停", "大涨", "跌停"))


def _dedup_titles(items):
    seen = set()
    out = []
    for item in items:
        key = item.get("title", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 新闻解析 + 合并（三级源）
# ---------------------------------------------------------------------------

def resolve_news(as_of_date, *, research_db_path=None, em_fetcher=None,
                 rss_fetcher=None, now=None):
    """合并三级新闻源 → 扁平列表（国内在前、海外在后；国内 ≤6、海外 ≤6，合计 ≤12）。

    时窗：as_of 前一日 15:00 → now（缺省当前北京时间）。
    优先级：东财快讯 > RSSHub 财联社/金十 > policy_events/公司公告（标题去重）。
    过滤：涨停/定增/推广、招聘秋招软文、纯日历早报、相似重复标题不进要闻；
    政策/监管/数据/外盘商品类优先保留。
    """
    window_start = _window_start_dt(as_of_date)
    window_end = now or _now_dt()
    em = fetch_eastmoney_flash(fetcher=em_fetcher)
    rss = fetch_rss_items(window_start, window_end, fetcher=rss_fetcher)
    combined = []
    for item in em + rss:
        published = item.get("published")
        if published is None or not (window_start <= published <= window_end):
            continue
        combined.append({"title": item["title"], "source": item["source"],
                         "url": str(item.get("url") or ""),
                         "time": published.strftime("%H:%M"),
                         "_sort": (0, published)})
    for item in load_policy_items(as_of_date, research_db_path=research_db_path) + \
            load_company_announcements(as_of_date, research_db_path=research_db_path):
        item["url"] = str(item.get("url") or "")
        item["_sort"] = (1, str(item.get("date") or ""))
        combined.append(item)
    # 组内时间新→旧：带时刻的在前按 published 降序，仅带日期的在后按日期降序
    combined.sort(key=lambda x: x["_sort"], reverse=True)
    for item in combined:
        item.pop("_sort", None)
    domestic, overseas_news = _split_and_cap(_dedup_titles(combined))
    return domestic + overseas_news


def _window_start_dt(as_of_date):
    from datetime import datetime as dt
    return dt.strptime(as_of_date, "%Y-%m-%d").replace(
        hour=15, minute=0, second=0, tzinfo=SHANGHAI) - timedelta(days=1)


def _now_dt():
    return datetime.now(SHANGHAI)


def _has_priority(title):
    return any(k in title for k in _PRIORITY_KWS)


def _filter_news(items):
    """过滤（命中即丢）：推广/涨停/定增；招聘秋招校招/展台/人才画像类软文；
    纯日历早报（「新华财经早报：9月15日」式，或早报类前缀且无任何事件词）；
    同一事件只留一条：规范化相等或主题组交集非空 → 重复，保留规范化标题
    更长（更具体）者，长度相同保留先出现（时间更新）者。"""
    seen = []  # [(normalized, topics, item)]
    out = []
    for item in items:
        title = str(item.get("title") or "")
        if not title:
            continue
        if _is_stock_promo(title) or any(m in title for m in _DROP_PATTERNS):
            continue
        if any(k in title for k in _SOFT_AD_KWS):
            continue
        if _CALENDAR_ONLY_RE.search(title):
            continue
        # 早报类前缀：去前缀后仍很短（<8 字）且无事件词 → 纯日历占位，丢；
        # 有实质内容的早报（如「早报：标普500创阶段新低」）保留。
        if _CALENDAR_PREFIX_RE.search(title):
            body = _BULLET_PREFIX_RE.sub("", title).strip()
            if len(body) < 8 and not any(k in title for k in _EVENT_HINT_KWS):
                continue
        normalized = _normalize_title(title)
        topics = _title_topics(title)
        hit = None
        for entry in seen:
            if normalized == entry[0] or (topics and entry[1] & topics):
                hit = entry
                break
        if hit is None:
            seen.append([normalized, topics, item])
            out.append(item)
        elif len(normalized) > len(hit[0]):
            old_item = hit[2]
            hit[0], hit[2] = normalized, item  # 同事件换更具体的标题
            for i, existing in enumerate(out):
                if existing is old_item:
                    out[i] = item
    return out


def _rank_stamp(item):
    """跨天排序戳：优先完整 published_at（含日期），回落 HH:MM/date。"""
    pub = str(item.get("published_at") or "")
    if len(pub) >= 16:
        return pub[:16]
    return str(item.get("time") or item.get("date") or "")


def _rank_and_cap(items, cap):
    """组内排序：按完整时间戳跨天降序（时间新在前），截 cap。
    「优先词」只用于筛选保留与丢弃取舍，不做排序前置——窗口跨两天时
    优先词前置会把昨天下午的高优条排到今天早上之前（时间倒挂）。"""
    timed = sorted(items, key=lambda i: _rank_stamp(i), reverse=True)
    return timed[:cap]


def _split_and_cap(combined):
    """分区（写死）：先判国内强制词（官方数据来源，即使同时含原油/黄金/美元
    也是国内数据），再判海外词；一条只进一侧。"""
    domestic, overseas_news = [], []
    for item in combined:
        title = item.get("title", "")
        if _is_stock_promo(title) or any(mark in title for mark in _DROP_PATTERNS):
            continue
        if any(k in title for k in _DOMESTIC_FORCE_KWS):
            domestic.append(item)
        elif _is_overseas_news(title):
            overseas_news.append(item)
        else:
            domestic.append(item)
    return (_rank_and_cap(_filter_news(domestic), _NEWS_DOMESTIC_MAX),
            _rank_and_cap(_filter_news(overseas_news), _NEWS_OVERSEAS_MAX))


# ---------------------------------------------------------------------------
# 要点 + 判断（纯函数，无模型也可运行）
# ---------------------------------------------------------------------------

_BULLET_PREFIX_RE = re.compile(r"^(【[^】]{1,8}】\s*|界面早报\s*[|｜]\s*)")
_HAWK_KWS = ("加息", "上调利率", "通胀高于预期", "美股大跌", "油价大涨")
_DOVE_KWS = ("降息", "下调利率", "宽松", "刺激", "美股大涨")
# 未落地词：标题同时含方向词与任一未落地词（悬念/预期/是否…）→ 该命中不计入冷/暖。
# 先判「未落地」再判「已落地」，避免「加息悬念待揭晓」被误判偏冷。
_STANCE_PENDING_KWS = ("悬念", "待揭晓", "待公布", "预期", "是否", "可能",
                       "或将", "料将", "观望", "等待决议")
_BULLET_MAX = 30


def compress_title(title, limit=_BULLET_MAX):
    """要点压缩：去「界面早报｜」「【早报】」类前缀，截到 limit 字。"""
    cleaned = _BULLET_PREFIX_RE.sub("", str(title or "").strip())
    return cleaned[:limit]


def _landed_hits(titles, keywords):
    """已落地命中：标题含方向词、且不含任一未落地词。返回命中的词表（去重、按词表序）。"""
    hits = []
    for kw in keywords:
        for title in titles:
            if kw in title and not any(pending in title for pending in _STANCE_PENDING_KWS):
                hits.append(kw)
                break
    return hits


def draft_morning_takeaway(news_items, overseas_items):
    """从要闻标题归纳要点与方向，返回 {bullets, stance, reason}。

    规则（先判未落地，再判已落地）：标题命中方向词但同含任一未落地词
    （悬念/待揭晓/待公布/预期/是否/可能/或将/料将/观望/等待决议）→ 不计入；
    已落地命中 加息/上调利率/通胀高于预期/美股大跌/油价大涨 → 偏冷；
    已落地命中 降息/下调利率/宽松/刺激/美股大涨 → 偏暖；两边都有或都没有 → 中性
    （都没有时可引用外盘涨跌家数）；新闻 0 条 → 资料不足。
    reason 只引用标题里出现的关键词或外盘涨跌家数。仅词面判断，无 LLM。
    """
    titles = [str(i.get("title") or "") for i in (news_items or []) if i.get("title")]
    hawk_hits = _landed_hits(titles, _HAWK_KWS)
    dove_hits = _landed_hits(titles, _DOVE_KWS)
    if not titles:
        stance, reason = "资料不足", "隔夜要闻为空"
    elif hawk_hits and dove_hits:
        stance = "中性"
        reason = f"「{hawk_hits[0]}」与「{dove_hits[0]}」信号并存"
    elif hawk_hits:
        stance = "偏冷"
        reason = f"要闻出现「{hawk_hits[0]}」"
    elif dove_hits:
        stance = "偏暖"
        reason = f"要闻出现「{dove_hits[0]}」"
    else:
        stance = "中性"
        changes = [x.get("change_pct") for x in (overseas_items or [])
                   if x.get("change_pct") is not None]
        ups = sum(1 for c in changes if c > 0)
        downs = sum(1 for c in changes if c < 0)
        # 判断原因只来自要闻；禁止指数点位/涨跌幅/几涨几跌表述
        reason = "要闻无明确方向信号"
    bullets = []
    for t in titles:
        b = compress_title(t)
        if b and b not in bullets:
            bullets.append(b)
        if len(bullets) >= 3:
            break
    return {"bullets": bullets, "stance": stance, "reason": reason}


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def _load_l1_names():
    """库内申万 L1 名集合（industry_taxonomy 为空时返回空集合 → 可能相关整段省略）。"""
    import sqlite3
    import os as _os

    db = _os.path.join(
        _os.environ.get("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading"),
        "research.db")
    try:
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT DISTINCT canonical_name FROM industry_taxonomy").fetchall()
        finally:
            conn.close()
        return {str(r[0]).strip() for r in rows if r[0]}
    except Exception:  # noqa: BLE001 - 对不上就不输出，绝不造行业名
        return set()


def _possible_l1s(items):
    """标题关键词 → 申万 L1（白名单内才输出），去重保序，最多 4 个。"""
    names = []
    for item in items:
        title = str(item.get("title") or "")
        for kw, l1 in _KEYWORD_L1_MAP:
            if kw in title and l1 in _SW_L1_WHITELIST and l1 not in names:
                names.append(l1)
                if len(names) >= 4:
                    return names
    return names


def _interpret_lines(items):
    """规则式解读：每条保留要闻找第一条命中规则的解读句，去重，最多 3 条。
    标题信息不足（无命中）→ 返回空列表（调用方输出「把握低」整句）。
    能源产量/发电量等数量型数据不配价格解读（宁可少一句，不要错配）。"""
    out = []
    seen = set()
    for item in items:
        title = str(item.get("title") or "")
        if any(k in title for k in ("原油", "天然气", "发电")) and \
                any(k in title for k in ("产量", "同比", "万吨", "亿立方米", "亿千瓦时")):
            text = "国内能源产量数据更新，先看供需数量，不直接等同于价格涨跌。"
            if text not in seen:
                out.append(text)
                seen.add(text)
            if len(out) >= 3:
                break
            continue
        for kw, text in _INTERPRET_RULES:
            if kw in title and text not in seen:
                out.append(text)
                seen.add(text)
                break
        if len(out) >= 3:
            break
    return out



def build_morning_macro_brief(
    as_of_date: str,
    as_of_time: str = "08:00+08:00",
    *,
    news: list[dict[str, Any]] | None = None,
    macro_shadow: bool | None = None,
    research_db_path: Path | str | None = None,
) -> dict[str, Any]:
    """构建早盘宏观速览 V4（新闻为主：国内 6 + 海外 6 + 解读 + 可能相关行业）。

    news=None 时只读 morning_flash_items 缓存表（08:00 主路径，不发网络请求）；
    直传 news 时同样过滤（软文/日历/同事件去重）。
    删除段：数字对照 / 中间价 / 环境 / 要点。判断原因只来自要闻。
    """
    if news is None:
        news = _load_flash_items(as_of_date, research_db_path=research_db_path)
    # 直传的 news 也过一遍过滤（软文/日历/同事件），保证判断与版式同源
    news = _filter_news(_dedup_titles(list(news)))
    if macro_shadow is None:
        try:
            from src.investment_research_supervisor.next_session_outlook import load_forecast

            forecast = load_forecast(as_of_date)
            macro_shadow = bool(forecast and forecast.get("status") == "SHADOW")
        except Exception:
            macro_shadow = False

    domestic, overseas = _split_and_cap(list(news))
    lines: list[str] = []

    # 【隔夜要闻】国内 6 / 海外 6；一侧 0 条省略小标题；两侧 0 条资料不足
    if not domestic and not overseas:
        lines.append("【隔夜要闻】资料不足。")
    else:
        lines.append("【隔夜要闻】")
        if domestic:
            lines.append("国内")
            for item in domestic:
                time_str = str(item.get("time") or item.get("date") or "")
                title = _assert_boss_safe(str(item.get("title") or ""))
                lines.append(f"- {time_str} {title}" if time_str else f"- {title}")
                url = str(item.get("url") or "")
                if url.startswith("http"):
                    lines.append(url)  # 链接行：每条要闻可点开
        if overseas:
            lines.append("海外")
            for item in overseas:
                time_str = str(item.get("time") or item.get("date") or "")
                title = _assert_boss_safe(str(item.get("title") or ""))
                lines.append(f"- {time_str} {title}" if time_str else f"- {title}")
                url = str(item.get("url") or "")
                if url.startswith("http"):
                    lines.append(url)

    # 【解读】最多 3 行，只解释上面出现过的要闻，不复读标题
    interpretations = _interpret_lines(domestic + overseas)
    if interpretations:
        lines.append("【解读】")
        for text in interpretations:
            lines.append(_assert_boss_safe("· " + text))
    else:
        lines.append("【解读】把握低，标题信息不足，不做方向判断。")

    # 【可能相关】申万 L1 白名单，≤4 个；对不上整段省略
    l1_names = _possible_l1s(domestic + overseas)
    if l1_names:
        lines.append(_assert_boss_safe(
            "可能相关：" + "、".join(l1_names) + "。以上不改今天 Focus 名单。"))

    # 【判断】原因只来自要闻；SHADOW → 把握低；禁止指数/涨跌表述
    takeaway = draft_morning_takeaway(news, [])
    low_conf = bool(macro_shadow) or takeaway["stance"] == "资料不足"
    lines.append(_assert_boss_safe(
        f"【判断】{takeaway['stance']}" + ("（把握低）" if low_conf else "") +
        f"。{_assert_boss_safe(takeaway['reason'])}。"))

    return {"as_of_date": as_of_date, "as_of_time": as_of_time, "brief_type": BRIEF_TYPE,
            "text": "\n".join(lines),
            "items": {"domestic": domestic, "overseas": overseas, "news": news},
            "price_as_of": None, "narrative_as_of": None,
            "sentiment": takeaway["stance"], "macro_shadow": macro_shadow}


def render_feishu_card(brief):
    as_of_date = brief.get("as_of_date") or ""
    elements = [{"tag": "markdown", "content": ln}
                for ln in str(brief.get("text") or "").splitlines() if ln.strip()]
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "turquoise",
                       "title": {"tag": "plain_text", "content": f"早盘宏观速览 · {as_of_date}"}},
            "elements": elements or [{"tag": "markdown", "content": "资料不足"}]}


def morning_gate_decision(usable_overseas, news_count):
    """发送闸（纯判定）：有效要闻 ≥1 才放行；指数个数不再参与。
    参数 usable_overseas 保留仅为兼容旧签名，不参与判定。"""
    if news_count < 1:
        return {"ok": False, "status": "SKIPPED_EMPTY",
                "error": "隔夜要闻为空；网关 0 次调用，拒绝发送空卡"}
    return {"ok": True, "status": "PASS"}


# ---------------------------------------------------------------------------
# 快讯缓存表：交易日 15:05 → 次日 08:00 每 ≥10 分钟拉取入表；08:00 构建只读本表。
# 周末全天继续拉（覆盖周五 15:05 → 周一 08:00 的隔夜窗口）。
# ---------------------------------------------------------------------------

_FLASH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS morning_flash_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    region TEXT,
    raw_key TEXT UNIQUE,
    fetched_at TEXT,
    as_of_date TEXT
)
"""


def _flash_conn(research_db_path=None):
    import sqlite3

    if research_db_path is None:
        from src.config.paths import get_runtime_root

        research_db_path = get_runtime_root() / "research.db"
    conn = sqlite3.connect(str(research_db_path))
    conn.execute(_FLASH_TABLE_SQL)
    return conn


def ingest_morning_flash(now=None, *, research_db_path=None,
                         em_fetcher=None, rss_fetcher=None) -> dict:
    """拉一次东财/RSS 快讯入 morning_flash_items（只补缺 upsert，同 url 0 新增）。
    无 url 的条丢弃并记日志；单源失败记 errors 不抛；末尾清扫 14 天前旧行。"""
    now_dt = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    rows = []
    errors = 0
    try:
        rows.extend(fetch_eastmoney_flash(fetcher=em_fetcher, max_items=50))
    except Exception as exc:  # noqa: BLE001 - 单源失败不崩轮次
        errors += 1
        logger.warning("morning flash: eastmoney fetch failed: %s", exc)
    try:
        rows.extend(fetch_rss_items(now_dt - timedelta(hours=24), now_dt, fetcher=rss_fetcher))
    except Exception as exc:  # noqa: BLE001
        errors += 1
        logger.warning("morning flash: rss fetch failed: %s", exc)
    fetched = len(rows)
    inserted = skipped = 0
    fetched_at = now_dt.isoformat()
    as_of_date = now_dt.date().isoformat()
    conn = _flash_conn(research_db_path)
    try:
        with conn:
            conn.execute(_FLASH_TABLE_SQL)
            for item in rows:
                url = str(item.get("url") or "").strip()
                if not url.startswith("http"):
                    skipped += 1
                    logger.info("morning flash: drop item without url: %s",
                                str(item.get("title"))[:40])
                    continue
                published = item.get("published")
                published_at = published.isoformat() if isinstance(published, datetime) else None
                raw_key = f"{item.get('source') or ''}|{url}"
                cur = conn.execute(
                    "INSERT OR IGNORE INTO morning_flash_items"
                    "(source, title, url, published_at, region, raw_key, fetched_at, as_of_date)"
                    " VALUES(?,?,?,?,NULL,?,?,?)",
                    (str(item.get("source") or ""), str(item.get("title") or ""), url,
                     published_at, raw_key, fetched_at, as_of_date))
                if cur.rowcount:
                    inserted += 1
                else:
                    skipped += 1
            cutoff = (now_dt - timedelta(days=14)).date().isoformat()
            conn.execute("DELETE FROM morning_flash_items WHERE as_of_date < ?", (cutoff,))
    finally:
        conn.close()
    return {"fetched": fetched, "inserted": inserted, "skipped": skipped, "errors": errors}


_COLLECT_START = time(15, 5)
_COLLECT_END = time(8, 0)


def in_collect_window(now=None) -> bool:
    """收集窗口：工作日 15:05 → 次日 08:00；周末全天继续（覆盖周末隔夜）。"""
    local = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    t = local.time()
    if local.weekday() >= 5:
        return True  # 周末全天继续拉
    return t >= _COLLECT_START or t < _COLLECT_END


class MorningFlashScheduler:
    """60s 看钟；收集窗口内且距上次 ≥10 分钟 → ingest 一次。单线程。"""

    def __init__(self, *, check_interval_s: float = 60.0, min_gap_s: float = 600.0) -> None:
        self.check_interval_s = float(check_interval_s)
        self.min_gap_s = float(min_gap_s)
        self._last = None
        self._stop = threading.Event()
        self._thread = None

    def should_collect(self, now: datetime | None = None) -> bool:
        now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        if not in_collect_window(now):
            return False
        if self._last is not None and (now - self._last).total_seconds() < self.min_gap_s:
            return False
        return True

    def tick(self, now: datetime | None = None) -> dict:
        now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        if not self.should_collect(now):
            return {"status": "SKIP", "now": now.isoformat()}
        self._last = now
        result = ingest_morning_flash(now=now)
        return {"status": "INGESTED", **result}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="morning-flash-scheduler")
        self._thread.start()
        logger.info("MorningFlashScheduler started (check=%ss, gap=%ss)",
                    self.check_interval_s, self.min_gap_s)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.check_interval_s):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程永不退出
                logger.exception("morning flash scheduler tick failed")


_flash_scheduler = None


def start_morning_flash_scheduler() -> dict:
    """backend 启动挂点：HZ_MORNING_MACRO=on 才与 08:00 发送一起拉起，默认 off。"""
    global _flash_scheduler
    if not morning_macro_enabled():
        return {"started": False, "reason": "HZ_MORNING_MACRO!=on"}
    if _flash_scheduler is None:
        _flash_scheduler = MorningFlashScheduler()
    _flash_scheduler.start()
    return {"started": True}


def _previous_trading_date(as_of_date: str) -> str:
    """上一交易日（本地日历优先；超出覆盖按工作日回退，最多回看 10 天）。"""
    from datetime import date as _date

    d = _date.fromisoformat(as_of_date) - timedelta(days=1)
    for _ in range(10):
        if _is_trading_day(d.isoformat()):
            return d.isoformat()
        d -= timedelta(days=1)
    return d.isoformat()


def _load_flash_items(as_of_date: str, *, research_db_path=None, limit: int = 200) -> list:
    """08:00 构建只读缓存表：published_at ∈ (上一交易日 15:00, 当日 08:00]。
    必须有 url 才返回（无链接不进卡）；日期级（无时分）从宽：上一交易日或当日均可。"""
    prev_date = _previous_trading_date(as_of_date)
    conn = _flash_conn(research_db_path)
    try:
        rows = conn.execute(
            "SELECT source, title, url, published_at FROM morning_flash_items "
            "WHERE url != '' ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
    out = []
    seen_url = set()
    for source, title, url, published_at in rows:
        pub = str(published_at or "")
        if len(pub) >= 16:  # 有时分：完整窗口过滤
            day, hhmm = pub[:10], pub[11:16]
            ok = ((prev_date < day < as_of_date)
                  or (day == prev_date and hhmm >= "15:00")
                  or (day == as_of_date and hhmm <= "08:00"))
        else:  # 日期级（未知时分）：从宽——上一交易日或当日均可
            day = pub[:10]
            ok = day in (prev_date, as_of_date)
        if not ok or url in seen_url:
            continue
        seen_url.add(url)
        out.append({"source": source, "title": title, "url": url,
                    "time": hhmm if len(pub) >= 16 else "", "published_at": pub})
    return out


def send_morning_macro_brief(as_of_date, *, research_db_path=None, force=False):
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings, ShortLivedFeishuBriefSender)
    from src.investment_research_supervisor.daily_brief_store import (
        InvestmentResearchDailyBriefRepository)

    repository = InvestmentResearchDailyBriefRepository(research_db_path)
    settings = DailyBriefNotificationSettings.from_channels_config()
    try:
        existing = repository.delivery(
            research_as_of=as_of_date, channel=DELIVERY_CHANNEL, target_id=settings.target_id)
        if existing and existing.get("status") == "SENT" and not force:
            return {"status": "REUSED", "as_of_date": as_of_date, "delivery": existing}
        if not settings.enabled or not settings.target_id:
            return {"status": "SKIPPED_DISABLED", "as_of_date": as_of_date}

        brief = build_morning_macro_brief(as_of_date, research_db_path=research_db_path)
        news_count = len((brief.get("items") or {}).get("news") or [])
        gate = morning_gate_decision(0, news_count)
        if not gate["ok"]:
            logger.error("morning macro brief skipped: empty content for %s; gateway calls = 0",
                         as_of_date)
            return {"status": gate["status"], "as_of_date": as_of_date,
                    "error": gate["error"]}
        card = render_feishu_card(brief)
        # 通道拆分（2026-09-14）：早盘用 Hermes 凭证的短命 client 直发，
        # 与收盘同源凭证但独立于 channels.feishu_supervisor.enabled；
        # 投递表仍记 channel=feishu_morning_macro（独立幂等键）。
        sender = ShortLivedFeishuBriefSender()
        try:
            message_id = sender.send_interactive_card(target_id=settings.target_id, card=card)
        except Exception as exc:
            logger.error("morning macro brief send failed: %s", exc)
            delivery = repository.record_delivery(
                research_as_of=as_of_date, channel=DELIVERY_CHANNEL,
                target_id=settings.target_id, status="FAILED",
                error=f"{type(exc).__name__}: {exc}"[:160], increment_attempt=True)
            return {"status": "FAILED", "as_of_date": as_of_date,
                    "error": f"{type(exc).__name__}: {exc}"[:160], "delivery": delivery}
        delivery = repository.record_delivery(
            research_as_of=as_of_date, channel=DELIVERY_CHANNEL,
            target_id=settings.target_id, status="SENT", message_id=message_id)
        return {"status": "SENT", "as_of_date": as_of_date, "text": brief["text"], "delivery": delivery}
    finally:
        repository.close()


class MorningMacroScheduler:
    def __init__(self):
        self.owner = f"morning-macro:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._handled_dates = set()

    def start(self):
        if not morning_macro_enabled():
            logger.info("morning macro scheduler not started: HZ_MORNING_MACRO != on")
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="morning-macro-scheduler", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self):
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("morning-macro scheduler tick failed")
            self._wake.wait(60)
            self._wake.clear()

    def tick(self, current=None, *, trading_day_check=None, sender=None):
        now = current or datetime.now(SHANGHAI).astimezone(SHANGHAI)
        as_of = now.date().isoformat()
        if now.weekday() >= 5:
            return {"status": "SKIPPED_WEEKEND", "date": as_of}
        if as_of in self._handled_dates:
            return {"status": "ALREADY_HANDLED", "date": as_of}
        if not _is_trading_day(as_of, trading_day_check):
            self._handled_dates.add(as_of)
            return {"status": "SKIPPED_NOT_TRADING_DAY", "date": as_of}
        due = datetime.combine(now.date(), MORNING_SEND_TIME, tzinfo=SHANGHAI)
        if now < due:
            return {"status": "BEFORE_SEND_TIME", "date": as_of}
        self._handled_dates.add(as_of)
        send_fn = sender or send_morning_macro_brief
        result = send_fn(as_of) or {}
        return {"status": str(result.get("status") or "UNKNOWN"), "date": as_of, "result": result}


def _is_trading_day(as_of_date, trading_day_check=None):
    if trading_day_check is not None:
        return trading_day_check(as_of_date)
    key = _compact(as_of_date)
    if not key:
        return True
    try:
        if datetime.strptime(key, "%Y%m%d").weekday() >= 5:
            return False  # 周末必然休市
    except ValueError:
        return True
    try:
        from src.tdx_data.store import TdxDataStore
        store = TdxDataStore()
        try:
            local_days = sorted(str(r.get("key") or "") for r in
                                store.list_records("trading_dates", limit=5000)["items"])
        finally:
            store.close()
    except Exception:
        return True
    if key in local_days:
        return True  # 日历内确认为交易日
    if local_days and key < local_days[-1]:
        return False  # 日历覆盖区间内的缺失日 = 休市（节假日）
    # 本地日历尚未覆盖 as_of（数据滞后/未来日）：周一~周五按交易日处理，
    # 否则交易日历每天只到昨日，08:00 的「今天」永远查不到 → 永不发送。
    return True


_scheduler = None
_scheduler_lock = threading.Lock()


def get_morning_macro_scheduler():
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = MorningMacroScheduler()
        return _scheduler


def start_morning_macro_scheduler():
    get_morning_macro_scheduler().start()


def stop_morning_macro_scheduler():
    if _scheduler is not None:
        _scheduler.stop()
