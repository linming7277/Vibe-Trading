"""Deterministic 14-section CIO report builder (plan §14).

Every section is built 100% from persisted read-only research results; the
narrative here is template text (the "70%" layer).  The single synthesis LLM
in service.py re-narrates on top (the "30%"), with this template output as
the mandatory fallback (plan §15.2).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

CIO_REPORT_FORMULA_VERSION = "cio-report-v1"
SECTION_TEMPLATE_VERSION = "cio-section-template-v2"  # round1: 10-column table, PE/PB, labels, watchpoint fallback

SECTION_TITLES: dict[str, str] = {
    "company_position": "01 公司与产业位置",
    "leader_quality": "02 龙头质量与同行优势",
    "financial_path": "03 多年财务路径",
    "latest_quarter": "03b 最新季度边际变化",
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

_TRADING_LANGUAGE_RE = re.compile(r"买入|卖出|推荐|仓位|止盈|止损|加仓|减仓|建仓")


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


# Timestamps that mirror the research clock or intraday quote refreshes must
# never change a section fingerprint (quality fix §9): price VALUES still do
# (they stay in the payload), only their timestamp bookkeeping is stripped.
_FP_VOLATILE_KEY_RE = re.compile(r"(?:_?as_of|_?updated_at|data_dates)$")


def _fingerprint_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _fingerprint_safe(item)
            for key, item in value.items()
            if not _FP_VOLATILE_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [_fingerprint_safe(item) for item in value]
    return value


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
        """Read the latest Business Research snapshot without an as_of filter.

        The snapshot's own ``data_as_of`` is the natural PIT date — a same-day
        TDX cache refresh would otherwise hide a valid snapshot behind the
        CIO's research clock (this was the §7 mapping bug).
        """
        from src.business_research import get_business_research_service

        try:
            row = get_business_research_service().store.latest(self.code) or {}
        except Exception:  # noqa: BLE001
            return {}
        if not row:
            return {}
        # The real business text lives inside row["snapshot"], not at the top
        # level of the raw store row.
        snap = dict(row.get("snapshot") or {})
        analysis = dict(row.get("analysis") or {})
        return {
            "analysis_status": row.get("analysis_status"),
            "main_business": snap.get("main_business"),
            "products": snap.get("products") or [],
            "business_changes": snap.get("business_changes") or [],
            "product_note": snap.get("product_note"),
            "data_quality": snap.get("data_quality") or {},
            "sources": snap.get("sources") or {},
            "claims": analysis.get("claims") or [],
            "executive_summary": analysis.get("executive_summary"),
            "data_as_of": row.get("data_as_of"),
        }

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
        safe_narrative = _TRADING_LANGUAGE_RE.sub("■■", narrative)
        return {
            "section_type": section_type,
            "title": SECTION_TITLES[section_type],
            "input_fingerprint": _digest({"v": SECTION_TEMPLATE_VERSION, "payload": _fingerprint_safe(payload)}),
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
        # Main-business fallback chain (deep-research audit §3): Business
        # Research first, then the always-available TDX CompanyBusinessProfile
        # (pool-external companies usually have the profile but no research
        # snapshot yet), and only then "资料不足".
        profile_main_business = ""
        try:
            from src.level3_leaders.business_profiles import CompanyBusinessProfileService

            profile = CompanyBusinessProfileService().profile(self.code)
            profile_main_business = str((profile or {}).get("main_business") or "").strip()
        except Exception:  # noqa: BLE001 - display fallback only
            profile_main_business = ""
        main_business = (str(business.get("main_business") or "").strip()
                         or str(identity.get("main_business") or "").strip()
                         or profile_main_business)
        payload = {
            "stock_name": identity.get("stock_name"),
            "level1_name": identity.get("level1_name"), "level2_name": identity.get("level2_name"),
            "level3_name": identity.get("level3_name"),
            "is_current_l3_leader": identity.get("is_current_l3_leader"),
            "main_business": main_business or None,
            "main_business_source": "business_research" if str(business.get("main_business") or "").strip()
            else ("business_profile" if profile_main_business and main_business == profile_main_business else None),
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
    _LEADER_STATUS_LABELS = {
        "STRONG": "强", "ABOVE_AVERAGE": "偏强", "AVERAGE": "中性",
        "BELOW_AVERAGE": "偏弱", "WEAK": "弱", "UNKNOWN": "资料不足",
    }

    @classmethod
    def _leader_dim_lines(cls, entries: list[Any], prefix: str) -> list[str]:
        """Render strength/weakness entries as boss-readable labels, never dict reprs."""
        lines = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or entry.get("dimension") or "")
            status = cls._LEADER_STATUS_LABELS.get(
                str(entry.get("status") or ""), str(entry.get("status") or "未知"))
            metrics = "、".join(str(m) for m in list(entry.get("metrics") or [])[:3])
            lines.append(f"{prefix}{label}{status}" + (f"（{metrics}）" if metrics else ""))
        return lines

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
        strengths = self._leader_dim_lines(payload.get("strengths"), "") or ["—"]
        weaknesses = self._leader_dim_lines(payload.get("weaknesses"), "") or ["—"]
        narrative = (
            f"龙头质量状态：{payload.get('status') or '未知'}；"
            f"行业内排名 {payload.get('rank_in_industry') or '—'} / {payload.get('industry_member_count') or '—'}。\n"
            f"主要强项：{'；'.join(strengths)}。\n主要弱项：{'；'.join(weaknesses)}。"
        )
        return self._section("leader_quality", payload, narrative)

    # -- 03 多年财务路径 ---------------------------------------------------
    def build_financial_path(self) -> dict[str, Any]:
        financial = self._financial()
        rows = [dict(r) for r in list(financial.get("history") or []) if str(r.get("period_type") or "") == "annual"]
        if not rows:
            return self._gap_section("financial_path", "暂无年化财务历史")
        rows = rows[-8:]
        # Ten target columns (quality fix §4); missing values render as — and
        # are never recomputed.
        table_lines = [
            "| 年度 | 营收 | 归母净利 | 毛利率 | 净利率 | ROE | 经营现金流 | 应收账款 | 存货 | 资产负债率 | 资本开支 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            table_lines.append(
                f"| {str(row.get('report_date') or '')[:4]} | {_yi(row.get('revenue'))} | "
                f"{_yi(row.get('net_profit'))} | {_pct(row.get('gross_margin'))} | "
                f"{_pct(row.get('net_margin'))} | {_pct(row.get('roe'))} | "
                f"{_yi(row.get('operating_cash_flow'))} | {_yi(row.get('accounts_receivable'))} | "
                f"{_yi(row.get('inventory'))} | {_pct(row.get('debt_ratio'))} | {_yi(row.get('capex'))} |"
            )
        payload = {"rows": rows, "feature_status": financial.get("feature_status")}
        return self._section("financial_path", payload, "\n".join(table_lines))

    # -- 03b 最新一期边际变化（季度） ------------------------------------------
    def build_latest_quarter(self) -> dict[str, Any]:
        """Render the latest quarterly period separately from the 5-year annual
        table (task §4): YoY vs the same quarter last year, never mixed into
        the annual table."""
        financial = self._financial()
        history = list(financial.get("history") or [])
        quarterly = [dict(r) for r in history if str(r.get("period_type") or "") != "annual" and r.get("report_date")]
        if not quarterly:
            return self._gap_section("latest_quarter", "暂无最新季度数据")
        latest_q = quarterly[-1]
        latest_date = str(latest_q.get("report_date") or "")[:10]

        # Find the same quarter of the prior year from raw TDX history.
        prior_q = None
        try:
            from src.tdx_data import get_tdx_service
            from src.tdx_data.financial_history import FinancialHistoryService

            raw = FinancialHistoryService(store=getattr(get_tdx_service(), "store", None))
            rows = list(raw.query(self.code, as_of=self.as_of).get("items") or [])
            month_day = latest_date[5:]
            prior_year = str(int(latest_date[:4]) - 1)
            prior_date = f"{prior_year}-{month_day}"
            prior_q = next((dict(r) for r in rows if str(r.get("report_date") or "")[:10] == prior_date), None)
        except Exception:  # noqa: BLE001
            prior_q = None

        def _yoy(cur, prev):
            try:
                cur_v, prev_v = float(cur), float(prev)
                if prev_v == 0:
                    return None
                return (cur_v / prev_v - 1) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                return None

        metrics: list[dict[str, Any]] = []
        for field, label, kind in (
            ("revenue", "营收", "yi"), ("net_profit", "归母净利润", "yi"),
            ("gross_margin", "毛利率", "pct"), ("operating_cash_flow", "经营现金流", "yi"),
            ("accounts_receivable", "应收账款", "yi"), ("inventory", "存货", "yi"),
            ("debt_ratio", "资产负债率", "pct"), ("interest_bearing_debt_ratio", "带息债务率", "pct"),
        ):
            value = latest_q.get(field)
            if value is None:
                continue
            yoy = _yoy(value, (prior_q or {}).get(field)) if prior_q else latest_q.get(f"{field}_yoy") if field == "net_profit" else None
            metrics.append({
                "item": label,
                "value": _pct(value) if kind == "pct" else _yi(value),
                "yoy": f"{yoy:+.1f}%" if yoy is not None else "—",
            })
        payload = {
            "report_date": latest_date, "period_type": latest_q.get("period_type"),
            "prior_quarter_date": str((prior_q or {}).get("report_date") or "")[:10] or None,
            "metrics": metrics,
            "deducted_net_profit_available": False,  # audited: no such field in TDX
        }
        lines = [f"**最新季度：{latest_date}**（与{str((prior_q or {}).get('report_date') or '上期')[:10]}对比）", ""]
        lines.append("；".join(f"{m['item']} {m['value']}（同比 {m['yoy']}）" for m in metrics[:8]) + "。")
        lines.append("")
        lines.append("以上为季度数据，与年报表分开解读，不混入五年路径。")
        return self._section("latest_quarter", payload, "\n".join(lines))
    def build_operating_stage(self) -> dict[str, Any]:
        financial = self._financial()
        feature = dict(financial.get("feature") or {})
        # feature.growth.<metric> is a LIST of {report_date, announcement_date,
        # period_type, value} points (engine._points), never a dict.
        revenue_points = [dict(p) for p in list((feature.get("growth") or {}).get("revenue") or [])
                          if isinstance(p, dict) and p.get("period_type") == "annual" and p.get("value") is not None]
        profit_points = [dict(p) for p in list((feature.get("growth") or {}).get("net_profit") or [])
                         if isinstance(p, dict) and p.get("period_type") == "annual" and p.get("value") is not None]
        revenue_points = revenue_points[-5:]
        profit_points = profit_points[-5:]

        def _yoy(points: list[dict[str, Any]]) -> list[float]:
            result = []
            for prev, cur in zip(points, points[1:]):
                try:
                    prev_value, cur_value = float(prev["value"]), float(cur["value"])
                except (TypeError, ValueError):
                    continue
                if prev_value:
                    result.append((cur_value / prev_value - 1) * 100)
            return result

        revenue_yoy = _yoy(revenue_points)
        profit_values = [float(p["value"]) for p in profit_points if _num(p["value"]) != "—"] if profit_points else []

        stage, stage_detail = self._classify_stage(revenue_yoy, profit_values)
        payload = {
            "stage": stage, "stage_detail": stage_detail,
            "revenue_yoy_series": [round(v, 2) for v in revenue_yoy],
            "revenue_years": [str(p.get("report_date") or "")[:4] for p in revenue_points],
            "forecast_status": (financial.get("forecast") or {}).get("status"),
        }
        yoy_text = "、".join(f"{v:+.1f}%" for v in revenue_yoy) or "—"
        narrative = (
            f"经营阶段判定：{stage}（{stage_detail}）。\n"
            f"依据（近五个年报营收同比序列，确定性计算）：{yoy_text}。"
            f"系统情景引擎状态：{payload['forecast_status'] or '未知'}（详见 Bear/Base/Bull 一节）。"
        )
        return self._section("operating_stage", payload, narrative)

    @staticmethod
    def _classify_stage(revenue_yoy: list[float], profit_values: list[float]) -> tuple[str, str]:
        """Deterministic stage classification from annual paths only (plan fix §2).

        UNKNOWN whenever the annual series is too short — no model priors.
        """
        if len(revenue_yoy) < 2:
            return "UNKNOWN", "年报营收序列不足，无法确定性判定"
        latest_yoy = revenue_yoy[-1]
        growing = [y > 0 for y in revenue_yoy]
        if all(growing) and min(revenue_yoy) >= 10:
            return "GROWTH", "营收连续较快增长"
        if all(growing):
            return "STABLE_GROWTH", "营收连续温和增长"
        if len(profit_values) >= 3:
            peak = max(profit_values)
            trough = min(profit_values)
            latest = profit_values[-1]
            if trough < 0 < latest and latest < peak:
                return "CYCLICAL_RECOVERY", "盈利经历亏损后处于修复期，尚未回到前高"
            if latest > trough and trough >= 0 and peak > 0:
                return "RECOVERY", "盈利自低位回升"
        if latest_yoy < 0 and len([y for y in revenue_yoy if y < 0]) >= 2:
            return "DECLINING", "营收连续下滑"
        if latest_yoy < 0:
            return "DECLINING", "最新年报营收同比转负"
        return "STABLE_GROWTH", "营收波动后保持增长"

    # -- 05 盈利质量与财务风险 ----------------------------------------------
    def build_quality_risk(self) -> dict[str, Any]:
        risk = self._risk()
        risks = [dict(r) for r in list(risk.get("risks") or [])[:6]]
        financial = self._financial()
        annual = [dict(r) for r in list(financial.get("history") or [])
                  if str(r.get("period_type") or "") == "annual"]
        latest = annual[-1] if annual else {}
        # Fact observations supplement the confirmed rule risks — they are
        # never promoted to a risk level (quality fix §6).
        observations: list[dict[str, Any]] = []
        try:
            ocf = float(latest.get("operating_cash_flow"))
            profit = float(latest.get("net_profit"))
            observations.append({
                "item": "OCF/归母净利", "value": f"{ocf / profit:.2f}" if profit else "—",
                "note": f"{_yi(ocf)} vs {_yi(profit)}（{str(latest.get('report_date') or '')[:4]}年报）",
            })
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        for key, label in (
            ("accounts_receivable", "应收账款"), ("inventory", "存货"),
            ("capex", "资本开支"), ("interest_bearing_debt_ratio", "带息债务率"),
            ("debt_ratio", "资产负债率"),
        ):
            value = latest.get(key)
            if value is None:
                continue
            observations.append({
                "item": label,
                "value": _pct(value) if "ratio" in key else _yi(value),
                "note": f"{str(latest.get('report_date') or '')[:4]}年报",
            })
        if not risks and not risk and not observations:
            return self._gap_section("quality_risk", "暂无风险研究结果")
        payload = {
            "overall_risk": risk.get("overall_risk"),
            "summary": risk.get("summary"),
            "risks": [{"risk_type": r.get("risk_type"), "severity": r.get("severity"),
                       "text": r.get("text")} for r in risks],
            "value_trap_risk": risk.get("value_trap_risk"),
            "fact_observations": observations,
        }
        lines = [f"总体风险：{risk.get('overall_risk') or '未知'}。{risk.get('summary') or ''}"]
        if risks:
            lines.append("已确认风险（来自风险研究规则）：")
            for r in risks:
                lines.append(f"- [{r.get('severity')}] {r.get('risk_type')}：{r.get('text') or ''}")
        if risk.get("value_trap_risk"):
            lines.append(f"低估陷阱维度：{risk['value_trap_risk']}。")
        if observations:
            lines.append("财务事实观察项（仅为数值观察，不构成风险等级）：")
            lines.append("；".join(f"{o['item']} {o['value']}（{o['note']}）" for o in observations[:6]) + "。")
        return self._section("quality_risk", payload, "\n".join(lines))

    # -- 06 经营与业务结构 --------------------------------------------------
    def build_business_structure(self) -> dict[str, Any]:
        business = self._business()
        claims = [dict(c) for c in list(business.get("claims") or [])[:8]]
        if not business.get("main_business"):
            # Profile-only fallback (deep-research audit §3).
            profile_main = ""
            try:
                from src.level3_leaders.business_profiles import CompanyBusinessProfileService

                profile = CompanyBusinessProfileService().profile(self.code)
                profile_main = str((profile or {}).get("main_business") or "").strip()
            except Exception:  # noqa: BLE001
                profile_main = ""
            if not profile_main:
                return self._gap_section("business_structure", "尚无经营研究")
            business["main_business"] = profile_main
            business["analysis_status"] = "PROFILE_ONLY"
        payload = {
            "analysis_status": business.get("analysis_status"),
            "main_business": business.get("main_business"),
            "products": business.get("products") or [],
            "business_changes": business.get("business_changes") or [],
            "claims": [{"type": c.get("type"), "statement": c.get("statement"),
                        "source_keys": c.get("source_keys")} for c in claims],
            "data_as_of": business.get("data_as_of"),
            "source_count": len(business.get("sources") or {}),
        }
        lines = [f"主营业务：{business.get('main_business')}"]
        products = business.get("products") or []
        if products:
            lines.append(f"主要产品/服务：{'、'.join(str(p) for p in products[:8])}。")
        note = business.get("product_note")
        if note and "产品收入占比" in str(note):
            lines.append("当前系统尚无可靠分部收入比例，不能给出各产品收入占比。")
        changes = business.get("business_changes") or []
        real_changes = [str(c) for c in changes if str(c).strip() and not str(c).startswith("UNKNOWN")]
        if real_changes:
            lines.append("经营变化：")
            for c in real_changes[:4]:
                lines.append(f"- {c}")
        elif claims:
            lines.append("披露事实：")
            for c in claims:
                lines.append(f"- [{c.get('type')}] {c.get('statement')}")
        if not real_changes and not claims:
            lines.append("当前系统尚无可靠分部收入比例与经营变化资料。")
        return self._section("business_structure", payload, "\n".join(lines))

    # -- 07 竞争优势 ------------------------------------------------------
    _MOAT_STATUS_LABELS = {
        "SUPPORTED": "有较明确证据支持",
        "PARTIAL": "存在部分证据，但持续性/同行相对优势仍不足",
        "UNKNOWN": "当前资料不足，暂无法判断",
        "COUNTER_EVIDENCE": "存在反证",
    }

    def build_moat(self) -> dict[str, Any]:
        from src.moat_research import get_moat_research_service

        try:
            moat = dict(get_moat_research_service().get_research(self.market, self.code, as_of=self.as_of) or {})
        except Exception:  # noqa: BLE001
            moat = {}
        if not moat:
            return self._gap_section("moat", "尚无护城河研究")
        dims = [dict(d) for d in list(moat.get("dimensions") or []) if isinstance(d, dict)]
        payload = {
            "evidence_count": moat.get("evidence_count"),
            "counter_evidence_count": moat.get("counter_evidence_count"),
            "dimensions": [
                {"dimension": d.get("moat_dimension") or d.get("dimension"),
                 "label": d.get("label"),
                 "status": d.get("status"),
                 "summary": d.get("summary"),
                 "evidence": [
                     {"claim": (e.get("claim") or e.get("fact") or "")[:120],
                      "source_type": e.get("source_type") or e.get("evidence_type"),
                      "period": e.get("data_as_of") or e.get("announcement_date")}
                     for e in list(d.get("supporting_evidence") or [])[:3]
                     if isinstance(e, dict)
                 ]}
                for d in dims
            ],
        }
        lines = [f"竞争优势研究：证据 {moat.get('evidence_count') or 0} 条、反证 {moat.get('counter_evidence_count') or 0} 条。"]
        for d in payload["dimensions"]:
            status = str(d.get("status") or "")
            zh_status = self._MOAT_STATUS_LABELS.get(status, status)
            name = str(d.get("label") or d.get("dimension") or "")
            lines.append(f"\n**{name}**：{zh_status}。")
            summary = str(d.get("summary") or "")
            if summary and summary != "None":
                lines.append(f"  {summary[:200]}")
            for ev in d.get("evidence") or []:
                source = str(ev.get("source_type") or "来源")
                period = str(ev.get("period") or "")[:10]
                lines.append(f"  - {ev.get('claim')}（{source}{'，' + period if period else ''}）")
            if status == "SUPPORTED" and not d.get("evidence"):
                lines.append("  （证据详情暂未映射到研究快照）")
        has_supported = any(str(d.get("status")) == "SUPPORTED" for d in payload["dimensions"])
        has_counter = moat.get("counter_evidence_count") and int(moat["counter_evidence_count"]) > 0
        if not has_supported:
            lines.append("\n当前无任何维度获得较明确证据支持，不据此认定竞争优势；规模、排名或知名度本身不构成护城河。")
        if has_counter:
            lines.append(f"\n⚠️ 存在 {moat['counter_evidence_count']} 条反证，需与支持证据一并权衡。")
        return self._section("moat", payload, "\n".join(lines))

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
        # PE/PB/dividend_yield live in the financial snapshot's
        # identity.market_valuation, not in the zones payload (audit fix §3).
        market_valuation = dict((self._financial().get("identity") or {}).get("market_valuation") or {})
        payload = {
            "current_price": zones.get("current_price"),
            "valuation_status": valuation.get("status"),
            "fair_value_low": valuation.get("fair_value_low"), "fair_value_mid": valuation.get("fair_value_mid"),
            "fair_value_high": valuation.get("fair_value_high"),
            "pe": market_valuation.get("pe"), "pb": market_valuation.get("pb"),
            "dividend_yield": market_valuation.get("dividend_yield"),
            "market_cap": market_valuation.get("market_cap"),
            "as_of": zones.get("as_of"),
            "plain_summary": zones.get("plain_summary"),
            "peer_methods": [
                {"name": m.get("name"), "status": m.get("status"), "peer_count": m.get("peer_count"),
                 "multiple_low": m.get("multiple_low"), "multiple_mid": m.get("multiple_mid"),
                 "multiple_high": m.get("multiple_high")}
                for m in list(valuation.get("methods") or []) if isinstance(m, dict)
            ],
        }
        try:
            mid = float(payload["fair_value_mid"])
            price = float(payload["current_price"])
            distance = f"{(price / mid - 1) * 100:+.1f}%"
        except (TypeError, ValueError, ZeroDivisionError):
            distance = "—"
        peer_lines = []
        for m in payload["peer_methods"]:
            if str(m.get("status")) != "READY":
                continue
            multiples = (
                f"{_num(m.get('multiple_low'))} / {_num(m.get('multiple_mid'))} / {_num(m.get('multiple_high'))}"
                if m.get("multiple_low") is not None else ""
            )
            peer_lines.append(
                f"- {m.get('name')}：{m.get('peer_count')} 家可比，P25/P50/P75 = {multiples}"
                if multiples else f"- {m.get('name')}（{m.get('peer_count')} 家可比）"
            )
        narrative = (
            f"现价 {payload['current_price'] or '—'}（{str(payload['as_of'] or '')[:10]}）；"
            f"估值判定 {payload['valuation_status'] or '未知'}；"
            f"系统合理价值区间 {_num(payload['fair_value_low'])} – {_num(payload['fair_value_high'])}"
            f"（中值 {_num(payload['fair_value_mid'])}），现价相对中值 {distance}。\n"
            f"PE {_num(payload['pe'])} / PB {_num(payload['pb'])} / 股息率 "
            + (f"{float(payload['dividend_yield']):.2f}%" if payload["dividend_yield"] is not None else "UNKNOWN")
            + f" / 总市值 {_num(payload['market_cap'])} 亿。"
            + (("\n同行估值：\n" + "\n".join(peer_lines)) if peer_lines else "")
            + f"\n{zones.get('plain_summary') or ''}"
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
        focus = self._focus_entry()
        financial = self._financial()
        annual = [dict(r) for r in list(financial.get("history") or [])
                  if str(r.get("period_type") or "") == "annual"]
        quarterly = [dict(r) for r in list(financial.get("history") or [])
                     if str(r.get("period_type") or "") != "annual"]
        reasons: list[str] = []
        # Priority order (task §12): positive changes > business drivers >
        # valuation > moat/leadership > risk-not-triggered (last, never first).
        if focus.get("tier") in {"A", "B"}:
            reasons.append(f"焦点档位 {focus['label']}" + (f"（{focus['reason']}）" if focus.get("reason") else ""))
        if len(annual) >= 2:
            first_rev, last_rev = _f(annual[0].get("revenue")), _f(annual[-1].get("revenue"))
            if first_rev and last_rev and last_rev > first_rev * 1.3:
                reasons.append(f"收入持续增长（{str(annual[0].get('report_date'))[:4]}–{str(annual[-1].get('report_date'))[:4]}年均复合增长显著）")
            profits = [_f(r.get("net_profit")) for r in annual if _f(r.get("net_profit")) is not None]
            if len(profits) >= 3 and min(profits) < 0 < profits[-1]:
                reasons.append("利润从亏损恢复至盈利，经营基本面出现修复信号")
            ocf_last = _f(annual[-1].get("operating_cash_flow")) if annual else None
            if ocf_last and len(annual) >= 2:
                ocf_prev = _f(annual[-2].get("operating_cash_flow"))
                if ocf_prev and ocf_last > ocf_prev * 2 and ocf_last > 0:
                    reasons.append(f"最新年度经营现金流明显改善（{_yi(ocf_last)}）")
        if quarterly:
            q = quarterly[-1]
            q_yoy = _f(q.get("net_profit_yoy"))
            if q_yoy and q_yoy > 20:
                reasons.append(f"最新季度净利润同比 +{q_yoy:.0f}%，边际改善延续")
        if valuation_status in {"UNDERVALUED", "DEEPLY_UNDERVALUED"}:
            reasons.append(f"估值处于低估区域（{valuation_status}）")
        moat_supported = [d for d in list(moat.get("dimensions") or [])
                          if isinstance(d, dict) and str(d.get("status")) == "SUPPORTED"]
        if moat_supported:
            dim_labels = "、".join(str(d.get("label") or d.get("dimension") or "") for d in moat_supported[:2])
            reasons.append(f"竞争优势研究显示部分维度有证据支持（{dim_labels}）")
        if not reasons:
            if risk.get("overall_risk") in {"LOW", "MEDIUM"}:
                reasons.append(f"风险等级 {risk.get('overall_risk')}，无已触发的高危降级项（仅为限制条件，非首要亮点）")
            else:
                reasons.append("当前各研究层未给出明显的继续研究信号；以数据边界为准")
        payload = {"reasons": reasons[:5], "valuation_status": valuation_status,
                   "focus_tier": focus.get("tier")}
        return self._section("why_research", payload,
                             "\n".join(f"- {r}" for r in reasons[:5]))

    def _focus_entry(self) -> dict[str, Any]:
        """Read the real FocusSelection contract: tier keys are "A"/"B"/"C"."""
        try:
            from src.focus_selection import get_focus_selection_service

            data = get_focus_selection_service().get_focus_selection(as_of=self.as_of) or {}
            for tier_key, tier_label in (("A", "A 档重点研究"), ("B", "B 档继续观察"), ("C", "C 档暂缓优先研究")):
                for item in list(data.get(tier_key) or []):
                    if str(item.get("stock_code") or "").upper() == self.code:
                        reasons = [str(r) for r in list(item.get("focus_reasons") or []) if str(r).strip()]
                        return {
                            "tier": tier_key, "label": tier_label,
                            "reason": reasons[0] if reasons else "",
                            "reasons": reasons[:3],
                        }
        except Exception:  # noqa: BLE001
            return {}
        return {}

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
            "fallback_watchpoints": [] if thesis or metrics else self._fallback_watchpoints(),
            # Deep-research task §8: a valid DRAFT is a middle state — shown
            # as "AI 研究草稿 · 待人工确认", never as a formal thesis.
            "thesis_draft": self._thesis_draft_payload() if not thesis else None,
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
                # Conditions may arrive as plain strings or as
                # {condition, status} dicts — extract the text, never repr.
                conditions = [
                    str(item.get("condition") or item.get("text") or "") if isinstance(item, dict) else str(item)
                    for item in items[:5]
                ]
                conditions = [c for c in conditions if c.strip()]
                if conditions:
                    lines.append("证伪条件：\n" + "\n".join(f"- {c}" for c in conditions))
        else:
            lines.append("核心逻辑：尚未建立（本节验证点为无核心逻辑时的确定性降级生成，非公司论点）。")
        if metrics:
            lines.append("关键跟踪指标（来自已保存财务研究）：\n" + "\n".join(f"- {m}" for m in metrics))
        elif not thesis:
            lines.append("验证点（确定性降级生成）：")
            for item in payload["fallback_watchpoints"]:
                lines.append(f"- {item}")
        return self._section("thesis_watchpoints", payload, "\n".join(lines))

    def _thesis_draft_payload(self) -> dict[str, Any] | None:
        """Latest valid thesis DRAFT for the §13 middle state (draft ≠ thesis)."""
        try:
            from src.company_thesis.draft_store import CompanyThesisDraftRepository

            draft = CompanyThesisDraftRepository().latest("CN", self.code)
            if not draft or str(draft.get("draft_status") or "") != "DRAFT":
                return None
            return {
                "title": draft.get("title"), "core_thesis": draft.get("core_thesis"),
                "core_drivers": list(draft.get("core_drivers") or [])[:4],
                "key_assumptions": list(draft.get("key_assumptions") or [])[:4],
                "invalid_conditions": [
                    str(c.get("condition") if isinstance(c, dict) else c or "")
                    for c in list(draft.get("invalid_conditions") or [])[:4]
                ],
                "key_metrics_to_monitor": list(draft.get("key_metrics_to_monitor") or [])[:4],
                "source_data_as_of": draft.get("source_data_as_of"),
            }
        except Exception:  # noqa: BLE001 - the draft is enrichment only
            return None

    def _fallback_watchpoints(self) -> list[str]:
        """Deterministic no-thesis watchpoints from persisted results only (fix §7).

        No invented thesis, no trading parameters (no MA/stop/target/position).
        """
        financial = self._financial()
        annual = [dict(r) for r in list(financial.get("history") or [])
                  if str(r.get("period_type") or "") == "annual"]
        items: list[str] = []
        if len(annual) >= 2:
            prev, latest = annual[-2], annual[-1]
            year = str(latest.get("report_date") or "")[:4]
            if latest.get("gross_margin") is not None:
                items.append(f"毛利率是否延续修复（{year}年报 {_pct(latest.get('gross_margin'))}，前一年 {_pct(prev.get('gross_margin'))}）")
            if latest.get("operating_cash_flow") is not None and latest.get("net_profit"):
                items.append(f"经营现金流与净利润的匹配程度（{year}年报 OCF {_yi(latest.get('operating_cash_flow'))} vs 净利 {_yi(latest.get('net_profit'))}）")
            if latest.get("accounts_receivable") is not None or latest.get("inventory") is not None:
                items.append(f"应收账款（{_yi(latest.get('accounts_receivable'))}）与存货（{_yi(latest.get('inventory'))}）的后续变化")
        scenarios = dict((financial.get("forecast") or {}).get("scenarios") or {})
        base_rows = [dict(r) for r in list((scenarios.get("BASE") or {}).get("forecast") or [])]
        if base_rows:
            last = base_rows[-1]
            items.append(f"基准情景是否兑现（{last.get('year')} 营收 {_yi(last.get('revenue'))}，来自系统 Forecast）")
        zones = self._zones()
        mid = (zones.get("valuation") or {}).get("fair_value_mid")
        if mid is not None:
            items.append(f"现价相对系统合理价值中值 {_num(mid)} 的偏离是否收敛")
        return items[:6] or ["暂无可确定性生成的验证点（财务历史不足）"]

    # -- 14 CIO 最终研究结论 ------------------------------------------------
    def build_cio_conclusion(self) -> dict[str, Any]:
        focus = self._focus_entry()
        tier = str(focus.get("tier") or "")
        zones = self._zones()
        valuation_status = str((zones.get("valuation") or {}).get("status") or "")
        financial = self._financial()
        # Tier first (plan §14): A 档 → 重点研究；C 档估值再低也不升级为
        # 重点研究；只有无档位信息时才退回估值/资料判定。
        if tier == "A":
            verdict = "重点研究"
        elif tier == "C":
            verdict = "暂缓优先研究"
        elif tier == "B" or valuation_status in {"UNDERVALUED", "DEEPLY_UNDERVALUED"}:
            verdict = "继续观察"
        elif not financial:
            verdict = "资料不足"
        else:
            verdict = "暂缓优先研究"
        payload = {"verdict": verdict, "focus_tier": tier or None,
                   "focus_reason": focus.get("reason") or None,
                   "valuation_status": valuation_status or None}
        focus_text = f"{focus['label']}" + (f"（{focus['reason']}）" if focus.get("reason") else "") if tier else ""
        narrative = (
            f"CIO 结论：{verdict}。" + (f"依据：{focus_text}。" if focus_text else "") +
            (f"估值状态 {valuation_status}。" if valuation_status else "") +
            "本结论不构成任何交易指令。"
        )
        return self._section("cio_conclusion", payload, narrative)


_BUILDERS: dict[str, Callable[[CioSectionBuilder], dict[str, Any]]] = {
    "company_position": CioSectionBuilder.build_company_position,
    "leader_quality": CioSectionBuilder.build_leader_quality,
    "financial_path": CioSectionBuilder.build_financial_path,
    "latest_quarter": CioSectionBuilder.build_latest_quarter,
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
            # Raw exception text must never reach the boss-facing narrative;
            # it goes to the log for diagnosis instead (quality fix §2.5).
            import logging

            logging.getLogger(__name__).warning(
                "CIO section %s failed for %s@%s: %s: %s",
                section_type, stock_code, as_of, type(exc).__name__, exc,
            )
            sections.append(builder._gap_section(section_type, "该节数据处理暂不可用，已按资料不足降级"))
    return sections


def template_report_markdown(sections: list[dict[str, Any]], *, stock_code: str, as_of: str) -> str:
    """Deterministic fallback narrative: concatenates section templates."""
    parts = [f"# CIO 深度研究报告 · {stock_code}（研究基准日 {as_of}）", ""]
    for section in sections:
        parts.append(f"## {section['title']}")
        parts.append(section["narrative_md"] or "（本节资料不足）")
        parts.append("")
    return "\n".join(parts).strip()
