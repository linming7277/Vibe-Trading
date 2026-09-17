# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\AI\hzstock\agent")
import os
os.environ.setdefault("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading")
from src.cio_report.service import get_cio_report_service

result = get_cio_report_service().ensure_focus_tier_reports(as_of="2026-09-08")
print("RESULT:", result, flush=True)
