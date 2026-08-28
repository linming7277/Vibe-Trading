"""Deterministic 14-section CIO report builder (plan §14).

Every section is built 100% from persisted read-only research results; the
narrative here is template text (the "70%" layer).  The single synthesis LLM
in service.py re-narrates on top (the "30%"), with this template output as
the mandatory fallback (plan §15.2).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

CIO_REPORT_FORMULA_VERSION = "cio-report-v1"
SECTION_TEMPLATE_VERSION = "cio-section-template-v1"

SECTION_TITLES: dict[str, str] = {
    "company_position": "01 公司与产业位置",
    "leader_quality": "02 龙头质量与同行优势",
    "financial_path": "03 多年财务路径",
    "operating_stage": "04 当前经营阶段",
    "quality_risk": "05 盈利质量与财务风险",
    "business_structure": "06 经营与业务结构",
    "moat": "07 竞争优势",
    "capital_allocation": "08 资本配置",
    "valuation": "09 当前估值",
    "scenarios": "10 Bear / Base / Bull",
    "why_research": "11 为什么值得继续研究",
    "why_caution": "12 为什么需要谨慎",
    "thesis_watchpoints": "13 核心逻辑、证伪条件与验证点",
    "cio_conclusion": "14 CIO 最终研究结论",
}

_TRADING_LANGUAGE = "买入|卖出|推荐|仓位|止盈|止损|加仓|减仓|建仓"


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _yi(value: Any) -> str:
    try:
        return f"{float(value) / 1e8:.2f} 亿"
    except (TypeError, ValueError):
        return "—"


def _num(value: Any, suffix: str = "") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "—"


class CioSectionBuilder:
    """Read-only section extraction; any failure degrades to a data-gap section."""

    def __init__(self, market: str, stock_code: str, as_of: str) -> None:
        self.market, self.code, self.as_of = market, stock_code.upper(), as_of

    # -- shared reads ---------------------------------------------------
    def _financial(self) -> dict[str, Any]:
        from src.financial_analysis.service import get_financial_analysis_service

        return dict(get_financial_analysis_service().get_saved_resolved_analysis(self.code, as_of=self.as_of) or {})

    def _business(self) -> dict[str, Any]:
        from src.business_research import get_business_research_service

        try:
            return dict(get_business_research_service().get_saved_research(self.code, as_of=self.as_of) or {})
        except Exception:  # noqa: BLE001
            return {}

    def _zones(self) -> dict[str, Any]:
        from src.value_price_zones import get_value_price_zone_service

        try:
            return dict(get_value_price_zone_service().get_price_zones(self.market, self.code, as_of=self.as_of) or {})
        except Exception:  # noqa: BLE001
            return {}

    def _risk(self) -> dict[str, Any]:
        from src.risk_research import get_risk_research_service

        try:
            return dict(get_risk_research_service().get_risk_research(self.market, self.code, as_of=self.as_of) or {})
        except Exception:  # noqa: BLE001
            return {}

    def _thesis(self) -> dict[str, Any]:
        from src.company_thesis.store import CompanyThesisRepository

        try:
            return dict(CompanyThesisRepository().get_current_thesis(self.market, self.code) or {})
        except Exception:  # noqa: BLE001
            return {}

    def _section(self, section_type: str, payload: dict[str, Any], narrative: str,
                 refs: list[str] | None = None) -> dict[str, Any]:
        import re as _re

        safe_narrative = _re.sub(_TRADING_LANGUAGE, "■■", narrative)
        return {
            "section_type": section_type,
            "title": SECTION_TITLES[section_type],
            "input_fingerprint": _digest({"v": SECTION_TEMPLATE_VERSION, "payload": payload}),
            "freshness_status": "FRESH",
            "structured_payload": payload,
            "narrative_md": safe_narrative.strip(),
            "source_refs": refs or [],
        }

    def _gap_section(self, section_type: str, reason: str) -> dict[str, Any]:
        return self._section(section_type, {"status": "MISSING", "reason": reason},
                             f"（{SECTION_TITLES[section_type]}）当前资料不足：{reason}。")

    # -- 01 公司与产业位置 ------------------------------------------------
    def build_company_position(self) -> dict[str, Any]:
        financial = self._financial()
        identity = dict(financial.get("identity") or {})
        if not identity:
            return self._gap_section("company_position", "尚无财务身份快照")
        business = self._business()
        payload = {
            "stock_name": identity.get("stock_name"),
            "level1_name": identity.get("level1_name"), "level2_name": identity.get("level2_name"),
            "level3_name": identity.get("level3_name"),
            "is_current_l3_leader": identity.get("is_current_l3_leader"),
            "main_business": business.get("main_business") or identity.get("main_business"),
            "products": (business.get("products") or [])[:8],
            "data_dates": identity.get("data_dates"),
        }
        industry = " / ".join(str(identity.get(k) or "") for k in ("level1_name", "level2_name", "level3_name")).strip(" /") or "行业归属未知"
        leader = "当前 L3 龙头池成员" if identity.get("is_current_l3_leader") else "非当前 L3 龙头池成员"
        products = "、".join(str(p) for p in payload["products"]) if payload["products"] else "暂无产品资料"
        narrative = (
            f"{payload['stock_name'] or self.code}（{self.code}）所属行业：{industry}；{leader}。\n"
            f"主营业务：{payload['main_business'] or '暂无主营业务描述'}。\n"
            f"主要产品/业务：{products}。"
        )
        return self._section("company_position", payload, narrative)

    # -- 02 龙头质量 -----------------------------------------------------
    def build_leader_quality(self) -> dict[str, Any]:
        from src.leader_quality_profile import get_leader_quality_profile_service

        try:
            profile = dict(get_leader_quality_profile_service().get_profile(self.market, self.code, as_of=self.as_of) or {})
        except Exception:  # noqa: BLE001
            profile = {}
        if not profile:
            return self._gap_section("leader_quality", "该公司不在当前 L3 龙头快照中")
        payload = {
            key: profile.get(key) for key in (
                "status", "overall_grade", "rank_in_industry", "industry_member_count",
                "strengths", "weaknesses", "short_window_stability", "formula_version",
            ) if key in profile
        } or {"status": profile.get("status"), "keys": sorted(profile)[:12]}
        strengths = "、".join(str(s) for s in (payload.get("strengths") or [])[:4]) or "—"
        weaknesses = "、".join(str(s) for s in (payload.get("weaknesses") or [])[:4]) or "—"
        narrative = (
            f"龙头质量状态：{payload.get('status') or '未知'}；"
            f"行业内排名 {payload.get('rank_in_industry') or '—'} / {payload.get('industry_member_count') or '—'}。\n"
            f"强项：{strengths}\n弱项：{weaknesses}"
        )
        return self._section("leader_quality", payload, narrative)

    # -- 03 多年财务路径 ---------------------------------------------------
    def build_financial_path(self) -> dict[str, Any]:
        financial = self._financial()
        rows = [dict(r) for r in list(financial.get("history") or []) if str(r.get("period_type") or "") == "annual"]
        if not rows:
            return self._gap_section("financial_path", "暂无年化财务历史")
        rows = rows[-8:]
        table_lines = ["| 年度 | 营收 | 归母净利 | 毛利率 | ROE | 经营现金流 | 资产负债率 |",
                       "| --- | --- | --- | --- | --- | --- | --- |"]
        for row in rows:
            table_lines.append(
                f"| {str(row.get('report_date') or '')[:4]} | {_yi(row.get('revenue'))} | "
                f"{_yi(row.get('net_profit'))} | {_pct(row.get('gross_margin'))} | "
                f"{_pct(row.get('roe'))} | {_yi(row.get('operating_cash_flow'))} | {_pct(row.get('debt_ratio'))} |"
            )
        payload = {"rows": rows, "feature_status": financial.get("feature_status")}
        return self._section("financial_path", payload, "\n".join(table_lines))

    # -- 04 当前经营阶段 ---------------------------------------------------
    def build_operating_stage(self) -> dict[str, Any]:
        financial = self._financial()
        feature = dict(financial.get("feature") or {})
        forecast = dict(financial.get("forecast") or {})
        growth = dict((feature.get("growth") or {}).get("revenue") or {})
        items = [dict(i) for i in list(growth.get("items") or []) if i.get("period_type") == "annual"][-3:]
        if len(items) >= 2:
            try:
                latest, prev = float(items[-1]["value"]), float(items[-2]["value"])
                yoy = (latest / prev - 1) * 100 if prev else None
            except (TypeError, ValueError, ZeroDivisionError):
                yoy = None
        else:
            yoy = None
        if yoy is None:
            stage = "资料不足"
        elif yoy >= 15:
            stage = "较快增长"
        elif yoy >= 0:
            stage = "温和增长"
        elif yoy >= -15:
            stage = "增长放缓或小幅下滑"
        else:
            stage = "明显下滑"
        payload = {"stage": stage, "revenue_yoy_latest": yoy,
                   "forecast_status": forecast.get("status"),
                   "gross_margin_trend": "见财务路径表"}
        narrative = (
            f"按最近两个年报营收同比（{_num(yoy, '%') if yoy is not None else '—'}）确定性归类：{stage}。\n"
            f"系统情景引擎状态：{forecast.get('status') or '未知'}（详见 Bear/Base/Bull 一节）。"
        )
        return self._section("operating_stage", payload, narrative)

    # -- 05 盈利质量与财务风险 ----------------------------------------------
    def build_quality_risk(self) -> dict[str, Any]:
        risk = self._risk()
        risks = [dict(r) for r in list(risk.get("risks") or [])[:6]]
        if not risks and not risk:
            return self._gap_section("quality_risk", "暂无风险研究结果")
        payload = {
            "overall_risk": risk.get("overall_risk"),
            "summary": risk.get("summary"),
            "risks": [{"risk_type": r.get("risk_type"), "severity": r.get("severity"),
                       "text": r.get("text")} for r in risks],
            "value_trap_risk": risk.get("value_trap_risk"),
        }
        lines = [f"总体风险：{risk.get('overall_risk') or '未知'}。{risk.get('summary') or ''}"]
        for r in risks:
            lines.append(f"- [{r.get('severity')}] {r.get('risk_type')}：{r.get('text') or ''}")
        if risk.get("value_trap_risk"):
            lines.append(f"低估陷阱维度：{risk['value_trap_risk']}。")
        return self._section("quality_risk", payload, "\n".join(lines))

    # -- 06 经营与业务结构 --------------------------------------------------
    def build_business_structure(self) -> dict[str, Any]:
        business = self._business()
        if not business or not business.get("main_business"):
            return self._gap_section("business_structure", "尚无经营研究")
        claims = [dict(c) for c in list(business.get("claims") or [])[:8]]
        payload = {
            "analysis_status": business.get("analysis_status"),
            "main_business": business.get("main_business"),
            "products": business.get("products") or [],
            "claims": [{"type": c.get("type"), "statement": c.get("statement"),
                        "source_keys": c.get("source_keys")} for c in claims],
            "data_as_of": business.get("data_as_of"),
        }
        lines = [f"主营业务：{business.get('main_business')}（研究状态 {business.get('analysis_status')}）。"]
        for c in claims:
            lines.append(f"- [{c.get('type')}] {c.get('statement')}")
        lines.append("FACT=已验证事实；INFERENCE=带来源推断；UNKNOWN=资料缺口。无产品收入占比时不判断产品贡献。")
        return self._section("business_structure", payload, "\n".join(lines))

    # -- 07 竞争优势 ------------------------------------------------------
    def build_moat(self) -> dict[str, Any]:
        from src.moat_research import get_moat_research_service

        try:
            moat = dict(get_moat_research_service().get_research(self.market, self.code, as_of=self.as_of) or {})
        except Exception:  # noqa: BLE001
            moat = {}
        if not moat:
            return self._gap_section("moat", "尚无护城河研究")
        payload = {key: moat.get(key) for key in (
            "status", "dimensions", "evidence_count", "counter_evidence_count",
        ) if key in moat} or {"keys": sorted(moat)[:10]}
        dims = payload.get("dimensions")
        if isinstance(dims, list):
            dim_lines = [
                f"- {d.get('moat_dimension') or d.get('dimension')}：{d.get('status') or d.get('support')}（证据 {d.get('evidence_count', '—')}）"
                for d in dims[:8] if isinstance(d, dict)
            ]
        else:
            dim_lines = []
        narrative = (
            f"护城河研究状态：{payload.get('status') or '未知'}；证据 {payload.get('evidence_count') or 0} 条、"
            f"反证 {payload.get('counter_evidence_count') or 0} 条。\n"
            + ("\n".join(dim_lines) if dim_lines else "按维度展示支持度（SUPPORTED/PARTIAL/UNKNOWN/反证），不输出宽窄护城河结论。")
        )
        return self._section("moat", payload, narrative)

    # -- 08 资本配置 ------------------------------------------------------
    def build_capital_allocation(self) -> dict[str, Any]:
        from src.capital_allocation_research import get_capital_allocation_research_service

        try:
            capital = dict(get_capital_allocation_research_service().get_research(self.market, self.code, as_of=self.as_of) or {})
        except Exception:  # noqa: BLE001
            capital = {}
        if not capital:
            return self._gap_section("capital_allocation", "尚无资本配置研究")
        payload = {key: capital.get(key) for key in ("status", "dimensions", "data_gaps", "formula_version") if key in capital}
        dims = payload.get("dimensions")
        lines = [f"资本配置状态：{payload.get('status') or '未知'}。"]
        if isinstance(dims, list):
            for d in dims[:7]:
                if isinstance(d, dict):
                    lines.append(f"- {d.get('dimension') or d.get('name')}：{d.get('status') or d.get('summary') or '—'}")
        lines.append("仅展示再投资/分红/负债/股本/现金事实与数据缺口，不评价管理层。")
        return self._section("capital_allocation", payload, "\n".join(lines))

    # -- 09 当前估值 ------------------------------------------------------
    def build_valuation(self) -> dict[str, Any]:
        zones = self._zones()
        if not zones:
            return self._gap_section("valuation", "暂无估值研究")
        valuation = dict(zones.get("valuation") or {})
        payload = {
            "current_price": zones.get("current_price"),
            "valuation_status": valuation.get("status"),
            "fair_value_low": valuation.get("fair_value_low"), "fair_value_mid": valuation.get("fair_value_mid"),
            "fair_value_high": valuation.get("fair_value_high"),
            "pe": valuation.get("pe") or valuation.get("pe_ttm"), "pb": valuation.get("pb") or valuation.get("pb_mrq"),
            "as_of": zones.get("as_of"),
            "plain_summary": zones.get("plain_summary"),
        }
        try:
            mid = float(payload["fair_value_mid"])
            price = float(payload["current_price"])
            distance = f"{(price / mid - 1) * 100:+.1f}%"
        except (TypeError, ValueError, ZeroDivisionError):
            distance = "—"
        narrative = (
            f"现价 {payload['current_price'] or '—'}（{str(payload['as_of'] or '')[:10]}）；"
            f"估值判定 {payload['valuation_status'] or '未知'}；"
            f"系统合理价值区间 {_num(payload['fair_value_low'])} – {_num(payload['fair_value_high'])}"
            f"（中值 {_num(payload['fair_value_mid'])}），现价相对中值 {distance}。\n"
            f"PE {_num(payload['pe'])} / PB {_num(payload['pb'])}。{zones.get('plain_summary') or ''}"
        )
        return self._section("valuation", payload, narrative)

    # -- 10 Bear/Base/Bull ------------------------------------------------
    def build_scenarios(self) -> dict[str, Any]:
        financial = self._financial()
        forecast = dict(financial.get("forecast") or {})
        scenarios = dict(forecast.get("scenarios") or {})
        if not scenarios:
            return self._gap_section("scenarios", "情景引擎未生成（LIMITED 或资料不足）")
        payload = {"status": forecast.get("status"), "scenarios": scenarios}
        lines = [f"情景引擎状态：{forecast.get('status')}。V1 不做主观概率加权。"]
        for key in ("BEAR", "BASE", "BULL"):
            sc = dict(scenarios.get(key) or {})
            rows = [dict(r) for r in list(sc.get("forecast") or [])]
            last = rows[-1] if rows else {}
            lines.append(
                f"- {sc.get('label') or key}：末年营收 {_yi(last.get('revenue'))}"
                f"（{str(last.get('year') or '')}）；净利 {_yi(last.get('net_profit')) if last.get('net_profit') is not None else '未生成（LIMITED）'}"
            )
        return self._section("scenarios", payload, "\n".join(lines))

    # -- 11 为什么值得继续研究 -----------------------------------------------
    def build_why_research(self) -> dict[str, Any]:
        zones, risk, moat = self._zones(), self._risk(), self.build_moat()["structured_payload"]
        valuation_status = str((zones.get("valuation") or {}).get("status") or "")
        reasons: list[str] = []
        if valuation_status in {"UNDERVALUED", "DEEPLY_UNDERVALUED"}:
            reasons.append(f"估值处于低估区域（{valuation_status}）")
        if moat.get("evidence_count"):
            reasons.append(f"护城河证据 {moat.get('evidence_count')} 条，存在可跟踪的竞争优势线索")
        if risk.get("overall_risk") in {"LOW", "MEDIUM"}:
            reasons.append(f"规则风险等级 {risk.get('overall_risk')}，无高危项")
        focus = self._focus_entry()
        if focus:
            reasons.append(f"低估池/焦点档位：{focus}")
        if not reasons:
            reasons.append("当前各研究层未给出明显的继续研究信号；以数据边界为准")
        payload = {"reasons": reasons[:5], "valuation_status": valuation_status}
        return self._section("why_research", payload,
                             "\n".join(f"- {r}" for r in reasons[:5]))

    def _focus_entry(self) -> str:
        try:
            from src.focus_selection import get_focus_selection_service

            data = get_focus_selection_service().get_focus_selection(as_of=self.as_of) or {}
            for tier_key in ("focus_a", "focus_b"):
                for item in list((data.get(tier_key) or {}) or []):
                    if str(item.get("stock_code") or "").upper() == self.code:
                        label = {"focus_a": "A 档重点研究", "focus_b": "B 档继续观察"}[tier_key]
                        return f"{label}（{(item.get('focus_reasons') or ['—'])[0]}）"
        except Exception:  # noqa: BLE001
            return ""
        return ""

    # -- 12 为什么需要谨慎 --------------------------------------------------
    def build_why_caution(self) -> dict[str, Any]:
        risk = self._risk()
        cautions = [
            f"[{r.get('severity')}] {r.get('risk_type')}：{r.get('text') or ''}"
            for r in [dict(r) for r in list(risk.get("risks") or [])[:3]]
        ]
        if risk.get("value_trap_risk") and str(risk.get("value_trap_risk")) not in {"NONE", "NOT_APPLICABLE"}:
            cautions.append(f"低估陷阱维度：{risk['value_trap_risk']}")
        financial = self._financial()
        if str(financial.get("forecast_status")) == "LIMITED":
            cautions.append("情景引擎受限（历史盈利含亏损期），净利情景未生成")
        if not cautions:
            cautions.append("当前规则层无明确谨慎信号；关注数据边界")
        payload = {"cautions": cautions[:5]}
        return self._section("why_caution", payload, "\n".join(f"- {c}" for c in cautions[:5]))

    # -- 13 核心逻辑/证伪/验证点 ----------------------------------------------
    def build_thesis_watchpoints(self) -> dict[str, Any]:
        thesis = self._thesis()
        financial = self._financial()
        analysis = dict(financial.get("analysis") or {})
        metrics = [str(m) for m in list(analysis.get("key_metrics_to_monitor") or [])[:6]]
        if not thesis and not metrics:
            return self._gap_section("thesis_watchpoints", "尚未建立核心逻辑，且无已保存关注指标")
        authority_labels = {
            "AI_PROVISIONAL": "AI 初步待复核", "HUMAN_CONFIRMED": "Human Confirmed",
            "LEGACY_UNVERIFIED": "Legacy Unverified", "HUMAN_REJECTED": "Human Rejected",
        }
        payload = {
            "thesis_title": thesis.get("title"), "thesis_status": thesis.get("status"),
            "authority_status": thesis.get("authority_status"),
            "authority_label": authority_labels.get(str(thesis.get("authority_status")), str(thesis.get("authority_status") or "")),
            "invalid_conditions": thesis.get("invalid_conditions"),
            "key_metrics_to_monitor": metrics,
        }
        lines = []
        if thesis:
            lines.append(
                f"核心逻辑：{thesis.get('title') or '—'}（{thesis.get('status')}；"
                f"{payload['authority_label'] or '未标注权限'}）"
            )
            lines.append(f"论述：{thesis.get('core_thesis') or '—'}")
            invalid = thesis.get("invalid_conditions")
            if invalid:
                items = invalid if isinstance(invalid, list) else [invalid]
                lines.append("证伪条件：\n" + "\n".join(f"- {i}" for i in items[:5]))
        else:
            lines.append("核心逻辑：尚未建立。")
        if metrics:
            lines.append("关键跟踪指标（来自已保存财务研究）：\n" + "\n".join(f"- {m}" for m in metrics))
        return self._section("thesis_watchpoints", payload, "\n".join(lines))

    # -- 14 CIO 最终研究结论 ------------------------------------------------
    def build_cio_conclusion(self) -> dict[str, Any]:
        focus = self._focus_entry()
        zones = self._zones()
        valuation_status = str((zones.get("valuation") or {}).get("status") or "")
        financial = self._financial()
        if focus.startswith("A 档"):
            verdict = "重点研究"
        elif focus.startswith("B 档") or valuation_status in {"UNDERVALUED", "DEEPLY_UNDERVALUED"}:
            verdict = "继续观察"
        elif not financial:
            verdict = "资料不足"
        else:
            verdict = "暂缓优先研究"
        payload = {"verdict": verdict, "focus_entry": focus or None,
                   "valuation_status": valuation_status or None}
        narrative = (
            f"CIO 结论：{verdict}。" + (f"依据：{focus}。" if focus else "") +
            (f"估值状态 {valuation_status}。" if valuation_status else "") +
            "本结论不构成任何交易指令。"
        )
        return self._section("cio_conclusion", payload, narrative)


_BUILDERS: dict[str, Callable[[CioSectionBuilder], dict[str, Any]]] = {
    "company_position": CioSectionBuilder.build_company_position,
    "leader_quality": CioSectionBuilder.build_leader_quality,
    "financial_path": CioSectionBuilder.build_financial_path,
    "operating_stage": CioSectionBuilder.build_operating_stage,
    "quality_risk": CioSectionBuilder.build_quality_risk,
    "business_structure": CioSectionBuilder.build_business_structure,
    "moat": CioSectionBuilder.build_moat,
    "capital_allocation": CioSectionBuilder.build_capital_allocation,
    "valuation": CioSectionBuilder.build_valuation,
    "scenarios": CioSectionBuilder.build_scenarios,
    "why_research": CioSectionBuilder.build_why_research,
    "why_caution": CioSectionBuilder.build_why_caution,
    "thesis_watchpoints": CioSectionBuilder.build_thesis_watchpoints,
    "cio_conclusion": CioSectionBuilder.build_cio_conclusion,
}


def build_all_sections(market: str, stock_code: str, as_of: str) -> list[dict[str, Any]]:
    builder = CioSectionBuilder(market, stock_code, as_of)
    sections = []
    for section_type in SECTION_TITLES:
        try:
            sections.append(_BUILDERS[section_type](builder))
        except Exception as exc:  # noqa: BLE001 - one failed section must not sink the report
            sections.append(builder._gap_section(section_type, f"{type(exc).__name__}: {exc}"))
    return sections


def template_report_markdown(sections: list[dict[str, Any]], *, stock_code: str, as_of: str) -> str:
    """Deterministic fallback narrative: concatenates section templates."""
    parts = [f"# CIO 深度研究报告 · {stock_code}（研究基准日 {as_of}）", ""]
    for section in sections:
        parts.append(f"## {section['title']}")
        parts.append(section["narrative_md"] or "（本节资料不足）")
        parts.append("")
    return "\n".join(parts).strip()
