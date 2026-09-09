"""等待首个真实 V28 生产 EOD 完成（不触发任何 run，只读轮询）。"""
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB = r"C:\Users\Administrator\.vibe-trading\research.db"
DEADLINE = "2026-09-08T10:30:00+00:00"  # 18:30 +08 兜底（EOD 16:45 + 重试窗口）

while True:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT last_status, last_error, updated_at FROM value_research_automation LIMIT 1").fetchone()
    briefs = conn.execute(
        "SELECT id, formula_version, status, updated_at FROM investment_research_daily_briefs "
        "WHERE research_as_of='2026-09-08'").fetchall()
    conn.close()
    v28_today = any(r["formula_version"] == "daily-brief-v28" and r["status"] == "READY" for r in briefs)
    print(f"{now} last_status={row['last_status']} brief_0908_v28={v28_today}", flush=True)
    if v28_today and row["last_status"] == "completed":
        print("EOD_V28_COMPLETED", flush=True)
        break
    if now > DEADLINE:
        print("WAIT_TIMEOUT", flush=True)
        break
    time.sleep(300)
