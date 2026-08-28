"""Deterministic extraction of source-traceable competition-advantage evidence.

This is an evidence layer only.  It intentionally never emits a company moat
rating, never changes Leader Score/Thesis/Risk, and never downloads CNINFO
reports or invokes a model during extraction or reads.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

from src.business_research.store import BusinessResearchStore
from src.disclosure_materials.store import DisclosureMaterialStore
from src.leader_quality_profile.service import get_leader_quality_profile_service
from src.level3_leaders.business_profiles import CompanyBusinessProfileService
from src.research_workspace.store import normalize_market, normalize_symbol

from .store import MoatEvidenceStore


EXTRACTOR_VERSION = "moat-evidence-extraction-v1.0.0"
EVIDENCE_TYPES = {"QUANTIFIED_FACT", "DISCLOSED_FACT", "MANAGEMENT_CLAIM", "INFERENCE", "COUNTER_EVIDENCE", "UNKNOWN"}
DIMENSIONS = {
    "BRAND", "SWITCHING_COST", "NETWORK_EFFECT", "COST_ADVANTAGE", "EFFICIENT_SCALE", "TECHNOLOGY",
    "CHANNEL", "REGULATORY", "CUSTOMER_RELATIONSHIP", "SUPPLY_CHAIN", "DATA_PLATFORM_ECOSYSTEM",
}

_INDUSTRY_DIMENSIONS = {
    "餐饮": {"BRAND", "CHANNEL", "SUPPLY_CHAIN", "CUSTOMER_RELATIONSHIP", "SWITCHING_COST", "EFFICIENT_SCALE", "COST_ADVANTAGE"},
    "半导体": {"TECHNOLOGY", "CUSTOMER_RELATIONSHIP", "EFFICIENT_SCALE", "COST_ADVANTAGE", "SUPPLY_CHAIN"},
    "空调": {"BRAND", "CHANNEL", "SUPPLY_CHAIN", "COST_ADVANTAGE", "EFFICIENT_SCALE", "TECHNOLOGY", "CUSTOMER_RELATIONSHIP"},
    "银行": {"CUSTOMER_RELATIONSHIP", "REGULATORY", "CHANNEL", "COST_ADVANTAGE", "DATA_PLATFORM_ECOSYSTEM"},
}
_DEFAULT_DIMENSIONS = {"EFFICIENT_SCALE", "COST_ADVANTAGE", "TECHNOLOGY", "CHANNEL", "CUSTOMER_RELATIONSHIP", "SUPPLY_CHAIN"}
_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("品牌", "BRAND"), ("商标", "BRAND"), ("复购", "SWITCHING_COST"), ("粘性", "SWITCHING_COST"),
    ("转换成本", "SWITCHING_COST"), ("网络效应", "NETWORK_EFFECT"), ("研发", "TECHNOLOGY"),
    ("专利", "TECHNOLOGY"), ("技术", "TECHNOLOGY"), ("认证", "CUSTOMER_RELATIONSHIP"),
    ("客户", "CUSTOMER_RELATIONSHIP"), ("渠道", "CHANNEL"), ("门店", "CHANNEL"), ("服务网络", "CHANNEL"),
    ("售后", "CHANNEL"), ("供应链", "SUPPLY_CHAIN"), ("牌照", "REGULATORY"), ("准入", "REGULATORY"),
    ("市场地位", "EFFICIENT_SCALE"), ("产能", "EFFICIENT_SCALE"), ("规模", "EFFICIENT_SCALE"),
    ("成本", "COST_ADVANTAGE"), ("平台", "DATA_PLATFORM_ECOSYSTEM"), ("数据", "DATA_PLATFORM_ECOSYSTEM"),
)
_COUNTER_WORDS = ("下降", "下滑", "减少", "收缩", "关闭", "压力", "加剧", "恶化", "亏损", "减值", "低于", "下跌")
_CLAIM_WORDS = ("核心竞争力", "竞争优势", "具有", "领先", "优势", "影响力", "壁垒", "能力")
_DISCLOSED_FACT_WORDS = ("拥有", "获得", "建成", "覆盖", "通过", "达到", "位居", "设立", "投入", "服务于", "建立")
_NUMBER = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>亿元|万元|万家|万台|万件|家|项|件|台|套|人|%|倍)")


def _short_text(value: str, maximum: int = 260) -> str:
    return " ".join(value.split())[:maximum].strip()


def _normalized_claim(value: str) -> str:
    return re.sub(r"\s+|[，。；、：:（）()]", "", value).lower()[:360]


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _context(text: str, position: int, length: int) -> str:
    left = max(text.rfind("。", 0, position), text.rfind("；", 0, position), text.rfind("\n", 0, position))
    right_candidates = [item for item in (text.find("。", position + length), text.find("；", position + length), text.find("\n", position + length)) if item >= 0]
    right = min(right_candidates) if right_candidates else min(len(text), position + length + 180)
    # Keep a short but complete sentence separate from a preceding numeric
    # sentence.  Otherwise "门店100家。公司认为品牌…" would incorrectly turn
    # a management claim into a quantified brand fact.
    if right - left < 28 and left < 0 and not right_candidates:
        left, right = max(0, position - 110), min(len(text), position + length + 170)
    return _short_text(text[max(0, left + 1):right + 1])


class MoatEvidenceExtractionService:
    def __init__(
        self, *, evidence_store: MoatEvidenceStore | None = None, disclosure_store: DisclosureMaterialStore | None = None,
        business_store: BusinessResearchStore | None = None, business_profiles: CompanyBusinessProfileService | None = None,
        leader_profile_loader: Callable[[str, str, str | None], dict[str, Any]] | None = None,
    ) -> None:
        self.evidence_store = evidence_store or MoatEvidenceStore()
        self.disclosure_store = disclosure_store or DisclosureMaterialStore(self.evidence_store.db_path)
        self.business_store = business_store or BusinessResearchStore(self.evidence_store.db_path)
        self.business_profiles = business_profiles or CompanyBusinessProfileService()
        self._leader_profile_loader = leader_profile_loader or (lambda market, symbol, as_of: get_leader_quality_profile_service().get_profile(market, symbol, as_of))
        self._owns = {"evidence": evidence_store is None, "disclosure": disclosure_store is None, "business": business_store is None}

    def close(self) -> None:
        if self._owns["evidence"]:
            self.evidence_store.close()
        if self._owns["disclosure"]:
            self.disclosure_store.close()
        if self._owns["business"]:
            self.business_store.close()

    @staticmethod
    def _industry_dimensions(industry_name: str) -> set[str]:
        for token, dimensions in _INDUSTRY_DIMENSIONS.items():
            if token in industry_name:
                return set(dimensions)
        return set(_DEFAULT_DIMENSIONS)

    @staticmethod
    def _dimension_for_gap(text: str) -> str | None:
        for keyword, dimension in _KEYWORDS:
            if keyword in text:
                return dimension
        if "市场份额" in text or "产能" in text:
            return "EFFICIENT_SCALE"
        if "单位成本" in text or "价格" in text:
            return "COST_ADVANTAGE"
        return None

    @staticmethod
    def _evidence_type(excerpt: str, keyword: str) -> tuple[str | None, dict[str, Any]]:
        if any(token in excerpt for token in _COUNTER_WORDS):
            return "COUNTER_EVIDENCE", {}
        numeric = _NUMBER.search(excerpt)
        if numeric:
            return "QUANTIFIED_FACT", {"metric_name": keyword, "value": float(numeric["value"]), "unit": numeric["unit"]}
        if any(token in excerpt for token in _CLAIM_WORDS):
            return "MANAGEMENT_CLAIM", {}
        if any(token in excerpt for token in _DISCLOSED_FACT_WORDS):
            return "DISCLOSED_FACT", {}
        # A bare keyword match is only a retrieval candidate, never evidence.
        return None, {}

    @staticmethod
    def _source_hash(document: dict[str, Any]) -> str:
        return str(document.get("text_sha256") or document.get("pdf_sha256") or "")

    def _save(self, raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        assert raw["evidence_type"] in EVIDENCE_TYPES
        assert raw["moat_dimension"] in DIMENSIONS
        claim = _short_text(str(raw["claim"]), 360)
        raw["claim"] = claim
        raw["normalized_claim"] = _normalized_claim(claim)
        raw["fingerprint"] = _fingerprint(
            raw["stock_code"], raw["moat_dimension"], raw["evidence_type"], str(raw.get("source_document_id") or raw.get("source_type")), raw["normalized_claim"],
        )
        raw.setdefault("summary", claim[:120])
        raw.setdefault("supports_or_challenges", "CHALLENGES" if raw["evidence_type"] == "COUNTER_EVIDENCE" else "SUPPORTS")
        raw.setdefault("source_url", "")
        raw.setdefault("source_quote_ref", "")
        raw.setdefault("source_hash", "")
        raw.setdefault("value", {})
        raw.setdefault("confidence", "LOW")
        raw.setdefault("data_quality", "PARTIAL")
        raw.setdefault("extractor_version", EXTRACTOR_VERSION)
        raw.setdefault("fact_key", f"{raw['moat_dimension']}:{raw.get('metric_name') or raw.get('keyword') or raw['evidence_type']}")
        raw.setdefault("status", "ACTIVE")
        return self.evidence_store.save(raw)

    def _document_evidence(
        self, document: dict[str, Any], *, market: str, symbol: str, research_as_of: str, applicable: set[str],
    ) -> list[dict[str, Any]]:
        path = Path(str(document.get("text_path") or ""))
        if not path.is_file():
            return []
        try:
            pages = path.read_text(encoding="utf-8", errors="replace").split("\f")
        except OSError:
            return []
        extracted: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str]] = set()
        accepted_by_keyword: dict[str, int] = {}
        for page_index, text in enumerate(pages, start=1):
            for keyword, dimension in _KEYWORDS:
                if dimension not in applicable:
                    continue
                if accepted_by_keyword.get(keyword, 0) >= 2:
                    continue
                start = 0
                occurrences = 0
                while occurrences < 3:
                    position = text.find(keyword, start)
                    if position < 0:
                        break
                    start, occurrences = position + len(keyword), occurrences + 1
                    excerpt = _context(text, position, len(keyword))
                    if len(excerpt) < 8 or (page_index, dimension, _normalized_claim(excerpt)) in seen:
                        continue
                    seen.add((page_index, dimension, _normalized_claim(excerpt)))
                    evidence_type, value = self._evidence_type(excerpt, keyword)
                    if evidence_type is None:
                        continue
                    accepted_by_keyword[keyword] = accepted_by_keyword.get(keyword, 0) + 1
                    extracted.append({
                        "market": market, "stock_code": symbol, "research_as_of": research_as_of,
                        "moat_dimension": dimension, "evidence_type": evidence_type, "claim": excerpt,
                        "summary": f"定期报告中与{keyword}相关的{'反证' if evidence_type == 'COUNTER_EVIDENCE' else '披露'}。",
                        "source_type": "CNINFO_PERIODIC_REPORT", "source_document_id": document["id"], "source_material_id": None,
                        "source_url": document.get("source_url") or "", "report_date": document.get("report_period"),
                        "announcement_date": document.get("announcement_date"), "page_number": page_index,
                        "source_quote_ref": f"page:{page_index};keyword:{keyword}", "source_hash": self._source_hash(document),
                        "value": {**value, "period": document.get("report_period")} if value else {},
                        "confidence": "HIGH" if evidence_type == "QUANTIFIED_FACT" else "MEDIUM" if evidence_type in {"DISCLOSED_FACT", "COUNTER_EVIDENCE"} else "LOW",
                        "data_quality": "SOURCE_PAGE_VERIFIED", "metric_name": value.get("metric_name") if value else None,
                        "keyword": keyword,
                        "fact_key": f"{dimension}:{value.get('metric_name') if value else keyword}:{document.get('report_kind') or 'REPORT'}",
                    })
        return extracted

    def _material_evidence(
        self, material: dict[str, Any], *, market: str, symbol: str, research_as_of: str, applicable: set[str]) -> list[dict[str, Any]]:
        mapping = {"CUSTOMER_CONCENTRATION": "CUSTOMER_RELATIONSHIP", "BUSINESS_PRODUCT_STRUCTURE": "EFFICIENT_SCALE"}
        dimension = mapping.get(str(material.get("material_type") or ""))
        if not dimension or dimension not in applicable or str(material.get("status") or "") != "FOUND":
            return []
        result: list[dict[str, Any]] = []
        for excerpt in material.get("excerpts") or []:
            if not isinstance(excerpt, dict) or not str(excerpt.get("text") or "").strip():
                continue
            text, keyword = _short_text(str(excerpt["text"])), str(excerpt.get("keyword") or material["material_type"])
            evidence_type, value = self._evidence_type(text, keyword)
            if evidence_type is None:
                evidence_type = "DISCLOSED_FACT"
            result.append({
                "market": market, "stock_code": symbol, "research_as_of": research_as_of, "moat_dimension": dimension,
                "evidence_type": evidence_type, "claim": text, "summary": "已保存定期报告材料摘录。",
                "source_type": "CNINFO_PERIODIC_REPORT_MATERIAL", "source_document_id": material.get("document_id"),
                "source_material_id": material.get("id"), "source_url": material.get("source_url") or "",
                "report_date": material.get("report_period"), "announcement_date": material.get("announcement_date"),
                "page_number": excerpt.get("page"), "source_quote_ref": f"material:{material.get('material_type')};keyword:{keyword}",
                "source_hash": material.get("text_sha256") or "", "value": {**value, "period": material.get("report_period")} if value else {},
                "confidence": "HIGH" if evidence_type == "QUANTIFIED_FACT" else "MEDIUM", "data_quality": "SOURCE_MATERIAL_VERIFIED",
                "metric_name": value.get("metric_name") if value else None, "keyword": keyword,
                "fact_key": f"{dimension}:{value.get('metric_name') if value else keyword}:{material.get('report_kind') or 'REPORT'}",
            })
        return result

    def _business_evidence(self, snapshot: dict[str, Any] | None, *, market: str, symbol: str, research_as_of: str, applicable: set[str]) -> list[dict[str, Any]]:
        if not snapshot:
            return []
        analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
        claims = analysis.get("claims") if isinstance(analysis.get("claims"), list) else []
        result: list[dict[str, Any]] = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_type, text = str(claim.get("type") or "").upper(), _short_text(str(claim.get("text") or ""))
            citations = claim.get("citations") if isinstance(claim.get("citations"), list) else []
            if not text or claim_type == "UNKNOWN":
                continue
            dimension = next((dimension for keyword, dimension in _KEYWORDS if keyword in text and dimension in applicable), None)
            if not dimension:
                continue
            if claim_type == "FACT" and citations:
                evidence_type, quality = "DISCLOSED_FACT", "BUSINESS_CITATION_COMPLETE"
            elif claim_type == "INFERENCE":
                evidence_type, quality = "INFERENCE", "BUSINESS_INFERENCE"
            else:
                continue
            result.append({
                "market": market, "stock_code": symbol, "research_as_of": research_as_of, "moat_dimension": dimension,
                "evidence_type": evidence_type, "claim": text, "summary": "既有 Business Research 的带引用结论。",
                "source_type": "BUSINESS_RESEARCH", "source_document_id": None, "source_material_id": None, "source_url": "",
                "report_date": None, "announcement_date": str(snapshot.get("data_as_of") or "")[:10] or None, "page_number": None,
                "source_quote_ref": f"business_snapshot:{snapshot.get('id')};citations:{len(citations)}", "source_hash": str(snapshot.get("source_hash") or ""),
                "value": {}, "confidence": str(claim.get("confidence") or "LOW").upper(), "data_quality": quality,
                "keyword": str(claim.get("topic") or "BUSINESS"), "fact_key": f"{dimension}:business:{claim.get('topic') or 'claim'}",
            })
        return result

    def _leader_profile_evidence(self, profile: dict[str, Any], *, market: str, symbol: str, research_as_of: str, applicable: set[str]) -> list[dict[str, Any]]:
        """Convert only factual profile bases into clearly labelled inferences.

        A strong peer percentile is deliberately never mapped to brand or
        technology.  It may be a limited, traceable inference for scale/cost
        only, and always retains the underlying L3 run identity.
        """
        position = profile.get("leader_position") if isinstance(profile.get("leader_position"), dict) else {}
        if position.get("status") != "READY":
            return []
        trace = profile.get("source_traceability") if isinstance(profile.get("source_traceability"), dict) else {}
        run_id = str(trace.get("l3_run_id") or position.get("run_id") or "")
        run_as_of = str(trace.get("l3_run_as_of") or research_as_of)
        category_dimension = {"SCALE": "EFFICIENT_SCALE", "PROFITABILITY": "COST_ADVANTAGE", "CASH_QUALITY": "COST_ADVANTAGE"}
        result: list[dict[str, Any]] = []
        for category in profile.get("peer_advantage_categories") or []:
            if not isinstance(category, dict):
                continue
            dimension = category_dimension.get(str(category.get("dimension") or ""))
            status = str(category.get("status") or "")
            if not dimension or dimension not in applicable or status not in {"STRONG", "ABOVE_AVERAGE", "BELOW_AVERAGE"}:
                continue
            evidence_type = "COUNTER_EVIDENCE" if status == "BELOW_AVERAGE" else "INFERENCE"
            result.append({
                "market": market, "stock_code": symbol, "research_as_of": research_as_of, "moat_dimension": dimension,
                "evidence_type": evidence_type,
                "claim": f"Leader Quality 的{category.get('label') or category.get('dimension')}同行事实状态为 {status}。这仅是竞争优势的待验证事实基础。",
                "summary": "由 L3 同行事实画像形成的受限推断，不确认任何护城河。",
                "source_type": "LEADER_QUALITY_PROFILE", "source_document_id": None, "source_material_id": None,
                "source_url": "", "report_date": None, "announcement_date": run_as_of[:10], "page_number": None,
                "source_quote_ref": f"leader_quality_run:{run_id};category:{category.get('dimension')}",
                "source_hash": f"{profile.get('formula_version')}:{run_id}", "value": {"status": status, "metrics": category.get("metrics") or []},
                "confidence": "LOW", "data_quality": "PROFILE_FACTUAL_BASIS", "keyword": str(category.get("dimension") or ""),
                "fact_key": f"{dimension}:leader_quality:{category.get('dimension')}",
            })
        pricing = profile.get("pricing_power_proxy") if isinstance(profile.get("pricing_power_proxy"), dict) else {}
        pricing_status = str(pricing.get("status") or "")
        if "COST_ADVANTAGE" in applicable and pricing_status in {"STRONG_PROXY", "MODERATE_PROXY", "WEAK_PROXY"}:
            evidence_type = "COUNTER_EVIDENCE" if pricing_status == "WEAK_PROXY" else "INFERENCE"
            result.append({
                "market": market, "stock_code": symbol, "research_as_of": research_as_of, "moat_dimension": "COST_ADVANTAGE",
                "evidence_type": evidence_type,
                "claim": f"利润率表现代理为 {pricing_status}；这不是定价权或成本护城河确认。",
                "summary": "Leader Quality 的定价权代理，仅作待验证事实基础。",
                "source_type": "LEADER_QUALITY_PROFILE", "source_document_id": None, "source_material_id": None,
                "source_url": "", "report_date": None, "announcement_date": run_as_of[:10], "page_number": None,
                "source_quote_ref": f"leader_quality_run:{run_id};pricing_power_proxy", "source_hash": f"{profile.get('formula_version')}:{run_id}",
                "value": {"status": pricing_status, "peer_margin_percentile": pricing.get("peer_margin_percentile")},
                "confidence": "LOW", "data_quality": "PROFILE_PROXY", "keyword": "pricing_power_proxy",
                "fact_key": "COST_ADVANTAGE:pricing_power_proxy",
            })
        for gap in profile.get("moat_data_gaps") or []:
            dimension = self._dimension_for_gap(str(gap))
            if not dimension or dimension not in applicable:
                continue
            result.append({
                "market": market, "stock_code": symbol, "research_as_of": research_as_of, "moat_dimension": dimension,
                "evidence_type": "UNKNOWN", "claim": f"当前资料不足：{gap}。",
                "summary": "Leader Quality Profile 明确的数据缺口。", "source_type": "LEADER_QUALITY_PROFILE",
                "source_document_id": None, "source_material_id": None, "source_url": "", "report_date": None,
                "announcement_date": run_as_of[:10], "page_number": None, "source_quote_ref": f"leader_quality_run:{run_id};data_gap",
                "source_hash": f"{profile.get('formula_version')}:{run_id}", "value": {}, "confidence": "LOW",
                "data_quality": "DATA_GAP", "keyword": str(gap), "fact_key": f"{dimension}:data_gap:{gap}",
            })
        return result

    def extract(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        if normalized_market != "CN":
            raise ValueError("Moat Evidence Extraction V1 当前仅支持 A 股（CN）")
        symbol = normalize_symbol(normalized_market, stock_code)
        target = str(as_of)[:10] if as_of else None
        if target:
            date.fromisoformat(target)
        leader = self._leader_profile_loader(normalized_market, symbol, target)
        research_as_of = str(leader.get("research_as_of") or target or "")[:10]
        if not research_as_of:
            raise ValueError("leader_quality_profile_as_of_unavailable")
        position = leader.get("leader_position") if isinstance(leader.get("leader_position"), dict) else {}
        industry_name = str(((position.get("level3") or {}).get("name") if isinstance(position.get("level3"), dict) else "") or "")
        applicable = self._industry_dimensions(industry_name)
        business_profile = self.business_profiles.profile(symbol)
        if business_profile and target and str(business_profile.get("updated_at") or "")[:10] > target:
            business_profile = None
        documents = self.disclosure_store.list_documents(symbol, as_of=research_as_of)
        materials = self.disclosure_store.list_materials(symbol, as_of=research_as_of)
        business = self.business_store.latest(symbol, as_of=research_as_of)
        candidates: list[dict[str, Any]] = []
        for document in documents:
            candidates.extend(self._document_evidence(document, market=normalized_market, symbol=symbol, research_as_of=research_as_of, applicable=applicable))
        for material in materials:
            candidates.extend(self._material_evidence(material, market=normalized_market, symbol=symbol, research_as_of=research_as_of, applicable=applicable))
        candidates.extend(self._business_evidence(business, market=normalized_market, symbol=symbol, research_as_of=research_as_of, applicable=applicable))
        candidates.extend(self._leader_profile_evidence(leader, market=normalized_market, symbol=symbol, research_as_of=research_as_of, applicable=applicable))
        saved, duplicate = [], 0
        for candidate in candidates:
            row, created = self._save(candidate)
            (saved if created else []).append(row)
            duplicate += int(not created)
        return {
            "company": {"market": normalized_market, "stock_code": symbol, "stock_name": leader.get("company", {}).get("stock_name") or symbol},
            "research_as_of": research_as_of, "industry": {"level3_name": industry_name, "applicable_dimensions": sorted(applicable)},
            "source_status": {"cninfo": "READY" if documents else "NOT_COLLECTED", "business_research": "READY" if business else "NOT_COLLECTED", "business_profile": "READY" if business_profile else "NOT_COLLECTED", "leader_quality": "READY" if position.get("status") == "READY" else "NOT_COLLECTED"},
            "created": len(saved), "duplicates": duplicate, "evidence": saved,
            "extractor_version": EXTRACTOR_VERSION, "disclaimer": "仅保存竞争优势相关证据与反证，不生成最终护城河判断或交易结论。",
        }

    def get_evidence(self, market: str, stock_code: str, *, as_of: str | None = None, dimension: str | None = None, evidence_type: str | None = None, active: bool | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        if normalized_market != "CN":
            raise ValueError("Moat Evidence Extraction V1 当前仅支持 A 股（CN）")
        symbol = normalize_symbol(normalized_market, stock_code)
        if as_of:
            date.fromisoformat(str(as_of)[:10])
        if dimension and dimension.upper() not in DIMENSIONS:
            raise ValueError("invalid moat evidence dimension")
        if evidence_type and evidence_type.upper() not in EVIDENCE_TYPES:
            raise ValueError("invalid moat evidence type")
        items = self.evidence_store.list(normalized_market, symbol, as_of=as_of, dimension=dimension, evidence_type=evidence_type, active=active)
        return {"company": {"market": normalized_market, "stock_code": symbol}, "as_of": str(as_of)[:10] if as_of else None, "dimension": dimension.upper() if dimension else None, "evidence_type": evidence_type.upper() if evidence_type else None, "active": active, "items": items, "total": len(items), "extractor_version": EXTRACTOR_VERSION, "disclaimer": "只读证据层；管理层自述、事实、推断和反证不会被合并为护城河结论。"}


_service: MoatEvidenceExtractionService | None = None


def get_moat_evidence_extraction_service() -> MoatEvidenceExtractionService:
    global _service
    if _service is None:
        _service = MoatEvidenceExtractionService()
    return _service
