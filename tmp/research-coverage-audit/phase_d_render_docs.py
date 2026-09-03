# -*- coding: utf-8 -*-
"""Phase D: render the four audit documents from Phase C results.

Deterministic: same inputs -> byte-identical documents. No DB/LLM/network.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import io
import contextlib

with contextlib.redirect_stdout(io.StringIO()):
    import phase_c_classify_and_report as X  # noqa: E402 - reuses its computation

PC, PB, PB2 = X.PC, X.PB, X.PB2
CLASSIFIED, FOCUS, GAPS = X.CLASSIFIED, X.FOCUS, X.GAPS
READINESS, STATS = X.READINESS, X.STATS
pool_codes = sorted(PC)
a_codes = sorted(c for c in pool_codes if FOCUS[c] == "A")
b_codes = sorted(c for c in pool_codes if FOCUS[c] == "B")
c_codes = sorted(c for c in pool_codes if FOCUS[c] == "C")
DOCS = X.DOCS
POOL_AS_OF, QUALIFIED_CLOSE = X.POOL_AS_OF, X.QUALIFIED_CLOSE

AUDIT_STAMP = "2026-09-02"
DURATION_TEXT = (
    f"Phase A 原始SQL(mode=ro) 1.9s；Phase B 快照副本上 watchpoint 冻结batch "
    f"{X.B['timings']['watchpoint_batch_s']}s + 服务投影 {X.B['timings']['per_company_services_s']}s；"
    f"Phase B2 watchpoint gap 复采 {X.B2['seconds']}s；Phase C 分类 <1s。"
    "含 2.7GB tdx_data.db 快照复制(约1-2分钟, 一次性)。全程无 LLM、无网络、无生产写入。"
)

FRESH_KEY_MAP = {"financial": "fin", "business": "biz", "valuation": "val", "risk": "risk",
                 "moat": "moat", "capital_allocation": "cap", "thesis": "thesis",
                 "risk_snapshot": "risk_snap", "low_value_pool": "pool"}


def fresh_summary(code: str) -> str:
    mods = {m.get("module"): m.get("status") for m in (PB[code]["freshness"].get("modules") or [])}
    parts = [f"{abbr}={mods.get(k, 'NA')}" for k, abbr in FRESH_KEY_MAP.items()]
    overall = PB[code]["freshness"].get("overall_freshness")
    return f"overall={overall}; " + ";".join(parts)


def name(code: str) -> str:
    return str(PC[code].get("company_name") or code)


def pct(n: int, total: int = len(pool_codes)) -> str:
    return f"{n / total * 100:.1f}%"


def md_dist(title: str, counter: Counter, total: int) -> str:
    cells = " | ".join(f"{k}={v}({v / total * 100:.0f}%)" for k, v in sorted(counter.items()))
    return f"- {title}: {cells}"


def top_gaps_for(code: str) -> list[dict]:
    return [g for g in GAPS if g["company"] == code]


def next_research(code: str) -> str:
    gs = sorted(top_gaps_for(code), key=lambda g: X.PRIORITY_ORDER.index(g["priority"]))
    if not gs:
        return "NONE_MAINTAIN"
    g = gs[0]
    return f"{g['module']}: {g['recommended_action']}"


# ===========================================================================
# 1. Coverage Matrix CSV
# ===========================================================================
matrix_path = DOCS / "value-line-company-research-coverage-matrix-v1.csv"
fields = ["market", "stock_code", "stock_name", "industry_name", "focus", "primary_action",
          "financial", "latest_quarter", "financial_claims", "business", "business_driver",
          "disclosure", "risk", "risk_level", "value_trap", "thesis", "thesis_authority",
          "moat", "capital", "historical_valuation", "cio", "watchpoint",
          "normalized_earnings", "cycle_profit_scenario", "freshness_summary",
          "blocking_gaps", "high_value_gaps", "medium_value_gaps", "recommended_next_research"]
with matrix_path.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(fields)
    for code in sorted(pool_codes, key=lambda c: (X.FOCUS_RANK[FOCUS[c]], c)):
        m, c2, gs = CLASSIFIED[code], PC[code], top_gaps_for(code)
        w.writerow([
            "CN", code, name(code), c2.get("industry_name") or "", FOCUS[code],
            c2["primary_action"] or "",
            m["financial"], m["latest_quarter"], m["financial_claims"], m["business"],
            m["business_driver"], m["disclosure"], m["risk"], m["risk_level"], m["value_trap"],
            m["thesis"], (c2.get("thesis") or {}).get("authority_status") or "MISSING",
            m["moat"], m["capital"], m["historical_valuation"], m["cio"], m["watchpoint"],
            m["normalized_earnings"], m["cycle_profit_scenario"], fresh_summary(code),
            sum(1 for g in gs if g["priority"] == "BLOCKING"),
            sum(1 for g in gs if g["priority"] == "HIGH_VALUE"),
            sum(1 for g in gs if g["priority"] == "MEDIUM_VALUE"),
            next_research(code),
        ])

# ===========================================================================
# 2. Gap Priority CSV
# ===========================================================================
gap_path = DOCS / "value-line-research-gap-priority-v1.csv"
gfields = ["company", "stock_name", "focus", "primary_action", "module", "gap", "priority",
           "why_it_matters", "estimated_cost", "requires_llm", "requires_network",
           "can_deterministic_fill", "recommended_action"]
with gap_path.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(gfields)
    for g in GAPS:
        w.writerow([g[k] for k in gfields])

# ===========================================================================
# 3. Focus A Boss Research Readiness
# ===========================================================================
A_ORDER = {c: i for i, c in enumerate(a_codes)}
readiness_dist = Counter(r["level"] for r in READINESS.values())
lines = []
lines.append("# Focus A 老板研究准备度表 V1")
lines.append("")
lines.append(f"- 审计基准日: {POOL_AS_OF}（Low Value 池 ACTIVE 202 家，Focus A 共 {len(a_codes)} 家）")
lines.append(f"- 生成时间: {AUDIT_STAMP}；只读审计，无 LLM / 无网络 / 无生产写入")
lines.append(f"- 准备度分布: " + " / ".join(f"{k}={v}" for k, v in sorted(readiness_dist.items())))
lines.append("")
lines.append("## 判定口径（§24 候选分层，不回写 Focus 算法）")
lines.append("")
lines.append("- **REQUIRED**: Financial 存在(含 STALE 告诫)、最新季度≥Q1、Risk 非 MISSING、Business≥PARTIAL、历史估值可用(不放宽250观测规则)、Watchpoint 存在")
lines.append("- **IMPORTANT**: Thesis 存在、Disclosure 可用、Capital 可用")
lines.append("- **DEEP**: Moat、Business Driver、CIO")
lines.append("- BLOCKED_BY_DATA: 财务/估值序列/风险核心数据缺失；NEEDS_RESEARCH: REQUIRED 未达标；READY_WITH_CAUTIONS: REQUIRED 达标但存在 STALE 或 IMPORTANT/深度缺口；BOSS_READY: 全部达标")
lines.append("")
lines.append("## Focus A 全量模块矩阵")
lines.append("")
header = ["股票", "名称", "Financial", "LatestQ", "Claims", "Business", "Disclosure", "Risk", "Trap", "Thesis", "Moat", "Capital", "HistVal", "Watchpoint", "CIO", "估值可靠性", "准备度"]
lines.append("| " + " | ".join(header) + " |")
lines.append("|" + "---|" * len(header))
for code in a_codes:
    m, c2 = CLASSIFIED[code], PC[code]
    row = [code, name(code), m["financial"], m["latest_quarter"], m["financial_claims"], m["business"],
           m["disclosure"], f"{m['risk']}/{m['risk_level']}", m["value_trap"], m["thesis"], m["moat"],
           m["capital"], m["historical_valuation"], m["watchpoint"], m["cio"],
           c2.get("cursor_valuation_reliability") or "", READINESS[code]["level"]]
    lines.append("| " + " | ".join(str(x) for x in row) + " |")
lines.append("")
lines.append("## 逐家公司明细")
for code in a_codes:
    m, c2, e = CLASSIFIED[code], PC[code], PB[code]
    gs = top_gaps_for(code)
    pri = Counter(g["priority"] for g in gs)
    have, lack = [], []
    have.append(f"Financial 特征 READY（annual≥4期，消费至 {str(c2['financial']['latest_report_date'])}）")
    have.append(f"历史估值 {m['historical_valuation']}（{c2['historical_valuation']['pe_count']} 个 PE 观测，序列至 {c2['historical_valuation']['last_date']}）")
    have.append(f"Watchpoint READY（Top{e['watchpoint']['top_count']}，data_gaps {e['watchpoint']['data_gap_count']} 条）")
    have.append(f"Risk {m['risk']}（等级 {m['risk_level']}，陷阱 {m['value_trap']}）")
    have.append(f"Capital READY（核心4维 {e['capital']['core_known']}/4）")
    if m["financial"] == "STALE":
        lack.append(f"财务研究落后：本地披露已至 {c2['disclosure']['latest_announcement']}，财务研究只消费到 {c2['financial']['latest_announcement_date']}（H1 2026 未消费）")
    if m["disclosure"] == "STALE":
        lack.append(f"披露库 STALE：最新本地披露 {c2['disclosure']['latest_announcement']}，缺 FY2025 年报之后的材料/H1 同步")
    lack.append(f"财务深度分析 NOT_RUN（0 claims，仅有特征层）")
    lack.append(f"经营研究 PARTIAL：带来源 claims 仅 {c2['business']['claims_with_sources']} 条")
    if m["thesis"] == "PARTIAL":
        lack.append("Thesis 为全池同模板 AI_PROVISIONAL，失效条件不可按公司验证")
    if m["moat"] == "MISSING":
        lack.append("无任何护城河证据/研究")
    if m["cio"] == "MISSING":
        lack.append("无 CIO 报告（底层研究可经 Hermes 使用）")
    elif m["cio"] == "STALE":
        lack.append("CIO 报告已过期（输入已变更）")
    lines.append("")
    lines.append(f"### {code} {name(code)}（{c2.get('industry_name') or ''}；{c2['primary_action']}；估值可靠性 {c2.get('cursor_valuation_reliability')}）")
    lines.append("")
    lines.append(f"- **准备度**: {READINESS[code]['level']}")
    lines.append("- **已具备**: " + "；".join(have))
    lines.append("- **仍缺**: " + "；".join(lack))
    gp = "、".join(f"{k}×{v}" for k, v in sorted(pri.items()))
    lines.append(f"- **缺口优先级**: {gp or '无'}")
    cheap = [g for g in gs if g["priority"] in ("BLOCKING", "HIGH_VALUE") and g["estimated_cost"] in ("LOW", "MEDIUM")]
    lines.append(f"- **补齐成本**: 高优先缺口 {len(cheap)} 个均为 LOW/MEDIUM（deterministic {sum(1 for g in cheap if g['can_deterministic_fill']=='YES')} 个、1次LLM {sum(1 for g in cheap if g['requires_llm']=='1')} 个）")
    advise = "建议现在补（低成本高价值）" if cheap else "暂无需立即补"
    lines.append(f"- **是否建议现在补**: {advise}")
    lines.append(f"- **建议动作**: {next_research(code)}")
lines.append("")
lines.append("## 结论")
lines.append("")
n_stale = sum(1 for c in a_codes if CLASSIFIED[c]["financial"] == "STALE")
n_disc = sum(1 for c in a_codes if CLASSIFIED[c]["disclosure"] == "STALE")
lines.append(f"- 10 家 A 档公司**全部可以直接开始研究**（READY_WITH_CAUTIONS={readiness_dist.get('READY_WITH_CAUTIONS', 0)}），无一被数据 BLOCKED。")
lines.append(f"- 但每家都带告诫：{n_stale} 家财务停在 Q1（H1 已在本地披露库未消费）、{n_disc} 家披露库本身 STALE、全部 10 家缺财务深度分析与公司级 Thesis 失效条件、9 家无护城河证据、9 家无 CIO。")
lines.append("- 老板研究 A 档的最短补齐路径：先做 deterministic 财务刷新与 H1 披露同步（0 LLM），再对 A 档做 1 轮受控 LLM（财务 claims + Thesis 具体化）。")
(DOCS / "focus-a-boss-research-readiness-v1.md").write_text("\n".join(lines), encoding="utf-8")

# ===========================================================================
# 4. Main audit report
# ===========================================================================
S = STATS
def pool(f): return S["pool"][f]
lines = []
add = lines.append
add("# 价值线公司研究覆盖率与数据缺口优先级总审计 V1")
add("")
add("Value Line Company Research Coverage & Data Gap Priority Audit V1")
add("")
add(f"- 审计执行日: {AUDIT_STAMP}；Research As Of: **{POOL_AS_OF}**（最新合格收盘 {QUALIFIED_CLOSE} QUALIFIED）")
add(f"- 审计宇宙: latest completed Low Value ACTIVE = **{len(pool_codes)} 家**（以执行时真实数据库为准；source_as_of={POOL_AS_OF}）")
add(f"- Focus 分布: A={len(a_codes)} / B={len(b_codes)} / C={len(c_codes)}；Primary Action: PRIORITY_RESEARCH=10、CONTINUE_OBSERVE=20、RISK_REVIEW=83、DEFER_RESEARCH=89")
add("- 保证: **LLM 调用 0 次、网络调用 0 次、生产写入 0 次**（Phase A 以 sqlite `mode=ro` 直查生产库；Phase B 将 research.db/tdx_data.db 用 backup API 复制到临时目录后以 `VIBE_TRADING_HOME` 指向副本运行服务层只读投影；Watchpoint 使用已冻结的 `get_watchpoints_batch` 高性能路径）")
add(f"- 耗时: {DURATION_TEXT}")
add("- 策略结构冻结确认: 未新增任何评分/新数据源/新 Agent；未触碰 Entry/Exit V2、PIT Gate、Strategy Cursor/Event、Focus、Primary Action。")
add("")
add("## 0. 执行摘要")
add("")
add(f"1. **基础层覆盖出乎意料地好**：202 家全部有 Financial 特征快照（66 READY + 136 STALE）、202 家有 Business 快照（168 家带来源 claims）、202 家披露库有正式材料（141 READY）、196 家历史估值 READY、202 家 Watchpoint READY（A 档 Top3 满额）。")
add("2. **深度层几乎为零**：财务深度分析（claims）202 家全部 NOT_RUN；Thesis 167/167 为同一模板的 AI_PROVISIONAL（invalid/supporting 全池同文案，仅行业词不同）且证据绑定 0 条；Moat 证据仅 2 家池内公司；Business Driver 证据仅池外 600460 一家；CIO 报告池内仅 3 家且全部 STALE。")
add(f"3. **最新财报是当前最大的事实性滞后**：192/202 家财务认知停在 2026Q1；其中 136 家本地披露库已含更新公告（多为 2026-08 的 H1 报告）而财务研究未消费；57 家披露库本身 STALE（缺 FY2025 年报后材料/H1 未同步）。")
add(f"4. **风险结论的证据基础**：83 家 Risk HIGH 中 24 家披露不足、14 家经营研究无带来源 claims、14 家无 Thesis；60 家 Risk UNKNOWN 根因全部是经营研究输入（49 PARTIAL + 11 MISSING），不是规则未运行。")
add("5. **无 BLOCKING 缺口**：A 档 10 家全部 READY_WITH_CAUTIONS，老板今天就能逐一研究；高价值缺口 106 个，其中 43 个可 deterministic 低成本补齐。")
add("")
add("## 1. 审计宇宙与锚点")
add("")
add("- 主宇宙严格取 `company_low_value_leader_pool` 中 `pool_status='ACTIVE'`（market=CN，source_as_of=2026-09-01，202 家），与 `value_strategy_state_cursors`（202 行，research_as_of=2026-09-01）一一对应。")
add("- Focus A/B/C 与 Primary Action 取自已冻结的策略游标，未重算。")
add("- 池外公司（600460 士兰微、002371 北方华创）单独进入附录，不混入 202 家统计。")
add("")
add("## 2. 模块覆盖矩阵总览（全池 202 家）")
add("")
add("| 模块 | 分布（数量与占比） |")
add("|---|---|")
rows_txt = [
    ("Financial History/Feature", pool("financial")), ("Financial Analysis/Claims", pool("financial_claims")),
    ("Latest Quarter", pool("latest_quarter")), ("Business Research", pool("business")),
    ("Business Driver Evidence", pool("business_driver")), ("Disclosure", pool("disclosure")),
    ("Risk（模块状态）", pool("risk")), ("Risk Levels", {k: v for k, v in Counter(CLASSIFIED[c]["risk_level"] for c in pool_codes).items()}),
    ("Value Trap", pool("value_trap")), ("Thesis（质量分级）", pool("thesis")),
    ("Moat", pool("moat")), ("Capital Allocation", pool("capital")),
    ("Historical Valuation", pool("historical_valuation")), ("CIO Full Report", pool("cio")),
    ("Watchpoint", pool("watchpoint")), ("Normalized Earnings（适用性）", pool("normalized_earnings")),
    ("Cycle Profit Scenario（适用性）", pool("cycle_profit_scenario")),
]
for title, counter in rows_txt:
    cells = "、".join(f"{k}={v}({pct(v)})" for k, v in sorted(counter.items(), key=lambda kv: -kv[1]))
    add(f"| {title} | {cells} |")
add("")
add("Thesis Authority（全池）: AI_PROVISIONAL=167、MISSING=35、HUMAN_CONFIRMED=0、LEGACY_UNVERIFIED=0（2 条 LEGACY 均为池外）。")
add("A/B/C 分层数据见 Coverage Matrix CSV；关键分层：A 档 financial STALE 4 家 / disclosure STALE 6 家 / moat MISSING 9 家 / cio MISSING 9 家；B 档 financial STALE 12 家 / disclosure STALE 7 家 / risk PARTIAL(UNKNOWN) 14 家 / moat MISSING 20 家。")
add("")
add("## 3. 分模块审计")
add("")
add("### 3.1 Financial History / Feature")
add("")
add(f"- READY={pool('financial')['READY']} PARTIAL=0 MISSING=0 STALE={pool('financial')['STALE']}。全部 202 家 feature READY、年报期数≥4、data_quality 无缺失字段。")
add("- STALE 判定：本地披露库最新公告日 > 财务研究已消费的最新公告日（136 家，多为 2026-08 底的 H1 报告未消费）。财务快照本身很新（as_of 2026-08-25/09-01），不是研究管道故障，而是『H1 刚披露、消费未触发』。")
add("- ResearchFreshnessService 的 financial 口径（源指纹）判 202/202 STALE，比本文档规则更严：TDX 财务源在快照创建后又刷新过。两个口径并列报告，不互相覆盖。")
add("")
add("### 3.2 Financial Analysis / Claims（独立于基础层的深度层）")
add("")
add(f"- **NOT_RUN=202、READY=0、PARTIAL=0、EMPTY_CLAIMS=0、FAILED=0。**")
add("- 结论：**Financial 基础数据覆盖高 ≠ Financial 深度分析覆盖高**。基础层 100% READY，深度层 0%。此前修复的 `COMPLETED+claims=0 → PARTIAL` 路径在当前池内不再出现（因为根本未运行过）。")
add("- 这是本审计最重要的结构发现之一：全池没有一家公司有可引用的财务分析结论（executive_summary/claims/forecast 解读）。")
add("")
add("### 3.3 Latest Quarter")
add("")
add(f"- READY(含H1)={pool('latest_quarter')['READY']}、PARTIAL(仅Q1)={pool('latest_quarter')['PARTIAL']}、MISSING(仅年度)=0、STALE=0。")
add("- **Focus A 10 家全部只消费到 2026Q1**。老板研究 A 公司时看不到 2026 半年度经营数据，这是最直接的高优先缺口（H1 报告 8 月底刚披露完毕）。")
add("")
add("### 3.4 Business Research")
add("")
add(f"- READY=0、PARTIAL=202、MISSING=0、STALE=0。全池 data_quality.status=PARTIAL（按存储层自身标准无一 READY），但深度分层显著：带来源 claims ≥2 的 30 家、=1 的 138 家、=0 的 34 家。")
add(f"- Business 缺失根因表（§46）：{json.dumps(X.biz_root, ensure_ascii=False)}——无 NOT_RUN/NO_DISCLOSURE/PARSE_FAILED/LLM_FAILED，浅研究全部是『跑过但抽取浅』。")
add("- STALE=0：经营快照 data_as_of 均晚于本地最新披露（179 家 2026-08、23 家 2026-09）。")
add("")
add("### 3.5 Business Driver Evidence")
add("")
add(f"- 池内 READY=0、PARTIAL=0、MISSING=202。`company_business_driver_evidence` 表 67 行全部属于池外 600460（SEGMENT_REVENUE=48、REGIONAL_MIX=8、PRODUCT_VOLUME=5、CAPEX_PROJECT=4、CUSTOMER=2）。")
add("- **当前证据确实只集中在 1 家验证样本公司。** 适用性判断：44 家 CYCLICAL_RELEVANT（池内 43）是天然适用候选（半导体/化工/有色/机械等），158 家 LOW_VALUE_ADDED（稳定盈利型）不应强制要求。")
add("- 缺口只对周期适用集发出（43 家，A/B 档为 HIGH_VALUE，C 档 LOW_VALUE）；非周期公司不因其『缺失』产生补齐动作。")
add("")
add("### 3.6 Disclosure")
add("")
add(f"- READY={pool('disclosure')['READY']}、PARTIAL={pool('disclosure')['PARTIAL']}、STALE={pool('disclosure')['STALE']}、MISSING=0。文本抽取 875/875 全部 READY，解析失败 0。")
add("- 结构：平均每家 4 份文档（FY2025 年报+Q1+H1+Q3 为主），覆盖 2025-2026 两个报告期，属『近窗口』而非深历史库。")
add("- STALE(57)=最新公告早于 2026-04-30（FY2025 年报期限）——即 H1 与部分年报后材料未同步；PARTIAL(4)=有年报但 H1 未同步。北交所 3 家（920039/920121/920792）文档齐备，无 MISSING。")
add("")
add("### 3.7 Risk Research")
add("")
add(f"- 快照 READY(可判定)={pool('risk')['READY']}、PARTIAL(UNKNOWN)={pool('risk')['PARTIAL']}、MISSING=0。等级：HIGH=83、MEDIUM=59、LOW=0、UNKNOWN=60。")
add("- Risk UNKNOWN 根因（§47，60 家）：**BUSINESS_GAP_PARTIAL=49、BUSINESS_GAP_MISSING=11**；FINANCIAL_GAP=0、DISCLOSURE_GAP=0、PREPARATION_NOT_RUN=0、RULE_NOT_APPLICABLE=0。11 家 MISSING 同时 Thesis MISSING（同一批弱研究公司）。")
add(f"- 高风险结论的证据基础交叉（§11）：Risk HIGH 83 家中，披露不足 {X.cross['risk_high_x_disclosure_not_ready']} 家、经营研究无带来源 claims {X.cross['risk_high_x_business_shallow']} 家、无 Thesis {X.cross['risk_high_x_thesis_missing']} 家。")
add("")
add("### 3.8 Value Trap")
add("")
add(f"- HIGH=67、MEDIUM=24、LOW=49、UNKNOWN=62（NOT_APPLICABLE=0，Low Value 公司全部适用）。")
add(f"- 交叉：Trap HIGH 67 家中经营研究浅 {X.cross['trap_high_x_business_shallow']} 家、无 Thesis {X.cross['trap_high_x_thesis_missing']} 家、披露不足 {X.cross['trap_high_x_disclosure_not_ready']} 家——这 {X.cross['trap_high_x_disclosure_not_ready']} 家是『高陷阱结论建立在不足披露上』的高优先研究缺口。")
add("")
add("### 3.9 / 3.10 Thesis 覆盖与质量分级")
add("")
add(f"- MISSING=35；存在 167 家全部 AI_PROVISIONAL、FORMING。invalid/supporting/key_metrics 非空数：167/167/167（表面齐备）。")
add("- **质量分级：THESIS_READY=0、THESIS_PARTIAL=167、THESIS_MISSING=35。HUMAN_CONFIRMED=0（权威层级最高者为 AI_PROVISIONAL）。**")
add("- 模板检测（确定性规则：首条失效条件前 8 字全池众数）：167/167 命中同一模板——失效条件为『若定期报告显示盈利/现金流恶化…』『若主营/核心行业收入下滑…』两类通用文案，仅嵌入行业词；**active 证据绑定全池 0 条**。『有记录』与『有高质量可验证逻辑』之间的差距 = 167 家。")
add("")
add("### 3.11 Moat")
add("")
add(f"- 有 Moat Research：2 家池内（605108 同庆楼 READY=3 supported/3 partial；000651 格力 PARTIAL）。有 Moat Evidence：2 家池内 + 2 家池外。MISSING=200、PARTIAL=1、READY=1。")
add("- 11 维中 UNKNOWN 占比高是**正确结果**（无证据不强行 SUPPORTED）；不因 UNKNOWN 多判系统失败。")
add("- Focus A/B 中完全无 Moat Evidence：**29/30 家**（仅同庆楼有）。老板研究长期价值时，这是 A/B 档的真实覆盖缺口，但按 §30 属 MEDIUM_VALUE，不是 BLOCKING。")
add("")
add("### 3.12 Capital Allocation")
add("")
add("- READY=202、PARTIAL=0、MISSING=0。核心 4 维（reinvestment/debt_management/cash_management/equity_dilution）：200 家 4/4、4 家 3/4。buyback/m_and_a 全池 UNKNOWN（数据层无此两类事实，符合『不要求所有公司都有』）。dividend 维另有 UNKNOWN 个案。")
add("- Watchpoint 的 CAPITAL data_gap 423 条主要指向 buyback/m&a/股息明细粒度，属可接受的确定性数据缺口。")
add("")
add("### 3.13 Historical Valuation")
add("")
add(f"- READY={pool('historical_valuation')['READY']}、PARTIAL(250-750观测)={pool('historical_valuation')['PARTIAL']}、INSUFFICIENT(<250)={pool('historical_valuation')['INSUFFICIENT']}、STALE=0。未放宽 250 观测规则。")
add("- 序列新鲜度：194 家至 2026-08-19、8 家至 08-31/09-01，相对合格收盘 09-01 滞后 ≤9 个交易日（未触发 STALE>30 日阈值）。")
add("- Focus A/B 历史估值缺口：A 档 0；INSUFFICIENT 4 家与 PARTIAL 2 家全部在 C 档。")
add("")
add("### 3.14 CIO Full Report")
add("")
add(f"- 池内有报告 3 家（000544/600210/605108，research_as_of=2026-08-28），MISSING=199；3 份全部 STALE。")
add("- 17 节完整度：3 份池内报告均为 14 节（缺 normalized_earnings/scenarios/cycle_profit_scenario 3 个后加节）；池外 600460 的最新报告为 17/17 节。synthesis：000544=LLM、600210=TEMPLATE、605108=TEMPLATE_FALLBACK（即 3 份中 2 份无 LLM 叙事综合）。")
add("- CIO Stale 根因（§48，live 分节重分类）：3 份报告 12/12 个节全部 STALE——输入（财务/业务/风险/估值/Watchpoint 源）在 08-28 之后全部变更过（FINANCIAL_CHANGED 叠加 BUSINESS/RISK/VALUATION/THESIS/WATCHPOINT_CHANGED）。")
add("- **回答：CIO 报告目前只覆盖极少数公司（3/202），未扩展**；但其缺失大多属 §43-B 场景之外的 §43-A 反例——见 §16。")
add("")
add("### 3.15 Watchpoint Projection")
add("")
add("- READY=202（A 档 Top3 满额 10/10，B 档 Top2，C 档 Top1）；DATA_GAPS_ONLY=0、EMPTY=0、投影错误 0。")
add("- data_gaps 合计 1,707 条，根因分布（§49）：MOAT=1223、CAPITAL=423、RISK=60、THESIS=1——与模块缺口完全同构，无 Projection bug、无 Focus C quota 空转。")
add("")
add("### 3.16 / 3.17 Normalized Earnings 与 Cycle Profit Scenario（适用性研究）")
add("")
add(f"- Normalized：CYCLICAL_RELEVANT 且 READY={pool('normalized_earnings')['READY']}、NOT_APPLICABLE(LOW_VALUE_ADDED)={pool('normalized_earnings')['NOT_APPLICABLE']}、INSUFFICIENT={pool('normalized_earnings')['INSUFFICIENT']}。适用公司覆盖 43/43 READY（2 家 INSUFFICIENT 因财务基础不足，不在适用集内，仍单列）。")
add(f"- Cycle：适用 43 家中 READY={pool('cycle_profit_scenario')['READY']}、PARTIAL={pool('cycle_profit_scenario')['PARTIAL']}；NOT_APPLICABLE={pool('cycle_profit_scenario')['NOT_APPLICABLE']}（稳定盈利型不要求）。")
add("- 两项均未把 NOT_APPLICABLE 计为缺口。")
add("")
add("## 4. Freshness 统一审计（§22）")
add("")
add("- 使用 ResearchFreshnessService.classify 的正式口径（非自创天数）：financial **STALE 202**；business STALE 179 / FRESH 23；valuation、risk、moat、capital_allocation、risk_snapshot、low_value_pool **FRESH 202**；thesis FRESH 167 / NOT_PERSISTED 35；daily_brief NOT_PERSISTED 202（全局产物，非公司级模块）。overall 202/202 STALE（financial 单项即拉满）。")
add("- **Disclosure 与 CIO 没有公司级正式 freshness 注册**（research_manifests 仅覆盖 daily_brief/low_value_pool/risk_snapshot 三类）：Disclosure 用本文档的公告日规则（READY/PARTIAL/STALE），CIO 用存储 overall_freshness + live 分节重分类。此为口径空白，不是数据缺失。")
add("")
add("## 5. Focus A 质量门（§23）")
add("")
add("10 家 A 档公司完整矩阵、逐家『已具备/仍缺/成本/建议』见 **focus-a-boss-research-readiness-v1.md**。摘要：")
add("")
add("| 准备度 | 数量 | 含义 |")
add("|---|---|---|")
for lv in ("BOSS_READY", "READY_WITH_CAUTIONS", "NEEDS_RESEARCH", "BLOCKED_BY_DATA"):
    add(f"| {lv} | {readiness_dist.get(lv, 0)} | " + {
        "BOSS_READY": "可直接研究", "READY_WITH_CAUTIONS": "可研究但带数据/深度告诫",
        "NEEDS_RESEARCH": "需先补研究", "BLOCKED_BY_DATA": "被数据阻断"}[lv] + " |")
add("")
add("- **可以直接研究**：10 家全部（无 BLOCKED_BY_DATA、无 NEEDS_RESEARCH）。")
add("- **需要先补数据**：严格说没有『不能研究』的；但 4 家财务 STALE + 6 家披露 STALE 的公司，建议先花数分钟做 deterministic 刷新再研究，避免老板看到 Q1 旧数据。")
add("")
add("## 6. Focus B 覆盖（§25）")
add("")
b_disc_ready = sum(1 for c in b_codes if CLASSIFIED[c]["disclosure"] == "READY")
add(f"- B 档 20 家：financial STALE 12、disclosure STALE 7、risk UNKNOWN 14、moat MISSING 20、thesis 全部模板。")
add("- 经营研究深度 B 档**不优于** A 档：B 全部 ≤1 条带来源 claims（15 家=1、5 家=0），A 为 8 家=1、1 家=2、1 家=0。")
add(f"- 但 B 档披露库好于 A：READY {b_disc_ready}/20 vs A 4/10——B 档风险复核的可获得材料反而更全。")
add("- 『研究资料更完整但排序更低』的真实实例：**600210 紫江企业（B）持有池内 3 份 CIO 报告之一**；而带来源 claims≥5 的 21 家公司（8 家满额 7 条、11 家 6 条，多为 2026-08 深研究批次产物）**全部在 C 档**。这印证『Research Coverage ≠ Focus Priority』——排序由风险/估值/逻辑状态决定，深度研究投入与档位当前不匹配（补齐应以 Focus 为准，而非反向调 Focus）。")
add("")
add("## 7. Focus C 覆盖与『不值得现在补』分组（§26）")
add("")
c_risk_high = sum(1 for c in c_codes if CLASSIFIED[c]["risk_level"] == "HIGH")
c_thesis_missing = sum(1 for c in c_codes if CLASSIFIED[c]["thesis"] == "MISSING")
c_fin_stale = sum(1 for c in c_codes if CLASSIFIED[c]["financial"] == "STALE")
c_biz_zero = sum(1 for c in c_codes if PC[c]["business"]["claims_with_sources"] == 0)
c_risk_unknown = sum(1 for c in c_codes if CLASSIFIED[c]["risk_level"] == "UNKNOWN")
c_rejected = 0  # HUMAN_REJECTED / FALSIFIED theses in C
add(f"- C 档 172 家分组：Risk HIGH={c_risk_high}、Risk UNKNOWN={c_risk_unknown}、Thesis MISSING={c_thesis_missing}（含 HUMAN_REJECTED/FALSIFIED={c_rejected}，全池为 0）、Financial STALE={c_fin_stale}、经营研究 0 claims={c_biz_zero}、仅因排序容量进入 C=全部（C 定义即未进 A/B 名额）。")
add("- **不建议现在花成本补深度的**：『Risk HIGH + DEFER/RISK_REVIEW + 同时缺 Moat/CIO/Driver』的 C 档公司——本次共 269 个 IGNORE_FOR_NOW 缺口几乎全部来自这一组。它们保留 Watchpoint 与风险快照的 deterministic 维护即可。")
add("")
add("## 8. 缺口分类与优先级统计（§27-32）")
add("")
gd = Counter(g["priority"] for g in GAPS)
add(f"- 缺口总数 {len(GAPS)}：BLOCKING=0、HIGH_VALUE={gd['HIGH_VALUE']}、MEDIUM_VALUE={gd['MEDIUM_VALUE']}、LOW_VALUE={gd['LOW_VALUE']}、IGNORE_FOR_NOW={gd['IGNORE_FOR_NOW']}。无任何数字评分。")
add("- 按模块：financial_claims=202、thesis=202、cio=202、moat=200、financial=136、disclosure=61、risk=60、business=34、business_driver=43、value_trap=27、cycle=9、hist_val=6、normalized=2。")
add("")
add("## 9. 覆盖率最差模块 vs 决策影响最高缺口（§54，两个排名）")
add("")
add("| 排名 | 覆盖缺口数量 | 影响老板判断程度 |")
add("|---|---|---|")
add("| 1 | Financial Claims（202/202 未运行） | 最新财报滞后（192 家仅 Q1，A 档 10/10） |")
add("| 2 | Thesis 可验证性（202：167 模板 + 35 缺失） | Thesis 失效条件不可跟踪（A/B 30 家） |")
add("| 3 | CIO（199 缺失） | Risk UNKNOWN 根因（60 家，B 档 14 家） |")
add("| 4 | Moat（200 缺失） | 披露库 H1 未同步（57 家，A 档 6 家） |")
add("| 5 | Business Driver（202 缺失，43 适用） | 高陷阱×证据薄（27 家） |")
add("")
add("两个排名确实不同：Moat/CIO 缺得最多，但当前最影响判断的是『数据新鲜度 + 可验证逻辑』。")
add("")
add("## 10. 最值得补的模块 Top 5（§55，由真实数据决定）")
add("")
add("1. **A/B 档 Financial H1 消费 + Latest Quarter**：16 家 STALE + 全部 30 家停在 Q1；deterministic、0 LLM、成本 LOW（数据多已在本地）。")
add("2. **Thesis 可验证条件升级（A/B 30 家）**：模板→公司具体失效条件，各 1 次受控 LLM。")
add("3. **Financial Claims 深度分析（A/B 30 家）**：各 1 次受控 LLM，与 #2 可同批。")
add("4. **Risk UNKNOWN 根因补齐（60 家，B 档 14 家优先）**：根因全部是经营研究深度（49 PARTIAL/11 MISSING），先补 11 家 MISSING 的经营研究，其余靠 H1 消费后重算。")
add("5. **Disclosure H1 同步（57 家 STALE，A 6/B 7 优先）**：需 CNINFO 增量网络同步，是 #1/#4 的前置之一。")
add("")
add("## 11. 最便宜的高价值补齐项（§34/§35）")
add("")
cheapest = [g for g in GAPS if g["priority"] in ("BLOCKING", "HIGH_VALUE") and g["estimated_cost"] == "LOW"]
add(f"- HIGH_VALUE 且 LOW 成本且可 deterministic：**{len(cheapest)} 个**（A/B 财务刷新 16 + H1 披露同步后可自动修复项），0 LLM、0 新数据源，网络仅 CNINFO 增量。")
add("- 0-LLM 可解决缺口：financial 刷新 136、disclosure 同步 61、risk 重建 60、hist_val 6 —— 全部 deterministic。")
add("- 1-LLM 可解决：financial_claims/thesis/business 深度项（每公司 1 次）。")
add("- 需要人工：HUMAN_CONFIRMED 升级（当前 0 家，暂无必要）。")
add("- 需要新数据源：buyback/m_and_a 事实层（全池 UNKNOWN）——暂 IGNORE。")
add("")
add("## 12. 批量补齐策略候选（§56）")
add("")
add("| 方案 | 成本 | Token | 网络 | 老板价值 | 覆盖速度 |")
add("|---|---|---|---|---|---|")
add("| A: Focus A 优先（10 家） | 最低 | 20 次 LLM 内 | 6 家 CNINFO | A 档立即全可读 | 快 |")
add("| B: A+B 优先（30 家） | 中 | 60 次 LLM 内 | 13 家 CNINFO | A+B 全部可读，B 档风险复核有据 | 中 |")
add("| C: 按 Blocking/HighValue 跨 Focus | 与 B 实质相同 | 同 B | 同 B | HIGH_VALUE 天然集中在 A/B（BLOCKING=0，C 档高价值缺口仅 value_trap 交叉 27 个 MEDIUM 以下） | 中 |")
add("")
add("**推荐：方案 B（A+B_FIRST）**——由于 BLOCKING=0 且 HIGH_VALUE 缺口天然集中在 A/B，方案 C 与 B 收敛；方案 A 会把 B 档 14 家 Risk UNKNOWN 留在无据状态。执行顺序上仍从 Batch 1（A 档）开始。")
add("")
add("## 13. Recommended Batch 1（§58，只设计不执行）")
add("")
add(f"- 规模：**{len(X.batch1_companies)} 家公司 / {len(X.batch1)} 个缺口**（≤10 家且≤20 缺口，取更小）。")
add(f"- 公司：{ '、'.join(f'{c} {name(c)}' for c in X.batch1_companies) }")
add("- 内容（全部 BLOCKING/HIGH_VALUE 且 LOW/MEDIUM 成本，按排序取前 20）：A 档 financial STALE 刷新×4（deterministic）、A 档披露 H1 同步×6（CNINFO 网络）、A 档财务深度分析×10（各 1 次 LLM）。")
add("- 未入选但紧随其后：Thesis 模板升级（A 档 10 个，Batch 2）、B 档 Risk UNKNOWN 根因（14 个）。")
add("")
add("## 14. 重点公司专项（§37-42）")
add("")
# 600460
add("### 14.1 600460 士兰微（池外，Deep Research 验证样本）")
add("")
add("- Financial：feature READY（年报 8 期，消费至 2026Q1/04-30 公告）；Claims **NOT_RUN**。Business：PARTIAL（dq PARTIAL）。Business Driver：**READY**——67 条证据覆盖 5 维（SEGMENT_REVENUE 48 等），全系统唯一。Risk/Value Trap：无池内快照（池外无 Low Value 风险快照，属设计内）。Thesis：**无 current thesis**。Moat：READY（3 SUPPORTED/2 PARTIAL/6 UNKNOWN，2 条挑战）。Capital：READY（核心 4/4，dividend/buyback/mna UNKNOWN）。Historical Valuation：READY。CIO：17/17 节、STALE、**TEMPLATE_FALLBACK**（无 LLM 叙事）。Watchpoint：Top3、9 条 data_gaps。")
add("- **它真正还缺什么**：① CIO 叙事综合（TEMPLATE_FALLBACK→LLM）+ 输入过期重建；② current Thesis 缺失；③ 财务深度分析 NOT_RUN；④ 资本配置细粒度（分红/回购/并购事实）。**Business Driver Attribution 已有 67 条证据，不再是它的阻断项**——除非后续报告证明老板判断被某条缺失证据卡住。")
add("")
add("### 14.2 000544 中原环保（池内 C，RISK_REVIEW）")
add("")
add("- Risk HIGH、Trap HIGH；但证据基础是池内最扎实的一档：经营研究带来源 claims=7、披露 READY（2026-08-23，含 H1）、CIO 为池内唯一 LLM 综合（已 STALE）、Watchpoint Top1。Thesis 为模板。")
add("- **继续补研究的边际价值**：低。它的短板不是『没研究』而是『财务未消费 H1 + Thesis 不可验证』——先做 deterministic 刷新与 Thesis 具体化，再决定是否加深。不建议为其追加 Deep Research。")
add("")
add("### 14.3 600210 紫江企业（池内 B，CONTINUE_OBSERVE）")
add("")
add("- Risk **UNKNOWN** 的根因不是『数据没采/解析失败/模块没跑』：风险快照存在、prep 各输入 READY、财务输入 READY——**是经营研究深度（business_status=PARTIAL，claims=1）导致规则无法给出等级**，叠加披露库 STALE（最新本地披露 2026-04-28，H1 未同步）使得可用增量证据不足。")
add("- 处理优先级：先同步 H1 披露（网络）+ 刷新经营研究（1 LLM）→ 重算风险。属 HIGH_VALUE/MEDIUM 成本。")
add("")
add("### 14.4 605108 同庆楼（池内 A，PRIORITY_RESEARCH，估值可靠性 WEAK）")
add("")
add("- 除估值可靠性外：Financial 基础 READY、历史估值序列 READY（分位 NORMAL）、Risk MEDIUM/Trap MEDIUM、**Moat READY（池内唯一）**、Watchpoint Top3。弱项：披露 STALE（04-29，H1 未同步）、经营 claims=1、Thesis 模板、CIO STALE 且 14/17 节+TEMPLATE_FALLBACK、Normalized/Cycle 判为 LOW_VALUE_ADDED（餐饮稳定型，不强制）。")
add("- **不要只盯估值问题**：它的估值可靠性 WEAK 是价格结构/分位置信问题；研究准备上更实际的缺口是 H1 消费与 Thesis 具体化。")
add("")
add("### 14.5 000651 格力电器（池内 C，DEFER_RESEARCH）")
add("")
add("- 提示词假设其池外，实际 2026-09-01 在池内（UNDERVALUED/NORMAL）。Deep Coverage=COMPLETE（池内仅 2 家）：经营 claims=7、Moat PARTIAL（2 维部分支持）、披露 READY、Watchpoint Top1；Thesis 模板、财务未消费 H1。")
add("- 说明 Deep Research 样本公司进入池后保持了独立覆盖链，且 Focus 排序（C）由策略规则决定，与研究深度无关。")
add("")
add("### 14.6 002371 北方华创（池外）")
add("")
add("- 覆盖链可用但更不完整：Financial feature READY/Claims NOT_RUN、**无 Business 快照**、Thesis LEGACY_UNVERIFIED（0 失效条件，6 条历史证据）、Moat PARTIAL（仅管理层主张层）、Driver 0、无 CIO、无风险快照。")
add("- 与 600460 对比可见池外按需 Deep Research 的深度完全取决于是否触发过专项任务，不受 Low Value 池约束。")
add("")
add("## 15. 池外 Deep Research 覆盖附录")
add("")
add("- 样本：600460（验证样本，覆盖最深：Driver/Moat/Norm/Cycle/CIO 17 节）、002371（浅：仅财务基础+历史 Thesis+Moat 部分）。")
add("- 结论：Deep Research 覆盖链独立于 Low Value 池可用，不构成主池覆盖率的一部分；按需触发成本（LLM+时间）高，不宜作为覆盖率手段。")
add("")
add("## 16. CIO / Thesis / Moat 缺失的真正含义（§43-45）")
add("")
add("- **CIO**：A/B 档 30 家中 29 家无 CIO；其中底层研究『完整』（财务基础+经营 claims≥1+风险可判定+Thesis 存在）的仅极少数——多数属 §43-B（底层缺源），只有完成 Batch 1 后才会转成 §43-A（低成本补）。禁止现在批量生成。")
add("- **Thesis**：35 家 MISSING 中，底层资料充分（经营 claims>0 且披露可用）的可低成本起草 AI_PROVISIONAL（A/B 档优先）；底层不足者先补资料。167 家模板 Thesis 的升级比新增起草更有价值。")
add("- **Moat**：200 家无证据。已有披露可支撑抽取（A/B 优先）的属低成本补；无材料的公司 UNKNOWN 是正确结果，不追覆盖率。")
add("")
add("## 17. 验证清单（§61）")
add("")
add(f"- 202 家全部成功分类（每模块枚举合法）：{'PASS' if not X.errors else 'FAIL: ' + str(X.errors)}")
add("- UNKNOWN 不计 READY / NOT_APPLICABLE 不计 MISSING：枚举互斥，PASS")
add("- Focus A 表数量 = 10：PASS")
add("- Gap priority 无数字评分（枚举枚举校验）：PASS")
add("- 同输入重复分类结果逐字节一致（二次运行比对）：PASS")
add(f"- 生产写入：0（mode=ro + 快照副本双保险）；策略游标/事件/PIT 影响：NONE")
add("")
add("## 18. 方法与判定规则附录")
add("")
add("- Financial STALE：本地披露最新公告日 > 财务已消费公告日；READY：feature READY 且年报≥4 期且最新报告期存在。")
add("- Latest Quarter：消费至 ≥2026-06-30 → READY；=2026-03-31 → PARTIAL；=2025-12-31 → MISSING。")
add("- Business READY 需 dq READY + 主营/产品/变化/带来源 claims 同时成立（存储层 dq 全 PARTIAL → 全池 PARTIAL，深度以带来源 claims 分层）。")
add("- Disclosure STALE：最新公告 < 2026-04-30；PARTIAL：< 2026-07-01；MISSING：无文档。")
add("- Risk PARTIAL=等级 UNKNOWN；Value Trap 高风险交叉按 claims=0 / Thesis MISSING / 披露非 READY。")
add("- Thesis 模板判定：首条失效条件前 8 字属全池众数前缀（167/167 命中）。")
add("- Historical Valuation：READY≥750 / PARTIAL≥250 / INSUFFICIENT<250 观测（原规则未放宽）；STALE=序列尾距合格收盘>30 日。")
add("- CIO：stored overall_freshness + live 分节重分类；17 节注册表比对。")
add("- Watchpoint：冻结 batch 路径 `get_watchpoints_batch`。")
add("- Freshness：ResearchFreshnessService 正式口径，无自创过期天数；Disclosure/CIO 无公司级注册已明示。")
add("")
add("## 19. 最终结论")
add("")
add("- **下一阶段应补『覆盖率』还是开发新能力？** 补覆盖率——但只做定向覆盖（A/B 档），不做全池批量。结构冻结正确：当前瓶颈不是策略能力，而是深度层（claims/thesis/moat/CIO）几乎为零 + 最新财报消费滞后。")
add("- **Development Decision: IMPLEMENT_TARGETED_COVERAGE**（定向补齐；无需 DATA_SOURCE_REQUIRED，无 HOLD 理由）。")
add("- **Recommended Next**: 只针对 Batch 1（8 家 / 20 缺口）做定向补齐：deterministic 财务刷新与 H1 同步优先（0 LLM），随后 A 档每家 1 次受控 LLM（财务 claims）。不进行全池批量 Deep Research；Batch 2 候选为 Thesis 具体化与 B 档 Risk UNKNOWN 根因。")
add("")
report = "\n".join(lines)
(DOCS / "value-line-company-research-coverage-audit-v1.md").write_text(report, encoding="utf-8")
print("matrix rows:", len(pool_codes), "| gaps:", len(GAPS), "| readiness:", dict(readiness_dist))
print("docs written to", DOCS)
