"""Build a human-confirmed initial Company Thesis draft from saved research."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.business_research.store import BusinessResearchStore
from src.disclosure_materials.store import DisclosureMaterialStore
from src.financial_analysis.service import FinancialAnalysisService
from src.financial_analysis.store import FinancialAnalysisStore
from src.level3_leaders.business_profiles import CompanyBusinessProfileService
from src.moat_research.service import get_moat_research_service
from src.research_workspace.store import normalize_market, normalize_symbol

if TYPE_CHECKING:
    from src.risk_research.service import RiskResearchService
    from src.value_price_zones import ValuePriceZoneService

from .draft_store import CompanyThesisDraftRepository
from .service import CompanyThesisService


DRAFT_VERSION = "company-thesis-draft-v1.1.0-moat-research"
_VERIFIED_CLAIM_TYPES = {"FACT", "INFERENCE"}
_TRADING_LANGUAGE = re.compile(r"建议买入|建议卖出|买入|卖出|目标价|目标仓位|止损|加仓|减仓")


class CompanyThesisDraftService:
    """Creates editable proposals only; it never automatically creates a Thesis."""

    def __init__(self, *, repository: CompanyThesisDraftRepository | None = None,
                 thesis_service: CompanyThesisService | None = None,
                 financial_store: FinancialAnalysisStore | None = None,
                 business_store: BusinessResearchStore | None = None,
                 disclosure_store: DisclosureMaterialStore | None = None,
                 business_profiles: CompanyBusinessProfileService | None = None,
                 risk_service: "RiskResearchService | None" = None,
                 price_zone_service: "ValuePriceZoneService | None" = None,
                 moat_research_loader: Any | None = None,
                 db_path: Path | None = None) -> None:
        self.repository = repository or CompanyThesisDraftRepository(db_path)
        path = self.repository.db_path
        self.thesis_service = thesis_service or CompanyThesisService(db_path=path)
        self.financial_store = financial_store or FinancialAnalysisStore(path)
        self.business_store = business_store or BusinessResearchStore(path)
        self.disclosure_store = disclosure_store or DisclosureMaterialStore(path)
        self.business_profiles = business_profiles or CompanyBusinessProfileService()
        if risk_service is None:
            from src.risk_research.service import RiskResearchService
            risk_service = RiskResearchService(
                financial_store=self.financial_store, business_store=self.business_store,
                thesis_repository=self.thesis_service.repository, disclosure_store=self.disclosure_store,
            )
        self.risk_service = risk_service
        if price_zone_service is None:
            from src.value_price_zones import get_value_price_zone_service
            price_zone_service = get_value_price_zone_service()
        self.price_zone_service = price_zone_service
        self.moat_research_loader = moat_research_loader or (
            lambda market, symbol, as_of: get_moat_research_service().get_research(market, symbol, as_of)
        )
        self._owns_repository = repository is None
        self._owns_thesis_service = thesis_service is None
        self._owns_financial_store = financial_store is None
        self._owns_business_store = business_store is None
        self._owns_disclosure_store = disclosure_store is None
        self._owns_risk_service = risk_service is None

    def close(self) -> None:
        if self._owns_business_store:
            self.business_store.close()
        if self._owns_risk_service:
            self.risk_service.close()
        if self._owns_disclosure_store:
            self.disclosure_store.close()
        if self._owns_financial_store:
            self.financial_store.close()
        if self._owns_thesis_service:
            self.thesis_service.close()
        if self._owns_repository:
            self.repository.close()

    @staticmethod
    def _key(market: str, stock_code: str) -> tuple[str, str]:
        normalized_market = normalize_market(market)
        return normalized_market, normalize_symbol(normalized_market, stock_code)

    @staticmethod
    def _claims(snapshot: dict[str, Any] | None, *, domain: str) -> list[dict[str, Any]]:
        analysis = (snapshot or {}).get("analysis") if isinstance((snapshot or {}).get("analysis"), dict) else {}
        raw_claims = analysis.get("claims") if isinstance(analysis.get("claims"), list) else []
        valid: list[dict[str, Any]] = []
        for raw in raw_claims:
            if not isinstance(raw, dict):
                continue
            claim_type = str(raw.get("type") or "").upper()
            text = re.sub(r"\s+", " ", str(raw.get("text") or raw.get("statement") or "").strip())
            source_keys = raw.get("source_keys", raw.get("evidence_keys", []))
            keys = list(dict.fromkeys(str(item).strip() for item in source_keys if str(item).strip())) if isinstance(source_keys, list) else []
            if claim_type in _VERIFIED_CLAIM_TYPES and text and keys and not _TRADING_LANGUAGE.search(text):
                claim = {"domain": domain, "type": claim_type, "text": text, "source_keys": keys,
                         "confidence": str(raw.get("confidence") or "LOW").upper()}
                if claim_type == "INFERENCE":
                    claim["factual_basis"] = str(raw.get("factual_basis") or f"基于已保存来源：{', '.join(keys)}。")
                valid.append(claim)
        return valid

    @staticmethod
    def _source_hash(financial: dict[str, Any], business: dict[str, Any], claims: list[dict[str, Any]],
                     *, research_as_of: str, extras: dict[str, Any]) -> str:
        material = {
            "version": DRAFT_VERSION,
            "financial_snapshot": financial.get("id"), "financial_source_hash": financial.get("source_hash"),
            "business_snapshot": business.get("id"), "business_source_hash": business.get("source_hash"),
            "claims": [{key: value for key, value in item.items() if key != "confidence"} for item in claims],
            "research_as_of": research_as_of, "extras": extras,
        }
        return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _deterministic_financial_claims(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Use existing PIT feature facts when an optional Financial LLM run is absent.

        The financial feature engine is deterministic and already persisted.  A
        missing conversational/LLM summary must not force users to run a model
        simply to prepare an initial human-reviewed thesis.
        """
        manifest = FinancialAnalysisService._evidence_manifest(snapshot)
        changes = (snapshot.get("feature") or {}).get("latest_changes") or []
        labels = {
            "revenue": "营业收入", "net_profit": "净利润", "operating_cash_flow": "经营现金流",
            "roe": "净资产收益率", "debt_ratio": "资产负债率",
        }
        result: list[dict[str, Any]] = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            metric = str(change.get("metric") or "")
            report_date = change.get("report_date")
            period = FinancialAnalysisService._manifest_period(report_date)
            key = f"FEATURE_{metric.upper()}_CHANGE_{period}"
            value = change.get("change_percent")
            if metric not in labels or key not in manifest or not isinstance(value, (int, float)):
                continue
            result.append({
                "domain": "FINANCIAL", "type": "FACT",
                "text": f"截至{report_date}，{labels[metric]}同比{'上升' if value >= 0 else '下降'}{abs(float(value)):.2f}%。",
                "source_keys": [key], "confidence": "HIGH",
            })
        return result

    @staticmethod
    def _moat_context_items(moat: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Map the deterministic moat contract into draft-compatible items.

        The result retains all evidence identifiers.  It never tries to make
        an advantage stronger than the already computed Moat Research status.
        """
        research = dict(moat or {})
        research_as_of = str(research.get("research_as_of") or "")[:10] or None
        advantages: list[dict[str, Any]] = []
        source_refs: list[dict[str, Any]] = []
        assumptions: list[dict[str, Any]] = []
        conditions: list[dict[str, Any]] = []
        monitors: list[dict[str, Any]] = []
        for item in research.get("dimensions") or []:
            if not isinstance(item, dict) or item.get("applicability") != "APPLICABLE":
                continue
            dimension, label = str(item.get("dimension") or ""), str(item.get("label") or item.get("dimension") or "竞争优势")
            assessment = str(item.get("status") or "UNKNOWN").upper()
            if assessment not in {"SUPPORTED", "PARTIAL", "UNKNOWN"}:
                assessment = "UNKNOWN"
            evidence_ids = [str(value) for value in item.get("supporting_evidence_ids") or [] if str(value)]
            counter_ids = [str(value) for value in item.get("counter_evidence_ids") or [] if str(value)]
            management_ids = [str(value) for value in item.get("management_claim_ids") or [] if str(value)]
            gaps = [str(value) for value in item.get("data_gaps") or [] if str(value)]
            evidence_source_refs = list(item.get("supporting_source_refs") or [])
            counter_source_refs = list(item.get("counter_source_refs") or [])
            management_source_refs = list(item.get("management_claim_source_refs") or [])
            source_keys = [f"MOAT_RESEARCH:{research_as_of or 'UNKNOWN'}:{dimension}", *evidence_ids, *management_ids]
            balance = str(item.get("evidence_balance") or "NO_SIGNAL")
            if assessment == "SUPPORTED":
                claim_type = "FACT"
                text = f"现有跨期或跨来源正式披露事实支持公司在{label}方面具有相对优势；该判断仍需结合反证持续复核。"
                basis = "Moat Research 已通过正式事实、持续性和行业相关性的严格支持门槛。"
            elif assessment == "PARTIAL":
                claim_type = "INFERENCE"
                management_only = bool(management_ids) and not evidence_ids
                text = (f"公司管理层披露认为{label}具有优势，但当前主要为管理层声明或有限资料，尚不足以确认其为长期竞争壁垒。"
                        if management_only else f"现有资料显示公司可能具备一定{label}优势，但证据仍不完整，尚不足以确认其为长期竞争壁垒。")
                basis = f"Moat Research 状态为 PARTIAL；{item.get('summary') or '当前正式经营验证不足。'}"
            else:
                claim_type = "UNKNOWN"
                missing = "、".join(gaps[:3]) or f"{label}相关的正式经营资料"
                text = f"当前缺少{missing}，暂无法判断{label}是否具有长期持续性。"
                basis = ""
                source_keys = []
            advantage = {
                "type": claim_type, "claim_type": claim_type, "dimension": dimension, "moat_dimension": dimension,
                "assessment": assessment, "text": text, "factual_basis": basis,
                "source_keys": source_keys, "moat_research_as_of": research_as_of,
                "moat_evidence_ids": evidence_ids, "counter_evidence_ids": counter_ids,
                "management_claim_ids": management_ids, "data_gaps": gaps,
                "confidence": str(item.get("confidence") or "LOW"), "evidence_balance": balance,
                "applicability": "APPLICABLE", "source_refs": [{"source": "moat_research", "formula_version": research.get("formula_version"), "research_as_of": research_as_of}, *evidence_source_refs, *management_source_refs, *counter_source_refs],
            }
            advantages.append(advantage)
            source_refs.append({
                "domain": "MOAT_RESEARCH", "type": claim_type, "text": text, "source_keys": source_keys,
                "confidence": advantage["confidence"], "moat_dimension": dimension, "assessment": assessment,
                "moat_evidence_ids": evidence_ids, "counter_evidence_ids": counter_ids, "data_gaps": gaps,
                "data_as_of": research_as_of, "factual_basis": basis, "evidence_source_refs": evidence_source_refs,
                "management_claim_source_refs": management_source_refs, "counter_source_refs": counter_source_refs,
            })
            formal_count = sum(int((item.get("evidence_counts") or {}).get(key) or 0) for key in ("quantified_fact", "disclosed_fact"))
            if formal_count:
                monitors.append({"type": "FACT", "metric": f"{label}相关已披露经营事实", "availability": "AVAILABLE",
                                 "text": f"持续跟踪{label}相关已披露经营事实是否保持或改善。", "source_keys": source_keys,
                                 "confidence": advantage["confidence"]})
            for gap in gaps[:3]:
                monitors.append({"type": "UNKNOWN", "metric": gap, "availability": "DATA_NOT_AVAILABLE",
                                 "text": f"{gap}当前没有稳定可验证数据，不能据此作出优势判断。", "source_keys": [], "confidence": "LOW"})
            if balance in {"MIXED", "CHALLENGED"}:
                counter_keys = [f"MOAT_RESEARCH:{research_as_of or 'UNKNOWN'}:{dimension}", *counter_ids]
                condition = f"如果{label}相关的反向经营表现持续出现或无法改善，需要重新评估该项竞争优势。"
                conditions.append({"type": "INFERENCE", "condition": condition, "status": "ACTIVE", "source_keys": counter_keys,
                                   "factual_basis": f"Moat Research 的证据平衡为 {balance}，存在可追溯反证：{', '.join(counter_ids) or '已记录反向资料'}。"})
                assumptions.append({"type": "INFERENCE", "text": f"{label}相关的已披露经营表现需维持，当前反证不能继续扩大。", "source_keys": counter_keys,
                                    "factual_basis": f"Moat Research 的{label}维度存在反证。", "confidence": "MEDIUM"})
        if not advantages:
            advantages.append({"type": "UNKNOWN", "claim_type": "UNKNOWN", "dimension": "MOAT_RESEARCH", "moat_dimension": "MOAT_RESEARCH",
                               "assessment": "UNKNOWN", "text": "当前没有足够的竞争优势研究资料，不能确认长期竞争壁垒。", "factual_basis": "", "source_keys": [],
                               "moat_research_as_of": research_as_of, "moat_evidence_ids": [], "counter_evidence_ids": [], "management_claim_ids": [],
                               "data_gaps": list(research.get("moat_data_gaps") or []), "confidence": "LOW", "evidence_balance": "NO_SIGNAL", "applicability": "UNKNOWN_APPLICABILITY", "source_refs": []})
        return advantages, source_refs, assumptions, conditions, monitors

    @staticmethod
    def _draft_payload(market: str, stock_code: str, financial: dict[str, Any], business: dict[str, Any],
                       financial_claims: list[dict[str, Any]], business_claims: list[dict[str, Any]], *,
                       research_as_of: str, profile: dict[str, Any] | None, disclosures: list[dict[str, Any]],
                       risk: dict[str, Any] | None, zones: dict[str, Any] | None,
                       industry_context: dict[str, Any] | None = None,
                       moat_research: dict[str, Any] | None = None) -> dict[str, Any]:
        company_name = str(financial.get("stock_name") or business.get("company_name") or stock_code)
        selected_financial, selected_business = financial_claims[:2], business_claims[:2]
        claims = selected_financial + selected_business
        source_data_as_of = research_as_of
        sentences = [item["text"].rstrip("。；;") for item in claims]
        main_business = str((profile or {}).get("main_business") or business.get("main_business") or "现有资料不足以完整说明主营业务").strip()
        valuation = dict((zones or {}).get("valuation") or {})
        valuation_text = "当前估值资料不足，不能据此说明价值基础。"
        if valuation.get("status") and valuation.get("status") != "INSUFFICIENT_DATA":
            valuation_text = f"当前估值状态为{valuation.get('status')}，因此值得继续核对价值基础；这不是交易结论。"
        core_thesis = (
            f"草案结论：{company_name}主要从{main_business}中获得收入。现有财务与经营资料显示，" + "；".join(sentences) +
            f"。{valuation_text} 本草案必须经人工确认后才会成为正式公司核心逻辑。"
        )
        moat_advantages, moat_refs, moat_assumptions, moat_conditions, moat_monitors = CompanyThesisDraftService._moat_context_items(moat_research)
        conditions = [
            {"type": "INFERENCE", "condition": "后续同口径财务资料显示盈利或经营现金流连续恶化，并与当前草案的增长/经营假设相冲突。", "status": "ACTIVE", "source_keys": [item["source_keys"][0] for item in selected_financial if item.get("source_keys")], "factual_basis": "当前财务研究中的收入、利润与现金流事实。"},
            {"type": "INFERENCE", "condition": f"后续可追溯经营资料显示{main_business}相关核心业务持续收缩或经营变化被证实为不利。", "status": "ACTIVE", "source_keys": [item["source_keys"][0] for item in selected_business if item.get("source_keys")], "factual_basis": "当前经营研究中的主营业务与经营变化事实。"},
        ]
        source_snapshots = [
            {"domain": "FINANCIAL", "snapshot_id": financial.get("id"), "data_as_of": financial.get("as_of"),
             "source_hash": financial.get("source_hash")},
            {"domain": "BUSINESS", "snapshot_id": business.get("id"), "data_as_of": business.get("data_as_of"),
             "source_hash": business.get("source_hash")},
            {"domain": "MOAT_RESEARCH", "snapshot_id": None, "data_as_of": (moat_research or {}).get("research_as_of"),
             "source_hash": (moat_research or {}).get("formula_version")},
        ]
        profile_ref = {"domain": "BUSINESS_PROFILE", "type": "FACT", "text": f"主营业务资料：{main_business}。",
                       "source_keys": ["BUSINESS_PROFILE_MAIN"], "confidence": "MEDIUM"} if main_business else None
        valuation_ref = {"domain": "VALUATION", "type": "FACT", "text": valuation_text,
                         "source_keys": ["VALUE_PRICE_ZONE"], "confidence": "MEDIUM"} if zones else None
        disclosure_refs = [
            {"domain": "DISCLOSURE", "type": "FACT", "text": f"已采集定期报告材料：{row.get('material_type')}。",
             "source_keys": [f"CNINFO:{row.get('announcement_id') or row.get('id')}:{row.get('material_type')}"], "confidence": "MEDIUM"}
            for row in disclosures if str(row.get("status") or "") == "FOUND"
        ][:3]
        source_refs = claims + ([profile_ref] if profile_ref else []) + ([valuation_ref] if valuation_ref else []) + disclosure_refs + moat_refs
        industry_context = dict(industry_context or {})
        industry_name = str(industry_context.get("industry_name") or "当前细分行业")
        industry_ref = {
            "domain": "INDUSTRY", "type": "FACT",
            "text": f"公司当前归属{industry_name}，行业内排名{industry_context.get('leader_rank') or '待核验'}，龙头评分{industry_context.get('leader_score') or '待核验'}。",
            "source_keys": ["L3_LEADER_CONTEXT"], "confidence": "MEDIUM",
        } if industry_context else None
        risk_items = [
            {"type": "FACT", "text": str(item.get("text") or ""), "source_keys": list(item.get("source_keys") or []),
             "confidence": str(item.get("severity") or "LOW")}
            for item in (risk or {}).get("risks") or [] if str(item.get("severity") or "") in {"HIGH", "MEDIUM"}
        ][:3]
        moat_challenges = [
            {"type": "INFERENCE", "text": str(condition["condition"]), "source_keys": list(condition["source_keys"]),
             "factual_basis": str(condition["factual_basis"]), "confidence": "MEDIUM", "research_domain": "MOAT_RESEARCH"}
            for condition in moat_conditions[:3]
        ]
        disclosure_status = "READY" if disclosures else "PARTIAL"
        profile_status = str((profile or {}).get("data_status") or "MISSING")
        data_quality = {
            "financial": str(financial.get("feature_status") or "MISSING"),
            "business_research": str(business.get("analysis_status") or "MISSING"),
            "business_profile": profile_status,
            "disclosure": disclosure_status,
            "moat_research": str((moat_research or {}).get("status") or "UNKNOWN"),
            "gaps": [item for item in (["BUSINESS_PROFILE_PARTIAL" if profile_status != "REAL" else None, "DISCLOSURE_NOT_COLLECTED" if not disclosures else None, *list((moat_research or {}).get("moat_data_gaps") or [])]) if item],
        }
        monitor_names = ("收入", "净利润", "经营现金流", "ROE", "毛利率", "应收账款", "存货", "债务")
        profile_text = " ".join(str((profile or {}).get(key) or "") for key in ("main_business", "main_products", "business_scope", "company_description"))
        if "银行" in profile_text:
            monitor_names = ("利息净收入", "净利润", "资本充足率", "不良贷款率", "拨备覆盖率", "流动性")
        all_source_refs = source_refs + ([industry_ref] if industry_ref else [])
        return {
            "market": market, "stock_code": stock_code, "company_name": company_name,
            "title": f"{company_name}初步核心逻辑", "core_thesis": core_thesis,
            "status": "FORMING", "confidence": "MEDIUM" if len(claims) >= 3 else "LOW",
            "invalid_conditions": conditions, "source_data_as_of": source_data_as_of, "research_as_of": research_as_of,
            "source_hash": CompanyThesisDraftService._source_hash(financial, business, all_source_refs, research_as_of=research_as_of, extras={"profile": profile_status, "disclosures": len(disclosures), "valuation": valuation.get("status"), "industry": industry_context, "moat_research": {"as_of": (moat_research or {}).get("research_as_of"), "formula_version": (moat_research or {}).get("formula_version"), "status": (moat_research or {}).get("status")}}),
            "source_snapshots": source_snapshots, "source_refs": all_source_refs, "thesis_summary": core_thesis,
            "core_drivers": selected_financial + selected_business,
            "competitive_advantages": moat_advantages,
            "key_assumptions": [{"type": "INFERENCE", "text": "盈利、现金流和主营业务的已验证趋势不会发生持续反转。", "source_keys": [item["source_keys"][0] for item in claims if item.get("source_keys")][:3], "factual_basis": "当前已保存的财务与经营事实来源。", "confidence": "MEDIUM"}, *moat_assumptions],
            "key_metrics_to_monitor": [
                {"type": "FACT", "metric": name, "text": f"持续跟踪{name}的同口径变化。", "source_keys": ["FINANCIAL_FEATURE"], "confidence": "HIGH"}
                for name in monitor_names
            ] + moat_monitors + ([{"type": "UNKNOWN", "metric": "行业经营指标", "text": "未来建议补充同店增长、门店数量等餐饮经营指标；当前系统没有可验证数值。", "source_keys": [], "confidence": "LOW", "availability": "DATA_NOT_AVAILABLE"}] if "餐饮" in main_business else []),
            "main_risks": risk_items + moat_challenges or [{"type": "UNKNOWN", "text": "当前确定性风险结果未形成可确认的重点风险；这不等于低风险。", "source_keys": [], "confidence": "LOW"}],
            "data_quality": data_quality, "workflow_status": "READY_FOR_REVIEW" if profile_status == "REAL" else "DRAFT",
            "invalid_conditions": conditions + moat_conditions,
            "metadata": {"draft_version": DRAFT_VERSION, "notice": "需人工确认；不会自动建立正式公司核心逻辑。", "generation": "SOURCE_BOUND_DRAFT", "industry_context": industry_context, "moat_research": {"research_as_of": (moat_research or {}).get("research_as_of"), "formula_version": (moat_research or {}).get("formula_version"), "status": (moat_research or {}).get("status"), "source_status": (moat_research or {}).get("source_status")}},
        }

    def get_latest(self, market: str, stock_code: str) -> dict[str, Any] | None:
        market, stock_code = self._key(market, stock_code)
        return self.repository.latest(market, stock_code)

    def get(self, market: str, stock_code: str, draft_id: str) -> dict[str, Any] | None:
        market, stock_code = self._key(market, stock_code)
        draft = self.repository.get(str(draft_id or "").strip())
        if not draft or (draft["market"], draft["stock_code"]) != (market, stock_code):
            return None
        return draft

    def generate(self, market: str, stock_code: str, *, research_as_of: str | None = None,
                 industry_context: dict[str, Any] | None = None) -> dict[str, Any]:
        market, stock_code = self._key(market, stock_code)
        current = self.thesis_service.get_current_thesis(market, stock_code)
        if current:
            return {"status": "THESIS_EXISTS", "draft": None, "thesis": current}
        financial = self.financial_store.latest(stock_code, as_of=research_as_of)
        business = self.business_store.latest(stock_code, as_of=research_as_of)
        target = str(research_as_of or (financial or {}).get("as_of") or (business or {}).get("data_as_of") or "")[:10]
        profile = self.business_profiles.profile(stock_code)
        # A persisted completed Business Research snapshot itself may carry the
        # last source-backed profile when a terminal cache is temporarily not
        # mounted (for example in an audit worker).  It is usable but marked
        # PARTIAL, never upgraded into a complete direct profile.
        if isinstance((business or {}).get("snapshot"), dict):
            saved_profile = dict((business or {}).get("snapshot", {}).get("profile") or {})
            direct_profile_is_future = bool(
                profile and target and str(profile.get("updated_at") or "")[:10] > target
            )
            if saved_profile and (not profile or direct_profile_is_future):
                profile = {**saved_profile, "data_status": saved_profile.get("data_status") or "PARTIAL", "updated_at": saved_profile.get("updated_at") or target}
        if (not financial or str(financial.get("feature_status") or "") != "READY" or not business
                or str(business.get("analysis_status") or "") != "COMPLETED" or not profile
                or str(profile.get("data_status") or "") == "MISSING"):
            return {"status": "DATA_INSUFFICIENT", "draft": None, "message": "需要已有的财务、经营研究和可用主营业务资料后，才能生成核心逻辑草案。"}
        if target and str(profile.get("updated_at") or "")[:10] > target:
            return {"status": "DATA_INSUFFICIENT", "draft": None, "message": "主营业务资料晚于研究基准日，不能用于本次草案。"}
        financial_claims = self._claims(financial, domain="FINANCIAL")
        if not financial_claims:
            financial_claims = self._deterministic_financial_claims(financial)
        business_claims = self._claims(business, domain="BUSINESS")
        if not financial_claims or not business_claims:
            return {"status": "DATA_INSUFFICIENT", "draft": None, "message": "已完成研究中缺少带来源的财务或经营观点，暂不生成草案。"}
        disclosures = self.disclosure_store.list_materials(stock_code, as_of=target or None)
        try:
            risk = self.risk_service.get_risk_research(market, stock_code, as_of=target or None)
        except Exception:
            risk = None
        try:
            zones = self.price_zone_service.get_price_zones(market, stock_code, as_of=target or None)
        except Exception:
            zones = None
        try:
            # Read-only query with exactly the same point-in-time cutoff as the
            # financial/business draft context. Failure or absent evidence does
            # not block a draft; it becomes explicit UNKNOWN content instead.
            moat_research = self.moat_research_loader(market, stock_code, target or None)
        except Exception:
            moat_research = {"research_as_of": target or None, "status": "UNKNOWN", "dimensions": [], "moat_data_gaps": ["竞争优势研究暂不可用"], "formula_version": "unavailable"}
        draft, created = self.repository.save(self._draft_payload(
            market, stock_code, financial, business, financial_claims, business_claims,
            research_as_of=target, profile=profile, disclosures=disclosures, risk=risk, zones=zones,
            industry_context=industry_context, moat_research=moat_research,
        ))
        return {"status": "CREATED" if created else "EXISTING", "draft": draft}

    @staticmethod
    def validate_for_provisional(draft: dict[str, Any], *, research_as_of: str,
                                 business_profile: dict[str, Any] | None = None) -> tuple[bool, str | None]:
        """Validate a saved draft before an agent-owned Thesis may be created."""
        for field in ("thesis_summary", "core_drivers", "key_assumptions", "invalid_conditions",
                      "key_metrics_to_monitor", "main_risks", "competitive_advantages"):
            value = draft.get(field)
            if not value or (isinstance(value, str) and not value.strip()):
                return False, f"REQUIRED_FIELD_MISSING:{field}"
        cutoff = str(research_as_of)[:10]
        if str(draft.get("research_as_of") or draft.get("source_data_as_of") or "")[:10] > cutoff:
            return False, "FUTURE_RESEARCH_DATA"
        dates = [str(item.get("data_as_of") or "")[:10] for item in (draft.get("source_snapshots") or []) if isinstance(item, dict)]
        if any(value > cutoff for value in dates if value):
            return False, "FUTURE_SOURCE_DATA"
        moat_metadata = ((draft.get("metadata") or {}).get("moat_research") or {}) if isinstance(draft.get("metadata"), dict) else {}
        for item in draft.get("competitive_advantages") or []:
            if not isinstance(item, dict) or not item.get("moat_dimension"):
                continue
            assessment = str(item.get("assessment") or "").upper()
            claim_type = str(item.get("type") or item.get("claim_type") or "").upper()
            applicability = str(item.get("applicability") or "")
            moat_as_of = str(item.get("moat_research_as_of") or moat_metadata.get("research_as_of") or "")[:10]
            if str(item.get("moat_dimension")) == "MOAT_RESEARCH":
                if assessment != "UNKNOWN" or claim_type != "UNKNOWN":
                    return False, "MOAT_UNKNOWN_PROMOTED"
                continue
            if assessment not in {"SUPPORTED", "PARTIAL", "UNKNOWN"} or applicability != "APPLICABLE":
                return False, "INVALID_MOAT_DIMENSION"
            if moat_as_of and moat_as_of > cutoff:
                return False, "FUTURE_MOAT_RESEARCH_DATA"
            if assessment == "SUPPORTED":
                if claim_type not in {"FACT", "INFERENCE"} or not (item.get("moat_evidence_ids") or []):
                    return False, "MOAT_SUPPORTED_WITHOUT_FACT_EVIDENCE"
            elif assessment == "PARTIAL":
                if claim_type != "INFERENCE":
                    return False, "MOAT_PARTIAL_PROMOTED"
                if not str(item.get("factual_basis") or "").strip():
                    return False, "MOAT_PARTIAL_WITHOUT_BOUNDARY"
            elif claim_type != "UNKNOWN":
                return False, "MOAT_UNKNOWN_PROMOTED"
            if item.get("management_claim_ids") and not item.get("moat_evidence_ids") and claim_type == "FACT":
                return False, "MOAT_MANAGEMENT_CLAIM_PROMOTED"
        for group in ("core_drivers", "competitive_advantages", "key_assumptions", "key_metrics_to_monitor", "main_risks", "source_refs"):
            for item in draft.get(group) or []:
                if not isinstance(item, dict):
                    return False, f"INVALID_ITEM:{group}"
                claim_type = str(item.get("type") or "").upper()
                text = str(item.get("text") or item.get("condition") or "").strip()
                sources = item.get("source_keys") or []
                if claim_type not in {"FACT", "INFERENCE", "UNKNOWN"} or not text:
                    return False, f"INVALID_CLAIM:{group}"
                if claim_type in {"FACT", "INFERENCE"} and not sources:
                    return False, f"UNSOURCED_{claim_type}:{group}"
                if claim_type == "INFERENCE" and not str(item.get("factual_basis") or "").strip():
                    return False, f"INFERENCE_WITHOUT_BASIS:{group}"
        # Invalid conditions are an inference group but use ``condition`` as
        # their display field, so validate them independently.
        for item in draft.get("invalid_conditions") or []:
            if not isinstance(item, dict) or str(item.get("type") or "").upper() != "INFERENCE":
                return False, "INVALID_CONDITION"
            if (not str(item.get("condition") or "").strip() or not (item.get("source_keys") or [])
                    or not str(item.get("factual_basis") or "").strip()):
                return False, "INFERENCE_WITHOUT_BASIS:invalid_conditions"
        profile_text = " ".join(str((business_profile or {}).get(key) or "") for key in ("main_business", "main_products", "business_scope", "company_description"))
        # Check generated prose only.  A harmless historical source key (for
        # example an old disclosure classifier key) must not itself trigger a
        # template mismatch.
        display_items = [str(draft.get("thesis_summary") or "")]
        for group in ("core_drivers", "competitive_advantages", "key_assumptions", "key_metrics_to_monitor", "main_risks", "invalid_conditions"):
            for item in draft.get(group) or []:
                if isinstance(item, dict):
                    display_items.append(str(item.get("text") or item.get("condition") or ""))
        body = " ".join(display_items)
        if "餐饮" in profile_text and any(token in body for token in ("PPP", "产能利用率")):
            return False, "INDUSTRY_TEMPLATE_MISMATCH"
        if "银行" in profile_text and any(token in body for token in ("库存", "毛利率", "产能利用率")):
            return False, "INDUSTRY_TEMPLATE_MISMATCH"
        return True, None

    def confirm(self, draft_id: str, *, title: str, core_thesis: str, status: str,
                confidence: str, invalid_conditions: list[dict[str, Any]] | None = None,
                supporting_conditions: list[dict[str, Any]] | None = None,
                key_metrics_to_monitor: list[Any] | None = None) -> dict[str, Any]:
        draft = self.repository.get(draft_id)
        if not draft:
            raise KeyError("thesis draft not found")
        supporting = supporting_conditions if supporting_conditions is not None else list(draft.get("key_assumptions") or [])
        metrics = key_metrics_to_monitor if key_metrics_to_monitor is not None else list(draft.get("key_metrics_to_monitor") or [])
        invalids = invalid_conditions if invalid_conditions is not None else draft["invalid_conditions"]
        current = self.thesis_service.get_current_thesis(draft["market"], draft["stock_code"])
        if draft["draft_status"] == "CONFIRMED" and current and current.get("authority_status") == "AI_PROVISIONAL":
            thesis = self.thesis_service.create_new_version(
                market=draft["market"], stock_code=draft["stock_code"], title=title, core_thesis=core_thesis,
                status=status, confidence=confidence, invalid_conditions=invalids,
                supporting_conditions=supporting, key_metrics_to_monitor=metrics,
                change_reason="人工确认当前公司核心逻辑", updated_by="HUMAN", source_data_as_of=draft.get("research_as_of"),
                authority_status="HUMAN_CONFIRMED", source_draft_id=draft_id,
                history_metadata={"authority_from": "AI_PROVISIONAL", "authority_to": "HUMAN_CONFIRMED", "source_draft_id": draft_id},
            )
            return {"status": "APPROVED", "draft": draft, "thesis": thesis}
        if draft["draft_status"] != "DRAFT":
            raise ValueError("only an active draft or AI provisional draft can be confirmed")
        thesis = self.thesis_service.create_initial_thesis(
            market=draft["market"], stock_code=draft["stock_code"], title=title,
            core_thesis=core_thesis, status=status, confidence=confidence,
            invalid_conditions=invalids,
            supporting_conditions=supporting, key_metrics_to_monitor=metrics,
            created_by="HUMAN", source_data_as_of=draft.get("source_data_as_of"),
            authority_status="HUMAN_CONFIRMED", source_draft_id=draft_id,
        )
        confirmed = self.repository.confirm(draft_id, thesis_id=thesis["thesis_id"], actor="HUMAN")
        return {"status": "APPROVED", "draft": confirmed, "thesis": thesis}

    def promote_to_provisional(self, market: str, stock_code: str, *, research_as_of: str | None = None,
                               industry_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Explicitly promote one validated draft; never overwrites a Thesis."""
        result = self.generate(market, stock_code, research_as_of=research_as_of, industry_context=industry_context)
        if result.get("status") == "THESIS_EXISTS":
            return result
        draft = result.get("draft")
        if not draft:
            return result
        profile = self.business_profiles.profile(stock_code)
        valid, reason = self.validate_for_provisional(
            draft, research_as_of=str(research_as_of or draft.get("research_as_of") or ""), business_profile=profile,
        )
        if not valid:
            return {"status": "VALIDATION_FAILED", "draft": draft, "message": reason}
        thesis = self.thesis_service.create_initial_thesis(
            market=draft["market"], stock_code=draft["stock_code"], title=draft["title"],
            core_thesis=draft["core_thesis"], status="FORMING", confidence=draft["confidence"],
            invalid_conditions=draft["invalid_conditions"],
            supporting_conditions=list(draft.get("key_assumptions") or []),
            key_metrics_to_monitor=list(draft.get("key_metrics_to_monitor") or []),
            created_by="AGENT",
            source_data_as_of=draft.get("research_as_of"), authority_status="AI_PROVISIONAL",
            source_draft_id=draft["draft_id"],
        )
        self.repository.confirm(draft["draft_id"], thesis_id=thesis["thesis_id"], actor="AGENT")
        return {"status": "AI_PROVISIONAL_CREATED", "draft": self.repository.get(draft["draft_id"]), "thesis": thesis}

    def reject(self, draft_id: str, *, reason: str = "人工未采纳") -> dict[str, Any]:
        draft = self.repository.reject(draft_id, reason=str(reason or "人工未采纳").strip())
        return {"status": "REJECTED", "draft": draft}


_service: CompanyThesisDraftService | None = None


def get_company_thesis_draft_service() -> CompanyThesisDraftService:
    global _service
    if _service is None:
        _service = CompanyThesisDraftService()
    return _service
