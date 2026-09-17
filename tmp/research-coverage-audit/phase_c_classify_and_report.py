# -*- coding: utf-8 -*-
"""Phase C: deterministic classification + document generation.

Value Line Company Research Coverage & Data Gap Priority Audit V1.
Reads phase_a.json / phase_b.json / phase_b2_wp_gaps.json only.
No DB, no service, no LLM, no network. Same inputs -> byte-identical outputs.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DOCS = REPO / "docs" / "research-coverage"
DOCS.mkdir(parents=True, exist_ok=True)

POOL_AS_OF = "2026-09-01"
QUALIFIED_CLOSE = "2026-09-01"
ANNUAL_DEADLINE = "2026-04-30"   # FY2025 annual must be disclosed by end of April
H1_THRESHOLD = "2026-07-01"      # H1 2026 should exist after June
PRIORITY_ORDER = ["BLOCKING", "HIGH_VALUE", "MEDIUM_VALUE", "LOW_VALUE", "IGNORE_FOR_NOW"]
FOCUS_RANK = {"A": 0, "B": 1, "C": 2}
ACTION_RANK = {"PRIORITY_RESEARCH": 0, "RISK_REVIEW": 1, "CONTINUE_OBSERVE": 2, "DEFER_RESEARCH": 3}
COST_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

MODULE_ENUMS = {
    "financial": {"READY", "PARTIAL", "MISSING", "STALE"},
    "financial_claims": {"READY", "PARTIAL", "NOT_RUN", "EMPTY_CLAIMS", "FAILED"},
    "latest_quarter": {"READY", "PARTIAL", "MISSING", "STALE"},
    "business": {"READY", "PARTIAL", "MISSING", "STALE"},
    "business_driver": {"READY", "PARTIAL", "MISSING"},
    "disclosure": {"READY", "PARTIAL", "MISSING", "STALE"},
    "risk": {"READY", "PARTIAL", "MISSING"},
    "value_trap": {"HIGH", "MEDIUM", "LOW", "UNKNOWN"},
    "thesis": {"READY", "PARTIAL", "MISSING"},
    "moat": {"READY", "PARTIAL", "MISSING"},
    "capital": {"READY", "PARTIAL", "MISSING"},
    "historical_valuation": {"READY", "PARTIAL", "INSUFFICIENT", "STALE"},
    "cio": {"FRESH", "PARTIALLY_STALE", "STALE", "MISSING"},
    "watchpoint": {"READY", "DATA_GAPS_ONLY", "EMPTY"},
    "normalized_earnings": {"READY", "NOT_APPLICABLE", "INSUFFICIENT"},
    "cycle_profit_scenario": {"READY", "PARTIAL", "NOT_APPLICABLE", "INSUFFICIENT"},
}


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


A = load("phase_a.json")
B = load("phase_b.json")
B2 = load("phase_b2_wp_gaps.json")
PC = A["per_company"]
PB = B["per_company"]
PB2 = B2["per_company"]


# --------------------------------------------------------------------------
# thesis template detection: pool-wide modal prefix of first invalid condition
# --------------------------------------------------------------------------
prefix_counts = Counter()
for c in PC.values():
    t = c.get("thesis")
    if t and t.get("invalid_conditions"):
        first = t["invalid_conditions"][0]
        text = first.get("condition") if isinstance(first, dict) else str(first)
        prefix_counts[str(text)[:8]] += 1
TEMPLATE_PREFIXES = {p for p, n in prefix_counts.items() if n >= 20}


def thesis_is_template(t: dict | None) -> bool:
    if not t or not t.get("invalid_conditions"):
        return False
    first = t["invalid_conditions"][0]
    text = str(first.get("condition") if isinstance(first, dict) else first)
    return str(text)[:8] in TEMPLATE_PREFIXES


# --------------------------------------------------------------------------
# module classification
# --------------------------------------------------------------------------
def classify_company(code: str) -> dict:
    c, e = PC[code], PB[code]
    fin, biz, dsc = c["financial"], c["business"], c["disclosure"]
    risk, prep, hv = c["risk"], c["preparation"], c["historical_valuation"]
    thesis, cio = c.get("thesis"), c.get("cio")
    wp, moat, cap = e["watchpoint"], e["moat"], e["capital"]
    norm, cyc = e["normalized"], e["cycle"]

    # ---- financial history / feature
    if not fin["has_snapshot"]:
        financial = "MISSING"
    else:
        annual_ok = (fin["annual_period_count"] or 0) >= 4 and bool(fin["latest_report_date"])
        base = "READY" if (fin["feature_status"] == "READY" and annual_ok) else "PARTIAL"
        newer_disclosure = str(dsc["latest_announcement"] or "0") > str(fin["latest_announcement_date"] or "0")
        financial = "STALE" if (newer_disclosure and base != "MISSING") else base

    # ---- financial analysis / claims
    st, n_claims = fin["analysis_status"], fin["claims_total"]
    if st in ("NOT_RUN", "CONFIGURATION_REQUIRED"):
        financial_claims = "NOT_RUN"
    elif st == "FAILED":
        financial_claims = "FAILED"
    elif n_claims == 0:
        financial_claims = "EMPTY_CLAIMS"
    else:
        financial_claims = "READY" if st == "COMPLETED" else "PARTIAL"

    # ---- latest quarter (from latest consumed report date)
    lrd = str(fin["latest_report_date"] or "")
    if not fin["has_snapshot"]:
        latest_quarter = "MISSING"
    elif lrd >= "2026-06-30":
        latest_quarter = "READY"
    elif lrd == "2026-03-31":
        latest_quarter = "PARTIAL"       # Q1 present, H1 not yet consumed
    elif lrd == "2025-12-31":
        latest_quarter = "MISSING"       # annual only
    else:
        latest_quarter = "STALE"

    # ---- business research
    if not biz["has_snapshot"]:
        business = "MISSING"
    else:
        base = "READY" if (
            biz["analysis_status"] == "COMPLETED"
            and biz["dq_status"] == "READY"
            and str(biz["main_business"]) not in ("", "UNKNOWN", "None")
            and biz["products_count"] > 0
            and biz["business_changes_count"] > 0
            and biz["claims_with_sources"] > 0
        ) else "PARTIAL"
        newer = str(dsc["latest_announcement"] or "0") > str(biz["data_as_of"] or "0")
        business = "STALE" if newer else base

    # ---- business driver evidence
    n_drv = c["business_driver"]["count"]
    n_dims = len(c["business_driver"]["dims"])
    business_driver = "READY" if (n_drv >= 5 and n_dims >= 2) else ("PARTIAL" if n_drv > 0 else "MISSING")

    # ---- disclosure
    if not dsc["doc_count"]:
        disclosure = "MISSING"
    elif str(dsc["latest_announcement"]) < ANNUAL_DEADLINE:
        disclosure = "STALE"      # FY2025 annual itself missing from local store
    elif str(dsc["latest_announcement"]) < H1_THRESHOLD:
        disclosure = "PARTIAL"    # H1 2026 not yet synced
    else:
        disclosure = "READY"

    # ---- risk
    if not risk["has_snapshot"]:
        risk_status = "MISSING"
    else:
        risk_status = "PARTIAL" if risk["overall_risk"] == "UNKNOWN" else "READY"

    value_trap = {
        "HIGH_TRAP_RISK": "HIGH", "MEDIUM_TRAP_RISK": "MEDIUM",
        "LOW_TRAP_RISK": "LOW", "NOT_APPLICABLE": "LOW", "UNKNOWN": "UNKNOWN", None: "UNKNOWN",
    }.get(risk["value_trap_risk"], "UNKNOWN")

    # ---- thesis quality
    if thesis is None:
        thesis_status = "MISSING"
    else:
        ok = (
            thesis["authority_status"] in ("AI_PROVISIONAL", "HUMAN_CONFIRMED", "LEGACY_UNVERIFIED")
            and thesis["core_thesis_len"] >= 50
            and thesis["invalid_count"] > 0
            and (thesis["evidence_count"] > 0 or thesis["supporting_count"] > 1)
            and thesis["metrics_count"] > 0
            and not thesis_is_template(thesis)
        )
        thesis_status = "READY" if ok else "PARTIAL"

    # ---- moat
    moat_n = c["moat_evidence"]["count"]
    if moat_n == 0 and moat["status"] in (None, "UNKNOWN"):
        moat_status = "MISSING"
    elif moat["status"] == "READY":
        moat_status = "READY"
    else:
        moat_status = "PARTIAL" if moat["status"] == "PARTIAL" else "MISSING"

    # ---- capital allocation
    cap_status = {"READY": "READY", "PARTIAL": "PARTIAL"}.get(cap["status"], "MISSING")
    if cap_status == "READY" and cap["core_known"] < 3:
        cap_status = "PARTIAL"

    # ---- historical valuation (do not relax the 250-observation rule)
    hvcs = hv.get("coverage_status") or "INSUFFICIENT"
    if hvcs == "INSUFFICIENT":
        historical_valuation = "INSUFFICIENT"
    else:
        lag = (int(QUALIFIED_CLOSE[:4]) - int(str(hv.get("last_date") or "0000")[:4])) * 372 + \
              (int(QUALIFIED_CLOSE[5:7]) - int(str(hv.get("last_date") or "0000-00")[5:7])) * 31 + \
              (int(QUALIFIED_CONFIRM_DAY) - int(str(hv.get("last_date") or "0000-00-00")[8:10]))
        historical_valuation = "STALE" if lag > 30 else ("READY" if hvcs == "READY" else "PARTIAL")

    # ---- cio
    if cio is None:
        cio_status = "MISSING"
    else:
        cio_status = cio["overall_freshness"] or "STALE"

    # ---- watchpoint
    if wp.get("error"):
        watchpoint_status = "EMPTY"
    elif wp["top_count"] > 0:
        watchpoint_status = "READY"
    elif wp["data_gap_count"] > 0:
        watchpoint_status = "DATA_GAPS_ONLY"
    else:
        watchpoint_status = "EMPTY"

    # ---- normalized earnings / cycle (applicability research only)
    if norm["status"] == "READY":
        normalized_earnings = "READY" if norm["applicability"] == "CYCLICAL_RELEVANT" else "NOT_APPLICABLE"
    else:
        normalized_earnings = "INSUFFICIENT"
    if cyc["status"] in ("READY", "PARTIAL"):
        cycle_profit_scenario = cyc["status"]
    elif cyc["status"] == "NOT_APPLICABLE":
        cycle_profit_scenario = "NOT_APPLICABLE"
    else:
        cycle_profit_scenario = "INSUFFICIENT"

    return {
        "financial": financial,
        "financial_claims": financial_claims,
        "latest_quarter": latest_quarter,
        "business": business,
        "business_driver": business_driver,
        "disclosure": disclosure,
        "risk": risk_status,
        "risk_level": risk["overall_risk"] or "UNKNOWN",
        "value_trap": value_trap,
        "thesis": thesis_status,
        "moat": moat_status,
        "capital": cap_status,
        "historical_valuation": historical_valuation,
        "cio": cio_status,
        "watchpoint": watchpoint_status,
        "normalized_earnings": normalized_earnings,
        "cycle_profit_scenario": cycle_profit_scenario,
    }


QUALIFIED_CONFIRM_DAY = QUALIFIED_CLOSE[8:10]

CLASSIFIED = {code: classify_company(code) for code in PC}
FOCUS = {code: (PC[code]["focus"] or "C") for code in PC}


# --------------------------------------------------------------------------
# gap generation
# --------------------------------------------------------------------------
def gap(company: str, module: str, gap_key: str, why: str, *, pri_by_focus: dict,
        cost: str, llm: str, network: str, deterministic: bool, action: str) -> dict:
    focus = FOCUS[company]
    priority = pri_by_focus.get(focus, pri_by_focus.get("*", "IGNORE_FOR_NOW"))
    return {
        "company": company, "stock_name": PC[company]["company_name"], "focus": focus,
        "primary_action": PC[company]["primary_action"] or "", "module": module, "gap": gap_key,
        "priority": priority, "why_it_matters": why, "estimated_cost": cost,
        "requires_llm": llm, "requires_network": network,
        "can_deterministic_fill": "YES" if deterministic else "NO",
        "recommended_action": action,
    }


GAPS: list[dict] = []
for code, m in CLASSIFIED.items():
    c, e = PC[code], PB[code]
    fin, biz, dsc, risk, thesis, hv = c["financial"], c["business"], c["disclosure"], c["risk"], c.get("thesis"), c["historical_valuation"]
    cyclical = e["normalized"]["applicability"] == "CYCLICAL_RELEVANT"

    if m["financial"] == "MISSING":
        GAPS.append(gap(code, "financial", "财务基础数据不存在", "老板无法做任何财务判断",
                        pri_by_focus={"A": "BLOCKING", "B": "BLOCKING", "C": "HIGH_VALUE"}, cost="HIGH",
                        llm="0", network="LOCAL_TDX_OR_CNINFO", deterministic=False,
                        action="先补 TDX/CNINFO 财务源，再重建快照"))
    elif m["financial"] == "STALE":
        GAPS.append(gap(code, "financial", "财务研究落后于本地最新披露(H1未消费)", "最新半年度数据已在本地披露库但财务研究仍停在 Q1",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "LOW_VALUE"}, cost="LOW",
                        llm="0", network="NONE_IF_LOCAL", deterministic=True,
                        action="deterministic 财务快照刷新(本地披露/TDX 已有数据)"))
    if m["latest_quarter"] == "MISSING":
        GAPS.append(gap(code, "latest_quarter", "最新季度缺失(仅年度)", "老板研究时缺少最新季度经营信息",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "MEDIUM_VALUE", "C": "LOW_VALUE"}, cost="MEDIUM",
                        llm="0", network="CNINFO_SYNC", deterministic=False,
                        action="先同步披露再刷新财务"))
    if m["financial_claims"] in ("NOT_RUN", "EMPTY_CLAIMS", "FAILED"):
        GAPS.append(gap(code, "financial_claims", "财务深度分析未运行(0 claims)", "只有财务特征没有可验证分析结论",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "LOW_VALUE"}, cost="MEDIUM",
                        llm="1", network="NONE", deterministic=False,
                        action="对 A/B 档执行 1 次受控 LLM 财务分析"))
    if m["business"] == "MISSING":
        GAPS.append(gap(code, "business", "经营研究缺失", "老板无法了解公司主营业务与变化",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "MEDIUM_VALUE", "C": "IGNORE_FOR_NOW"}, cost="MEDIUM",
                        llm="1", network="NONE_IF_LOCAL", deterministic=False,
                        action="用本地披露运行经营研究"))
    elif biz["claims_with_sources"] == 0:
        GAPS.append(gap(code, "business", "经营研究无带来源claims(研究浅)", "有主营描述但无可验证经营结论",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "LOW_VALUE"}, cost="MEDIUM",
                        llm="1", network="NONE_IF_LOCAL", deterministic=False,
                        action="基于已有披露重跑经营研究抽取"))
    if m["business_driver"] == "MISSING" and cyclical:
        GAPS.append(gap(code, "business_driver", "周期公司缺业务驱动证据", "周期属性公司的量价/份额证据缺失，难以判断周期位置",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "LOW_VALUE"}, cost="MEDIUM",
                        llm="1", network="NONE_IF_LOCAL", deterministic=False,
                        action="从已有定期披露抽取 segment/量价证据"))
    if m["disclosure"] == "STALE":
        GAPS.append(gap(code, "disclosure", "披露库落后(缺FY2025年报后材料/H1未同步)", "风险评估与经营研究输入不足",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "MEDIUM_VALUE"}, cost="MEDIUM",
                        llm="0", network="CNINFO_SYNC", deterministic=True,
                        action="CNINFO 增量同步年报/半年报"))
    elif m["disclosure"] == "PARTIAL":
        GAPS.append(gap(code, "disclosure", "H1 2026 未同步", "最新半年报不在本地库",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "MEDIUM_VALUE", "C": "MEDIUM_VALUE"}, cost="MEDIUM",
                        llm="0", network="CNINFO_SYNC", deterministic=True,
                        action="CNINFO 增量同步 2026 半年报"))
    if m["risk"] == "MISSING":
        GAPS.append(gap(code, "risk", "风险快照缺失", "无法计算风险等级",
                        pri_by_focus={"A": "BLOCKING", "B": "HIGH_VALUE", "C": "MEDIUM_VALUE"}, cost="LOW",
                        llm="0", network="NONE", deterministic=True,
                        action="deterministic 重建风险快照"))
    elif m["risk"] == "PARTIAL":
        GAPS.append(gap(code, "risk", f"风险等级UNKNOWN(输入根因见报告)", "UNKNOWN 不是低风险，影响 A/B 档判断",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "LOW_VALUE"}, cost="MEDIUM",
                        llm="0-1", network="NONE_IF_LOCAL", deterministic=True,
                        action="按根因补 business/thesis 输入后重建"))
    if m["value_trap"] == "HIGH" and (biz["claims_with_sources"] == 0 or m["thesis"] == "MISSING" or m["disclosure"] != "READY"):
        weak = []
        if biz["claims_with_sources"] == 0:
            weak.append("经营研究浅")
        if m["thesis"] == "MISSING":
            weak.append("无Thesis")
        if m["disclosure"] != "READY":
            weak.append("披露不足")
        GAPS.append(gap(code, "value_trap", f"高陷阱风险但证据基础弱({'+'.join(weak)})", "高风险结论建立在不足研究上",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "MEDIUM_VALUE"}, cost="MEDIUM",
                        llm="0-1", network="NONE_IF_LOCAL", deterministic=False,
                        action="先补齐经营/披露再复核陷阱结论"))
    if m["thesis"] == "MISSING":
        base_ok = biz["claims_with_sources"] > 0 and m["disclosure"] in ("READY", "PARTIAL")
        GAPS.append(gap(code, "thesis", "无Thesis" + ("(底层资料充分,可低成本起草)" if base_ok else "(底层证据不足,先补资料)"),
                        "老板缺少可跟踪的核心逻辑与失效条件",
                        pri_by_focus=({"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "IGNORE_FOR_NOW"} if base_ok
                                       else {"A": "HIGH_VALUE", "B": "MEDIUM_VALUE", "C": "IGNORE_FOR_NOW"}),
                        cost="MEDIUM" if base_ok else "HIGH", llm="1" if base_ok else "N/A",
                        network="NONE", deterministic=False,
                        action="AI_PROVISIONAL 草稿(仅 base_ok)" if base_ok else "先补经营/披露资料"))
    elif thesis_is_template(thesis):
        GAPS.append(gap(code, "thesis", "Thesis失效条件为模板(不可验证)", "invalid/supporting 全池同模板，无法真正跟踪逻辑失效",
                        pri_by_focus={"A": "HIGH_VALUE", "B": "HIGH_VALUE", "C": "LOW_VALUE"}, cost="MEDIUM",
                        llm="1", network="NONE", deterministic=False,
                        action="按公司具体业务重写可验证条件"))
    if m["moat"] == "MISSING":
        GAPS.append(gap(code, "moat", "无护城河证据/研究", "长期价值判断缺少护城河输入",
                        pri_by_focus={"A": "MEDIUM_VALUE", "B": "MEDIUM_VALUE", "C": "IGNORE_FOR_NOW"}, cost="MEDIUM",
                        llm="1", network="NONE_IF_LOCAL", deterministic=False,
                        action="A/B 档从已有披露抽取护城河证据"))
    if m["historical_valuation"] == "INSUFFICIENT":
        GAPS.append(gap(code, "historical_valuation", "历史估值序列不足250观测", "估值分位不可靠，影响当前便宜判断",
                        pri_by_focus={"A": "BLOCKING", "B": "HIGH_VALUE", "C": "LOW_VALUE"}, cost="LOW",
                        llm="0", network="LOCAL_TDX", deterministic=True,
                        action="本地 TDX 回填历史估值序列"))
    elif m["historical_valuation"] == "PARTIAL":
        GAPS.append(gap(code, "historical_valuation", "历史估值观测250-750(PARTIAL)", "分位可用但置信一般(不放宽250规则)",
                        pri_by_focus={"A": "MEDIUM_VALUE", "B": "MEDIUM_VALUE", "C": "LOW_VALUE"}, cost="LOW",
                        llm="0", network="LOCAL_TDX", deterministic=True,
                        action="继续回填至READY(>=750)"))
    if m["cio"] == "MISSING":
        deep_base = m["financial"] in ("READY", "STALE") and biz["claims_with_sources"] > 0 and m["risk"] == "READY" and m["thesis"] != "MISSING"
        GAPS.append(gap(code, "cio", "无CIO报告" + ("(底层研究完整,低成本可补)" if deep_base else "(底层研究缺失,先补源研究)"),
                        "CIO 是老板阅读入口；缺 CIO 时底层研究仍可经 Hermes 使用",
                        pri_by_focus=({"A": "MEDIUM_VALUE", "B": "MEDIUM_VALUE", "C": "LOW_VALUE"} if deep_base
                                       else {"A": "MEDIUM_VALUE", "B": "LOW_VALUE", "C": "IGNORE_FOR_NOW"}),
                        cost="LOW" if deep_base else "HIGH", llm="1" if deep_base else "N/A",
                        network="NONE", deterministic=False,
                        action="deterministic 重建+1次LLM综合" if deep_base else "先补源研究"))
    elif m["cio"] != "FRESH":
        GAPS.append(gap(code, "cio", f"CIO报告过期({m['cio']})", "报告事实可能落后于最新输入",
                        pri_by_focus={"A": "MEDIUM_VALUE", "B": "MEDIUM_VALUE", "C": "LOW_VALUE"}, cost="LOW",
                        llm="1", network="NONE", deterministic=False,
                        action="输入变更后重建"))
    if m["normalized_earnings"] == "INSUFFICIENT":
        GAPS.append(gap(code, "normalized_earnings", "正常化盈利不可计算(财务基础不足)", "周期公司盈利正常化缺失",
                        pri_by_focus={"*": "MEDIUM_VALUE"}, cost="MEDIUM", llm="0", network="NONE_IF_LOCAL",
                        deterministic=True, action="先修复财务基础"))
    if m["cycle_profit_scenario"] == "PARTIAL":
        GAPS.append(gap(code, "cycle_profit_scenario", "周期利润情景PARTIAL", "情景覆盖不完整",
                        pri_by_focus={"*": "LOW_VALUE"}, cost="LOW", llm="0", network="NONE",
                        deterministic=True, action="deterministic 补全情景"))

GAPS.sort(key=lambda g: (PRIORITY_ORDER.index(g["priority"]), FOCUS_RANK.get(g["focus"], 9),
                         ACTION_RANK.get(g["primary_action"], 9), COST_RANK.get(g["estimated_cost"], 9), g["company"]))

gap_by_company: dict[str, list] = defaultdict(list)
for g in GAPS:
    gap_by_company[g["company"]].append(g)


# --------------------------------------------------------------------------
# Focus A boss readiness
# --------------------------------------------------------------------------
def readiness(code: str, m: dict) -> dict:
    c, e = PC[code], PB[code]
    required_ok = (
        m["financial"] in ("READY", "STALE")
        and m["latest_quarter"] in ("READY", "PARTIAL")
        and m["risk"] != "MISSING"
        and m["business"] in ("READY", "PARTIAL", "STALE")
        and m["historical_valuation"] in ("READY", "PARTIAL")
        and m["watchpoint"] == "READY"
    )
    important_ok = (
        m["thesis"] != "MISSING"
        and m["disclosure"] in ("READY", "PARTIAL")
        and m["capital"] in ("READY", "PARTIAL")
    )
    if m["financial"] == "MISSING" or m["historical_valuation"] == "INSUFFICIENT" or m["risk"] == "MISSING":
        level = "BLOCKED_BY_DATA"
    elif not required_ok:
        level = "NEEDS_RESEARCH"
    elif not important_ok or m["financial"] == "STALE" or PC[code]["business"]["claims_with_sources"] == 0:
        level = "READY_WITH_CAUTIONS"
    else:
        level = "BOSS_READY"
    return {"required_ok": required_ok, "important_ok": important_ok, "level": level}


READINESS = {code: readiness(code, m) for code, m in CLASSIFIED.items() if FOCUS[code] == "A"}


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------
def dist(codes: list[str], field: str) -> Counter:
    return Counter(CLASSIFIED[c][field] for c in codes)


pool_codes = sorted(PC)
a_codes = sorted([c for c in pool_codes if FOCUS[c] == "A"])
b_codes = sorted([c for c in pool_codes if FOCUS[c] == "B"])
c_codes = sorted([c for c in pool_codes if FOCUS[c] == "C"])

STATS = {
    "pool": {f: dist(pool_codes, f) for f in MODULE_ENUMS},
    "A": {f: dist(a_codes, f) for f in MODULE_ENUMS},
    "B": {f: dist(b_codes, f) for f in MODULE_ENUMS},
    "C": {f: dist(c_codes, f) for f in MODULE_ENUMS},
}

# cross tabs
trap_high = [c for c in pool_codes if CLASSIFIED[c]["value_trap"] == "HIGH"]
cross = {
    "trap_high_x_business_shallow": sum(1 for c in trap_high if PC[c]["business"]["claims_with_sources"] == 0),
    "trap_high_x_thesis_missing": sum(1 for c in trap_high if CLASSIFIED[c]["thesis"] == "MISSING"),
    "trap_high_x_disclosure_not_ready": sum(1 for c in trap_high if CLASSIFIED[c]["disclosure"] != "READY"),
    "risk_high_x_business_shallow": sum(1 for c in pool_codes if CLASSIFIED[c]["risk_level"] == "HIGH" and PC[c]["business"]["claims_with_sources"] == 0),
    "risk_high_x_thesis_missing": sum(1 for c in pool_codes if CLASSIFIED[c]["risk_level"] == "HIGH" and CLASSIFIED[c]["thesis"] == "MISSING"),
    "risk_high_x_disclosure_not_ready": sum(1 for c in pool_codes if CLASSIFIED[c]["risk_level"] == "HIGH" and CLASSIFIED[c]["disclosure"] != "READY"),
    "risk_unknown_x_business_missing_snap": sum(1 for c in pool_codes if CLASSIFIED[c]["risk_level"] == "UNKNOWN" and PC[c]["risk"]["business_status"] == "MISSING"),
}

# root causes -------------------------------------------------------------
risk_unk = [c for c in pool_codes if CLASSIFIED[c]["risk_level"] == "UNKNOWN"]
risk_unk_root = Counter()
for c in risk_unk:
    r, p = PC[c]["risk"], PC[c]["preparation"]
    if not p["has_row"]:
        risk_unk_root["PREPARATION_NOT_RUN"] += 1
    elif r["financial_status"] != "READY":
        risk_unk_root["FINANCIAL_GAP"] += 1
    elif r["business_status"] == "MISSING":
        risk_unk_root["BUSINESS_GAP_MISSING"] += 1
    elif r["business_status"] != "READY":
        risk_unk_root["BUSINESS_GAP_PARTIAL"] += 1
    elif p["disclosure_status"] != "READY":
        risk_unk_root["DISCLOSURE_GAP"] += 1
    elif r["thesis_status"] != "READY":
        risk_unk_root["THESIS_GAP"] += 1
    else:
        risk_unk_root["OTHER_RULE_INDETERMINATE"] += 1

biz_root = Counter()
for c in pool_codes:
    b = PC[c]["business"]
    if not b["has_snapshot"]:
        biz_root["NO_SNAPSHOT"] += 1
    elif b["analysis_status"] == "NOT_RUN":
        biz_root["NOT_RUN"] += 1
    elif b["analysis_status"] == "FAILED":
        biz_root["LLM_FAILED"] += 1
    elif b["claims_total"] == 0:
        biz_root["PARTIAL_CLAIMS(0 claims)"] += 1
    elif b["claims_with_sources"] == 0:
        biz_root["PARTIAL_CLAIMS(无来源claims)"] += 1
    else:
        biz_root["OK(带来源claims>0)"] += 1

cio_stale_root = Counter()
for c in pool_codes:
    if CLASSIFIED[c]["cio"] in ("STALE", "PARTIALLY_STALE"):
        live = (B.get("cio_live_freshness") or {}).get(c) or {}
        stale_sections = live.get("stale_sections") or []
        for s in stale_sections:
            cio_stale_root[f"SECTION_STALE:{s}"] += 1
        if not stale_sections:
            cio_stale_root["INPUT_CHANGED_AFTER_REPORT"] += 1

wp_gap_root = B2["gap_categories"]

# freshness module table ----------------------------------------------------
fresh_mod = defaultdict(Counter)
for code in pool_codes:
    for mod in (PB[code]["freshness"].get("modules") or []):
        fresh_mod[mod.get("module")][mod.get("status")] += 1

# cheapest high value --------------------------------------------------------
cheapest = [g for g in GAPS if g["priority"] in ("BLOCKING", "HIGH_VALUE")
            and g["estimated_cost"] in ("LOW", "MEDIUM") and g["can_deterministic_fill"] == "YES"]

# batch 1 ---------------------------------------------------------------------
batch1: list[dict] = []
for g in GAPS:
    if g["priority"] in ("BLOCKING", "HIGH_VALUE") and g["estimated_cost"] in ("LOW", "MEDIUM"):
        if g["company"] not in {b["company"] for b in batch1} or len([b for b in batch1 if b["company"] == g["company"]]) < 3:
            batch1.append(g)
    if len(batch1) >= 20:
        break
batch1_companies = sorted({g["company"] for g in batch1})[:10]
batch1 = [g for g in batch1 if g["company"] in batch1_companies][:20]

# validation ------------------------------------------------------------------
errors = []
for code, m in CLASSIFIED.items():
    for f, val in m.items():
        enum = MODULE_ENUMS.get(f if f != "risk_level" else "risk_level")
        if enum and val not in enum:
            errors.append(f"{code}.{f}={val} not in enum")
if len(a_codes) != 10 or len(b_codes) != 20 or len(c_codes) != 172:
    errors.append(f"focus split {len(a_codes)}/{len(b_codes)}/{len(c_codes)} != 10/20/172")
if len(pool_codes) != 202:
    errors.append(f"pool size {len(pool_codes)} != 202")
for g in GAPS:
    if any(ch.isdigit() for ch in g["priority"]):
        errors.append("numeric priority found")
# determinism: recompute and compare
CLASSIFIED2 = {code: classify_company(code) for code in PC}
if json.dumps(CLASSIFIED, sort_keys=True) != json.dumps(CLASSIFIED2, sort_keys=True):
    errors.append("classification not deterministic")

# UNKNOWN never READY / NOT_APPLICABLE never MISSING by construction (enums are
# separate); re-assert:
for code, m in CLASSIFIED.items():
    if m["risk_level"] == "UNKNOWN" and m["risk"] == "READY":
        errors.append(f"{code}: risk UNKNOWN counted READY")

SUMMARY = {
    "research_as_of": POOL_AS_OF,
    "qualified_close": QUALIFIED_CLOSE,
    "active": len(pool_codes),
    "focus": {"A": len(a_codes), "B": len(b_codes), "C": len(c_codes)},
    "stats": {k: {f: dict(v) for f, v in mods.items()} for k, mods in STATS.items()},
    "readiness": {c: r["level"] for c, r in READINESS.items()},
    "readiness_dist": Counter(r["level"] for r in READINESS.values()),
    "gap_dist": Counter(g["priority"] for g in GAPS),
    "gap_module_dist": Counter(g["module"] for g in GAPS),
    "cross": cross,
    "risk_unknown_root": dict(risk_unk_root),
    "business_root": dict(biz_root),
    "cio_stale_root": dict(cio_stale_root),
    "wp_gap_root": wp_gap_root,
    "fresh_modules": {k: dict(v) for k, v in fresh_mod.items()},
    "cheapest_high_value": len(cheapest),
    "batch1_companies": batch1_companies,
    "batch1_gaps": len(batch1),
    "validation_errors": errors,
    "timings": {"phase_a_s": 1.9, "phase_b_watchpoint_batch_s": B["timings"]["watchpoint_batch_s"],
                "phase_b_services_s": B["timings"]["per_company_services_s"], "phase_b2_wp_s": B2["seconds"]},
}

(HERE / "phase_c_summary.json").write_text(
    json.dumps(SUMMARY, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

print(json.dumps({k: SUMMARY[k] for k in ("active", "focus", "gap_dist", "readiness_dist",
                                          "risk_unknown_root", "business_root", "validation_errors")},
                 ensure_ascii=False, indent=1))
print("batch1 companies:", batch1_companies)
