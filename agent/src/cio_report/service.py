"""CIO report orchestration: build (deterministic sections + one synthesis LLM),
persist, and read (plan §12-§15)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.cio_report.builder import (
    CIO_REPORT_FORMULA_VERSION,
    SECTION_TITLES,
    build_all_sections,
)
from src.cio_report.narrative import BOSS_SECTIONS, render_boss_report
from src.cio_report.store import CioReportStore

logger = logging.getLogger(__name__)

CIO_SYNTHESIS_PROMPT_VERSION = "cio-synthesis-v2"  # V2: boss-facing Chinese narrative structure
# Narrative-layer version rides the report fingerprint so a template upgrade
# re-renders persisted reports instead of being swallowed by idempotent reuse.
NARRATIVE_TEMPLATE_VERSION = "boss-narrative-v2"
_TRADING_LANGUAGE = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓|建仓")

# Delivery-layer status semantics (polish §4): research freshness and
# synthesis outcome are independent — FRESH + TEMPLATE_FALLBACK is a usable
# report.  synthesis_source persists these values; legacy rows (LLM/TEMPLATE)
# are normalized at read time so there is only ONE field, not two.
SYNTHESIS_LLM_COMPLETED = "LLM_COMPLETED"
SYNTHESIS_TEMPLATE_FALLBACK = "TEMPLATE_FALLBACK"
_LEGACY_SYNTHESIS = {"LLM": SYNTHESIS_LLM_COMPLETED, "TEMPLATE": SYNTHESIS_TEMPLATE_FALLBACK}


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
        # The section table has no title column; the section_type→title
        # registry restores it deterministically at read time (fix §8) —
        # no schema migration needed.
        sections = []
        for section in report.get("sections") or []:
            section["title"] = SECTION_TITLES.get(str(section.get("section_type") or ""),
                                                  str(section.get("section_type") or ""))
            sections.append(section)
        report["sections"] = sorted(sections, key=lambda s: list(SECTION_TITLES).index(s["section_type"]))
        return self._with_delivery_status(report)

    @staticmethod
    def _with_delivery_status(report: dict[str, Any]) -> dict[str, Any]:
        """Expose the two independent delivery statuses on every read:
        research_freshness (data age) vs synthesis_status (LLM outcome)."""
        raw_synthesis = str(report.get("synthesis_source") or "")
        report["synthesis_status"] = _LEGACY_SYNTHESIS.get(raw_synthesis, raw_synthesis)
        report["research_freshness"] = report.get("overall_freshness") or "UNKNOWN"
        return report

    # ------------------------------------------------------------------
    # build / refresh
    # ------------------------------------------------------------------
    @staticmethod
    def _default_research_as_of() -> str:
        """Routing fix §6: every request shares research_as_of = the latest
        qualified market close, never the calendar today (a bare "today" has
        no close snapshot and yields degraded reports)."""
        try:
            from src.tdx_data import get_tdx_service

            _ready, _reason, snapshot = get_tdx_service().latest_qualified_close_snapshot()
            market_date = str((snapshot or {}).get("market_date") or "")[:10]
            if market_date:
                return market_date
        except Exception:  # noqa: BLE001 - fall back to the date resolver
            pass
        from src.research_freshness import get_research_freshness_service

        return get_research_freshness_service()._resolve_as_of(None)

    def build_report(self, market: str, stock_code: str, *, as_of: str | None = None,
                     force_synthesis: bool = False) -> dict[str, Any]:
        """Build all 14 sections deterministically, then one synthesis LLM call.

        Section rebuilds are always safe (pure reads); the synthesis LLM only
        reruns when the report fingerprint actually changed (plan §15.2).
        """
        from src.research_freshness import get_research_freshness_service

        market, code = market.upper(), stock_code.upper()
        research_as_of = str(as_of or self._default_research_as_of())[:10]
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
            [NARRATIVE_TEMPLATE_VERSION] +
            [f"{name}:{fp}" for name, fp in sorted(section_fps.items())]
        )
        previous_fp = str((previous or {}).get("input_fingerprint") or "")
        unchanged = bool(previous) and previous_fp == report_fingerprint and not force_synthesis

        if unchanged and str((previous or {}).get("status")) == "READY":
            return self._with_delivery_status({**previous, "idempotent_reuse": True})

        template_md = render_boss_report(sections, stock_code=code, as_of=research_as_of)
        synthesis_status, narrative, model_name = SYNTHESIS_TEMPLATE_FALLBACK, template_md, ""
        if not unchanged:
            narrative, model_name, synthesis_status = self._synthesize_with_retry(
                code, research_as_of, sections, template_md)

        module_hashes = {
            name: str((fp or "")) for name, fp in section_fps.items()
        }
        saved = self.store.save_report(
            market=market, stock_code=code, research_as_of=research_as_of,
            overall_freshness=freshness["overall_freshness"],
            input_fingerprint=report_fingerprint,
            module_hashes=module_hashes, sections=sections,
            narrative_report_md=narrative, synthesis_source=synthesis_status,
            formula_version=CIO_REPORT_FORMULA_VERSION,
            prompt_version=CIO_SYNTHESIS_PROMPT_VERSION, model_version=model_name,
            previous_report_id=(previous or {}).get("id"),
        )
        return self._with_delivery_status({
            **saved,
            "idempotent_reuse": False,
            "module_freshness": module_status,
        })

    # ------------------------------------------------------------------
    # synthesis with exactly one transient-error retry (delivery polish §2/§3)
    # ------------------------------------------------------------------
    _SYNTHESIS_RETRY_BACKOFF_S = 2.0
    # Minimal reliable classification from the provider SDK exception names.
    _TRANSIENT_EXC_RE = re.compile(
        r"Timeout|Connection|ServiceUnavailable|RateLimit|Availability|Temporar|Overloaded", re.I)
    _PERMANENT_EXC_RE = re.compile(
        r"Auth|Permission|Invalid|Schema|NotFound|Unsupported|ValueError|KeyError|TypeError", re.I)

    @classmethod
    def _is_transient_synthesis_error(cls, exc: Exception) -> bool:
        name = type(exc).__name__
        if cls._PERMANENT_EXC_RE.search(name):
            return False
        return bool(cls._TRANSIENT_EXC_RE.search(name))

    def _synthesize_with_retry(
        self, stock_code: str, as_of: str, sections: list[dict[str, Any]], template_md: str,
    ) -> tuple[str, str, str]:
        """One LLM attempt, plus at most one retry for transient transport errors.

        Non-transient failures (auth/schema/invalid request/programming
        errors) fall back immediately; the fallback is always the persisted
        deterministic template (never re-runs underlying research).
        """
        model_name = ""
        for attempt in (1, 2):
            try:
                narrative, model_name = self._synthesize(stock_code, as_of, sections)
                logger.info(
                    "CIO synthesis result=LLM_COMPLETED attempt=%s stock=%s as_of=%s model=%s",
                    attempt, stock_code, as_of, model_name,
                )
                return narrative, model_name, SYNTHESIS_LLM_COMPLETED
            except Exception as exc:  # noqa: BLE001 - fallback contract (plan §15.2)
                transient = self._is_transient_synthesis_error(exc)
                if attempt == 1 and transient:
                    logger.warning(
                        "CIO synthesis attempt=1 transient retry scheduled stock=%s as_of=%s model=%s exc=%s",
                        stock_code, as_of, self._research_lead_model_name(), type(exc).__name__,
                    )
                    time.sleep(self._SYNTHESIS_RETRY_BACKOFF_S)
                    continue
                logger.warning(
                    "CIO synthesis result=TEMPLATE_FALLBACK attempt=%s stock=%s as_of=%s model=%s exc=%s: %s",
                    attempt, stock_code, as_of, self._research_lead_model_name(),
                    type(exc).__name__, str(exc)[:300],
                )
                return template_md, "", SYNTHESIS_TEMPLATE_FALLBACK
        return template_md, "", SYNTHESIS_TEMPLATE_FALLBACK  # pragma: no cover - loop always returns

    @staticmethod
    def _research_lead_model_name() -> str:
        try:
            from src.research_tasks.store import ResearchTaskStore

            return str(ResearchTaskStore().get_runtime_config("research_lead").get("model") or "")
        except Exception:  # noqa: BLE001
            return ""

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
            "boss_template": render_boss_report(sections, stock_code=stock_code, as_of=as_of),
        }
        instruction = (
            "你是恒值投资的投研主管，把研究 section 的结构化数据整合成老板直接阅读的中文深度报告。"
            f"必须使用这 14 个小节标题（顺序固定）：{'；'.join(f'{i}.{t}' for i, t in enumerate(BOSS_SECTIONS, 1))}。"
            "写作契约："
            "①全部中文表达，正文禁止出现英文后台状态词（如 GROWTH/FAIR/READY/LIMITED/HIGH/MEDIUM/BEAR 等），"
            "枚举一律写成中文（如：收入持续增长、估值处于合理区间、当前数据不足以形成完整判断、谨慎/基准/乐观情景）；"
            "L1/L2/L3 写成一级行业/二级行业/三级行业；PE/PB/ROE/OCF/Capex 写成市盈率/市净率/净资产收益率/经营现金流/资本开支。"
            "②所有数字必须逐字来自 payload 或 boss_template，禁止新增事实、业务占比或行业判断。"
            "③boss_template 是确定性底稿：你可以润色语言、加强连贯，但事实与结论方向不得改变，其核心矛盾、估值位置、风险归纳的框架必须保留。"
            "④第3节要讲清 高点→下滑→低谷→修复 的路径；第4节按 收入/利润/毛利率/现金流 四维归纳阶段；"
            "第5节一段话点出最核心经营矛盾；第10节必须回答价格在区间什么位置、偏离中值多少、市盈率是否因低利润失真、"
            "市净率对照同行的位置、历史估值缺失限制什么、当前估值最大前提；"
            "第6节先归纳'真正需要关注的是什么'再分 已确认风险/财务观察项/资料不足；"
            "第11节只用谨慎/基准/乐观情景，禁止主观概率；第13节验证点分最重要/其次/长期；"
            "第14节结论只能是 重点研究/继续观察/暂缓优先研究/资料不足 之一，并用一段话说明为什么、"
            "最重要的正面因素、最大限制、什么变化会升级或降级。"
            "⑤禁止买入、卖出、建仓、加仓、止损、止盈、仓位等一切交易表述。"
            "输出 Markdown，总长 1800-3500 字；"
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


    def get_quick_brief(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Read-only Quick Brief projection of the persisted Full Report.

        Never builds, refreshes, or calls any model — a missing report raises
        CIO_REPORT_NOT_FOUND instead of silently generating one (polish §12).
        """
        report = self.get_report(market, stock_code, as_of=as_of)
        if report is None:
            raise ValueError("CIO_REPORT_NOT_FOUND")
        from src.cio_report.quick_brief import build_quick_brief, render_quick_brief_md

        brief = build_quick_brief(report)
        result = brief.as_dict()
        result["brief_md"] = render_quick_brief_md(brief)
        return result

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

        research_as_of = str(as_of or self._default_research_as_of())[:10]
        try:
            focus = get_focus_selection_service().get_focus_selection(as_of=research_as_of) or {}
        except Exception:  # noqa: BLE001 - tier policy must not crash callers
            focus = {}

        def _codes(tier_key: str) -> list[str]:
            # FocusSelectionService returns tier keys "A"/"B"/"C" (not focus_a/b).
            return [str(item.get("stock_code") or "").upper() for item in (focus.get(tier_key) or [])]

        tier_a, tier_b = _codes("A"), _codes("B")
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
