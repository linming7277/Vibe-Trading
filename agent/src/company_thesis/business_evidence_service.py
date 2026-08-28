"""Convert verified Business Research Claims into immutable Thesis Evidence.

This is a deliberately narrow, single-company bridge.  It reads a completed
Business Research snapshot, writes only auditable Evidence for the Thesis that
remains current throughout extraction, and never changes a Thesis or creates a
Review.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from src.business_research.citations import BusinessClaimCitationResolver
from src.business_research.service import BusinessResearchService, _plain_language
from src.business_research.store import BusinessResearchStore
from src.research_tasks.providers import safe_provider_catalog
from src.research_tasks.service import ProviderModelRuntime
from src.research_tasks.store import ResearchTaskStore
from src.research_workspace.store import normalize_market, normalize_symbol
from src.structured_output import (
    StructuredOutputMode,
    StructuredOutputRuntime,
    resolve_structured_output_capabilities,
)

from .evidence_service import CompanyThesisEvidenceService


EXTRACTOR_VERSION = "company-thesis-business-evidence-v1.0.0"
_EFFECTS = {"SUPPORT", "CHALLENGE", "NEUTRAL"}
_CANDIDATE_TYPES = {"FACT", "INFERENCE"}
_TRADING_LANGUAGE = re.compile(r"建议买入|建议卖出|买入|卖出|目标价|目标仓位|止损|加仓|减仓")


class CompanyThesisBusinessEvidenceService:
    """Extract one company's verified Business Claims without a batch path."""

    def __init__(
        self,
        *,
        evidence_service: CompanyThesisEvidenceService | None = None,
        business_store: BusinessResearchStore | None = None,
        config_store: ResearchTaskStore | None = None,
        runtime: ProviderModelRuntime | None = None,
        structured_runtime: StructuredOutputRuntime | None = None,
        relevance_resolver: Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
        before_write_hook: Callable[[], None] | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.evidence_service = evidence_service or CompanyThesisEvidenceService(db_path=db_path)
        self.business_store = business_store or BusinessResearchStore(self.evidence_service.repository.db_path)
        self.config_store = config_store or ResearchTaskStore(self.evidence_service.repository.db_path)
        self.runtime = runtime or ProviderModelRuntime()
        self.structured_runtime = structured_runtime or StructuredOutputRuntime()
        self.relevance_resolver = relevance_resolver
        self.before_write_hook = before_write_hook
        self._owns_evidence_service = evidence_service is None
        self._owns_business_store = business_store is None
        self._owns_config_store = config_store is None

    def close(self) -> None:
        if self._owns_business_store:
            self.business_store.close()
        if self._owns_config_store:
            self.config_store.close()
        if self._owns_evidence_service:
            self.evidence_service.close()

    @staticmethod
    def _result(market: str, stock_code: str, **values: Any) -> dict[str, Any]:
        return {
            "market": market,
            "stock_code": stock_code,
            "status": "OK",
            "thesis_id": None,
            "business_snapshot_id": None,
            "created": 0,
            "unchanged": 0,
            "skipped_unknown": 0,
            "skipped_low_confidence": 0,
            "skipped_invalid": 0,
            "evidence": [],
            **values,
        }

    @staticmethod
    def _source_keys(claim: dict[str, Any]) -> list[str]:
        raw = claim.get("source_keys", [])
        if not isinstance(raw, list):
            return []
        return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))

    @staticmethod
    def _claim_text(claim: dict[str, Any]) -> str:
        return re.sub(r"\s+", " ", str(claim.get("text") or "").strip())

    @staticmethod
    def _fingerprint(thesis_id: str, snapshot_id: str, claim: str, source_keys: list[str]) -> str:
        value = {
            "thesis_id": thesis_id,
            "business_research_snapshot_id": snapshot_id,
            "claim": re.sub(r"\s+", " ", claim).strip(),
            "source_keys": sorted(source_keys),
        }
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _relevance_schema(count: int) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "assessments": {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_index": {"type": "integer"},
                            "effect": {"type": "string", "enum": sorted(_EFFECTS)},
                            "reason": {"type": "string", "maxLength": 240},
                        },
                        "required": ["claim_index", "effect", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["assessments"],
            "additionalProperties": False,
        }

    @staticmethod
    def _validate_relevance(result: dict[str, Any], expected_indexes: set[int]) -> dict[str, Any]:
        if set(result) != {"assessments"} or not isinstance(result.get("assessments"), list):
            raise ValueError("invalid business relevance result schema")
        normalized: list[dict[str, Any]] = []
        indexes: set[int] = set()
        for item in result["assessments"]:
            if not isinstance(item, dict) or set(item) != {"claim_index", "effect", "reason"}:
                raise ValueError("invalid business relevance assessment schema")
            index = item.get("claim_index")
            effect = str(item.get("effect") or "").upper()
            reason = str(item.get("reason") or "").strip()
            if not isinstance(index, int) or index not in expected_indexes or index in indexes:
                raise ValueError("invalid business relevance claim index")
            if effect not in _EFFECTS or not reason or len(reason) > 240:
                raise ValueError("invalid business relevance effect or reason")
            if _TRADING_LANGUAGE.search(reason) or not _plain_language(reason):
                raise ValueError("business relevance reason must be plain language without trading instructions")
            indexes.add(index)
            normalized.append({"claim_index": index, "effect": effect, "reason": reason})
        if indexes != expected_indexes:
            raise ValueError("business relevance assessments must cover every candidate exactly once")
        return {"assessments": normalized}

    def _agent_config(self) -> tuple[dict[str, Any], bool]:
        runtime_config = getattr(self.config_store, "get_runtime_config", self.config_store.get_config)
        config = runtime_config("financial_analyst")
        provider = next(
            (row for row in safe_provider_catalog(self.config_store.list_configs()) if row["provider"] == config["provider"]),
            None,
        )
        direct_ready = bool(config.get("base_url") and config.get("model"))
        ready = bool(config.get("enabled") and config.get("model") and (direct_ready or (provider and provider.get("configured"))))
        return config, ready

    def _judge_relevance(
        self, thesis: dict[str, Any], candidates: list[dict[str, Any]],
    ) -> tuple[dict[int, dict[str, str]] | None, dict[str, Any]]:
        if self.relevance_resolver is not None:
            try:
                raw = self.relevance_resolver(thesis, candidates)
                parsed = self._validate_relevance(
                    {"assessments": raw}, {item["claim_index"] for item in candidates},
                )
            except (TypeError, ValueError) as exc:
                return None, {"mode": "INJECTED_REJECTED", "reason": str(exc)}
            return (
                {item["claim_index"]: {"effect": item["effect"], "reason": item["reason"]} for item in parsed["assessments"]},
                {"mode": "INJECTED"},
            )

        config, ready = self._agent_config()
        if not ready:
            return None, {"mode": "UNAVAILABLE", "reason": "financial_analyst is not configured"}
        payload = {
            "current_thesis": {
                key: thesis.get(key)
                for key in ("thesis_id", "title", "core_thesis", "status", "confidence", "invalid_conditions")
            },
            "business_claims": [
                {
                    "claim_index": item["claim_index"],
                    "topic": item["topic"],
                    "type": item["type"],
                    "text": item["text"],
                    "confidence": item["confidence"],
                    "citations": item["citations"],
                }
                for item in candidates
            ],
        }
        instruction = (
            "你只判断每条已验证公司经营 Claim 与当前 Company Thesis 的关系。"
            "SUPPORT=直接支持；CHALLENGE=直接反证或削弱；NEUTRAL=关联不足或方向不明。"
            "不得改写 Claim、不得生成新事实、不得修改 Thesis、不得给出交易建议。"
            "reason 用普通人能理解的话，先说发生了什么、为什么重要、对当前 Thesis 意味着什么；"
            "首次使用专业词必须马上解释。每条不超过 240 字，只依据输入。"
            "只返回 JSON 对象：{\"assessments\":[{\"claim_index\":整数,\"effect\":\"SUPPORT|CHALLENGE|NEUTRAL\","
            "\"reason\":\"...\"}]}。每个输入 claim_index 必须且只能对应一项 assessment，不要 Markdown 或其他字段。"
        )
        expected_indexes = {item["claim_index"] for item in candidates}
        capabilities = resolve_structured_output_capabilities(config)

        def invoke(mode: StructuredOutputMode, response_format: dict[str, Any] | None) -> dict[str, Any]:
            connection_invoke = getattr(self.runtime, "invoke_with_connection", None)
            if config.get("base_url") and callable(connection_invoke):
                return connection_invoke(
                    role="financial_analyst",
                    phase="BUSINESS_THESIS_RELEVANCE",
                    model=config["model"],
                    base_url=config["base_url"],
                    api_key=config.get("api_key") or "",
                    instruction=instruction,
                    payload=payload,
                    target_schema=response_format,
                )
            return self.runtime.invoke(
                role="financial_analyst",
                phase="BUSINESS_THESIS_RELEVANCE",
                provider=config["provider"],
                model=config["model"],
                instruction=instruction,
                payload=payload,
                target_schema=response_format,
            )

        outcome = self.structured_runtime.run(
            config=config,
            instruction=instruction,
            payload=payload,
            contract_schema=self._relevance_schema(len(candidates)),
            capabilities=capabilities,
            text_instruction="不可用：只返回纯文本。",
            text_payload={},
            invoke_structured=invoke,
            validate=lambda value: self._validate_relevance(value, expected_indexes),
        )
        audit = {
            "mode": outcome.mode_used,
            "attempts": outcome.attempts,
            "error_types": outcome.error_types,
            "capability_source": outcome.capability_source,
        }
        if outcome.parsed is None:
            return None, audit
        return (
            {item["claim_index"]: {"effect": item["effect"], "reason": item["reason"]} for item in outcome.parsed["assessments"]},
            audit,
        )

    def extract_from_latest_business_research(self, market: str, stock_code: str) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        thesis = self.evidence_service.thesis_repository.get_current_thesis(normalized_market, symbol)
        result = self._result(normalized_market, symbol)
        if thesis is None:
            return {**result, "status": "THESIS_NOT_CREATED"}
        result["thesis_id"] = thesis["thesis_id"]

        snapshot = self.business_store.latest(symbol)
        if snapshot is None or snapshot.get("analysis_status") != "COMPLETED":
            return {**result, "status": "BUSINESS_RESEARCH_NOT_READY"}
        result["business_snapshot_id"] = snapshot["id"]
        analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
        metadata = analysis.get("analysis_metadata") if isinstance(analysis.get("analysis_metadata"), dict) else {}
        source_snapshot = snapshot.get("snapshot") if isinstance(snapshot.get("snapshot"), dict) else {}
        manifest = source_snapshot.get("sources") if isinstance(source_snapshot.get("sources"), dict) else {}
        if metadata.get("quality_status") != "STRUCTURED":
            return {**result, "status": "CLAIMS_NOT_EVIDENCE_READY"}
        try:
            BusinessResearchService.validate_claims(
                {"summary": analysis.get("summary"), "claims": analysis.get("claims")}, manifest,
            )
        except (TypeError, ValueError):
            return {**result, "status": "CLAIMS_NOT_EVIDENCE_READY"}

        resolved = BusinessClaimCitationResolver().resolve_snapshot({
            **source_snapshot,
            "id": snapshot["id"],
            "analysis_status": snapshot["analysis_status"],
            "analysis": analysis,
        })
        if resolved.get("traceability_status") != "COMPLETE":
            return {**result, "status": "TRACEABILITY_INCOMPLETE"}
        resolved_analysis = resolved.get("analysis") if isinstance(resolved.get("analysis"), dict) else {}
        candidates: list[dict[str, Any]] = []
        for index, raw in enumerate(resolved_analysis.get("claims") or []):
            if not isinstance(raw, dict):
                result["skipped_invalid"] += 1
                continue
            claim_type = str(raw.get("type") or "UNKNOWN").upper()
            if claim_type == "UNKNOWN":
                result["skipped_unknown"] += 1
                continue
            if claim_type not in _CANDIDATE_TYPES:
                result["skipped_invalid"] += 1
                continue
            confidence = str(raw.get("confidence") or "").upper()
            source_keys = self._source_keys(raw)
            citations = raw.get("citations") if isinstance(raw.get("citations"), list) else []
            text = self._claim_text(raw)
            topic = str(raw.get("topic") or "").upper()
            if (
                not text
                or not source_keys
                or not citations
                or any(not isinstance(item, dict) or item.get("status") != "RESOLVED" for item in citations)
            ):
                result["skipped_invalid"] += 1
                continue
            if claim_type == "INFERENCE" and confidence == "LOW":
                result["skipped_low_confidence"] += 1
                continue
            if confidence not in {"LOW", "MEDIUM", "HIGH"} or topic not in {
                "MAIN_BUSINESS", "PRODUCT", "BUSINESS_MODEL", "BUSINESS_CHANGE"
            }:
                result["skipped_invalid"] += 1
                continue
            candidates.append({
                "claim_index": index,
                "claim": raw,
                "type": claim_type,
                "topic": topic,
                "confidence": confidence,
                "text": text,
                "source_keys": source_keys,
                "citations": citations,
            })
        if not candidates:
            return result

        pending: list[dict[str, Any]] = []
        for candidate in candidates:
            fingerprint = self._fingerprint(thesis["thesis_id"], snapshot["id"], candidate["text"], candidate["source_keys"])
            if self.evidence_service.repository.find_active_evidence_by_fingerprint(thesis["thesis_id"], fingerprint):
                result["unchanged"] += 1
                continue
            candidate["fingerprint"] = fingerprint
            pending.append(candidate)
        if not pending:
            return result

        relevance, relevance_audit = self._judge_relevance(thesis, pending)
        result["relevance"] = relevance_audit
        if relevance is None:
            return {**result, "status": "RELEVANCE_NOT_READY"}
        if self.before_write_hook:
            self.before_write_hook()
        for candidate in pending:
            current = self.evidence_service.thesis_repository.get_current_thesis(normalized_market, symbol)
            if current is None or current["thesis_id"] != thesis["thesis_id"]:
                return {**result, "status": "THESIS_CHANGED_DURING_EXTRACTION"}
            fingerprint = candidate["fingerprint"]
            if self.evidence_service.repository.find_active_evidence_by_fingerprint(thesis["thesis_id"], fingerprint):
                result["unchanged"] += 1
                continue
            judgement = relevance[candidate["claim_index"]]
            hashes = sorted({str(item.get("source_hash") or "") for item in candidate["citations"] if item.get("source_hash")})
            evidence_metadata = {
                "extractor_version": EXTRACTOR_VERSION,
                "business_claim_index": candidate["claim_index"],
                "topic": candidate["topic"],
                "claim_type": candidate["type"],
                "source_keys": candidate["source_keys"],
                "resolved_citations": candidate["citations"],
                "source_hashes": hashes,
                "business_research_snapshot_id": snapshot["id"],
                "business_prompt_version": metadata.get("module_version"),
                "provider": snapshot.get("agent_provider"),
                "model": snapshot.get("agent_model"),
                "research_domain": "BUSINESS",
                "traceability_status": "COMPLETE",
                "relevance_reason": judgement["reason"],
            }
            try:
                evidence = self.evidence_service.create_evidence(
                    thesis_id=thesis["thesis_id"],
                    evidence_type="BUSINESS",
                    effect=judgement["effect"],
                    claim=candidate["text"],
                    summary=judgement["reason"],
                    source_type="COMPANY_RESEARCH_SNAPSHOT",
                    source_id=snapshot["id"],
                    source_ref=f"business-research:{snapshot['id']}:claim:{candidate['claim_index']}",
                    source_title=f"Business Research {symbol} Claim {candidate['claim_index']}",
                    source_date=snapshot.get("data_as_of"),
                    data_as_of=snapshot.get("data_as_of"),
                    confidence=candidate["confidence"],
                    created_by="AGENT_FINANCIAL",
                    metadata=evidence_metadata,
                    evidence_fingerprint=fingerprint,
                )
            except sqlite3.IntegrityError:
                result["unchanged"] += 1
                continue
            result["created"] += 1
            result["evidence"].append(evidence)
        return result


_service: CompanyThesisBusinessEvidenceService | None = None


def get_company_thesis_business_evidence_service() -> CompanyThesisBusinessEvidenceService:
    global _service
    if _service is None:
        _service = CompanyThesisBusinessEvidenceService()
    return _service
