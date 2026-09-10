# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\AI\hzstock\agent")
import os
os.environ.setdefault("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading")
from src.cio_report.service import get_cio_report_service
result = get_cio_report_service().build_report("CN", "600216.SH", as_of="2026-09-08")
md = str(result.get("narrative_report_md") or "")
print(f"status={result.get('status')} synthesis={result.get('synthesis_source')} 长度={len(md)}")
print("含'未来三年利润预估明细':", "未来三年利润预估明细" in md)
print("含'财报里容易被忽略的信息':", "财报里容易被忽略的信息" in md)
print("含三年明细表行:', ", "| 谨慎 | 2028E |" in md)
