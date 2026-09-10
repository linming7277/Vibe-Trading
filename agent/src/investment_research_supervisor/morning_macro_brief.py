"""早盘宏观速览 V2（新闻为主）：工作日 08:00 Asia/Shanghai。

独立于收盘日报。发送闸默认 off（HZ_MORNING_MACRO）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

SHANGHAI = timezone(timedelta(hours=8))
MORNING_SEND_TIME = time(8, 0, 0)
BRIEF_TYPE = "morning_macro"
DELIVERY_CHANNEL = "feishu_morning_macro"
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


def fetch_rss_items(window_start, window_end, *, fetcher=None):
    if fetcher is None:
        def fetcher(url):
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "vibe-trading-morning/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
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
                items.append({"title": title, "source": source_name, "published": published})
        except Exception:
            logger.warning("rss source failed: %s", source_name)
    return items


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


_OVERSEAS_ORDER = ("标普", "纳指", "道指", "恒生", "纳指100")
_SYMBOL_BY_FILE = {"12#A_IXIC.day": "^IXIC", "12#A_NDX.day": "^NDX", "27#HSI.day": "^HSI"}


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


def symbol_of(filename):
    return _SYMBOL_BY_FILE.get(filename, filename)


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


def build_morning_macro_brief(as_of_date, as_of_time="08:00+08:00", *,
                              overseas=None, news=None, cny_mid=None,
                              macro_shadow=None, tdx_home=None, research_db_path=None):
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

    lines = []
    if news:
        lines.append("【隔夜要闻】")
        for item in news:
            time_str = item.get("time") or item.get("date") or ""
            lines.append(_assert_boss_safe(
                f"{time_str} {item.get('source', '')}｜{item.get('title', '')}"))
    else:
        lines.append("【隔夜要闻】资料不足。")

    numbers = []
    for name in ("纳指", "纳指100", "恒生"):
        entry = next((x for x in overseas if x["name"] == name), None)
        if entry:
            sign = "+" if entry["change_pct"] >= 0 else ""
            numbers.append(f"{name} {entry['close']:.2f}({sign}{entry['change_pct'] * 100:.2f}%)")
    if numbers:
        lines.append("【数字对照】" + "，".join(numbers))

    up = [x for x in overseas if x["change_pct"] > 0]
    if len(overseas) < 2:
        sentiment, reason = "资料不足", "隔夜外盘数据不足"
    else:
        avg = sum(x["change_pct"] for x in overseas) / len(overseas)
        sentiment = "偏暖" if avg > 0.001 else ("偏冷" if avg < -0.001 else "中性")
    if cny_mid is not None:
        lines.append(f"人民币中间价 {cny_mid:.4f}")
    env_line = f"【对今日环境】{sentiment}"
    if macro_shadow:
        env_line += "；宏观预测把握低"
    lines.append(env_line + "。")

    return {"as_of_date": as_of_date, "as_of_time": as_of_time, "brief_type": "morning_macro",
            "text": "\n".join(lines), "items": {"overseas": overseas, "news": news},
            "sentiment": sentiment, "macro_shadow": macro_shadow}


def render_feishu_card(brief):
    as_of_date = brief.get("as_of_date") or ""
    elements = [{"tag": "markdown", "content": ln}
                for ln in str(brief.get("text") or "").splitlines() if ln.strip()]
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "turquoise",
                       "title": {"tag": "plain_text", "content": f"早盘宏观速览 · {as_of_date}"}},
            "elements": elements or [{"tag": "markdown", "content": "资料不足"}]}


def send_morning_macro_brief(as_of_date, *, research_db_path=None, force=False):
    from src.investment_research_supervisor.daily_brief_notification_service import (
        DailyBriefNotificationSettings, ExistingFeishuSupervisorSender)
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
        usable = [i for i in (brief.get("items") or {}).get("overseas") or [] if i.get("change_pct") is not None]
        news_count = len((brief.get("items") or {}).get("news") or [])
        if len(usable) < 2 and not news_count:
            logger.error("morning macro brief skipped: empty content for %s", as_of_date)
            return {"status": "SKIPPED_EMPTY", "as_of_date": as_of_date,
                    "error": "隔夜外盘与国内要闻均为空；拒绝发送空卡"}
        card = render_feishu_card(brief)
        sender = ExistingFeishuSupervisorSender()
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
        send_result = send_fn(as_of)
        status_text = str((send_result or {}).get("status") or "UNKNOWN")
        return {"status": status_text, "date": as_of, "result": send_result}


def _is_trading_day(as_of_date, trading_day_check=None):
    if trading_day_check is not None:
        return trading_day_check(as_of_date)
    try:
        from src.tdx_data.store import TdxDataStore
        store = TdxDataStore()
        try:
            local_days = {str(r.get("key") or "") for r in
                          store.list_records("trading_dates", limit=5000)["items"]}
        finally:
            store.close()
    except Exception:
        return True
    return as_of_date.replace("-", "") in local_days


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
