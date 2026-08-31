"""CIO Quick Brief — a deterministic projection of the persisted Full Report.

Zero specialist calls, zero LLM, zero external requests, zero research
refreshes (delivery polish §7).  The brief owns NO freshness of its own: it
inherits ``source_report_id`` / ``input_fingerprint`` / ``research_freshness``
from the Full Report, so it can never diverge from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.cio_report.service import SYNTHESIS_LLM_COMPLETED

_TRADING_LANGUAGE_RE = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓|建仓|不碰|试仓")
_MAX_ITEMS = 3


@dataclass(frozen=True)
class CioQuickBrief:
    stock_code: str
    stock_name: str
    research_as_of: str
    research_freshness: str
    synthesis_status: str

    verdict: str
    focus_tier: str | None
    valuation_status: str | None

    why_research: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    valuation_summary: str = ""
    thesis_summary: str = ""
    thesis_authority: str = ""
    watchpoints: list[str] = field(default_factory=list)

    source_report_id: int | None = None
    input_fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code, "stock_name": self.stock_name,
            "research_as_of": self.research_as_of,
            "research_freshness": self.research_freshness,
            "synthesis_status": self.synthesis_status,
            "verdict": self.verdict, "focus_tier": self.focus_tier,
            "valuation_status": self.valuation_status,
            "why_research": self.why_research, "cautions": self.cautions,
            "valuation_summary": self.valuation_summary,
            "thesis_summary": self.thesis_summary, "thesis_authority": self.thesis_authority,
            "watchpoints": self.watchpoints,
            "source_report_id": self.source_report_id,
            "input_fingerprint": self.input_fingerprint,
        }


def _clean(text: str) -> str:
    return _TRADING_LANGUAGE_RE.sub("■■", str(text or "")).strip()


def _section(report: dict[str, Any], section_type: str) -> dict[str, Any]:
    return next((s for s in report.get("sections") or []
                 if s.get("section_type") == section_type), {})


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        key = re.sub(r"\s|[（(].*?[)）]", "", item)[:24]
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _num(value: Any, suffix: str = "") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _brief_verdict(payload: dict[str, Any]) -> str:
    verdict = str(payload.get("verdict") or "")
    return verdict if verdict in {"重点研究", "继续观察", "暂缓优先研究", "资料不足"} else "资料不足"


def build_quick_brief(report: dict[str, Any]) -> CioQuickBrief:
    """Project one persisted CIO Full Report into the fixed six-block brief."""
    conclusion = dict(_section(report, "cio_conclusion").get("structured_payload") or {})
    why = dict(_section(report, "why_research").get("structured_payload") or {})
    caution = dict(_section(report, "why_caution").get("structured_payload") or {})
    valuation = dict(_section(report, "valuation").get("structured_payload") or {})
    thesis = dict(_section(report, "thesis_watchpoints").get("structured_payload") or {})
    risk = dict(_section(report, "quality_risk").get("structured_payload") or {})
    position = dict(_section(report, "company_position").get("structured_payload") or {})

    # 3) Cautions — UNKNOWN risk is a data boundary, never "low risk".
    cautions = [_clean(c) for c in list(caution.get("cautions") or []) if str(c).strip()]
    if str(risk.get("overall_risk") or "") == "UNKNOWN":
        cautions = [
            "风险等级 UNKNOWN＝当前资料不足，并非低风险；需结合后续财务与经营资料复核",
            *[c for c in cautions if "无明确谨慎信号" not in c],
        ]
    cautions = _dedupe(cautions)[:_MAX_ITEMS]

    # 4) Valuation summary — display arithmetic only (distance to mid).
    try:
        distance = f"{(float(valuation['current_price']) / float(valuation['fair_value_mid']) - 1) * 100:+.1f}%"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        distance = "—"
    valuation_bits = [
        f"现价 {_num(valuation.get('current_price'))}"
        f"（判定 {valuation.get('valuation_status') or '未知'}）",
        f"合理价值区间 {_num(valuation.get('fair_value_low'))}–{_num(valuation.get('fair_value_high'))}"
        f"，中值 {_num(valuation.get('fair_value_mid'))}，现价相对中值 {distance}",
        f"PE {_num(valuation.get('pe'))} / PB {_num(valuation.get('pb'))}",
    ]
    peers = [m for m in list(valuation.get("peer_methods") or [])
             if isinstance(m, dict) and str(m.get("status")) == "READY"]
    if peers:
        m = peers[0]
        valuation_bits.append(
            f"同行：{m.get('name')}（{m.get('peer_count')} 家，P25/P50/P75 = "
            f"{_num(m.get('multiple_low'))}/{_num(m.get('multiple_mid'))}/{_num(m.get('multiple_high'))}）")
    valuation_summary = "；".join(v for v in valuation_bits if "—" not in v or "区间" in v or "PE" in v) + "。"
    if "未物化" in str(valuation.get("plain_summary") or ""):
        valuation_summary += "历史估值分位尚未物化。"

    # 5) Thesis — AI_PROVISIONAL must read as pending human review.
    if thesis.get("thesis_title"):
        thesis_summary = _clean(f"{thesis.get('thesis_title')}：{thesis.get('thesis_status')}")
        authority = str(thesis.get("authority_label") or "")
        if str(thesis.get("authority_status")) == "AI_PROVISIONAL":
            authority = "AI初步核心逻辑 · 待人工复核"
        elif str(thesis.get("authority_status")) == "HUMAN_CONFIRMED":
            authority = "人工已确认"
        thesis_authority = authority
    else:
        thesis_summary = "当前尚未建立正式核心逻辑。"
        thesis_authority = ""

    # 6) Watchpoints — thesis metrics first, else the Round-1 deterministic fallback.
    watchpoints = [
        _clean(w) for w in list(thesis.get("key_metrics_to_monitor") or [])
        if str(w).strip()
    ]
    if not thesis.get("thesis_title"):
        watchpoints = [_clean(w) for w in list(thesis.get("fallback_watchpoints") or []) if str(w).strip()]
    if not watchpoints:
        # invalid_conditions may be {condition, status} dicts — extract text.
        watchpoints = [
            _clean(w.get("condition") or w.get("text") or "") if isinstance(w, dict) else _clean(w)
            for w in list(thesis.get("invalid_conditions") or [])
        ]
        watchpoints = [w for w in watchpoints if w]

    synthesis_status = str(report.get("synthesis_status")
                           or report.get("synthesis_source") or SYNTHESIS_LLM_COMPLETED)
    return CioQuickBrief(
        stock_code=str(report.get("stock_code") or ""),
        stock_name=str(position.get("stock_name") or report.get("stock_code") or ""),
        research_as_of=str(report.get("research_as_of") or ""),
        research_freshness=str(report.get("research_freshness")
                               or report.get("overall_freshness") or "UNKNOWN"),
        synthesis_status=synthesis_status,
        verdict=_brief_verdict(conclusion),
        focus_tier=conclusion.get("focus_tier"),
        valuation_status=conclusion.get("valuation_status"),
        why_research=_dedupe([_clean(r) for r in list(why.get("reasons") or [])])[:_MAX_ITEMS],
        cautions=cautions,
        valuation_summary=valuation_summary,
        thesis_summary=thesis_summary,
        thesis_authority=thesis_authority,
        watchpoints=watchpoints[:_MAX_ITEMS],
        source_report_id=report.get("id"),
        input_fingerprint=str(report.get("input_fingerprint") or ""),
    )


def render_quick_brief_md(brief: CioQuickBrief) -> str:
    """Feishu-friendly rendering (polish §14) — six blocks, never the 14 sections."""
    tier_text = f" · {brief.focus_tier} 档" if brief.focus_tier else ""
    lines = [
        f"【研究结论】{brief.verdict}{tier_text}"
        + (f" · 估值 {brief.valuation_status}" if brief.valuation_status else ""),
        "",
        "【为什么值得看】",
        *[f"{i}. {item}" for i, item in enumerate(brief.why_research or ["当前无突出研究理由"], 1)],
        "",
        "【主要风险】",
        *[f"{i}. {item}" for i, item in enumerate(brief.cautions or ["暂无已确认风险项"], 1)],
        "",
        f"【当前估值】{brief.valuation_summary}",
        "",
        f"【核心逻辑】{brief.thesis_summary}"
        + (f"（{brief.thesis_authority}）" if brief.thesis_authority else ""),
        "",
        "【接下来重点看】",
        *[f"{i}. {item}" for i, item in enumerate(brief.watchpoints or ["暂无可列验证点"], 1)],
        "",
        f"— 研究基准日 {brief.research_as_of} · 数据新鲜度 {brief.research_freshness}"
        f" · 综合 {brief.synthesis_status}。需要完整分析时可查看 CIO 深度报告。",
    ]
    return "\n".join(lines)
