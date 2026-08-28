"""CIO report orchestration: build (deterministic sections + one synthesis LLM),
persist, and read (plan §12-§15)."""

from __future__ import annotations

import re
from typing import Any

from src.cio_report.builder import (
    CIO_REPORT_FORMULA_VERSION,
    SECTION_TITLES,
    build_all_sections,
    template_report_markdown,
)
from src.cio_report.store import CioReportStore

CIO_SYNTHESIS_PROMPT_VERSION = "cio-synthesis-v1"
_TRADING_LANGUAGE = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓|建仓")


class CioReportService:
    def __init__(self, store: CioReportStore | None = None) -> None:
        self.store = store or CioReportStore()

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------
    def get_report(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        report = self.store.latest_report(market, stock_code.upper(), as_of=as_of)
        if report is None:
            return None
        report["sections"] = sorted(report.get("sections") or [], key=lambda s: list(SECTION_TITLES).index(s["section_type"]))
        return report

    # ------------------------------------------------------------------
    # build / refresh
    # ------------------------------------------------------------------
    def build_report(self, market: str, stock_code: str, *, as_of: str | None = None,
                     force_synthesis: bool = False) -> dict[str, Any]:
        """Build all 14 sections deterministically, then one synthesis LLM call.

        Section rebuilds are always safe (pure reads); the synthesis LLM only
        reruns when the report fingerprint actually changed (plan §15.2).
        """
        from src.research_freshness import get_research_freshness_service

        market, code = market.upper(), stock_code.upper()
        research_as_of = str(as_of or get_research_freshness_service()._resolve_as_of(None))[:10]
        freshness = get_research_freshness_service().classify(market, code, research_as_of)
        module_status = {m["module"]: m["status"] for m in freshness["modules"]}

        sections = build_all_sections(market, code, research_as_of)
        previous = self.store.latest_report(market, code, as_of=research_as_of)
        # Section-level incremental audit (plan §16/§25): unchanged sections
        # are marked REUSED and changed ones REFRESHED — only a changed set
        # justifies a new synthesis.
        previous_sections = {
            s["section_type"]: str(s.get("input_fingerprint") or "")
            for s in (previous or {}).get("sections") or []
        }
        for section in sections:
            old_fp = previous_sections.get(section["section_type"])
            section["freshness_status"] = "REUSED" if old_fp == section["input_fingerprint"] else "REFRESHED"
        section_fps = {s["section_type"]: s["input_fingerprint"] for s in sections}
        report_fingerprint = "|".join(
            f"{name}:{fp}" for name, fp in sorted(section_fps.items())
        )
        previous_fp = str((previous or {}).get("input_fingerprint") or "")
        unchanged = bool(previous) and previous_fp == report_fingerprint and not force_synthesis

        if unchanged and str((previous or {}).get("status")) == "READY":
            return {**previous, "idempotent_reuse": True}

        template_md = template_report_markdown(sections, stock_code=code, as_of=research_as_of)
        synthesis_source, narrative, model_name = "TEMPLATE", template_md, ""
        if not unchanged:
            try:
                narrative, model_name = self._synthesize(code, research_as_of, sections)
                synthesis_source = "LLM"
            except Exception:  # noqa: BLE001 - template fallback is mandatory (plan §15.2)
                narrative, synthesis_source = template_md, "TEMPLATE"

        module_hashes = {
            name: str((fp or "")) for name, fp in section_fps.items()
        }
        saved = self.store.save_report(
            market=market, stock_code=code, research_as_of=research_as_of,
            overall_freshness=freshness["overall_freshness"],
            input_fingerprint=report_fingerprint,
            module_hashes=module_hashes, sections=sections,
            narrative_report_md=narrative, synthesis_source=synthesis_source,
            formula_version=CIO_REPORT_FORMULA_VERSION,
            prompt_version=CIO_SYNTHESIS_PROMPT_VERSION, model_version=model_name,
            previous_report_id=(previous or {}).get("id"),
        )
        return {
            **saved,
            "idempotent_reuse": False,
            "module_freshness": module_status,
        }

    # ------------------------------------------------------------------
    # the single synthesis LLM (plan §15.2)
    # ------------------------------------------------------------------
    def _synthesize(self, stock_code: str, as_of: str, sections: list[dict[str, Any]]) -> tuple[str, str]:
        from src.research_tasks.service import ProviderModelRuntime
        from src.research_tasks.store import ResearchTaskStore

        config = ResearchTaskStore().get_runtime_config("research_lead")
        if not config.get("enabled") or not config.get("model"):
            raise RuntimeError("research_lead 模型未启用")
        payload = {
            "stock_code": stock_code, "research_as_of": as_of,
            "sections": [
                {"title": s["title"], "data": s["structured_payload"], "template": s["narrative_md"]}
                for s in sections
            ],
        }
        instruction = (
            "你是恒值投资的 CIO，把 14 个研究 section 的结构化数据整合成一份深度研究报告正文。"
            "规则：所有数字必须逐字来自 payload，禁止新增事实或数字；"
            "模板列（template）已经是确定性叙述，你可以重写语言但不得改变事实与结论方向；"
            "第 14 节结论只能是 重点研究/继续观察/暂缓优先研究/资料不足 之一，与 payload 一致；"
            "禁止买入、卖出、仓位、止盈止损等交易指令；"
            "输出为 Markdown，使用 payload 中的 14 个小节标题，总长 1500-3000 字；"
            "输出必须是且仅是一个 JSON 对象，不要 markdown 代码块和任何额外文字："
            '{"report_md":"<完整报告>"}。'
        )
        runtime = ProviderModelRuntime()
        kwargs: dict[str, Any] = {
            "role": "research_lead", "phase": "CIO_SYNTHESIS",
            "model": str(config["model"]), "instruction": instruction, "payload": payload,
        }
        if config.get("base_url") and hasattr(runtime, "invoke_with_connection"):
            output = runtime.invoke_with_connection(
                **kwargs, base_url=str(config["base_url"]), api_key=str(config.get("api_key") or ""),
            )
        else:
            output = runtime.invoke(**kwargs, provider=str(config.get("provider") or "openai"))
        report_md = str(dict(output).get("report_md") or "").strip()
        if not report_md or _TRADING_LANGUAGE.search(report_md):
            raise ValueError("CIO synthesis failed safety validation")
        return report_md, str(config["model"])


    # ------------------------------------------------------------------
    # report-level freshness (plan §17)
    # ------------------------------------------------------------------
    def classify_report_sections(self, market: str, stock_code: str, *,
                                 as_of: str | None = None) -> dict[str, Any] | None:
        """Live per-section FRESH/STALE against the persisted report.

        This powers the "only refresh the stale section" behaviour: Hermes or
        the refresh endpoint can see exactly which parts moved without paying
        for a synthesis.
        """
        market, code = market.upper(), stock_code.upper()
        report = self.store.latest_report(market, code, as_of=as_of)
        if report is None:
            return None

        research_as_of = str(report.get("research_as_of") or "")[:10]
        current = build_all_sections(market, code, research_as_of)
        persisted = {s["section_type"]: str(s.get("input_fingerprint") or "") for s in report.get("sections") or []}
        statuses = {
            s["section_type"]: ("FRESH" if persisted.get(s["section_type"]) == s["input_fingerprint"] else "STALE")
            for s in current
        }
        stale = [name for name, status in statuses.items() if status == "STALE"]
        overall = "FRESH" if not stale else ("STALE" if len(stale) == len(statuses) else "PARTIALLY_STALE")
        return {
            "stock_code": code, "research_as_of": research_as_of,
            "overall": overall, "sections": statuses, "stale_sections": stale,
        }

    # ------------------------------------------------------------------
    # Focus A/B/C resource policy (plan §11, Sprint 4)
    # ------------------------------------------------------------------
    def ensure_focus_tier_reports(self, *, as_of: str | None = None) -> dict[str, Any]:
        """Keep CIO reports aligned with the Focus A/B/C resource tiers.

        A 档：报告始终 READY（缺失或指纹变化即重建）。
        B 档：缺失才补建；已有报告不动（正常 EOD 已保障数据层）。
        C 档：不自动生成（被问时由 Hermes 按需 refresh）。
        """
        from src.focus_selection import get_focus_selection_service
        from src.research_freshness import get_research_freshness_service

        research_as_of = str(as_of or get_research_freshness_service()._resolve_as_of(None))[:10]
        try:
            focus = get_focus_selection_service().get_focus_selection(as_of=research_as_of) or {}
        except Exception:  # noqa: BLE001 - tier policy must not crash callers
            focus = {}

        def _codes(key: str) -> list[str]:
            return [str(item.get("stock_code") or "").upper() for item in (focus.get(key) or [])]

        tier_a, tier_b = _codes("focus_a"), _codes("focus_b")
        built_a = built_b = reused = 0
        for code in tier_a:
            result = self.build_report("CN", code, as_of=research_as_of)
            if result.get("idempotent_reuse"):
                reused += 1
            else:
                built_a += 1
        for code in tier_b:
            if self.get_report("CN", code, as_of=research_as_of) is None:
                self.build_report("CN", code, as_of=research_as_of)
                built_b += 1
        return {
            "research_as_of": research_as_of,
            "tier_a": len(tier_a), "tier_b": len(tier_b),
            "built_a": built_a, "built_b": built_b, "reused_a": reused,
            "policy": "A=always READY; B=build when missing; C=on demand only",
        }


_service: CioReportService | None = None


def get_cio_report_service() -> CioReportService:
    global _service
    if _service is None:
        _service = CioReportService()
    return _service
