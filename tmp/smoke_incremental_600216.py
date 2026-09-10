# -*- coding: utf-8 -*-
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\AI\hzstock\agent")

import os

os.environ.setdefault("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading")

from src.cio_report.service import get_cio_report_service

t0 = time.time()
result = get_cio_report_service().build_report(
    "CN", "600216.SH", as_of="2026-09-08", force_synthesis=True)
print(f"RESULT: status={result.get('status')} synthesis={result.get('synthesis_source')} "
      f"model={result.get('model_version')} 用时{time.time() - t0:.0f}s", flush=True)
