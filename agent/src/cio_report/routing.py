"""Deterministic CIO-first intent classification (runtime routing fix §3/§4).

This is the single reference implementation of the routing contract that
SOUL.md and the investment-research-supervisor skill both state in natural
language.  Keeping it executable means the contract is testable, and a future
tool-policy guard can reuse it verbatim.

Semantics (mutually exclusive, checked in order):

REFRESH      only explicit redo wording containing 重新 (重新分析/重新评估/
             重新生成完整报告/用最新数据重新跑一次).  "深度" alone is NEVER
             a refresh trigger — 深度分析 is a READ, not a redo.
FULL_REPORT  深度/完整 + 分析/报告 reading requests.
SPECIALIST_* domain-specific deepening; the CIO section is read first and the
             specialist is a ≤1-time fallback only.
QUICK        ordinary company questions.
GENERAL      everything else (small talk, capability questions).
"""

from __future__ import annotations

import re

WATCHPOINT = "WATCHPOINT"
QUICK = "QUICK"
FULL_REPORT = "FULL_REPORT"
REFRESH = "REFRESH"
SPECIALIST_FINANCIAL = "SPECIALIST_FINANCIAL"
SPECIALIST_VALUATION = "SPECIALIST_VALUATION"
SPECIALIST_RISK = "SPECIALIST_RISK"
SPECIALIST_MACRO = "SPECIALIST_MACRO"
GENERAL = "GENERAL"

_REFRESH_RE = re.compile(r"重新(?:分析|深度分析|评估|研究|生成|跑)|重新生成完整报告|用最新数据重新跑")
_FULL_RE = re.compile(r"(?:深度|完整)(?:分析|解读|报告)|给我.*完整报告|完整报告")
_FINANCIAL_RE = re.compile(r"应收|存货|毛利|现金流|营收|净利|负债|财报|财务|资本开支")
_VALUATION_RE = re.compile(r"估值|合理价值|价值区间|PE|PB|市盈率|市净率|支撑|压力区")
_RISK_RE = re.compile(r"风险|低估陷阱|债务|违约|质押")
_MACRO_RE = re.compile(r"宏观|流动性|通胀|CPI|利率|降息|加息|社融|政策传导")
_SPECIALIST_DEEPEN_RE = re.compile(r"具体|详细|展开|为什么|怎么回事|怎么形成")
_WATCHPOINT_RE = re.compile(r"接下来|重点看什么|验证什么|最需要验证|盯什么|下一份财报|关注哪些指标|核心验证点")
_QUICK_RE = re.compile(r"怎么看|怎么样|怎样|简单说下|值得.{0,3}(?:关注|看|研究)|分析一下|研究一下|现在|目前")


def classify_company_question(question: str) -> str:
    text = str(question or "").strip()
    if not text:
        return GENERAL
    if _REFRESH_RE.search(text):
        return REFRESH
    if _FULL_RE.search(text):
        return FULL_REPORT
    if _WATCHPOINT_RE.search(text):
        return WATCHPOINT
    if _MACRO_RE.search(text) and not _QUICK_RE.search(text):
        return SPECIALIST_MACRO
    if _SPECIALIST_DEEPEN_RE.search(text):
        if _RISK_RE.search(text):
            return SPECIALIST_RISK
        if _VALUATION_RE.search(text):
            return SPECIALIST_VALUATION
        if _FINANCIAL_RE.search(text):
            return SPECIALIST_FINANCIAL
    if _QUICK_RE.search(text):
        return QUICK
    if _FINANCIAL_RE.search(text) or _VALUATION_RE.search(text) or _RISK_RE.search(text):
        return QUICK  # domain questions default to the brief/section read
    return GENERAL
