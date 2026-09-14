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
_NEWS_MAX = 5
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
_OVERSEAS_KW = ("美联储", "美国", "美元", "纳指", "标普", "道指", "欧洲", "欧央行",
                "日本", "日经", "英国", "原油", "黄金", "比特币", "全球")


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
    """合并三级新闻源 → 扁平列表（国内在前、海外在后；国内 ≤5、海外 ≤5 条）。

    时窗：as_of 前一日 15:00 → now（缺省当前北京时间）。
    优先级：东财快讯 > RSSHub 财联社/金十 > policy_events/公司公告（标题去重）。
    涨停/定增/推广类标题不进要闻。
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
    return _classify_and_cap(_dedup_titles(combined))


def _window_start_dt(as_of_date):
    from datetime import datetime as dt
    return dt.strptime(as_of_date, "%Y-%m-%d").replace(
        hour=15, minute=0, second=0, tzinfo=SHANGHAI) - timedelta(days=1)


def _now_dt():
    return datetime.now(SHANGHAI)


def _classify_and_cap(combined):
    domestic, overseas_news = [], []
    for item in combined:
        title = item.get("title", "")
        if _is_stock_promo(title) or any(mark in title for mark in _DROP_PATTERNS):
            continue
        if _is_overseas_news(title):
            overseas_news.append(item)
        else:
            domestic.append(item)
    return domestic[:5] + overseas_news[:5]


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
        reason = (f"要闻无方向信号，隔夜外盘 {ups} 涨 {downs} 跌"
                  if changes else "要闻与外盘均无方向信号")
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

def build_morning_macro_brief(
    as_of_date: str,
    as_of_time: str = "08:00+08:00",
    *,
    overseas: list[dict[str, Any]] | None = None,
    news: list[dict[str, Any]] | None = None,
    cny_mid: float | None = None,
    macro_shadow: bool | None = None,
    macro_environment: dict[str, Any] | None = None,
    tdx_home: Path | str | None = None,
    research_db_path: Path | str | None = None,
) -> dict[str, Any]:
    """构建早盘宏观速览（只读）。缺数写「数据不足」，绝不编造。

    news 格式：[{"time": "09:32", "source": "财联社", "title": "...",
                 "url": "http://..."}, ...]（组内新→旧；url 可省，省略不渲染链接行）。
    """
    if overseas is None:
        overseas = fetch_overseas_indices(as_of_date, tdx_home=tdx_home)
    if news is None:
        news = []
    if cny_mid is None:
        cny_mid = load_cny_mid(as_of_date, research_db_path=research_db_path)
    if macro_shadow is None:
        try:
            from src.investment_research_supervisor.next_session_outlook import load_forecast

            forecast = load_forecast(as_of_date)
            macro_shadow = bool(forecast and forecast.get("status") == "SHADOW")
        except Exception:
            macro_shadow = False

    lines: list[str] = []
    takeaway = draft_morning_takeaway(news, overseas)

    # 【隔夜要闻】HH:MM 来源 标题 + 有 url 才跟「链接: URL」行（不做行数裁剪）
    if news:
        lines.append("【隔夜要闻】")
        for item in news:
            time_str = item.get("time") or item.get("date") or ""
            lines.append(_assert_boss_safe(
                f"{time_str} {item.get('source', '')} {item.get('title', '')}"))
            link = _abs_http_url(item.get("url"))
            if link:
                lines.append(f"链接: {link}")
    else:
        lines.append("【隔夜要闻】资料不足。")

    # 【要点】最多 3 条，只从标题归纳
    if takeaway["bullets"]:
        lines.append("【要点】")
        for b in takeaway["bullets"]:
            lines.append(_assert_boss_safe("· " + b))

    # 【判断】SHADOW 或资料不足 → 含「把握低」
    low_conf = bool(macro_shadow) or takeaway["stance"] == "资料不足"
    judge = f"【判断】{takeaway['stance']}" + ("（把握低）" if low_conf else "") + \
        f"。{_assert_boss_safe(takeaway['reason'])}。"
    lines.append(judge)

    # 【环境】日报同源宏观快照一句（缺数半句自带于原句）；无快照整段省略
    if macro_environment is None:
        macro_environment = load_macro_environment_line(as_of_date)
    env_text = str((macro_environment or {}).get("text") or "").strip()
    if env_text:
        lines.append(_assert_boss_safe("【环境】" + env_text))

    if cny_mid is not None:
        lines.append(_assert_boss_safe(f"人民币中间价 {cny_mid:.4f}"))

    # 【数字对照】无指数则整段省略
    numbers = []
    for name in ("纳指", "纳指100", "恒生"):
        entry = next((x for x in overseas if x["name"] == name), None)
        if entry:
            sign = "+" if entry["change_pct"] >= 0 else ""
            numbers.append(f"{name} {entry['close']:.2f}({sign}{entry['change_pct'] * 100:.2f}%)")
    if numbers:
        lines.append("【数字对照】" + "，".join(numbers))

    return {"as_of_date": as_of_date, "as_of_time": as_of_time, "brief_type": BRIEF_TYPE,
            "text": "\n".join(lines), "items": {"overseas": overseas, "news": news},
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
    """发送闸（纯判定）：要闻 0 条且有效外盘 <2 → 拒发空卡。口径不变。"""
    if usable_overseas < 2 and not news_count:
        return {"ok": False, "status": "SKIPPED_EMPTY",
                "error": "隔夜外盘与国内要闻均为空；拒绝发送空卡"}
    return {"ok": True, "status": "PASS"}


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

        news = resolve_news(as_of_date, research_db_path=research_db_path)
        brief = build_morning_macro_brief(as_of_date, news=news,
                                          research_db_path=research_db_path)
        usable = [i for i in (brief.get("items") or {}).get("overseas") or [] if i.get("change_pct") is not None]
        news_count = len((brief.get("items") or {}).get("news") or [])
        gate = morning_gate_decision(len(usable), news_count)
        if not gate["ok"]:
            logger.error("morning macro brief skipped: empty content for %s", as_of_date)
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
