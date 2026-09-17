# -*- coding: utf-8 -*-
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\AI\hzstock\agent")
import os
os.environ.setdefault("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading")
from src.historical_valuation.service import HistoricalValuationService

t0 = time.time()
svc = HistoricalValuationService()
result = svc.backfill_current_l3_pool(as_of="2026-09-10", batch_size=50, throttle_seconds=0.1)
print(f"回填完成: {result.get('status')} 处理={result.get('processed')}/{result.get('total_count')} "
      f"用时{(time.time()-t0)/60:.0f}min", flush=True)
