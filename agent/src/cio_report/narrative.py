"""Boss-facing Chinese narrative layer for the CIO Full Report (V2).

纯展示层：后台枚举、数据库字段、研究算法一律不动；这里只做中文映射、
14 节老板端重组（新标题/新顺序）、跨 section 确定性综合（核心矛盾、多维
经营阶段、路径叙事、估值六问、风险逻辑、验证点分级、裁决段）与飞书友
好双表格。所有句子只从已有 section payload 归纳，不生成新事实。
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# 老板端中文映射（§一）
# ---------------------------------------------------------------------------
STATUS_ZH: dict[str, str] = {
    "GROWTH": "收入持续增长", "STABLE_GROWTH": "稳定增长", "RECOVERY": "盈利修复",
    "CYCLICAL_RECOVERY": "周期修复", "DECLINING": "经营下行",
    "UNKNOWN": "当前资料不足，暂无法判断",
    "HIGH": "高风险", "MEDIUM": "中等风险", "LOW": "低风险",
    "FAIR": "估值处于合理区间", "UNDERVALUED": "低估", "DEEPLY_UNDERVALUED": "深度低估",
    "OVERVALUED": "高估", "DEEPLY_OVERVALUED": "明显高估", "INSUFFICIENT_DATA": "资料不足",
    "READY": "资料已具备", "PARTIAL": "资料部分具备",
    "LIMITED": "当前数据不足以形成完整判断",
    "STRONG": "较强", "ABOVE_AVERAGE": "高于同行平均", "AVERAGE": "同行平均",
    "BELOW_AVERAGE": "低于同行平均", "WEAK": "偏弱",
    "NOT_APPLICABLE": "该项暂不适用", "NOT_RUN": "尚未生成", "NOT_COLLECTED": "尚未采集",
    "WATCH": "观察", "FRESH": "数据未变化", "STALE": "上游已变化",
    "FORMING": "构建中", "AI_PROVISIONAL": "AI 初步待复核",
    "HUMAN_CONFIRMED": "人工已确认", "LEGACY_UNVERIFIED": "历史遗留未经核实",
    "ACTIVE": "有效", "REUSED": "沿用", "REFRESHED": "已更新",
    "MISSING": "暂缺",
}

TERM_ZH = [
    (re.compile(r"\bL1\b"), "一级行业"), (re.compile(r"\bL2\b"), "二级行业"),
    (re.compile(r"\bL3\b"), "三级行业"),
]

_ENGLISH_TOKEN_RE = re.compile(r"\b(GROWTH|STABLE_GROWTH|RECOVERY|CYCLICAL_RECOVERY|DECLINING|UNKNOWN|HIGH|MEDIUM|LOW|FAIR|UNDERVALUED|DEEPLY_UNDERVALUED|OVERVALUED|DEEPLY_OVERVALUED|INSUFFICIENT_DATA|READY|PARTIAL|LIMITED|STRONG|ABOVE_AVERAGE|BELOW_AVERAGE|WEAK|NOT_APPLICABLE|NOT_RUN|NOT_COLLECTED|WATCH|FORMING|AI_PROVISIONAL|HUMAN_CONFIRMED|LEGACY_UNVERIFIED|BEAR|BASE|BULL|MISSING|CIO)\b")


def zh(value: Any, default: str = "当前资料不足，暂无法判断") -> str:
    text = str(value or "").strip()
    return STATUS_ZH.get(text, text if text else default)


def boss_text(text: str) -> str:
    """Map leftover backend tokens inside a rendered paragraph to Chinese."""
    result = str(text or "")
    result = _ENGLISH_TOKEN_RE.sub(lambda m: STATUS_ZH.get(m.group(1), m.group(1)), result)
    result = result.replace("BEAR", "谨慎情景").replace("BASE", "基准情景").replace("BULL", "乐观情景")
    for pattern, replacement in TERM_ZH:
        result = pattern.sub(replacement, result)
    result = result.replace("CIO 深度研究报告", "投研主管深度研究报告")
    return result


def _yi(value: Any) -> str:
    try:
        return f"{float(value) / 1e8:.2f} 亿"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _num(value: Any, suffix: str = "") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# 老板端 14 节新结构（§二）：展示顺序 → (标题, 组装器)
# ---------------------------------------------------------------------------
BOSS_SECTIONS: list[str] = [
    "投研主管结论", "公司与行业位置", "过去五年发生了什么", "最新季度边际变化",
    "当前处于什么经营阶段", "当前最核心的经营矛盾", "盈利质量与财务风险",
    "主营业务与经营变化", "竞争优势是否成立", "资本投入与资金使用", "当前价格贵不贵",
    "谨慎 / 基准 / 乐观三种路径", "为什么值得继续研究",
    "接下来验证什么、什么情况说明判断错了", "最终研究判断",
]


def _sec(sections: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    return dict((sections.get(key) or {}).get("structured_payload") or {})


class BossRenderer:
    """Deterministic cross-section composition (no new facts, Chinese only)."""

    def __init__(self, sections: list[dict[str, Any]], stock_code: str, as_of: str) -> None:
        by_type = {s["section_type"]: s for s in sections}
        self.s = by_type
        self.fin = _sec(by_type, "financial_path")
        self.rows = [dict(r) for r in list(self.fin.get("rows") or []) if str(r.get("period_type") or "") == "annual"]
        self.stage = _sec(by_type, "operating_stage")
        self.risk = _sec(by_type, "quality_risk")
        self.valuation = _sec(by_type, "valuation")
        self.conclusion = _sec(by_type, "cio_conclusion")
        self.thesis = _sec(by_type, "thesis_watchpoints")
        self.why = _sec(by_type, "why_research")
        self.caution = _sec(by_type, "why_caution")
        self.position = _sec(by_type, "company_position")
        self.code, self.as_of = stock_code, as_of

    # -- 3 财务路径叙事（§五） -------------------------------------------
    def _profit_path(self) -> tuple[str, str]:
        """Return (路径句, 收入利润同步判断)."""
        if len(self.rows) < 3:
            return "年度数据不足，暂无法刻画完整路径。", "当前资料不足，暂无法判断"
        profits = [(str(r.get("report_date") or "")[:4], _f(r.get("net_profit"))) for r in self.rows]
        revenues = [(str(r.get("report_date") or "")[:4], _f(r.get("revenue"))) for r in self.rows]
        peak_year, peak = max(profits, key=lambda x: x[1])
        trough_year, trough = min(profits, key=lambda x: x[1])
        last_year, last = profits[-1]
        rev_first, rev_last = revenues[0][1], revenues[-1][1]
        grew = rev_last > rev_first
        if trough < 0:
            path = (
                f"净利润从 {peak_year} 年高点 {_yi(peak)} 一路下滑，{trough_year} 年出现亏损 {_yi(trough)}，"
                f"{last_year} 年恢复至 {_yi(last)}（约为高点的 {last / peak * 100:.0f}%）"
                if peak and last else "利润路径数据不完整。"
            )
        else:
            path = f"净利润高点出现在 {peak_year} 年（{_yi(peak)}），最新年度为 {_yi(last)}。"
        sync = (
            "收入与利润同步增长" if grew and last > 0 and last >= profits[-2][1] and (not peak or last >= peak * 0.6)
            else "收入增长但利润未同步恢复" if grew
            else "收入与利润均承压")
        return path, sync

    def section_financial_path(self) -> str:
        if not self.rows:
            return "当前暂无足够年度财务资料。"
        rows = self.rows[-5:]
        table1 = ["**表1：盈利路径**", "", "| 年度 | 营收 | 净利润 | 毛利率 | 净利率 | 净资产收益率 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        table2 = ["**表2：现金与资产质量**", "", "| 年度 | 经营现金流 | 应收账款 | 存货 | 资产负债率 | 资本开支 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for r in rows:
            year = str(r.get("report_date") or "")[:4]
            table1.append(
                f"| {year} | {_yi(r.get('revenue'))} | {_yi(r.get('net_profit'))} | "
                f"{_pct(r.get('gross_margin'))} | {_pct(r.get('net_margin'))} | {_pct(r.get('roe'))} |")
            table2.append(
                f"| {year} | {_yi(r.get('operating_cash_flow'))} | {_yi(r.get('accounts_receivable'))} | "
                f"{_yi(r.get('inventory'))} | {_pct(r.get('debt_ratio'))} | {_yi(r.get('capex'))} |")
        path, sync = self._profit_path()
        first, last = rows[0], rows[-1]
        gm_line = ""
        try:
            gm_first, gm_last = float(first["gross_margin"]), float(last["gross_margin"])
            direction = "下行" if gm_last < gm_first - 1 else ("上行" if gm_last > gm_first + 1 else "基本持平")
            gm_line = f"毛利率由 {first.get('report_date', '')[:4]} 年的 {_pct(gm_first)} 变化为 {direction}至 {_pct(gm_last)}。"
        except (KeyError, TypeError, ValueError):
            gm_line = ""
        ocf_line = ""
        try:
            ocf_last = float(last["operating_cash_flow"])
            ocf_line = f"最新年度经营现金流 {_yi(ocf_last)}，" + (
                "为近年较好水平。" if ocf_last > 0 else "仍为负值。")
        except (KeyError, TypeError, ValueError):
            ocf_line = ""
        return "\n".join(
            table1 + [""] + table2 + [""] + [path, f"收入与利润的同步性：{sync}。{gm_line}{ocf_line}"])

    # -- 4 经营阶段多维（§四） ---------------------------------------------
    def section_stage(self) -> str:
        def trend(values: list[float]) -> str:
            if len(values) < 3:
                return "资料不足"
            if all(v > 0 for v in values[-3:]) and values[-1] > values[-3] * 0.9:
                return "持续增长" if values[-1] > values[0] else "温和增长"
            if values[-1] < values[-3]:
                return "近年回落"
            return "低位企稳"

        revenue = [ _f(r.get("revenue")) for r in self.rows if _f(r.get("revenue")) is not None ]
        profit = [ _f(r.get("net_profit")) for r in self.rows if _f(r.get("net_profit")) is not None ]
        gm = [ _f(r.get("gross_margin")) for r in self.rows if _f(r.get("gross_margin")) is not None ]
        ocf = [ _f(r.get("operating_cash_flow")) for r in self.rows if _f(r.get("operating_cash_flow")) is not None ]
        dims = []
        dims.append(("收入", trend(revenue) if revenue else "资料不足"))
        if profit:
            peak = max(profit)
            dims.append(("利润", "低谷后修复" if min(profit) < 0 < profit[-1] < peak else trend(profit)))
        else:
            dims.append(("利润", "资料不足"))
        dims.append(("毛利率", "低位企稳" if gm and gm[-1] <= min(gm) * 1.15 else (trend(gm) if gm else "资料不足")))
        dims.append(("现金流", "最近年度明显改善" if ocf and len(ocf) >= 2 and ocf[-1] > max(ocf[:-1]) else (trend(ocf) if ocf else "资料不足")))
        missing = [name for name, status in dims if status == "资料不足"]
        lines = ["；".join(f"{name}：{status}" for name, status in dims) + "。"]
        if not missing:
            dim_map = dict(dims)
            phrases = []
            if "持续增长" in dim_map["收入"] or "温和增长" in dim_map["收入"]:
                phrases.append("收入扩张")
            if dim_map["利润"] == "低谷后修复":
                phrases.append("盈利修复")
            elif "回落" in dim_map["利润"]:
                phrases.append("盈利承压")
            if phrases:
                lines.append(f"综合归纳：当前处于{'、'.join(phrases)}阶段。")
            else:
                lines.append(f"综合归纳：{zh(self.stage.get('stage'))}。")
        else:
            lines.append(f"综合归纳受限于：{'、'.join(missing)}维度资料不足，暂无法给出完整阶段判断。")
        return "\n".join(lines)

    # -- 5 核心矛盾（§三） --------------------------------------------------
    def section_core_conflict(self) -> str:
        if len(self.rows) < 3:
            return "年度资料不足，暂无法归纳核心经营矛盾。"
        profits = [_f(r.get("net_profit")) for r in self.rows if _f(r.get("net_profit")) is not None]
        revenues = [_f(r.get("revenue")) for r in self.rows if _f(r.get("revenue")) is not None]
        ocf_last = _f(self.rows[-1].get("operating_cash_flow"))
        capex_last = _f(self.rows[-1].get("capex"))
        debt_first, debt_last = _f(self.rows[0].get("debt_ratio")), _f(self.rows[-1].get("debt_ratio"))
        growing = revenues and revenues[-1] > revenues[0]
        profit_below_peak = profits and max(profits) and profits[-1] < max(profits) * 0.6
        heavy_capex = capex_last is not None and ocf_last is not None and capex_last > ocf_last
        debt_up = debt_first is not None and debt_last is not None and debt_last > debt_first + 3
        parts: list[str] = []
        if growing:
            parts.append("收入持续增长")
        if profit_below_peak:
            parts.append("盈利能力相比历史高点大幅下降")
        if heavy_capex:
            parts.append("保持较高资本投入" + ("并伴随债务上升" if debt_up else ""))
        if parts:
            head = "、".join(parts[:-1]) + ("，同时" + parts[-1] if len(parts) > 1 else parts[0]) + "。"
        else:
            head = "经营面资料有限。"
        if growing and profit_below_peak and heavy_capex:
            core = "当前核心问题不是公司有没有增长，而是新增收入能否重新转化为足够高的利润和现金回报。"
        elif growing and profit_below_peak:
            core = "当前核心问题是盈利能力能否跟随收入恢复。"
        elif growing:
            core = "当前主要观察收入增长的持续性及其盈利质量。"
        else:
            core = "当前核心问题是经营基本面能否企稳。"
        return f"{head}{core}"

    # -- 10 估值六问（§六） --------------------------------------------------
    def section_valuation(self) -> str:
        v = self.valuation
        try:
            price, mid = float(v["current_price"]), float(v["fair_value_mid"])
            low, high = float(v["fair_value_low"]), float(v["fair_value_high"])
            pos = (price - low) / (high - low) * 100 if high > low else None
            dev = (price / mid - 1) * 100
            pos_text = f"当前价格处于系统合理价值区间的约 {pos:.0f}% 位置（{'接近上沿' if pos > 80 else '中部偏上' if pos > 50 else '中部或以下'}），相对中值偏离 {dev:+.1f}%。"
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pos_text = "当前估值区间资料不足，暂无法定位价格位置。"
        pe, pb = v.get("pe"), v.get("pb")
        profits = [_f(r.get("net_profit")) for r in self.rows if _f(r.get("net_profit")) is not None]
        low_profit = profits and max(profits) and profits[-1] < max(profits) * 0.5
        pe_text = (
            f"当前市盈率约 {_num(pe)} 倍，数值偏高；但公司利润仍处于低位修复期，单独用市盈率容易放大估值昂贵程度，需结合市净率、同行估值与盈利恢复情况综合判断。"
            if pe and low_profit else
            (f"当前市盈率约 {_num(pe)} 倍。" if pe else "市盈率暂缺。"))
        peer = next((m for m in list(v.get("peer_methods") or [])
                     if isinstance(m, dict) and str(m.get("status")) == "READY" and m.get("multiple_mid")), None)
        peer_text = (
            f"市净率 {_num(pb)} 倍，对照同三级行业可比公司中位约 {_num(peer.get('multiple_mid'))} 倍"
            + ("，处于可比区间内。" if pb and peer.get("multiple_low") and peer.get("multiple_high")
               and float(peer["multiple_low"]) <= float(pb) <= float(peer["multiple_high"]) else "。")
            if pb and peer else (f"市净率 {_num(pb)} 倍（暂无可比对照）。" if pb else "市净率暂缺。"))
        history_text = "历史估值数据尚未完整物化，因此无法对照公司自身历史估值位置，这一缺口限制了对估值周期位置的判断。"
        premise = "当前估值最大的前提是盈利修复能够兑现；若利润修复不及预期，仅凭现有价格对应的估值支撑会明显减弱。"
        return "\n".join([pos_text, pe_text, peer_text, history_text, premise])

    # -- 6 风险逻辑（§七） ----------------------------------------------------
    def section_risk(self) -> str:
        risks = [dict(r) for r in list(self.risk.get("risks") or [])]
        observations = [dict(o) for o in list(self.risk.get("fact_observations") or [])]
        profits = [_f(r.get("net_profit")) for r in self.rows if _f(r.get("net_profit")) is not None]
        capex_last, ocf_last = _f(self.rows[-1].get("capex")) if self.rows else None, _f(self.rows[-1].get("operating_cash_flow")) if self.rows else None
        debt_first, debt_last = (_f(self.rows[0].get("debt_ratio")) if self.rows else None), (_f(self.rows[-1].get("debt_ratio")) if self.rows else None)
        debt_up = any("负债" in str(r.get("risk_type") or "") or "债务" in str(r.get("risk_type") or "") for r in risks) or (
            debt_first is not None and debt_last is not None and debt_last > debt_first + 3)
        weak_profit = profits and profits[-1] < (max(profits) * 0.5 if max(profits) > 0 else 0)
        summary = "真正需要关注的是"
        if debt_up and capex_last and ocf_last and capex_last > ocf_last and weak_profit:
            summary += "：重资产扩张与盈利恢复速度之间是否匹配——债务上升、资本开支高企与盈利能力偏弱同时存在，任何一端失衡都会放大压力。"
        elif debt_up and weak_profit:
            summary += "：债务上行与盈利偏弱的组合能否被经营现金流消化。"
        elif risks:
            summary += f"：{zh(risks[0].get('severity'))}项——{str(risks[0].get('text') or '')[:60]}。"
        else:
            summary = "当前规则层未发现需要重点关注的已确认风险。"
        lines = [f"整体风险等级：{zh(self.risk.get('overall_risk'))}。{summary}"]
        if risks:
            lines.append("")
            lines.append("**已确认风险**（来自风险研究规则）")
            for r in risks[:4]:
                lines.append(f"- [{zh(r.get('severity'))}] {boss_text(r.get('text') or r.get('risk_type'))}")
        if observations:
            lines.append("")
            lines.append("**财务观察项**（数值观察，不构成风险等级）")
            lines.append("；".join(f"{o.get('item')} {o.get('value')}" for o in observations[:6]) + "。")
        lines.append("")
        lines.append("资料不足项：无主营业务资料、护城河证据与公告材料，相关风险维度暂无法核验。")
        return "\n".join(lines)

    # -- 11 三情景（§九） -----------------------------------------------------
    def section_scenarios(self) -> str:
        fin = _sec(self.s, "scenarios")
        scenarios = dict(fin.get("scenarios") or {})
        if not scenarios:
            return "当前情景分析受限：历史盈利含亏损期，系统情景引擎未生成完整净利预测。"
        names = {"BEAR": "谨慎情景", "BASE": "基准情景", "BULL": "乐观情景"}
        lines = []
        for key in ("BEAR", "BASE", "BULL"):
            sc = dict(scenarios.get(key) or {})
            rows = [dict(r) for r in list(sc.get("forecast") or [])]
            if not rows:
                continue
            last = rows[-1]
            profit_text = _yi(last.get("net_profit")) if last.get("net_profit") is not None else "未生成（情景受限）"
            lines.append(f"- {names[key]}：{last.get('year')} 营收 {_yi(last.get('revenue'))}，净利 {profit_text}")
        lines.append("以上为系统情景引擎输出，不含任何主观概率与概率加权价格。")
        status = str(fin.get("status") or "")
        if status == "LIMITED":
            lines.append("当前情景分析受限：净利润情景未生成，盈利端解释力有限。")
        return "\n".join(lines)

    # -- 13 验证点分级（§十） ---------------------------------------------------
    def section_watchpoints(self) -> str:
        t = self.thesis
        has_thesis = bool(t.get("thesis_title"))
        draft = t.get("thesis_draft") if isinstance(t.get("thesis_draft"), dict) else None
        metrics = [str(m) for m in list(t.get("key_metrics_to_monitor") or []) if str(m).strip()]
        invalid = []
        for w in list(t.get("invalid_conditions") or []):
            text = str(w.get("condition") if isinstance(w, dict) else w or "").strip()
            if text:
                invalid.append(text)
        fallback = [str(w) for w in list(t.get("fallback_watchpoints") or []) if str(w).strip()]
        if has_thesis:
            head = f"核心逻辑（{zh(t.get('authority_status'))}）：{t.get('thesis_title')}"
        elif draft:
            head = "AI 研究草稿 · 待人工确认，尚未成为正式核心逻辑（草稿不等于系统认定）"
        else:
            head = "以下为系统根据现有财务和估值数据生成的观察项，不代表已经形成正式公司核心逻辑。"
        lines = [head]
        if draft:
            lines.append("")
            lines.append(f"草稿要点：{str(draft.get('core_thesis') or draft.get('title') or '')[:180]}")
            for label, key in (("核心驱动", "core_drivers"), ("关键假设", "key_assumptions"),
                               ("失效条件", "invalid_conditions"), ("跟踪指标", "key_metrics_to_monitor")):
                items = [str(x) for x in list(draft.get(key) or []) if str(x).strip()][:3]
                if items:
                    lines.append(f"{label}：" + "；".join(items))
            lines.append("（草稿需经人工确认后才转为正式核心逻辑；确认前不作为任何档位硬条件）")
        pool = metrics or invalid or fallback
        if not pool:
            return "\n".join(lines)
        # Priority split: 1 / 1-2 / rest; short pools still show all tiers.
        if len(pool) >= 3:
            top, second, long_term = pool[:1], pool[1:2], pool[2:]
        else:
            top, second, long_term = pool[:1], pool[1:2], []
        lines.append("")
        lines.append("**最重要验证点**：" + "；".join(top))
        if second:
            lines.append("**其次验证点**：" + "；".join(second))
        if long_term:
            lines.append("**长期观察点**：" + "；".join(long_term))
        return "\n".join(lines)

    # -- 14 最终裁决（§十一） ---------------------------------------------------
    def section_final_verdict(self) -> str:
        verdict = self.conclusion.get("verdict") or "资料不足"
        positives: list[str] = []
        if self.why.get("reasons"):
            positives = [str(r) for r in list(self.why["reasons"])[:2]]
        limits: list[str] = []
        if self.caution.get("cautions"):
            limits = [str(c) for c in list(self.caution["cautions"])[:2]]
        if not self.thesis.get("thesis_title"):
            limits.append("尚未形成正式核心逻辑")
        upgrade = "若毛利率与经营现金流持续修复、且核心逻辑与护城河证据补齐，可升级为重点研究"
        downgrade = "若债务与资本开支继续上升而盈利未恢复，或估值跌破合理区间下沿的基本面原因是基本面恶化，则应下调"
        body = (
            f"为什么是「{verdict}」：当前最重要的正面因素是{'；'.join(positives) if positives else '暂无突出正面信号'}；"
            f"当前最大的限制是{'；'.join(limits[:2]) if limits else '资料覆盖不足'}。"
            f"{upgrade}；{downgrade}。本判断不构成任何交易指令。")
        return body

    # -- 其余节的简单映射 -----------------------------------------------------
    def section_conclusion_head(self) -> str:
        tier = self.conclusion.get("focus_tier")
        tier_text = {"A": "（A 档重点研究名单）", "B": "（B 档继续观察名单）", "C": "（C 档暂缓优先研究名单）"}.get(str(tier or ""), "")
        return (
            f"研究判断：{self.conclusion.get('verdict') or '资料不足'}{tier_text}；"
            f"估值状态：{zh(self.conclusion.get('valuation_status'))}；研究基准日 {self.as_of}。"
            "一句话：先看第五节核心矛盾与第十节估值位置，再决定是否深入。")

    def section_position(self) -> str:
        p = self.position
        industry = " / ".join(str(p.get(k) or "") for k in ("level1_name", "level2_name", "level3_name")).strip(" /")
        leader = "当前三级行业龙头池成员" if p.get("is_current_l3_leader") else "非当前三级行业龙头池成员"
        main = p.get("main_business") or "暂无主营业务资料"
        return f"{p.get('stock_name') or self.code}（{self.code}），一级行业 {industry or '未知'}；{leader}。主营业务：{main}。"

    def section_business(self) -> str:
        b = _sec(self.s, "business_structure")
        if not b.get("main_business"):
            return "当前暂无足够资料：主营业务研究尚未生成，无法展开业务结构与经营变化。"
        claims = [str(c.get("statement") or "") for c in list(b.get("claims") or [])[:5] if isinstance(c, dict)]
        lines = [f"主营业务：{b.get('main_business')}。"]
        if claims:
            lines.append("已验证/带来源的经营要点：" + "；".join(claims) + "。")
        lines.append("无产品收入占比资料时不判断产品贡献大小。")
        return "\n".join(lines)

    def section_moat(self) -> str:
        m = _sec(self.s, "moat")
        if not m:
            return "护城河资料暂缺。"
        dims = [dict(d) for d in list(m.get("dimensions") or []) if isinstance(d, dict)]
        supported = [d for d in dims if str(d.get("status") or "").startswith("SUPPORTED")]
        counter = [d for d in dims if str(d.get("status") or "") == "COUNTER_EVIDENCE"]  # noqa: F841
        if counter:
            lines_hint = "注意：存在反证维度，需与支持证据一并权衡。"
        else:
            lines_hint = ""
        if not dims:
            return f"护城河证据 {m.get('evidence_count') or 0} 条、反证 {m.get('counter_evidence_count') or 0} 条：暂无法判断竞争优势是否成立。"
        lines = [f"证据 {m.get('evidence_count') or 0} 条、反证 {m.get('counter_evidence_count') or 0} 条。"]
        for d in dims[:8]:
            status = str(d.get("status") or "")
            label = "有证据支持" if status.startswith("SUPPORTED") else ("存在反证" if status == "COUNTER_EVIDENCE" else "暂无法判断")
            name = str(d.get("label") or d.get("moat_dimension") or d.get("dimension") or "")
            lines.append(f"- {name}：{label}")
        if not supported:
            lines.append("结论：当前无任何维度获得充分证据，不据此认定竞争优势；规模、排名或知名度本身不构成护城河。" + lines_hint)
        return "\n".join(lines)

    def section_capital(self) -> str:
        c = _sec(self.s, "capital_allocation")
        label_map = {"reinvestment": "再投资", "dividend": "分红", "debt_management": "负债管理",
                     "equity_dilution": "股本", "cash_management": "现金管理", "buyback": "回购", "m_and_a": "并购"}
        lines = []
        dims = c.get("dimensions")
        if isinstance(dims, dict):
            for key, d in dims.items():
                if not isinstance(d, dict):
                    continue
                name = label_map.get(str(key), str(key))
                status = str(d.get("status") or "")
                zh_status = zh(status) if status in STATUS_ZH else ("有事实支持" if status.startswith("SUPPORTED") else "暂无法判断")
                observation = str(d.get("observation") or d.get("summary") or "")[:90]
                lines.append(f"- {name}：{zh_status}。{observation}")
        elif isinstance(dims, list):
            for d in [dict(x) for x in dims if isinstance(x, dict)]:
                name = label_map.get(str(d.get("dimension") or ""), str(d.get("dimension") or d.get("name") or ""))
                status = str(d.get("status") or "")
                zh_status = "有事实支持" if status.startswith("SUPPORTED") else "暂无法判断"
                lines.append(f"- {name}：{zh_status}。{str(d.get('summary') or '')[:90]}")
        if not lines:
            return "资本配置资料暂缺。"
        lines.append("仅呈现事实与数据缺口，不对管理层作评价。")
        return "\n".join(lines)

    def section_why_research(self) -> str:
        reasons = [boss_text(str(r)) for r in list(self.why.get("reasons") or [])]
        return "\n".join(f"- {r}" for r in reasons) if reasons else "当前各研究层未给出明显的继续研究信号。"


def render_boss_report(sections: list[dict[str, Any]], *, stock_code: str, as_of: str) -> str:
    """Full boss-facing report: new 14-section order + Chinese mapping."""
    renderer = BossRenderer(sections, stock_code, as_of)
    builders = {
        "投研主管结论": renderer.section_conclusion_head,
        "公司与行业位置": renderer.section_position,
        "过去五年发生了什么": renderer.section_financial_path,
        "最新季度边际变化": renderer.section_latest_quarter,
        "当前处于什么经营阶段": renderer.section_stage,
        "当前最核心的经营矛盾": renderer.section_core_conflict,
        "盈利质量与财务风险": renderer.section_risk,
        "主营业务与经营变化": renderer.section_business,
        "竞争优势是否成立": renderer.section_moat,
        "资本投入与资金使用": renderer.section_capital,
        "当前价格贵不贵": renderer.section_valuation,
        "谨慎 / 基准 / 乐观三种路径": renderer.section_scenarios,
        "为什么值得继续研究": renderer.section_why_research,
        "接下来验证什么、什么情况说明判断错了": renderer.section_watchpoints,
        "最终研究判断": renderer.section_final_verdict,
    }
    parts = [f"投研主管深度研究报告 · {renderer.position.get('stock_name') or stock_code}（{stock_code}）",
             f"研究基准日 {as_of}。本报告由系统已保存研究结果整理，不含任何交易指令。", ""]
    for index, title in enumerate(BOSS_SECTIONS, 1):
        parts.append(f"## {index}. {title}")
        parts.append(boss_text(builders[title]()))
        parts.append("")
    return "\n".join(parts).strip()


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
