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
        market, code = market.upper(), stock_code.upper()
        # 读路径 PRICE 块补刷（2026-09-14）：仅「最新」读取且行情收盘快照就绪时
        # 执行——确定性 refresh，指纹变了才写，绝不触发 LLM/analyze/全文重建。
        # TDX 未就绪或显式历史 as_of → 直接读存档，不写库。
        report = self.store.latest_report(market, code, as_of=as_of)
        if report is None:
            # 读取路径绝不创建：没有报告就是没有（quick-brief §12 不静默生成）
            return None
        # 读路径 PRICE 块补刷（2026-09-14）：仅「已存报告」且行情收盘快照就绪时
        # 原地更新——确定性 refresh，指纹变了才写，绝不触发 LLM/analyze/全文重建，
        # 也绝不为无报告的公司凭空建报告（quick-brief §12）。
        if not as_of:
            resolved_as_of = self._default_research_as_of()
            if resolved_as_of:
                try:
                    self.refresh_cio_block(market, code, "PRICE", as_of=resolved_as_of)
                except Exception:  # noqa: BLE001 - 读路径 fail-soft，旧报告照常返回
                    logger.warning("read-path PRICE refresh failed for %s", code, exc_info=True)
            else:
                logger.warning(
                    "qualified close snapshot unavailable; serving archived CIO report for %s", code)
            report = self.store.latest_report(market, code, as_of=as_of) or report
        # The section table has no title column; the section_type→title
        # registry restores it deterministically at read time (fix §8) —
        # no schema migration needed.
        sections = []
        for section in report.get("sections") or []:
            section["title"] = SECTION_TITLES.get(str(section.get("section_type") or ""),
                                                  str(section.get("section_type") or ""))
            sections.append(section)
        # 未知 section_type（旧/异构报告）排在末尾，绝不因排序抛内部错误
        section_order = {t: i for i, t in enumerate(SECTION_TITLES)}
        report["sections"] = sorted(
            sections, key=lambda s: section_order.get(str(s.get("section_type") or ""), 999))
        valuation = next((s for s in report["sections"] if s.get("section_type") == "valuation"), None)
        val_payload = (valuation or {}).get("structured_payload") or {}
        report["price_as_of"] = (str(val_payload.get("close_as_of") or val_payload.get("as_of") or "")[:10]) or None
        report["narrative_as_of"] = str(report.get("created_at") or "")[:10] or None
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
    def _default_research_as_of() -> str | None:
        """Routing fix §6: every request shares research_as_of = the latest
        qualified market close, never the calendar today (a bare "today" has
        no close snapshot and yields degraded reports).

        TDX 收盘快照未就绪时返回 **None**：调用方必须拒绝生成，禁止回退到
        宏观序列日（08-19 事故根因——fallback 曾把批量报告冻结在过期基准日）。
        """
        try:
            from src.tdx_data import get_tdx_service

            _ready, _reason, snapshot = get_tdx_service().latest_qualified_close_snapshot()
            market_date = str((snapshot or {}).get("market_date") or "")[:10]
            if market_date:
                return market_date
        except Exception:  # noqa: BLE001 - treat any resolver failure as "not ready"
            pass
        logger.warning(
            "qualified close snapshot unavailable; refusing default research_as_of "
            "(no CIO generation without an explicit as_of)")
        return None

    def build_report(self, market: str, stock_code: str, *, as_of: str | None = None,
                     force_synthesis: bool = False) -> dict[str, Any]:
        """Build all 14 sections deterministically, then one synthesis LLM call.

        Section rebuilds are always safe (pure reads); the synthesis LLM only
        reruns when the report fingerprint actually changed (plan §15.2).
        """
        from src.research_freshness import get_research_freshness_service

        market, code = market.upper(), stock_code.upper()
        resolved_as_of = str(as_of) if as_of else self._default_research_as_of()
        if not resolved_as_of:
            # 行情收盘快照未就绪且未显式给 as_of：拒绝生成，绝不落过期基准日。
            return {"status": "RESEARCH_DATE_UNAVAILABLE", "stock_code": code,
                    "message": "行情收盘快照未就绪，无法确定研究基准日；已拒绝生成（禁止宏观序列日兜底）"}
        research_as_of = resolved_as_of[:10]
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
        pattern = re.compile(r"^## (.+?)\s*$", re.M)
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
    # Block-level refresh by data freshness (phase 1: PRICE only).
    # Deterministic section rebuild — never runs business research or the
    # full-report synthesis LLM (规格 §产品：价格变动不得触发全文 LLM).
    # ------------------------------------------------------------------
    def refresh_cio_block(self, market: str, stock_code: str, block_id: str,
                          *, as_of: str | None = None) -> dict[str, Any]:
        from src.cio_report.blocks import (
            CIO_BLOCK_CONTRACT, MISSING_SECTIONS_NOTE, block_fingerprint,
            price_block_inputs, stored_block_fingerprint,
        )
        from src.cio_report.builder import SECTION_TITLES, CioSectionBuilder, template_report_markdown

        contract = CIO_BLOCK_CONTRACT.get(str(block_id or "").upper())
        block_id = str(block_id or "").upper()
        if not contract or contract.get("phase") != 1:
            return {"status": "NOT_ENABLED", "block_id": block_id,
                    "phase": (contract or {}).get("phase")}
        market, code = market.upper(), stock_code.upper()
        resolved_as_of = str(as_of) if as_of else self._default_research_as_of()
        if not resolved_as_of:
            return {"status": "RESEARCH_DATE_UNAVAILABLE", "block_id": block_id, "code": code,
                    "message": "行情收盘快照未就绪；PRICE 块刷新需要显式 as_of 或就绪的收盘快照"}
        research_as_of = resolved_as_of[:10]

        builder = CioSectionBuilder(market, code, research_as_of)
        zones = builder._zones()
        if not zones:
            return {"status": "NO_PRICE_DATA", "block_id": block_id, "code": code}
        inputs = price_block_inputs(zones, code=code)
        fingerprint = block_fingerprint(code, inputs, block_id=block_id)

        previous = self.store.latest_report(market, code, as_of=None)
        prev_sections = list((previous or {}).get("sections") or [])
        if prev_sections:
            target = next((s for s in prev_sections
                           if s.get("section_type") in contract["section_types"]), None)
            if stored_block_fingerprint(target, block_id) == fingerprint:
                return {"status": "REUSED", "block_id": block_id, "code": code,
                        "fingerprint": fingerprint, "report_id": (previous or {}).get("id")}

        new_section = builder.build_valuation()
        payload = dict(new_section.get("structured_payload") or {})
        payload.update({
            "position_label": inputs["position_label"],
            "zone_low": inputs["zone_low"], "zone_high": inputs["zone_high"],
            "close_as_of": inputs["close_as_of"],
            "block_fingerprints": {block_id: fingerprint},
        })
        new_section["structured_payload"] = payload
        new_section["freshness_status"] = "REFRESHED"

        module_hashes = {contract["hash_column"]: fingerprint}
        if not previous:
            sections = [new_section] + [
                {"section_type": st, "title": SECTION_TITLES[st], "freshness_status": "MISSING",
                 "input_fingerprint": "", "structured_payload": {},
                 "narrative_md": MISSING_SECTIONS_NOTE, "source_refs": []}
                for st in SECTION_TITLES if st not in contract["section_types"]
            ]
            narrative = template_report_markdown(sections, stock_code=code, as_of=research_as_of)
            synthesis_source, model_name = "BLOCK_ONLY_TEMPLATE", ""
            overall_freshness = "PARTIAL"
            previous_id = None
        else:
            sections = [
                new_section if s.get("section_type") in contract["section_types"] else s
                for s in prev_sections
            ]
            for column, block in (("financial_hash", "FINANCIAL"), ("business_hash", "BUSINESS"),
                                  ("risk_hash", "RISK"), ("thesis_hash", "THESIS"),
                                  ("leader_hash", "LEADER"), ("moat_hash", "MOAT"),
                                  ("capital_allocation", "CAPITAL_ALLOCATION"), ("focus_hash", "FOCUS")):
                module_hashes[column] = (previous or {}).get(column)
            narrative = str(previous.get("narrative_report_md") or "")
            synthesis_source = str(previous.get("synthesis_source") or "")
            model_name = str(previous.get("model_version") or "")
            overall_freshness = str(previous.get("overall_freshness") or "PARTIAL")
            previous_id = (previous or {}).get("id")

        report_fingerprint = "|".join(
            [NARRATIVE_TEMPLATE_VERSION]
            + [f"{s['section_type']}:{s.get('input_fingerprint') or ''}" for s in sections]
        )
        saved = self.store.save_report(
            market=market, stock_code=code, research_as_of=research_as_of,
            overall_freshness=overall_freshness,
            input_fingerprint=report_fingerprint,
            module_hashes=module_hashes, sections=sections,
            narrative_report_md=narrative, synthesis_source=synthesis_source,
            formula_version=CIO_REPORT_FORMULA_VERSION,
            prompt_version=CIO_SYNTHESIS_PROMPT_VERSION, model_version=model_name,
            previous_report_id=previous_id,
        )
        # save_report 的同指纹幂等分支会跳过节写回——若存档节里块指纹仍未登记
        # （例如节曾被就地覆盖），就地补写一次，保证契约字段自愈。
        saved_sections = saved.get("sections") or []
        saved_target = next((s for s in saved_sections
                             if s.get("section_type") in contract["section_types"]), None)
        if stored_block_fingerprint(saved_target, block_id) != fingerprint:
            self.store.update_report_section(int(saved["id"]), new_section)
            self.store.set_module_hash(int(saved["id"]), contract["hash_column"], fingerprint)
            saved = self.store.latest_report(market, code, as_of=research_as_of) or saved
        return {"status": "REFRESHED", "block_id": block_id, "code": code,
                "fingerprint": fingerprint, "report_id": saved.get("id"),
                "previous_report_id": previous_id}

    def refresh_cio_price_blocks(self, *, as_of: str | None = None,
                                 universe: str = "leader_pool") -> dict[str, Any]:
        """按数据新鲜度批量刷新龙头池的 PRICE 块（fail-soft，逐只隔离）。"""
        from src.cio_report.blocks import leader_pool_codes

        if universe != "leader_pool":
            return {"status": "UNKNOWN_UNIVERSE", "universe": universe}
        resolved_as_of = str(as_of) if as_of else self._default_research_as_of()
        if not resolved_as_of:
            return {"status": "RESEARCH_DATE_UNAVAILABLE", "universe": universe, "count": 0,
                    "built": 0, "reused": 0, "failed": 0,
                    "message": "行情收盘快照未就绪；PRICE 批量刷新需要显式 as_of 或就绪的收盘快照"}
        codes = leader_pool_codes()
        built = reused = failed = 0
        for code in codes:
            try:
                result = self.refresh_cio_block("CN", code, "PRICE", as_of=resolved_as_of)
            except Exception as exc:  # noqa: BLE001 - per-stock isolation
                logger.warning("price block refresh failed for %s: %s: %s",
                               code, type(exc).__name__, exc)
                failed += 1
                continue
            status = str(result.get("status") or "")
            if status == "REFRESHED":
                built += 1
            elif status == "REUSED":
                reused += 1
            else:
                failed += 1
        return {"status": "COMPLETED", "universe": universe, "count": len(codes),
                "built": built, "reused": reused, "failed": failed}

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

        research_as_of = str(as_of or self._default_research_as_of() or "")[:10]
        if not research_as_of:
            return {"status": "RESEARCH_DATE_UNAVAILABLE", "research_as_of": "",
                    "tier_a": 0, "tier_b": 0, "built_a": 0, "built_b": 0, "reused_a": 0,
                    "policy": "A=always READY; B=build when missing; C=on demand only",
                    "message": "行情收盘快照未就绪，无法确定研究基准日；已拒绝批量生成"}
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
