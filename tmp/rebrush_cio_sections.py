# -*- coding: utf-8 -*-
"""补刷：给已有 9/8 报告的公司重建，带上新增的 10a/05c 两章节。"""
import io
import sqlite3
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\AI\hzstock\agent")

import os

os.environ.setdefault("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading")

from src.cio_report.service import get_cio_report_service

AS_OF = "2026-09-08"
conn = sqlite3.connect(r"C:\Users\Administrator\.vibe-trading\research.db")
codes = [r[0].upper() for r in conn.execute(
    "SELECT DISTINCT stock_code FROM company_cio_research_reports WHERE research_as_of=?", (AS_OF,))]
conn.close()
print(f"补刷目标: {len(codes)} 家", flush=True)

service = get_cio_report_service()
done = skipped = failed = 0
t_start = time.time()
for idx, code in enumerate(codes, 1):
    t0 = time.time()
    try:
        latest = service.get_report("CN", code, as_of=AS_OF)
        sections = (latest or {}).get("sections") or []
        if len(sections) >= 19:
            skipped += 1
            continue
        result = service.build_report("CN", code, as_of=AS_OF)
        done += 1
        print(f"[{idx}/{len(codes)}] {code} 重建完成 synthesis={result.get('synthesis_source')} "
              f"用时{time.time() - t0:.0f}s", flush=True)
    except Exception as exc:
        failed += 1
        print(f"[{idx}/{len(codes)}] {code} 失败 {type(exc).__name__}: {str(exc)[:120]}", flush=True)
    time.sleep(2)
print(f"REBRUSH DONE: 重建{done} 跳过{skipped} 失败{failed} 用时{(time.time() - t_start) / 3600:.1f}h", flush=True)
