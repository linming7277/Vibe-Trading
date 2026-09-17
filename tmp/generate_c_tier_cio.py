# -*- coding: utf-8 -*-
"""C 档 CIO 报告顺序生成：一家一家来，可断点续跑（已有 9/8 报告自动跳过）。"""
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\AI\hzstock\agent")

import os

os.environ.setdefault("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading")

from src.cio_report.service import get_cio_report_service
from src.focus_selection import get_focus_selection_service

AS_OF = "2026-09-08"

focus = get_focus_selection_service().get_focus_selection(as_of=AS_OF) or {}
c_codes = [str(i.get("stock_code") or "").upper() for i in (focus.get("C") or [])]
print(f"C 档目标: {len(c_codes)} 家", flush=True)

service = get_cio_report_service()
t_start = time.time()
ok = failed = skipped = 0
for idx, code in enumerate(c_codes, 1):
    t0 = time.time()
    try:
        existing = service.get_report("CN", code, as_of=AS_OF)
        if existing and str(existing.get("research_as_of")) == AS_OF:
            skipped += 1
            print(f"[{idx}/{len(c_codes)}] {code} 已有 9/8 报告，跳过", flush=True)
            continue
        result = service.build_report("CN", code, as_of=AS_OF)
        ok += 1
        print(f"[{idx}/{len(c_codes)}] {code} {result.get('stock_name') or ''} 完成 "
              f"synthesis={result.get('synthesis_source')} 用时{time.time() - t0:.0f}s", flush=True)
    except Exception as exc:
        failed += 1
        print(f"[{idx}/{len(c_codes)}] {code} 失败 {type(exc).__name__}: {str(exc)[:120]}", flush=True)
elapsed = time.time() - t_start
print(f"BATCH DONE: 完成{ok} 跳过{skipped} 失败{failed} 总用时{elapsed/3600:.1f}h", flush=True)
