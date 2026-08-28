"""Deterministic, read-only synthesis of saved moat evidence.

This module deliberately does not extract evidence, fetch disclosures, run an
LLM, recalculate L3, or mutate Thesis/Risk/portfolio data.  Its job is only to
make the limits of the already-saved evidence intelligible.
"""

from __future__ import annotations

from collections import defaultdict
import json
from typing import Any, Callable

from src.business_research.store import BusinessResearchStore
from src.leader_quality_profile.service import get_leader_quality_profile_service
from src.level3_leaders.business_profiles import CompanyBusinessProfileService
from src.moat_evidence.store import MoatEvidenceStore
from src.research_workspace.store import normalize_market, normalize_symbol


FORMULA_VERSION = "moat-research-v1.0.0"
DIMENSIONS = (
    "BRAND", "SWITCHING_COST", "NETWORK_EFFECT", "COST_ADVANTAGE", "EFFICIENT_SCALE",
    "TECHNOLOGY", "CHANNEL", "REGULATORY", "CUSTOMER_RELATIONSHIP", "SUPPLY_CHAIN",
    "DATA_PLATFORM_ECOSYSTEM",
)
DIMENSION_LABELS = {
    "BRAND": "品牌与无形资产", "SWITCHING_COST": "客户转换成本", "NETWORK_EFFECT": "网络效应",
    "COST_ADVANTAGE": "成本优势", "EFFICIENT_SCALE": "规模与有效规模", "TECHNOLOGY": "技术与专利",
    "CHANNEL": "渠道优势", "REGULATORY": "牌照与准入", "CUSTOMER_RELATIONSHIP": "客户关系与认证",
    "SUPPLY_CHAIN": "供应链能力", "DATA_PLATFORM_ECOSYSTEM": "数据、平台与生态",
}

# This is an applicability guard, not a judgement about whether an advantage
# exists.  It keeps, for example, network effects out of ordinary restaurants.
INDUSTRY_DIMENSIONS = {
    "餐饮": {"BRAND", "CHANNEL", "SUPPLY_CHAIN", "CUSTOMER_RELATIONSHIP", "SWITCHING_COST", "EFFICIENT_SCALE", "COST_ADVANTAGE"},
    "半导体": {"TECHNOLOGY", "CUSTOMER_RELATIONSHIP", "SWITCHING_COST", "COST_ADVANTAGE", "EFFICIENT_SCALE", "SUPPLY_CHAIN"},
    "空调": {"BRAND", "CHANNEL", "TECHNOLOGY", "COST_ADVANTAGE", "EFFICIENT_SCALE", "SUPPLY_CHAIN", "CUSTOMER_RELATIONSHIP"},
    "家电": {"BRAND", "CHANNEL", "TECHNOLOGY", "COST_ADVANTAGE", "EFFICIENT_SCALE", "SUPPLY_CHAIN", "CUSTOMER_RELATIONSHIP"},
    "银行": {"REGULATORY", "CUSTOMER_RELATIONSHIP", "COST_ADVANTAGE", "EFFICIENT_SCALE", "DATA_PLATFORM_ECOSYSTEM", "SWITCHING_COST"},
    "医药": {"TECHNOLOGY", "REGULATORY", "CUSTOMER_RELATIONSHIP", "CHANNEL", "BRAND"},
    "制造": {"COST_ADVANTAGE", "EFFICIENT_SCALE", "TECHNOLOGY", "CUSTOMER_RELATIONSHIP", "SUPPLY_CHAIN"},
}


def _date(value: Any) -> str:
    return str(value or "")[:10]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


class MoatResearchService:
    """Synthesize saved evidence with deliberately conservative gates."""

    def __init__(
        self, *, evidence_store: MoatEvidenceStore | None = None,
        leader_profile_loader: Callable[[str, str, str | None], dict[str, Any]] | None = None,
        business_profiles: CompanyBusinessProfileService | None = None,
        business_store: BusinessResearchStore | None = None,
    ) -> None:
        self.evidence_store = evidence_store or MoatEvidenceStore()
        self._owns_store = evidence_store is None
        self.business_store = business_store or BusinessResearchStore(self.evidence_store.db_path)
        self._owns_business_store = business_store is None
        self.leader_profile_loader = leader_profile_loader or (
            lambda market, symbol, as_of: get_leader_quality_profile_service().get_profile(market, symbol, as_of)
        )
        self.business_profiles = business_profiles or CompanyBusinessProfileService()

    def close(self) -> None:
        if self._owns_store:
            self.evidence_store.close()
        if self._owns_business_store:
            self.business_store.close()

    @staticmethod
    def _industry_dimensions(leader_profile: dict[str, Any], business_profile: dict[str, Any] | None,
                             business_research: dict[str, Any] | None) -> tuple[set[str] | None, str | None]:
        position = leader_profile.get("leader_position") or {}
        if position.get("status") != "READY":
            return None, None
        name = str((position.get("level3") or {}).get("name") or "")
        for token, dimensions in INDUSTRY_DIMENSIONS.items():
            if token in name:
                enriched = set(dimensions)
                # These saved descriptions only decide whether a dimension can
                # be relevant. They never act as evidence of an advantage.
                context = json.dumps({"profile": business_profile or {}, "research": (business_research or {}).get("snapshot") or {}}, ensure_ascii=False)
                if any(keyword in context for keyword in ("平台", "网络", "生态", "数据")):
                    enriched.update({"NETWORK_EFFECT", "DATA_PLATFORM_ECOSYSTEM"})
                return enriched, name
        # A known L3 classification with no V1-specific guard does not mean all
        # dimensions apply.  It is safer to leave applicability unresolved.
        return None, name or None

    @staticmethod
    def _periods(items: list[dict[str, Any]]) -> list[str]:
        return sorted({_date(item.get("report_date")) or _date(item.get("announcement_date")) for item in items if _date(item.get("report_date")) or _date(item.get("announcement_date"))})

    @staticmethod
    def _strong_counter(items: list[dict[str, Any]]) -> bool:
        return len(items) >= 2 or any(str(item.get("confidence")).upper() == "HIGH" for item in items)

    @staticmethod
    def _persistence(official: list[dict[str, Any]], counters: list[dict[str, Any]]) -> str:
        periods = MoatResearchService._periods(official)
        if len(periods) >= 2 and counters:
            newest_counter = max((_date(item.get("report_date")) or _date(item.get("announcement_date")) for item in counters), default="")
            if newest_counter >= periods[-1]:
                return "DETERIORATING"
        return "REPEATED" if len(periods) >= 2 else "SINGLE_PERIOD"

    @staticmethod
    def _official_support_gate(official: list[dict[str, Any]], leader_context: bool, strong_counter: bool) -> bool:
        if not official or strong_counter:
            return False
        # Cross-period reporting is the clearest persistence proof.  Separate
        # formal documents also count.  Peer context may reinforce, never
        # replace, one traceable formal fact.
        periods = MoatResearchService._periods(official)
        documents = {str(item.get("source_document_id") or item.get("source_material_id") or "") for item in official}
        facts = {str(item.get("fact_key") or item.get("id")) for item in official}
        return len(periods) >= 2 or len(documents - {""}) >= 2 or (len(facts) >= 2 and leader_context)

    @staticmethod
    def _leader_context(profile: dict[str, Any], dimension: str) -> bool:
        categories = profile.get("peer_advantage_categories") or []
        strengths = {str(item.get("dimension")) for item in categories if item.get("status") in {"STRONG", "ABOVE_AVERAGE"}}
        if dimension in {"EFFICIENT_SCALE", "COST_ADVANTAGE"}:
            return bool(strengths & {"SCALE", "PROFITABILITY", "CASH_QUALITY", "FINANCIAL_STRENGTH"})
        if dimension == "TECHNOLOGY":
            return bool(strengths & {"GROWTH", "PROFITABILITY"})
        return False

    @staticmethod
    def _summary(dimension: str, status: str, balance: str, official_count: int, claims: int) -> str:
        label = DIMENSION_LABELS[dimension]
        if status == "SUPPORTED":
            return f"{label}有跨来源或跨期的正式披露事实支持；仍应结合反证持续复核。"
        if status == "PARTIAL":
            reason = "正式披露事实尚未形成持续验证" if official_count else "当前主要是管理层表述或有限推断"
            if balance == "MIXED":
                reason += "，且存在反向证据"
            return f"{label}存在一定证据，但{reason}，不足以确认其为长期竞争优势。"
        if balance == "CHALLENGED":
            return f"{label}目前只看到反向或挑战性资料，不能据此推断不存在优势。"
        return f"当前没有足够、且与行业匹配的资料判断{label}。"

    @staticmethod
    def _company_summary(strengths: list[dict[str, Any]], partials: list[dict[str, Any]], gaps: list[str]) -> str:
        supported = "、".join(item["label"] for item in strengths if item["status"] == "SUPPORTED")
        preliminary = "、".join(item["label"] for item in partials[:3])
        parts = [f"当前较有证据支持的竞争优势主要集中在{supported}。" if supported else "当前尚无维度达到“较充分事实支持”的严格门槛。"]
        if preliminary:
            parts.append(f"{preliminary}存在初步迹象，但仍主要依赖有限披露、管理层声明或财务代理，尚缺独立经营数据验证。")
        if gaps:
            parts.append(f"当前尤其缺少：{'、'.join(gaps[:4])}。")
        return "".join(parts)

    def get_research(self, market: str, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        leader = self.leader_profile_loader(normalized_market, symbol, as_of)
        research_as_of = str(as_of or leader.get("research_as_of") or "")[:10] or None
        all_evidence = self.evidence_store.list(normalized_market, symbol, as_of=research_as_of, active=None)
        business_research = self.business_store.latest(symbol, as_of=research_as_of)
        profile = self.business_profiles.profile(symbol)  # cached TDX text only; no request or refresh
        # Current cache records are unversioned; do not allow a later profile
        # to change applicability for a historical as-of request.
        profile_for_context = profile if not research_as_of or _date((profile or {}).get("updated_at")) <= research_as_of else None
        applicable, industry_name = self._industry_dimensions(leader, profile_for_context, business_research)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in all_evidence:
            if item.get("moat_dimension") in DIMENSIONS:
                grouped[str(item["moat_dimension"])].append(item)
        dimensions: list[dict[str, Any]] = []
        global_gaps = list(leader.get("moat_data_gaps") or [])
        for dimension in DIMENSIONS:
            items = grouped[dimension]
            if applicable is None:
                applicability = "UNKNOWN_APPLICABILITY"
            else:
                applicability = "APPLICABLE" if dimension in applicable else "NOT_APPLICABLE"
            official = [item for item in items if item.get("evidence_type") in {"QUANTIFIED_FACT", "DISCLOSED_FACT"}]
            claims = [item for item in items if item.get("evidence_type") == "MANAGEMENT_CLAIM"]
            inferences = [item for item in items if item.get("evidence_type") == "INFERENCE"]
            counters = [item for item in items if item.get("evidence_type") == "COUNTER_EVIDENCE"]
            unknowns = [item for item in items if item.get("evidence_type") == "UNKNOWN"]
            supports = official + claims + inferences
            balance = "MIXED" if supports and counters else "SUPPORTING" if supports else "CHALLENGED" if counters else "NO_SIGNAL"
            strong_counter = self._strong_counter(counters)
            if applicability != "APPLICABLE":
                status = "UNKNOWN"
            elif self._official_support_gate(official, self._leader_context(leader, dimension), strong_counter):
                status = "SUPPORTED"
            elif official or claims or inferences:
                status = "PARTIAL"
            else:
                status = "UNKNOWN"
            persistence = self._persistence(official, counters)
            gap_values = _unique([str(item.get("summary") or item.get("claim") or "") for item in unknowns])
            if applicability == "APPLICABLE" and not official:
                gap_values.append(f"缺少可追溯的{DIMENSION_LABELS[dimension]}正式披露事实")
            gap_values = _unique(gap_values)
            global_gaps.extend(gap_values)
            confidence = "HIGH" if status == "SUPPORTED" and not counters else "MEDIUM" if status == "PARTIAL" else "LOW"
            dimensions.append({
                "dimension": dimension, "label": DIMENSION_LABELS[dimension], "applicability": applicability,
                "status": status, "evidence_balance": balance, "confidence": confidence, "persistence": persistence,
                "summary": self._summary(dimension, status, balance, len(official), len(claims)),
                "supporting_evidence_ids": [item["id"] for item in official + inferences],
                "counter_evidence_ids": [item["id"] for item in counters],
                "management_claim_ids": [item["id"] for item in claims], "data_gaps": gap_values,
                "supporting_source_refs": [{
                    "evidence_id": item["id"], "source_type": item.get("source_type"), "source_document_id": item.get("source_document_id"),
                    "report_date": item.get("report_date"), "announcement_date": item.get("announcement_date"), "page_number": item.get("page_number"),
                } for item in official + inferences],
                "counter_source_refs": [{
                    "evidence_id": item["id"], "source_type": item.get("source_type"), "source_document_id": item.get("source_document_id"),
                    "report_date": item.get("report_date"), "announcement_date": item.get("announcement_date"), "page_number": item.get("page_number"),
                } for item in counters],
                "management_claim_source_refs": [{
                    "evidence_id": item["id"], "source_type": item.get("source_type"), "source_document_id": item.get("source_document_id"),
                    "report_date": item.get("report_date"), "announcement_date": item.get("announcement_date"), "page_number": item.get("page_number"),
                } for item in claims],
                "evidence_counts": {"quantified_fact": sum(item.get("evidence_type") == "QUANTIFIED_FACT" for item in items), "disclosed_fact": sum(item.get("evidence_type") == "DISCLOSED_FACT" for item in items), "management_claim": len(claims), "inference": len(inferences), "counter": len(counters)},
            })
        advantages = [item for item in dimensions if item["status"] in {"SUPPORTED", "PARTIAL"} and item["evidence_balance"] in {"SUPPORTING", "MIXED"}]
        advantages.sort(key=lambda item: (item["status"] != "SUPPORTED", item["confidence"] != "HIGH", -len(item["supporting_evidence_ids"])))
        challenges = [
            {"dimension": item["dimension"], "label": item["label"], "summary": item["summary"], "evidence_balance": item["evidence_balance"], "counter_evidence_ids": item["counter_evidence_ids"]}
            for item in dimensions if item["counter_evidence_ids"]
        ]
        official_evidence_count = sum(
            1 for item in all_evidence if item.get("evidence_type") in {"QUANTIFIED_FACT", "DISCLOSED_FACT"}
        )
        overall = "READY" if any(item["status"] == "SUPPORTED" for item in dimensions) else "PARTIAL" if advantages or all_evidence else "UNKNOWN"
        company = dict(leader.get("company") or {})
        if not company.get("stock_name") and profile:
            company["stock_name"] = profile.get("stock_name")
        return {
            "company": {"market": normalized_market, "stock_code": symbol, "stock_name": company.get("stock_name") or symbol},
            "research_as_of": research_as_of, "status": overall, "industry_context": {
                "level3_name": industry_name, "applicability_status": "READY" if applicable is not None else "UNKNOWN",
                "business_profile_status": (profile or {}).get("data_status", "MISSING"),
                "business_research_status": (business_research or {}).get("analysis_status", "NOT_COLLECTED"),
            },
            "dimensions": dimensions, "supported_advantages": [{**item, "why_it_may_matter": item["label"] + "若能持续，可能帮助公司维持相对经营位置。", "key_evidence_ids": item["supporting_evidence_ids"]} for item in advantages[:5]],
            "moat_challenges": challenges, "moat_data_gaps": _unique(global_gaps),
            "moat_summary": self._company_summary([item for item in advantages if item["status"] == "SUPPORTED"], [item for item in advantages if item["status"] == "PARTIAL"], _unique(global_gaps)),
            "leader_quality_context": {"leader_position": leader.get("leader_position"), "strengths": leader.get("strengths", []), "pricing_power_proxy": leader.get("pricing_power_proxy"), "boundary": "龙头质量仅提供同行财务与排名事实，不能单独证明品牌、技术、渠道或成本优势。"},
            "source_status": {
                "moat_evidence": "READY" if official_evidence_count else "PARTIAL" if all_evidence else "NOT_COLLECTED",
                "official_evidence_count": str(official_evidence_count),
                "leader_quality": (leader.get("leader_position") or {}).get("status", "UNKNOWN"),
                "business_profile": (profile or {}).get("data_status", "MISSING"),
                "business_research": (business_research or {}).get("analysis_status", "NOT_COLLECTED"),
            },
            "formula_version": FORMULA_VERSION,
        }


_service: MoatResearchService | None = None


def get_moat_research_service() -> MoatResearchService:
    global _service
    if _service is None:
        _service = MoatResearchService()
    return _service
