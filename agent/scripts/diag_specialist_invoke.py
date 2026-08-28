# -*- coding: utf-8 -*-
"""Reproduce one risk-researcher dispatch call to surface the real failure."""
import os
import sys
import traceback

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.research_specialist_chat import get_research_specialist_chat_service  # noqa: E402

QUESTION = "围绕「深入分析一下600460」，排查财务与经营风险、低估陷阱风险与公司核心逻辑状态，给出可观察的证伪阈值与检查频率"

svc = get_research_specialist_chat_service()

company = svc._company(QUESTION, [])
print("company:", company)
as_of, error = svc._as_of(QUESTION)
print("as_of:", as_of, "error:", error)
if not company or not as_of:
    sys.exit("resolution failed; nothing to invoke")

config = svc._config("risk_researcher")
print("config enabled:", config.get("enabled"), "model:", config.get("model"), "base_url:", bool(config.get("base_url")))

context = svc._risk_agent_context(str(company["code"]), as_of)
print("context keys:", sorted(context))

# Surface the raw model output that breaks JSON parsing.
import src.research_tasks.service as rts  # noqa: E402
_orig_parse = rts._parse_json


def _loud_parse(content: str):
    try:
        return _orig_parse(content)
    except Exception:
        print("RAW CONTENT LEN:", len(content))
        print("RAW HEAD:", repr(content[:400]))
        print("RAW TAIL:", repr(content[-200:]))
        raise


rts._parse_json = _loud_parse

try:
    output = svc._invoke("risk_researcher", QUESTION, [], context)
    print("INVOKE OK")
    print("answer length:", len(str(output.get("answer") or "")))
    print("answer head:", str(output.get("answer"))[:300])
    print("data_gaps:", output.get("data_gaps"))
except Exception:
    print("INVOKE FAILED:")
    traceback.print_exc()
