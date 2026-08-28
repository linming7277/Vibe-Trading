# -*- coding: utf-8 -*-
"""Verify the supervisor research_lead summary path against the live endpoint."""
import os
import sys

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.investment_research_supervisor.dispatch import (  # noqa: E402
    DispatchOutcome, DispatchTask, _summarize_with_model,
)

outcomes = [
    DispatchOutcome(DispatchTask("financial_analyst", "q"), "营收五年从71.9亿增至93.6亿，但2026Q1经营现金流只有0.25亿，同比-93.8%，与净利润2.09亿明显背离。", "READY"),
    DispatchOutcome(DispatchTask("valuation_researcher", "q"), "当前价34.32，系统合理价值区间21.36-35.55，处于区间上沿；PE历史分位约20%。", "READY"),
    DispatchOutcome(DispatchTask("risk_researcher", "q"), "资产负债率从44.3%升至52.9%，有息负债率66.4%；资本开支16.3亿远超经营现金流3.0亿。", "READY"),
]
try:
    summary = _summarize_with_model("深入分析一下600460", outcomes)
    print("SUMMARY OK, length:", len(summary))
    print(summary[:400])
except Exception:
    import traceback
    print("SUMMARY FAILED:")
    traceback.print_exc()
