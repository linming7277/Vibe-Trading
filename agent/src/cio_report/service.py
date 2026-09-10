"""CIO report orchestration: build (deterministic sections + one synthesis LLM),
persist, and read (plan §12-§15)."""

from __future__ import annotations

import hashlib
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

CIO_SYNTHESIS_PROMPT_VERSION = "cio-synthesis-v4-incremental"  # 分节增量：每节小请求+缓存，单节失败降级底稿
# Narrative-layer version rides the report fingerprint so a template upgrade
# re-renders persisted reports instead of being swallowed by idempotent reuse.
NARRATIVE_TEMPLATE_VERSION = "boss-narrative-v3"  # round1: latest quarter, moat evidence, verdict depth
_TRADING_LANGUAGE = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓|建仓")

# Delivery-layer status semantics (polish §4): research freshness and
# synthesis outcome are independent — FRESH + TEMPLATE_FALLBACK is a usable
# report.  synthesis_source persists these values; legacy rows (LLM/TEMPLATE)
# are normalized at read time so there is only ONE field, not two.
SYNTHESIS_LLM_COMPLETED = "LLM_COMPLETED"
SYNTHESIS_LLM_PARTIAL = "LLM_PARTIAL"
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
                result = self._synthesize(stock_code, as_of, sections)
                if len(result) == 3:
                    narrative, model_name, section_status = result
                    logger.info(
                        "CIO synthesis result=%s attempt=%s stock=%s as_of=%s model=%s",
                        section_status or SYNTHESIS_LLM_COMPLETED, attempt, stock_code, as_of, model_name,
                    )
                    return narrative, model_name, section_status or SYNTHESIS_LLM_COMPLETED
                narrative, model_name = result
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
    def _split_boss_template(self, template_md: str) -> list[tuple[str, str]]:
        """按 "## N. 标题" 把老板底稿切成 (标题, 底稿块) 序列。"""
        pattern = re.compile(r"^## \d+\. (.+?)\s*$", re.M)
        matches = list(pattern.finditer(template_md))
        chunks: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(template_md)
            chunks.append((match.group(1).strip(), template_md[match.start():end].strip()))
        return chunks

    def _invoke_section_polish(self, config: dict[str, Any], stock_code: str, as_of: str,
                               title: str, chunk: str) -> str:
        """单节润色：小请求（120–300 字），超时 300 秒，数字契约同整体综合。"""
        from src.research_tasks.service import ProviderModelRuntime

        runtime = ProviderModelRuntime()
        instruction = (
            f"你是恒值投资投研主管。下面是报告《{title}》一节的确定性底稿。"
            "任务：把底稿改写成面向老板的连贯中文叙述，正文 120-300 字；"
            "所有数字、公司名、结论方向必须与底稿逐字一致，不得新增或改写任何数字与事实；"
            "底稿中的 markdown 表格必须原样完整保留；"
            "禁止英文状态词（如 GROWTH/FAIR/READY/HIGH）；"
            "禁止买入、卖出、加仓、减仓、仓位、止盈、止损等交易表述。"
            '输出必须是且仅是一个 JSON 对象：{"text":"<本节完整内容，含全部表格>"}'
        )
        payload = {
            "stock_code": stock_code, "research_as_of": as_of,
            "section_title": title, "draft": chunk,
        }
        kwargs: dict[str, Any] = {
            "role": "research_lead", "phase": "CIO_SECTION_POLISH",
            "model": str(config["model"]), "instruction": instruction, "payload": payload,
            "timeout_seconds": 300,
        }
        if config.get("base_url") and hasattr(runtime, "invoke_with_connection"):
            output = runtime.invoke_with_connection(
                **kwargs, base_url=str(config["base_url"]), api_key=str(config.get("api_key") or ""))
        else:
            output = runtime.invoke(**kwargs, provider=str(config.get("provider") or "openai"))
        text = str(dict(output).get("text") or "").strip()
        if not text or len(text) < 40 or _TRADING_LANGUAGE.search(text):
            raise ValueError("section polish failed safety validation")
        dropped = [
            line for line in chunk.splitlines()
            if line.startswith("|") and "---" not in line and line not in text
        ]
        if dropped:
            raise ValueError(f"section polish dropped {len(dropped)} table lines")
        return text

    def _synthesize(
        self, stock_code: str, as_of: str, sections: list[dict[str, Any]], template_md: str = "",
    ) -> tuple[str, str, str | None]:
        """分节增量综合：每节独立小请求 + 叙述缓存 + 单节失败降级底稿。

        相比整篇一次调用：单节 300 字级别的请求不会触发长文推理超时；
        底稿未变化的节直接命中缓存零调用；任何一节失败只降级该节
        （回退确定性底稿文本），整份报告始终可交付。
        返回 (report_md, model_name, 状态)；全部节失败时抛错，由重试包装
        降级为整篇确定性模板。
        """
        from src.research_tasks.store import ResearchTaskStore

        config = ResearchTaskStore().get_runtime_config("research_lead")
        if not config.get("enabled") or not config.get("model"):
            raise RuntimeError("research_lead 模型未启用")
        template = template_md or render_boss_report(
            sections, stock_code=stock_code, as_of=as_of)
        chunks = self._split_boss_template(template)
        if not chunks:
            raise RuntimeError("boss template has no sections to synthesize")
        model_name = str(config["model"])
        parts: list[str] = []
        polished_count = failed_count = cached_count = 0
        for title, chunk in chunks:
            chunk_hash = hashlib.sha256((title + chr(10) + chunk).encode("utf-8")).hexdigest()[:24]
            cached = self.store.load_section_narrative(stock_code, title, chunk_hash)
            if cached:
                parts.append(cached)
                cached_count += 1
                continue
            polished = ""
            for attempt in (1, 2):
                try:
                    polished = self._invoke_section_polish(config, stock_code, as_of, title, chunk)
                    break
                except Exception as exc:  # noqa: BLE001 - 单节失败降级，不拖垮整份
                    if attempt == 1 and self._is_transient_synthesis_error(exc):
                        logger.warning(
                            "CIO section polish transient retry stock=%s title=%s exc=%s",
                            stock_code, title, type(exc).__name__,
                        )
                        time.sleep(self._SYNTHESIS_RETRY_BACKOFF_S)
                        continue
                    logger.warning(
                        "CIO section polish fallback-to-draft stock=%s title=%s exc=%s: %s",
                        stock_code, title, type(exc).__name__, str(exc)[:200],
                    )
                    break
            if polished:
                self.store.save_section_narrative(stock_code, title, chunk_hash, polished, model_name)
                parts.append(polished)
                polished_count += 1
            else:
                parts.append(chunk)
                failed_count += 1
        report_md = (chr(10) * 2).join(parts).strip()
        if polished_count == 0 and cached_count == 0:
            raise RuntimeError("all section polish failed")
        if failed_count:
            model_name = f"{model_name}({failed_count}节降级底稿)"
        if _TRADING_LANGUAGE.search(report_md):
            raise ValueError("CIO synthesis failed safety validation")
        status = SYNTHESIS_LLM_COMPLETED if not failed_count else SYNTHESIS_LLM_PARTIAL
        return report_md, model_name, status

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
