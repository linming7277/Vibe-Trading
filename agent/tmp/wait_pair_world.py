"""等待 09-08 EOD 产物就绪（v28 日报 READY + 09-09 bundle 存在 + 锁清空）后退出。"""
import sqlite3
import time
from datetime import datetime, timezone

DB = r"C:\Users\Administrator\.vibe-trading\research.db"
DEADLINE = "2026-09-08T11:30:00+00:00"  # 19:30 +08 兜底

while True:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    brief = conn.execute(
        "SELECT COUNT(*) FROM investment_research_daily_briefs "
        "WHERE research_as_of='2026-09-08' AND formula_version='daily-brief-v28' AND status='READY'"
    ).fetchone()[0]
    bundle = conn.execute(
        "SELECT COUNT(*) FROM forecast_input_bundles WHERE target_date='2026-09-09'"
    ).fetchone()[0]
    lock = conn.execute(
        "SELECT lock_owner FROM value_research_automation").fetchone()[0]
    conn.close()
    ready = brief and bundle and not lock
    print(f"{now} brief0908={brief} bundle0909={bundle} lock={lock} ready={ready}", flush=True)
    if ready:
        print("PAIR_WORLD_READY", flush=True)
        break
    if now > DEADLINE:
        print("WAIT_TIMEOUT", flush=True)
        break
    time.sleep(120)
