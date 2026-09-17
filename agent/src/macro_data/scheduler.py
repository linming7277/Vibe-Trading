"""Daily forward-capture refresh for the cached macro series.

``run_refresh(mode="forward")`` pulls the bounded daily windows — the
treasury.gov yield curve, the FRED daily rates (DGS2/DGS10, WTI), chinamoney
and copper — into ``macro_series``.  Nothing in the product owned a cadence
for it: the 08:00 macro snapshot and the 16:45 EOD macro stages both read
whatever was last captured, so a skipped manual run showed up days later as
"美债/WTI 非 T-0".  This scheduler fires once each weekday morning before the
snapshot builds.  ``serve`` never loads the runtime dotenv, so the refresh
loads it first — without it ``FRED_API_KEY`` is invisible to the API process
and the FRED channel would report MISSING despite being configured.

Per-source failures are contained by ``run_refresh`` (source isolation,
fail-soft); a failed source must never block the scheduler thread.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")
REFRESH_TIME = time(7, 35)


def due_for_refresh(now: datetime, *, last_fire_date) -> bool:
    """工作日 07:35 触发一次；错过不补跑（下一个交易日槽位继续）。

    比较前必须截断秒/微秒：调度线程按 60s 相位唤醒，落在 07:35 分内的
    任意一秒（如 07:35:47）都应当触发。
    """
    local = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return False
    clock = local.time().replace(second=0, microsecond=0)
    if clock != REFRESH_TIME:
        return False
    return last_fire_date != local.date()


def run_forward_refresh() -> dict:
    """一次有界 forward 刷新；返回 run_refresh 的汇总 envelope。"""
    from src.providers.llm import _ensure_dotenv

    _ensure_dotenv()
    from src.macro_data import run_refresh

    summary = run_refresh(mode="forward")
    logger.info(
        "macro series forward refresh: overall=%s sources=%s",
        summary.get("overall"),
        [(item.get("source"), item.get("status")) for item in summary.get("sources") or []],
    )
    return summary


def run_domestic_refresh() -> dict[str, Any]:
    """国内宏观序列（SHIBOR/LPR/月度指标）经 AKShare 官方源刷新。

    与跨市场通路（treasury/FRED）分属两套抓取器；缺此环节时 SHIBOR 与
    月度指标停在最近一次手动刷新。fail-soft：单源失败不阻断调度。
    """
    from src.providers.llm import _ensure_dotenv

    _ensure_dotenv()
    from src.strategy_engines.macro_data import MacroDataService
    from src.strategy_engines.value_data_store import ValueDataStore

    result = MacroDataService(store=ValueDataStore()).refresh(date.today().isoformat())
    logger.info(
        "domestic macro refresh: status=%s rows=%s errors=%s",
        result.get("status"), result.get("series_rows"), len(result.get("errors") or []),
    )
    return result


def run_daily_refresh() -> dict[str, Any]:
    """07:35 槽位的完整日更：跨市场 forward + 国内官方源，两者相互独立。"""
    forward = run_forward_refresh()
    try:
        domestic = run_domestic_refresh()
    except Exception:  # noqa: BLE001 - 国内源失败不阻断整体
        logger.exception("domestic macro refresh failed")
        domestic = {"status": "FAILED"}
    return {"forward": forward, "domestic": domestic}


class MacroSeriesRefreshScheduler:
    """60s 看钟；工作日 07:35 → run_forward_refresh() 一次。单线程。"""

    def __init__(self, *, check_interval_s: float = 60.0) -> None:
        self.check_interval_s = float(check_interval_s)
        self._last_fire_date = None
        self._last_summary: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, now: datetime | None = None) -> dict:
        now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        if not due_for_refresh(now, last_fire_date=self._last_fire_date):
            return {"status": "SKIP", "now": now.isoformat()}
        self._last_fire_date = now.date()
        self._last_summary = run_daily_refresh()
        return {"status": "REFRESHED"}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="macro-series-refresh")
        self._thread.start()
        logger.info("MacroSeriesRefreshScheduler started (daily %s Asia/Shanghai)", REFRESH_TIME)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.check_interval_s):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程永不退出
                logger.exception("macro series refresh tick failed")


_scheduler: MacroSeriesRefreshScheduler | None = None


def start_macro_series_refresh_scheduler() -> dict:
    global _scheduler
    if _scheduler is None:
        _scheduler = MacroSeriesRefreshScheduler()
    _scheduler.start()
    return {"started": True}


def stop_macro_series_refresh_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
