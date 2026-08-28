"""Business fundamentals module owned by the existing Financial Researcher."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from src.disclosure_materials.store import DisclosureMaterialStore
from src.level3_leaders.business_profiles import CompanyBusinessProfileService
from src.level3_leaders.helpers import stable_hash
from src.research_tasks.providers import safe_provider_catalog
from src.research_tasks.service import ProviderModelRuntime
from src.research_tasks.store import ResearchTaskStore
from src.structured_output import (
    StructuredOutputMode,
    StructuredOutputRuntime,
    resolve_structured_output_capabilities,
)

from .citations import BusinessClaimCitationResolver
from .store import BusinessResearchStore


BUSINESS_RESEARCH_VERSION = "financial-researcher-business-v1.1.0"
BUSINESS_CLAIM_TYPES = {"FACT", "INFERENCE", "UNKNOWN"}
BUSINESS_TOPICS = {"MAIN_BUSINESS", "PRODUCT", "BUSINESS_MODEL", "BUSINESS_CHANGE"}
BUSINESS_CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
MAX_BUSINESS_CLAIMS = 8
BUSINESS_RESEARCH_MAX_TOKENS = 4096
TRADING_LANGUAGE = re.compile(r"建议买入|建议卖出|买入|卖出|目标价|目标仓位|止损|加仓|减仓")
NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?%?")
JARGON = ("客户集中度", "议价权", "护城河", "单位经济模型", "渠道下沉")
# Official reports contain far more than the business module should give an
# LLM at once.  Financial-note materials remain available to Risk Research;
# these are the company-operating subjects relevant to this module's contract.
BUSINESS_DISCLOSURE_TYPES = {
    "CUSTOMER_CONCENTRATION", "BUSINESS_PRODUCT_STRUCTURE", "PPP_COLLECTION",
}


class BusinessRuntime(Protocol):
    def invoke(self, **kwargs: Any) -> dict[str, Any]: ...


class BusinessClaimValidationError(ValueError):
    def __init__(self, code: str, message: str, *, claim_index: int | None = None,
                 source_keys: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.claim_index = claim_index
        self.source_keys = list(source_keys or [])

    def audit_dict(self) -> dict[str, Any]:
        return {
            "validation_error_code": self.code,
            "claim_index": self.claim_index,
            "error_summary": str(self),
            "source_keys": self.source_keys,
        }


def _split_products(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、;；/]+", value) if item.strip()]


def _plain_language(text: str) -> bool:
    for term in JARGON:
        if term not in text:
            continue
        tail = text[text.index(term) + len(term):text.index(term) + len(term) + 50]
        if not re.search(r"[（(].{2,}[）)]|也就是|简单说|意思是|简单理解", tail):
            return False
    return True


class BusinessResearchService:
    def __init__(self, *, store: BusinessResearchStore | None = None,
                 profiles: CompanyBusinessProfileService | None = None,
                 config_store: ResearchTaskStore | None = None,
                 runtime: BusinessRuntime | None = None,
                 structured_runtime: StructuredOutputRuntime | None = None,
                 disclosure_store: DisclosureMaterialStore | None = None) -> None:
        self.store = store or BusinessResearchStore()
        self.profiles = profiles or CompanyBusinessProfileService()
        self.config_store = config_store or ResearchTaskStore(self.store.db_path)
        self.runtime = runtime or ProviderModelRuntime()
        self.structured_runtime = structured_runtime or StructuredOutputRuntime()
        self.disclosure_store = disclosure_store or DisclosureMaterialStore(self.store.db_path)
        self._owns_disclosure_store = disclosure_store is None
        self.citation_resolver = BusinessClaimCitationResolver()

    def close(self) -> None:
        self.config_store.close()
        self.store.close()
        if self._owns_disclosure_store:
            self.disclosure_store.close()

    def _agent_config(self) -> tuple[dict[str, Any], bool]:
        runtime_config = getattr(self.config_store, "get_runtime_config", self.config_store.get_config)
        config = runtime_config("financial_analyst")
        provider = next((row for row in safe_provider_catalog(self.config_store.list_configs())
                         if row["provider"] == config["provider"]), None)
        direct_ready = bool(config.get("base_url") and config.get("model"))
        ready = bool(config.get("enabled") and config.get("model")
                     and (direct_ready or (provider and provider.get("configured"))))
        return config, ready

    @staticmethod
    def _source_manifest(profile: dict[str, Any], *, role: str = "CURRENT") -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        source_by_field: dict[str, dict[str, Any]] = {}
        for source in profile.get("source") or []:
            for field in source.get("fields") or []:
                source_by_field.setdefault(str(field), source)
        for field in ("main_business", "main_products", "business_scope", "company_description"):
            value = str(profile.get(field) or "").strip()
            if not value:
                continue
            source = source_by_field.get(field) or ((profile.get("source") or [{}])[0])
            key = f"BUSINESS_{role}_{field.upper()}"
            result[key] = {
                "source_type": "TDX_BUSINESS_PROFILE",
                "source_id": f"{source.get('dataset') or 'fundamentals'}:{profile['stock_code']}",
                "data_as_of": source.get("data_as_of") or profile.get("updated_at"),
                "field": field,
                "value": value,
                "source_hash": profile.get("source_hash"),
                "profile_role": role,
            }
        return result

    def _disclosure_manifest(self, stock_code: str, *, as_of: str | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """Expose stored official-report excerpts as bounded, cited inputs.

        This method never downloads a report and never infers a conclusion from
        a keyword.  The scheduled/explicit disclosure sync owns collection;
        Business Research merely consumes its already-persisted excerpts.
        """
        material_rows = (
            self.disclosure_store.list_materials(stock_code, as_of=as_of)
            if as_of else self.disclosure_store.list_materials(stock_code)
        )
        rows = [
            row for row in material_rows
            if str(row.get("status") or "") == "FOUND" and row.get("excerpts")
        ]
        by_subject: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if str(row.get("material_type") or "") not in BUSINESS_DISCLOSURE_TYPES:
                continue
            # A comparable business-change claim must have two filings of the
            # same kind.  Grouping prevents an H1 filing from being presented
            # as a direct comparison with an annual report.
            key = str(row.get("material_type") or "DISCLOSURE")
            by_subject.setdefault(key, []).append(row)
        result: dict[str, dict[str, Any]] = {}
        availability: dict[str, str] = {}
        for subject, candidates in by_subject.items():
            # One current/previous pair per subject is sufficient for this
            # module.  Select the newest comparable report kind (normally H1
            # versus prior-year H1) instead of feeding every report type and
            # many duplicate excerpts to the model.
            by_kind: dict[str, list[dict[str, Any]]] = {}
            for candidate in candidates:
                by_kind.setdefault(str(candidate.get("report_kind") or "REPORT"), []).append(candidate)
            comparable = max(
                by_kind.values(),
                key=lambda values: max(str(item.get("announcement_date") or "") for item in values),
            )
            candidates = comparable
            candidates.sort(key=lambda item: (str(item.get("announcement_date") or ""), str(item.get("id") or "")), reverse=True)
            for index, row in enumerate(candidates[:2]):
                role = "CURRENT" if index == 0 else "PREVIOUS"
                material_type = str(row.get("material_type") or "DISCLOSURE")
                report_kind = str(row.get("report_kind") or "REPORT")
                key = f"DISCLOSURE_{role}_{material_type}_{report_kind}"
                excerpts = row.get("excerpts") if isinstance(row.get("excerpts"), list) else []
                text = "\n".join(str(item.get("text") or "") for item in excerpts if isinstance(item, dict)).strip()
                if not text:
                    continue
                result[key] = {
                    "source_type": "CNINFO_PERIODIC_REPORT",
                    "source_id": str(row.get("source_url") or row.get("announcement_id") or ""),
                    "data_as_of": row.get("announcement_date"),
                    "field": material_type.lower(),
                    "value": text,
                    "source_hash": row.get("text_sha256"),
                    "profile_role": role,
                    "report_period": row.get("report_period"),
                    "report_kind": report_kind,
                    "announcement_id": row.get("announcement_id"),
                    "pages": [item.get("page") for item in excerpts if isinstance(item, dict) and item.get("page")],
                }
                availability[material_type] = "READY"
        return result, availability

    def input_fingerprint(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        """Recompute the current business source hash without any write.

        Mirrors prepare()'s hash inputs (profile, disclosure text hashes,
        previous snapshot pairing) for ResearchFreshnessService comparison
        (plan §7/§9).  Pure reads only.
        """
        try:
            profile = self.profiles.profile(stock_code)
            if profile is None:
                return None
            if as_of and str(profile.get("updated_at") or "")[:10] > str(as_of)[:10]:
                return None
            disclosure_sources, _availability = self._disclosure_manifest(profile["stock_code"], as_of=as_of)
            latest = self.store.latest(profile["stock_code"], as_of=as_of)
            latest_profile_hash = str((latest or {}).get("snapshot", {}).get("profile", {}).get("source_hash") or "")
            if latest is not None and latest_profile_hash != profile["source_hash"]:
                previous = latest
            elif latest is not None:
                previous = self.store.latest_before_hash(profile["stock_code"], latest["source_hash"], as_of=as_of)
            else:
                previous = None
            previous_profile = (previous or {}).get("snapshot", {}).get("profile") or {}
            return {
                "source_hash": stable_hash({
                    "version": BUSINESS_RESEARCH_VERSION,
                    "profile_hash": profile["source_hash"],
                    "previous_profile_hash": previous_profile.get("source_hash"),
                    "disclosure_sources": {key: value.get("source_hash") for key, value in disclosure_sources.items()},
                }),
                "data_as_of": profile.get("updated_at"),
            }
        except Exception:  # noqa: BLE001 - classification must degrade, not fail
            return None

    def prepare(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        profile = self.profiles.profile(stock_code)
        if profile is None:
            raise ValueError(f"business profile not found for {stock_code.upper()}")
        if as_of and str(profile.get("updated_at") or "")[:10] > str(as_of)[:10]:
            raise ValueError(f"business_profile_after_as_of:{stock_code.upper()}:{profile.get('updated_at')}")
        current_sources = self._source_manifest(profile)
        disclosure_sources, disclosure_availability = self._disclosure_manifest(profile["stock_code"], as_of=as_of)
        latest = self.store.latest(profile["stock_code"], as_of=as_of)
        latest_profile_hash = str((latest or {}).get("snapshot", {}).get("profile", {}).get("source_hash") or "")
        if latest is not None and latest_profile_hash != profile["source_hash"]:
            previous = latest
        elif latest is not None:
            previous = self.store.latest_before_hash(profile["stock_code"], latest["source_hash"], as_of=as_of)
        else:
            previous = None
        previous_profile = (previous or {}).get("snapshot", {}).get("profile") or {}
        previous_sources = self._source_manifest(previous_profile, role="PREVIOUS") if previous_profile else {}
        sources = {**current_sources, **previous_sources, **disclosure_sources}
        main_business = str(profile.get("main_business") or "").strip()
        explicit_products = str(profile.get("main_products") or "").strip()
        products = _split_products(explicit_products or main_business) if explicit_products or main_business else []
        product_status = "READY" if explicit_products else "PARTIAL" if products else "MISSING"
        field_statuses = {
            "main_business": "READY" if main_business else "MISSING",
            "products": product_status,
            "business_model": "MISSING",
            "business_changes": "PARTIAL" if previous_sources or any("_PREVIOUS_" in key for key in disclosure_sources) else "MISSING",
        }
        # These are source-coverage indicators, not completed business
        # conclusions.  Keeping them separate avoids implying that a keyword
        # hit is enough to clear a core business-research gap.
        disclosure_field_statuses = {
            "customer_concentration_source": disclosure_availability.get("CUSTOMER_CONCENTRATION", "MISSING"),
            "product_structure_source": disclosure_availability.get("BUSINESS_PRODUCT_STRUCTURE", "MISSING"),
            "receivables_ageing_source": disclosure_availability.get("ACCOUNTS_RECEIVABLE_AGEING", "MISSING"),
            "receivables_impairment_source": disclosure_availability.get("RECEIVABLES_IMPAIRMENT", "MISSING"),
            "ppp_collection_source": disclosure_availability.get("PPP_COLLECTION", "MISSING"),
            "debt_maturity_source": disclosure_availability.get("DEBT_MATURITY", "MISSING"),
            "guarantees_source": disclosure_availability.get("GUARANTEES_CONTINGENCIES", "MISSING"),
        }
        missing = [key for key, value in field_statuses.items() if value == "MISSING"]
        status = "READY" if not missing and all(value == "READY" for value in field_statuses.values()) else (
            "PARTIAL" if sources else "MISSING"
        )
        snapshot = {
            "stock_code": profile["stock_code"],
            "company_name": profile["stock_name"],
            "data_as_of": profile["updated_at"],
            "main_business": main_business or "UNKNOWN",
            "products": products,
            "product_note": (
                "通达信有独立主要产品字段。" if explicit_products else
                "产品名称暂从主营业务原文展示；现有资料没有产品收入占比，不能判断哪个产品贡献最大。"
                if products else "现有资料没有可验证的产品信息。"
            ),
            "business_model": "UNKNOWN",
            "business_changes": ["UNKNOWN：缺少可比较的历史经营资料。"],
            "profile": profile,
            "sources": sources,
            "source_hash": stable_hash({
                "version": BUSINESS_RESEARCH_VERSION,
                "profile_hash": profile["source_hash"],
                "previous_profile_hash": previous_profile.get("source_hash"),
                "disclosure_sources": {key: value.get("source_hash") for key, value in disclosure_sources.items()},
            }),
            "data_quality": {
                "status": status,
                "field_statuses": field_statuses,
                "disclosure_field_statuses": disclosure_field_statuses,
                "missing_fields": missing,
                "limitations": [
                    "没有产品收入占比时，不判断产品贡献大小。",
                    "没有前后两期可比经营资料时，不判断经营变化。",
                    "商业模式仅允许基于已有业务资料形成带来源的推断。",
                    "公告资料仅提供可追溯原文摘录；未通过来源校验的资料不会生成经营结论。",
                ],
            },
            "module_version": BUSINESS_RESEARCH_VERSION,
        }
        config, configured = self._agent_config()
        row, created = self.store.save(
            snapshot, configured=configured, provider=str(config.get("provider") or ""),
            model=str(config.get("model") or ""),
        )
        return self._public(row, idempotent_reuse=not created)

    @staticmethod
    def _public(row: dict[str, Any], *, idempotent_reuse: bool = True) -> dict[str, Any]:
        snapshot = dict(row.get("snapshot") or {})
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else None
        claims = analysis.get("claims") if isinstance(analysis, dict) and isinstance(analysis.get("claims"), list) else []
        model_claims = [
            item for item in claims
            if isinstance(item, dict) and item.get("topic") == "BUSINESS_MODEL" and item.get("type") != "UNKNOWN"
        ]
        change_claims = [
            str(item.get("text") or "") for item in claims
            if isinstance(item, dict) and item.get("topic") == "BUSINESS_CHANGE" and item.get("text")
        ]
        if model_claims:
            snapshot["business_model"] = str(model_claims[0].get("text") or "UNKNOWN")
        if change_claims:
            snapshot["business_changes"] = change_claims
        return {
            "id": row.get("id"),
            **snapshot,
            "analysis_status": row.get("analysis_status"),
            "analysis": analysis,
            "agent_provider": row.get("agent_provider"),
            "agent_model": row.get("agent_model"),
            "agent_error": row.get("agent_error"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "idempotent_reuse": idempotent_reuse,
        }

    def get(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        prepared = self.prepare(stock_code, as_of=as_of)
        return self.citation_resolver.resolve_snapshot(prepared)

    def get_saved_research(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Read saved business research at or before a research cutoff."""
        row = self.store.latest(stock_code.upper(), as_of=as_of)
        return self.citation_resolver.resolve_snapshot(self._public(row)) if row else {}

    @staticmethod
    def contract_schema(manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "maxLength": 600},
                "claims": {
                    "type": "array", "maxItems": MAX_BUSINESS_CLAIMS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": sorted(BUSINESS_CLAIM_TYPES)},
                            "topic": {"type": "string", "enum": sorted(BUSINESS_TOPICS)},
                            "text": {"type": "string", "maxLength": 260},
                            "source_keys": {"type": "array", "items": {"type": "string", "enum": sorted(manifest)}},
                            "confidence": {"type": "string", "enum": sorted(BUSINESS_CONFIDENCES)},
                        },
                        "required": ["type", "topic", "text", "source_keys", "confidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["summary", "claims"],
            "additionalProperties": False,
        }

    @staticmethod
    def validate_claims(result: dict[str, Any], manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if set(result) != {"summary", "claims"} or not isinstance(result.get("claims"), list):
            raise BusinessClaimValidationError("TOP_LEVEL_SCHEMA_INVALID", "business claims schema is invalid")
        summary = str(result.get("summary") or "").strip()
        if not summary:
            raise BusinessClaimValidationError("TOP_LEVEL_SCHEMA_INVALID", "summary is required")
        if TRADING_LANGUAGE.search(json.dumps(result, ensure_ascii=False)):
            raise BusinessClaimValidationError("TRADING_LANGUAGE", "trading language is prohibited")
        if not _plain_language(summary):
            raise BusinessClaimValidationError("JARGON_WITHOUT_EXPLANATION", "summary contains unexplained jargon")
        claims = result["claims"]
        if len(claims) > MAX_BUSINESS_CLAIMS:
            raise BusinessClaimValidationError("TOO_MANY_CLAIMS", "too many business claims")
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(claims):
            if not isinstance(raw, dict) or set(raw) != {"type", "topic", "text", "source_keys", "confidence"}:
                keys = sorted(str(key) for key in raw) if isinstance(raw, dict) else []
                raise BusinessClaimValidationError(
                    "CLAIM_SCHEMA_INVALID", f"business claim schema is invalid; keys={keys}",
                    claim_index=index,
                )
            claim_type = str(raw.get("type") or "").upper()
            topic = str(raw.get("topic") or "").upper()
            text = str(raw.get("text") or "").strip()
            confidence = str(raw.get("confidence") or "").upper()
            keys = raw.get("source_keys")
            if claim_type not in BUSINESS_CLAIM_TYPES:
                raise BusinessClaimValidationError("INVALID_CLAIM_TYPE", "invalid business claim type", claim_index=index)
            if topic not in BUSINESS_TOPICS:
                raise BusinessClaimValidationError("INVALID_TOPIC", "invalid business claim topic", claim_index=index)
            if confidence not in BUSINESS_CONFIDENCES:
                raise BusinessClaimValidationError("INVALID_CONFIDENCE", "invalid business claim confidence", claim_index=index)
            if not text:
                raise BusinessClaimValidationError("EMPTY_CLAIM_TEXT", "business claim text is required", claim_index=index)
            if not _plain_language(text):
                raise BusinessClaimValidationError("JARGON_WITHOUT_EXPLANATION", "claim contains unexplained jargon", claim_index=index)
            if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
                raise BusinessClaimValidationError("UNKNOWN_SOURCE_KEY", "source_keys must be a string array", claim_index=index)
            keys = list(dict.fromkeys(key.strip() for key in keys if key.strip()))
            if claim_type in {"FACT", "INFERENCE"} and not keys:
                raise BusinessClaimValidationError(f"{claim_type}_WITHOUT_SOURCE", f"{claim_type} requires sources", claim_index=index)
            missing = sorted(set(keys) - set(manifest))
            if missing:
                raise BusinessClaimValidationError("UNKNOWN_SOURCE_KEY", "unknown business source key", claim_index=index, source_keys=missing)
            if topic == "BUSINESS_CHANGE" and claim_type in {"FACT", "INFERENCE"}:
                roles = {str(manifest[key].get("profile_role") or "CURRENT") for key in keys}
                if not {"CURRENT", "PREVIOUS"} <= roles:
                    raise BusinessClaimValidationError("CHANGE_WITHOUT_COMPARISON", "business change requires current and previous sources", claim_index=index, source_keys=keys)
            source_text = " ".join(str(manifest[key].get("value") or "") for key in keys)
            unsupported_numbers = [token for token in NUMERIC_TOKEN.findall(text) if token not in source_text]
            if unsupported_numbers:
                raise BusinessClaimValidationError("NUMERIC_MISMATCH", "claim contains numbers absent from sources", claim_index=index, source_keys=keys)
            share_match = re.search(r"占比(?:最高|最大)|贡献(?:最高|最大)|收入占比", text)
            if share_match and not re.search(r"占比|贡献", source_text):
                nearby = text[max(0, share_match.start() - 16):share_match.start()]
                if not re.search(r"没有|未说明|未披露|无法|不能|不足以|看不出", nearby):
                    raise BusinessClaimValidationError(
                        "UNSUPPORTED_PRODUCT_SHARE", "product contribution is absent from sources",
                        claim_index=index, source_keys=keys,
                    )
            normalized.append({
                "type": claim_type, "topic": topic, "text": text,
                "source_keys": keys, "confidence": confidence,
            })
        return {"summary": summary, "claims": normalized}

    @staticmethod
    def _instruction() -> str:
        return (
            "你是现有财报研究员中的公司经营研究模块。只根据 Business Source Manifest 输出 JSON，"
            "只允许 summary 和 claims。claims 最多 8 条，type 只允许 FACT、INFERENCE、UNKNOWN，"
            "topic 只允许 MAIN_BUSINESS、PRODUCT、BUSINESS_MODEL、BUSINESS_CHANGE。"
            "每条 claim 必须且只能包含 type、topic、text、source_keys、confidence；"
            "confidence 只允许 LOW、MEDIUM、HIGH。"
            "FACT 和 INFERENCE 必须引用 allowed_source_keys 中逐字一致的键名；"
            "source_keys 只能填写 Manifest 对象最外层的键，不能填写 source_id、股票代码或字段值；"
            "UNKNOWN 可以没有来源。"
            "本步骤没有 FORECAST。经营变化的 FACT/INFERENCE 必须同时引用同一资料类别的 CURRENT 和 PREVIOUS 成对资料；"
            "只引用 CURRENT、或只有静态主营/客户/产品资料时，BUSINESS_CHANGE 必须写 UNKNOWN，不能根据单期资料推断变化；"
            "没有可比历史资料就写 UNKNOWN，不得制造重大变化。没有产品收入占比就不得判断哪个产品贡献最大。"
            "summary 和 text 必须用普通人能理解的话，按发生了什么、为什么重要、意味着什么、还要观察什么来写。"
            "输出前逐条自查：不得直接使用“客户集中度、议价权、护城河、单位经济模型、渠道下沉”；"
            "如确有必要，必须紧跟括号解释，例如“客户集中度（简单说就是是否依赖少数几个客户）”。"
            "经营研究默认不要写任何阿拉伯数字；只有来源原文逐字出现、且确有必要时才可保留数字。"
            "不得把报告年份、页码、表格序号或单位换算成观点数字；不得补造产品、客户、收入占比、年份或数字。"
            "禁止买卖、目标价、仓位、止损、加减仓等交易建议。只返回 JSON，不要 Markdown。"
        )

    def analyze(self, stock_code: str, *, force: bool = False, as_of: str | None = None) -> dict[str, Any]:
        prepared = self.prepare(stock_code, as_of=as_of)
        if prepared.get("analysis_status") == "COMPLETED" and not force:
            return self.citation_resolver.resolve_snapshot(prepared)
        config, configured = self._agent_config()
        manifest = dict(prepared.get("sources") or {})
        if not manifest:
            unknowns = [
                ("MAIN_BUSINESS", "现有资料没有说明公司主要做什么。"),
                ("PRODUCT", "现有资料没有列出公司的主要产品或服务。"),
                ("BUSINESS_MODEL", "现有资料不足，无法判断公司主要通过什么方式获得收入。"),
                ("BUSINESS_CHANGE", "缺少前后两期可比较的经营资料，无法确认最近发生了什么经营变化。"),
            ]
            analysis = {
                "summary": "当前本地资料不足，不能可靠说明这家公司的主营业务、产品、赚钱方式和经营变化。",
                "claims": [
                    {"type": "UNKNOWN", "topic": topic, "text": text, "source_keys": [], "confidence": "LOW"}
                    for topic, text in unknowns
                ],
                "analysis_metadata": {
                    "quality_status": "DETERMINISTIC_UNKNOWN",
                    "module_version": BUSINESS_RESEARCH_VERSION,
                    "reason": "NO_BUSINESS_SOURCE",
                },
            }
            row = self.store.update_analysis(
                prepared["id"], status="COMPLETED", provider=str(config.get("provider") or ""),
                model=str(config.get("model") or ""), analysis=analysis,
            )
            return self.citation_resolver.resolve_snapshot(self._public(row, idempotent_reuse=False))
        if not configured:
            row = self.store.update_analysis(
                prepared["id"], status="CONFIGURATION_REQUIRED",
                provider=str(config.get("provider") or ""), model=str(config.get("model") or ""),
                error="Financial Researcher model is disabled or credentials are unavailable",
            )
            return self.citation_resolver.resolve_snapshot(self._public(row))
        payload = {
            "company": {"stock_code": prepared["stock_code"], "company_name": prepared["company_name"]},
            "allowed_source_keys": sorted(manifest),
            "business_source_manifest": {
                key: {field: value for field, value in source.items() if field != "source_id"}
                for key, source in manifest.items()
            },
            "data_quality": prepared["data_quality"],
            "module_version": BUSINESS_RESEARCH_VERSION,
        }
        capabilities = resolve_structured_output_capabilities(config)

        def invoke(mode: StructuredOutputMode, response_format: dict[str, Any] | None) -> dict[str, Any]:
            connection_invoke = getattr(self.runtime, "invoke_with_connection", None)
            if config.get("base_url") and callable(connection_invoke):
                return connection_invoke(
                    role="financial_analyst", phase="BUSINESS_RESEARCH", model=config["model"],
                    base_url=config["base_url"], api_key=config.get("api_key") or "",
                    instruction=self._instruction(), payload=payload, target_schema=response_format,
                    max_tokens=BUSINESS_RESEARCH_MAX_TOKENS,
                    extra_body=config.get("request_extra_body"),
                )
            return self.runtime.invoke(
                role="financial_analyst", phase="BUSINESS_RESEARCH", provider=config["provider"],
                model=config["model"], instruction=self._instruction(), payload=payload,
                target_schema=response_format, max_tokens=BUSINESS_RESEARCH_MAX_TOKENS,
                extra_body=config.get("request_extra_body"),
            )

        try:
            outcome = self.structured_runtime.run(
                config=config, instruction=self._instruction(), payload=payload,
                contract_schema=self.contract_schema(manifest), capabilities=capabilities,
                text_instruction="资料不足，无法生成可验证经营 Claims。", text_payload={},
                invoke_structured=invoke, validate=lambda value: self.validate_claims(value, manifest),
            )
            if outcome.parsed is None:
                analysis = {
                    "summary": "现有模型没有生成通过来源校验的经营结论，请先看确定性业务资料。",
                    "claims": [],
                    "analysis_metadata": {
                        "quality_status": "SUMMARY_ONLY", "structured_attempts": outcome.attempts,
                        "error_types": outcome.error_types, "module_version": BUSINESS_RESEARCH_VERSION,
                    },
                }
            else:
                analysis = {
                    **outcome.parsed,
                    "analysis_metadata": {
                        "quality_status": "STRUCTURED", "structured_mode": outcome.mode_used,
                        "structured_attempts": outcome.attempts, "error_types": outcome.error_types,
                        "module_version": BUSINESS_RESEARCH_VERSION,
                    },
                }
            row = self.store.update_analysis(
                prepared["id"], status="COMPLETED", provider=str(config.get("provider") or ""),
                model=str(config.get("model") or ""), analysis=analysis,
            )
            return self.citation_resolver.resolve_snapshot(self._public(row, idempotent_reuse=False))
        except Exception as exc:
            row = self.store.update_analysis(
                prepared["id"], status="FAILED", provider=str(config.get("provider") or ""),
                model=str(config.get("model") or ""), error=f"{type(exc).__name__}: {exc}",
            )
            return self.citation_resolver.resolve_snapshot(self._public(row, idempotent_reuse=False))


_service: BusinessResearchService | None = None


def get_business_research_service() -> BusinessResearchService:
    global _service
    if _service is None:
        _service = BusinessResearchService()
    return _service
