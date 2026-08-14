from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.research_workspace.schedules import ensure_workspace_schedules
from src.scheduled_research.store import ScheduledResearchJobStore


def test_workspace_schedules_are_idempotent_and_use_shanghai_wall_clock(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    assert ensure_workspace_schedules(store) == 3
    assert ensure_workspace_schedules(store) == 0
    jobs = store.load()
    morning = jobs["hengzhi-macro-morning"]
    close = jobs["hengzhi-cn-hk-close"]
    monitor = jobs["hengzhi-value-entry-monitor"]
    assert morning.schedule == "30 7 * * *"
    assert close.schedule == "30 16 * * 1-5"
    assert monitor.schedule == "45 16 * * 1-5"
    assert morning.timezone == close.timezone == monitor.timezone == "Asia/Shanghai"
    morning_time = datetime.fromtimestamp(morning.next_run_at / 1000, ZoneInfo("Asia/Shanghai"))
    close_time = datetime.fromtimestamp(close.next_run_at / 1000, ZoneInfo("Asia/Shanghai"))
    monitor_time = datetime.fromtimestamp(monitor.next_run_at / 1000, ZoneInfo("Asia/Shanghai"))
    assert (morning_time.hour, morning_time.minute) == (7, 30)
    assert (close_time.hour, close_time.minute) == (16, 30)
    assert (monitor_time.hour, monitor_time.minute) == (16, 45)
