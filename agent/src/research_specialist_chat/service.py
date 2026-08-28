"""Model-backed, locally grounded chat for dedicated Feishu specialists.

The service never starts a new research task and never mutates research data.
It reads the same deterministic snapshots used by the Value Line web pages,
then lets the configured specialist model explain only those facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

from src.research_tasks.service import ProviderModelRuntime
from src.research_tasks.store import ResearchTaskStore


ROLE_SPECS: dict[str, dict[str, str]] = {
    "risk_researcher": {
        "title": "风险研究员",
        "model_role": "risk",
        "scope": "公司财务风险、经营风险、低估陷阱风险和公司核心逻辑挑战",
    },
    "valuation_researcher": {
        "title": "估值研究员",
        "model_role": "valuation",
        "scope": "合理价值区间、历史估值位置、价格支撑与入场/退出研究状态",
    },
    "macro_policy_researcher": {
        "title": "宏观政策研究员",
        "model_role": "macro_policy",
        "scope": "增长、通胀、流动性、信用、金融条件和政策传导",
    },
}

_SELF_INTRO = re.compile(r"你是谁|什么模型|哪个模型|有什么功能|能做什么|如何使用|使用说明|介绍(?:一下)?你自己", re.I)
_DATE_PATTERN = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_TRADING_LANGUAGE = re.compile(r"买入|卖出|推荐|仓位|止盈|止损|加仓|减仓")

# Bumped when the specialist instruction/answer contract changes so cached
# answers from an older prompt are invalidated (research-cache plan §6).
SPECIALIST_CHAT_PROMPT_VERSION = "research-specialist-chat-v2"


# Every entry is an already-persisted, read-only capability.  The registry is
# deliberately declarative: the risk researcher may select and explain these
# sources, but it cannot trigger preparation, disclosure downloads, Thesis
# changes, or any market action.
RISK_AGENT_CAPABILITY_REGISTRY: dict[str, dict[str, str]] = {
    "risk_research": {"label": "风险规则结果", "boundary": "RiskResearchService，只读"},
    "financial_research": {"label": "财务研究与预测", "boundary": "已保存财务快照，只读"},
    "financial_history": {"label": "PIT财务历史", "boundary": "已公告可见的财务历史，只读"},
    "business_profile": {"label": "公司主营业务", "boundary": "通达信本地缓存，只读"},
    "business_research": {"label": "经营研究", "boundary": "已保存经营研究，只读"},
    "company_overview": {"label": "公司研究总览", "boundary": "已保存研究投影，只读"},
    "valuation_research": {"label": "估值与价格位置", "boundary": "已保存估值/价格结果，只读"},
    "thesis_research": {"label": "公司核心逻辑与研究证据", "boundary": "Thesis/Evidence/Review，只读"},
    "disclosure_materials": {"label": "已保存公告材料", "boundary": "CNINFO本地材料，只读；不下载"},
    "industry_context": {"label": "三级行业与龙头身份", "boundary": "已物化L3龙头池，只读"},
}

_RISK_HISTORY_FIELDS = (
    "report_date", "announcement_date", "revenue", "net_profit", "operating_cash_flow",
    "accounts_receivable", "inventory", "cash_and_equivalents", "current_assets",
    "current_liabilities", "non_current_liabilities", "interest_bearing_debt_ratio",
    "debt_ratio", "capex", "gross_margin", "roe",
)

_RISK_HISTORY_FIELD_LABELS = {
    "report_date": "报告期", "announcement_date": "公告日", "revenue": "营业收入",
    "net_profit": "净利润", "operating_cash_flow": "经营现金流",
    "accounts_receivable": "应收账款", "inventory": "存货",
    "cash_and_equivalents": "货币资金", "current_assets": "流动资产",
    "current_liabilities": "流动负债", "non_current_liabilities": "非流动负债",
    "interest_bearing_debt_ratio": "有息负债率", "debt_ratio": "资产负债率",
    "capex": "资本开支", "gross_margin": "毛利率", "roe": "ROE",
}


@dataclass(frozen=True)
class SpecialistBrief:
    agent: str
    title: str
    answer: str
    research_as_of: str | None
    model_name: str
    stock_code: str | None = None
    stock_name: str | None = None
    status: str = "READY"
    source_keys: tuple[str, ...] = ()
    data_gaps: tuple[str, ...] = ()
    sources: dict[str, Any] = field(default_factory=dict)


def _compact_risk(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "stock_code", "as_of", "status", "overall_risk", "summary", "value_trap_risk",
            "is_current_l3_leader", "valuation_status", "data_quality", "risks", "formula_version",
        )
    }


def _compact_valuation(
    zones: dict[str, Any],
    historical: dict[str, Any],
    entry: dict[str, Any],
    exit_result: dict[str, Any],
) -> dict[str, Any]:
    history_keys = (
        "historical_valuation_status", "pe_percentile", "pb_percentile",
        "dividend_yield_percentile", "coverage", "as_of",
    )
    return {
        "price_zones": {
            key: zones.get(key)
            for key in (
                "stock_code", "as_of", "current_price", "valuation", "support_zones",
                "resistance_zones", "confluence_zones", "thesis_status", "data_quality", "plain_summary",
            )
        },
        "historical_valuation": {key: historical.get(key) for key in history_keys},
        "entry_research": {
            key: entry.get(key)
            for key in ("entry_level", "entry_score", "reason_codes", "plain_explanation", "status", "as_of")
        },
        "exit_research": {
            key: exit_result.get(key)
            for key in ("exit_level", "exit_score", "reason_codes", "plain_explanation", "status", "as_of")
        },
    }


def _compact_macro(value: dict[str, Any]) -> dict[str, Any]:
    details = dict(value.get("details") or {})
    return {
        key: value.get(key)
        for key in (
            "as_of", "regime", "score", "coverage", "confidence", "status", "axes", "states",
            "missing_fields", "sources", "series_coverage", "release_verified_coverage", "formula_version",
        )
    } | {
        "details": {
            key: {
                field: item.get(field)
                for field in ("latest", "observation_date", "release_date", "source", "release_status")
            }
            for key, item in details.items()
            if isinstance(item, dict)
        }
    }


class ResearchSpecialistChatService:
    """Answer dedicated specialist questions from local research snapshots."""

    def __init__(
        self,
        *,
        store: ResearchTaskStore | None = None,
        runtime: Any | None = None,
        risk_service: Any | None = None,
        price_zone_service: Any | None = None,
        historical_valuation_service: Any | None = None,
        entry_service: Any | None = None,
        exit_service: Any | None = None,
        macro_service: Any | None = None,
        tdx_service: Any | None = None,
        financial_store: Any | None = None,
        financial_history_service: Any | None = None,
        business_profile_service: Any | None = None,
        business_store: Any | None = None,
        overview_service: Any | None = None,
        thesis_repository: Any | None = None,
        evidence_repository: Any | None = None,
        review_repository: Any | None = None,
        disclosure_store: Any | None = None,
        leader_pool_reader: Callable[[str | None], dict[str, Any] | None] | None = None,
        security_resolver: Callable[[str, str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.store = store or ResearchTaskStore()
        self.runtime = runtime or ProviderModelRuntime()
        if risk_service is None:
            from src.risk_research import get_risk_research_service
            risk_service = get_risk_research_service()
        if price_zone_service is None:
            from src.value_price_zones import get_value_price_zone_service
            price_zone_service = get_value_price_zone_service()
        if historical_valuation_service is None:
            from src.historical_valuation import get_historical_valuation_service
            historical_valuation_service = get_historical_valuation_service()
        if entry_service is None:
            from src.entry_research import get_entry_research_service
            entry_service = get_entry_research_service()
        if exit_service is None:
            from src.exit_research import get_exit_research_service
            exit_service = get_exit_research_service()
        if macro_service is None:
            from src.strategy_engines.macro_data import MacroDataService
            macro_service = MacroDataService()
        if tdx_service is None:
            from src.tdx_data import get_tdx_service
            tdx_service = get_tdx_service()
        if financial_store is None:
            financial_store = getattr(risk_service, "financial_store", None)
            if financial_store is None:
                from src.financial_analysis.store import FinancialAnalysisStore
                financial_store = FinancialAnalysisStore()
        if financial_history_service is None:
            from src.tdx_data.financial_history import FinancialHistoryService
            financial_history_service = FinancialHistoryService(store=getattr(tdx_service, "store", None))
        if business_profile_service is None:
            from src.level3_leaders.business_profiles import CompanyBusinessProfileService
            business_profile_service = CompanyBusinessProfileService(tdx_service)
        if business_store is None:
            business_store = getattr(risk_service, "business_store", None)
            if business_store is None:
                from src.business_research.store import BusinessResearchStore
                business_store = BusinessResearchStore(getattr(financial_store, "db_path", None))
        if overview_service is None:
            from src.company_research.overview_service import get_company_research_overview_service
            overview_service = get_company_research_overview_service()
        if thesis_repository is None:
            thesis_repository = getattr(risk_service, "thesis_repository", None)
            if thesis_repository is None:
                from src.company_thesis.store import CompanyThesisRepository
                thesis_repository = CompanyThesisRepository(getattr(financial_store, "db_path", None))
        if evidence_repository is None:
            evidence_repository = getattr(risk_service, "evidence_repository", None)
            if evidence_repository is None:
                from src.company_thesis.evidence_store import CompanyThesisEvidenceRepository
                evidence_repository = CompanyThesisEvidenceRepository(getattr(financial_store, "db_path", None))
        if review_repository is None:
            review_repository = getattr(risk_service, "review_repository", None)
            if review_repository is None:
                from src.company_thesis.review_store import CompanyThesisReviewRepository
                review_repository = CompanyThesisReviewRepository(getattr(financial_store, "db_path", None))
        if disclosure_store is None:
            disclosure_store = getattr(risk_service, "disclosure_store", None)
            if disclosure_store is None:
                from src.disclosure_materials.store import DisclosureMaterialStore
                disclosure_store = DisclosureMaterialStore(getattr(financial_store, "db_path", None))
        self.risk_service = risk_service
        self.price_zone_service = price_zone_service
        self.historical_valuation_service = historical_valuation_service
        self.entry_service = entry_service
        self.exit_service = exit_service
        self.macro_service = macro_service
        self.tdx_service = tdx_service
        self.financial_store = financial_store
        self.financial_history_service = financial_history_service
        self.business_profile_service = business_profile_service
        self.business_store = business_store
        self.overview_service = overview_service
        self.thesis_repository = thesis_repository
        self.evidence_repository = evidence_repository
        self.review_repository = review_repository
        self.disclosure_store = disclosure_store
        self.leader_pool_reader = leader_pool_reader or self._read_leader_pool
        self.security_resolver = security_resolver or self._resolve_security

    @staticmethod
    def _read_leader_pool(as_of: str | None) -> dict[str, Any] | None:
        from src.level3_leaders import get_level3_leader_service

        service = get_level3_leader_service()
        pools = service.store.list_pools(limit=200)
        if as_of:
            pools = [row for row in pools if str(row.get("as_of") or "")[:10] <= as_of]
        return service.get_pool(str(pools[0]["id"]), include_inactive=True) if pools else None

    @staticmethod
    def _resolve_security(question: str, entity: str = "") -> dict[str, Any] | None:
        from src.financial_analysis.service import FinancialAnalysisService
        return FinancialAnalysisService._resolve_cached_security(question, entity)

    @staticmethod
    def _history_security(history: Iterable[dict[str, Any]] | None) -> dict[str, Any] | None:
        for item in reversed(list(history or [])):
            code = str(item.get("stock_code") or "").strip()
            if code:
                return {"code": code, "name": str(item.get("stock_name") or code)}
        return None

    def _company(self, question: str, history: Iterable[dict[str, Any]] | None) -> dict[str, Any] | None:
        return self.security_resolver(question, "") or self._history_security(history)

    def _as_of(self, question: str) -> tuple[str | None, str | None]:
        match = _DATE_PATTERN.search(question)
        if match:
            try:
                return date.fromisoformat(match.group(1)).isoformat(), None
            except ValueError:
                return None, "指定的数据日期无效。"
        ready, reason, snapshot = self.tdx_service.latest_qualified_close_snapshot()
        market_date = str((snapshot or {}).get("market_date") or "")[:10]
        if not ready or not market_date:
            return None, reason or "最新合格收盘数据尚未就绪。"
        return market_date, None

    @staticmethod
    def _available_as_of(
        row: dict[str, Any] | None, as_of: str | None, *, data_key: str = "data_as_of", require_created: bool = True,
    ) -> bool:
        """Reject values sourced after the shared cutoff, and state created after it when requested."""
        if not row:
            return False
        if not as_of:
            return True
        target = str(as_of)[:10]
        created = str(row.get("created_at") or "")[:10]
        source = str(row.get(data_key) or row.get("source_data_as_of") or "")[:10]
        return bool((not require_created or (created and created <= target)) and (not source or source <= target))

    @staticmethod
    def _compact_financial_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if not snapshot:
            return {"status": "MISSING", "message": "当前系统没有可用的已保存财务研究快照。"}
        analysis = dict(snapshot.get("analysis") or {})
        feature = dict(snapshot.get("feature") or {})
        return {
            "status": "READY" if str(snapshot.get("feature_status") or "") == "READY" else "PARTIAL",
            "source_as_of": snapshot.get("as_of"), "created_at": snapshot.get("created_at"),
            "feature_status": snapshot.get("feature_status"), "forecast_status": snapshot.get("forecast_status"),
            "analysis_status": snapshot.get("analysis_status"), "data_gaps": snapshot.get("data_gaps") or [],
            "latest_changes": feature.get("latest_changes") or [],
            "forecast": dict(snapshot.get("forecast") or {}).get("scenarios") or {},
            "analysis_summary": analysis.get("executive_summary"),
            "analysis_claims": list(analysis.get("claims") or [])[:12],
            "key_metrics_to_monitor": list(analysis.get("key_metrics_to_monitor") or [])[:6],
        }

    @staticmethod
    def _compact_financial_history(value: dict[str, Any] | None) -> dict[str, Any]:
        raw_rows = (value or {}).get("items") if isinstance(value, dict) and "items" in value else (value or {}).get("history")
        rows = [dict(row) for row in raw_rows or [] if isinstance(row, dict)]
        if not rows:
            return {
                "status": "MISSING",
                "period_count": 0,
                "source_as_of": (value or {}).get("as_of"),
                "fields": list(_RISK_HISTORY_FIELDS),
                "periods": [],
                "field_coverage": {"covered_fields": [], "company_missing_fields": list(_RISK_HISTORY_FIELDS)},
                "message": "当前系统没有可用的PIT财务历史。",
            }
        covered = [name for name in _RISK_HISTORY_FIELDS if any(row.get(name) is not None for row in rows)]
        company_missing = [name for name in _RISK_HISTORY_FIELDS if name not in covered]
        message = None
        if company_missing:
            labels = "、".join(_RISK_HISTORY_FIELD_LABELS.get(name, name) for name in company_missing)
            message = (
                f"以下科目的字段管道系统已接入，但该公司的数据源暂未提供数值：{labels}。"
                "这是该公司数据缺失，不是系统未接入该指标，相关规则只能标记资料不足。"
            )
        return {
            "status": "READY",
            "period_count": len(rows),
            "source_as_of": (value or {}).get("as_of"),
            "fields": list(_RISK_HISTORY_FIELDS),
            "periods": [{key: row.get(key) for key in _RISK_HISTORY_FIELDS} for row in rows[-30:]],
            "field_coverage": {"covered_fields": covered, "company_missing_fields": company_missing},
            "message": message,
        }

    @staticmethod
    def _compact_business_research(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if not snapshot:
            return {"status": "MISSING", "message": "当前系统尚未生成公司经营研究。"}
        source, analysis = dict(snapshot.get("snapshot") or {}), dict(snapshot.get("analysis") or {})
        return {
            "status": str(snapshot.get("analysis_status") or "PARTIAL"),
            "data_as_of": snapshot.get("data_as_of"), "created_at": snapshot.get("created_at"),
            "main_business": source.get("main_business"), "products": source.get("products") or [],
            "business_model": source.get("business_model"), "data_quality": source.get("data_quality") or {},
            "claims": list(analysis.get("claims") or [])[:12],
        }

    @staticmethod
    def _compact_disclosures(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "status": "NOT_COLLECTED",
                "message": "当前系统尚未采集该公司的官方公告材料；这不表示公司没有披露。",
                "material_count": 0, "materials": [],
            }
        materials = []
        for row in rows[:20]:
            materials.append({
                "material_type": row.get("material_type"), "status": row.get("status"),
                "announcement_date": row.get("announcement_date"), "report_period": row.get("report_period"),
                "title": row.get("title"), "excerpts": list(row.get("excerpts") or [])[:4],
            })
        return {"status": "READY", "material_count": len(rows), "materials": materials}

    def _risk_agent_context(self, stock_code: str, as_of: str) -> dict[str, Any]:
        """Read all registered risk capabilities without creating or refreshing research."""
        gaps: list[str] = []

        def read(name: str, callback: Callable[[], Any], fallback: dict[str, Any]) -> Any:
            try:
                return callback()
            except Exception:
                gaps.append(name)
                return fallback

        risk = read("risk_research", lambda: dict(self.risk_service.get_risk_research("CN", stock_code, as_of=as_of) or {}), {})
        snapshot = read("financial_research", lambda: self.financial_store.latest(stock_code, as_of=as_of), None)
        if snapshot and not self._available_as_of(snapshot, as_of, data_key="as_of", require_created=False):
            snapshot = None
        financial_history = read("financial_history", lambda: dict(self.financial_history_service.query(stock_code, as_of=as_of) or {}), {})
        profile = read("business_profile", lambda: self.business_profile_service.profile(stock_code), None)
        if profile and str(profile.get("updated_at") or "")[:10] > as_of:
            profile = None
        business = read("business_research", lambda: self.business_store.latest(stock_code, as_of=as_of), None)
        if business and not self._available_as_of(business, as_of, require_created=False):
            business = None
        overview = read("company_overview", lambda: self.overview_service.get_overview("CN", stock_code, as_of=as_of), {})
        zones = read("valuation_research", lambda: dict(self.price_zone_service.get_price_zones("CN", stock_code, as_of=as_of) or {}), {})
        historical = read("valuation_research", lambda: dict(self.historical_valuation_service.get_valuation_history("CN", stock_code, as_of=as_of) or {}), {})
        entry = read("valuation_research", lambda: dict(self.entry_service.get_entry_research("CN", stock_code, as_of=as_of) or {}), {})
        exit_result = read("valuation_research", lambda: dict(self.exit_service.get_exit_research("CN", stock_code, as_of=as_of) or {}), {})

        def thesis_context() -> dict[str, Any]:
            versions = list(self.thesis_repository.list_thesis_versions("CN", stock_code) or [])
            thesis = next((row for row in versions if self._available_as_of(row, as_of, data_key="source_data_as_of")), None)
            if not thesis:
                return {"status": "MISSING", "message": "当前尚未建立公司核心逻辑。", "thesis": None, "evidence": [], "review": None}
            evidence = [row for row in self.evidence_repository.list_active_evidence_for_thesis(str(thesis["thesis_id"]))
                        if self._available_as_of(row, as_of)]
            reviews = [row for row in self.review_repository.list_reviews_for_company("CN", stock_code)
                       if self._available_as_of(row, as_of)]
            return {
                "status": "READY", "thesis": {key: thesis.get(key) for key in (
                    "thesis_id", "title", "core_thesis", "status", "confidence", "version", "updated_at", "invalid_conditions",
                )},
                "evidence": [{key: row.get(key) for key in (
                    "evidence_id", "effect", "confidence", "claim", "source_type", "source_ref", "data_as_of", "created_at",
                )} for row in evidence[:16]],
                "review": ({key: reviews[0].get(key) for key in (
                    "review_status", "is_stale", "support_count", "challenge_count", "review_reason", "created_at",
                )} if reviews else None),
            }

        thesis = read("thesis_research", thesis_context, {"status": "UNAVAILABLE", "message": "公司核心逻辑读取失败。"})
        disclosures = read(
            "disclosure_materials",
            lambda: self._compact_disclosures([
                row for row in self.disclosure_store.list_materials(stock_code, as_of=as_of)
                if self._available_as_of(row, as_of, data_key="announcement_date", require_created=False)
            ]),
            {"status": "UNAVAILABLE", "message": "已保存公告材料读取失败。", "materials": []},
        )

        def industry_context() -> dict[str, Any]:
            pool = self.leader_pool_reader(as_of) or {}
            member = next((row for row in pool.get("members") or []
                           if str(row.get("stock_code") or "").upper() == stock_code.upper()
                           and str(row.get("lifecycle_status") or "") in {"ACTIVE", "NEW", "REENTERED"}), None)
            if not member:
                return {"status": "NOT_CURRENT_LEADER", "pool_as_of": pool.get("as_of"), "message": "当前不在有效三级行业龙头池中。"}
            return {"status": "READY", "pool_as_of": pool.get("as_of"), "industry": {
                key: member.get(key) for key in ("level1_name", "level2_name", "level3_code", "level3_name", "leader_rank", "leader_score", "lifecycle_status")
            }}

        industry = read("industry_context", industry_context, {"status": "UNAVAILABLE", "message": "三级行业龙头身份读取失败。"})
        if gaps:
            gaps = list(dict.fromkeys(gaps))
        return {
            "research_as_of": as_of,
            "risk_research": _compact_risk(risk),
            "financial_research": self._compact_financial_snapshot(snapshot),
            "financial_history": self._compact_financial_history(financial_history),
            "business_profile": profile or {"status": "MISSING", "message": "当前通达信本地缓存没有可用主营业务资料。"},
            "business_research": self._compact_business_research(business),
            "company_overview": overview,
            "valuation_research": _compact_valuation(zones, historical, entry, exit_result),
            "thesis_research": thesis,
            "disclosure_materials": disclosures,
            "industry_context": industry,
            "orchestration_data_gaps": gaps,
        }

    def _config(self, agent: str) -> dict[str, Any]:
        return self.store.get_runtime_config(ROLE_SPECS[agent]["model_role"])

    def _intro(self, agent: str) -> SpecialistBrief:
        spec = ROLE_SPECS[agent]
        config = self._config(agent)
        model = str(config.get("model") or "尚未配置")
        answer = (
            f"我是{spec['title']}，负责{spec['scope']}。\n\n"
            f"当前角色配置模型：{model}。回答时先读取系统本地已有研究数据，再由角色模型做通俗解释；"
            "数据不足会明确说明，不会补造事实，也不会给出买卖或仓位指令。"
        )
        if agent != "macro_policy_researcher":
            answer += "你可以直接输入 A 股公司名称或六位股票代码继续提问。"
        else:
            answer += "你可以询问当前宏观环境、流动性、通胀、信用或政策传导。"
        return SpecialistBrief(agent, spec["title"], answer, None, model, source_keys=("ROLE_CONFIG",))

    @staticmethod
    def _target_schema(agent: str) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"{agent}_feishu_chat",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "maxLength": 1200},
                        "source_keys": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                        "data_gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                    },
                    "required": ["answer", "source_keys", "data_gaps"],
                    "additionalProperties": False,
                },
            },
        }

    def _invoke(self, agent: str, question: str, history: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
        spec = ROLE_SPECS[agent]
        config = self._config(agent)
        if not config.get("enabled") or not config.get("model"):
            raise RuntimeError(f"{spec['title']}模型未启用")
        instruction = (
            f"你是{spec['title']}。职责：{spec['scope']}。"
            "只能根据 payload.context 中的本地研究数据回答，不得补造事实。"
            "先给结论，再解释依据和数据缺口；使用通俗中文，控制在300至700字。"
            "禁止给出买入、卖出、仓位、止盈止损等交易指令。"
            "source_keys 只能填写 context 中实际存在的顶层键。"
            # The endpoint may ignore response_format, so the JSON contract
            # must also live in the instruction or the whole answer is lost
            # to the parser.
            "输出必须是且仅是一个 JSON 对象，不要 markdown 代码块，"
            "不要 JSON 以外的任何文字，格式为："
            '{"answer":"<完整回答正文>","source_keys":["<context顶层键>"],"data_gaps":["<数据缺口>"]}。'
        )
        if agent == "risk_researcher":
            instruction += (
                "本次是多工具、只读风险研究：risk_research 的 UNKNOWN 只表示该规则维度资料不足，"
                "绝不能因此停止；必须继续使用财务、主营业务、经营研究、估值、核心逻辑、公告材料和行业上下文。"
                "所有数字必须逐字来自上下文；不得修改任何风险等级、估值状态或底层数值。"
                "严格区分：已确认风险（有规则、事实或来源），需要重点观察（异常迹象但尚不足以确认严重），"
                "当前无法判断（系统资料不足）。第三类不得计入已确认风险。"
                "系统未采集公告材料不等于公司没有披露；缺数据不等于风险；深度低估不等于价值陷阱。"
                "不得写无法排除收入虚增、财务造假、市场因某风险低估公司等无来源判断。"
                "必须先看 business_profile 与 industry_context：不得把PPP回款、门店翻台率、制造业库存等行业模板"
                "套用到没有对应主营业务和来源的公司。行业特定指标缺失时，只能说明当前系统暂无该指标。"
                "financial_history.field_coverage.company_missing_fields 列出的是该公司数据源缺失的科目"
                "（系统字段已接入）：表述时必须写'该公司数据缺失'，不得写成'系统未接入'；两者性质不同。"
                "默认使用以下中文结构，不要展示内部枚举：\n"
                "【风险结论】\n【已确认风险】\n【需要重点观察】\n【经营与行业风险】\n"
                "【估值与低估陷阱】\n【当前无法判断】\n【接下来最值得核验】\n【数据截止日期与来源】。"
                "如果某章节没有事实，也要用一句自然中文说明资料边界。"
            )
        payload = {"question": question, "history": history[-6:], "context": context}
        kwargs = {
            "role": spec["model_role"],
            "phase": "FEISHU_CHAT",
            "model": str(config["model"]),
            "instruction": instruction,
            "payload": payload,
            "target_schema": self._target_schema(agent),
        }
        if config.get("base_url") and hasattr(self.runtime, "invoke_with_connection"):
            return dict(self.runtime.invoke_with_connection(
                **kwargs,
                base_url=str(config["base_url"]),
                api_key=str(config.get("api_key") or ""),
            ))
        return dict(self.runtime.invoke(**kwargs, provider=str(config.get("provider") or "openai")))

    @staticmethod
    def _fallback(agent: str, context: dict[str, Any]) -> str:
        if agent == "risk_researcher":
            risk = dict(context.get("risk_research") or {})
            return str(risk.get("summary") or "当前本地风险资料不足，暂时无法形成可靠判断。")
        if agent == "valuation_researcher":
            zones = dict((context.get("valuation_research") or {}).get("price_zones") or {})
            return str(zones.get("plain_summary") or "当前本地估值资料不足，暂时无法形成可靠判断。")
        macro = dict(context.get("macro_snapshot") or {})
        if not macro:
            return "当前尚未保存可用的宏观研究快照。"
        axes = dict(macro.get("axes") or {})
        axis_text = "、".join(f"{key} {value}" for key, value in axes.items() if value is not None)
        return f"当前宏观状态为 {macro.get('regime') or '资料不足'}，覆盖率为 {macro.get('coverage') or 0}。{axis_text}"

    def handle_question(
        self,
        *,
        agent: str,
        question: str,
        history: Iterable[dict[str, Any]] | None = None,
    ) -> SpecialistBrief:
        if agent not in ROLE_SPECS:
            raise ValueError(f"unsupported specialist agent: {agent}")
        if _SELF_INTRO.search(question.strip()):
            return self._intro(agent)
        spec = ROLE_SPECS[agent]
        config = self._config(agent)
        model_name = str(config.get("model") or "")
        history_rows = list(history or [])
        stock_code: str | None = None
        stock_name: str | None = None
        if agent == "macro_policy_researcher":
            snapshot = dict(self.macro_service.get() or {})
            as_of = str(snapshot.get("as_of") or "")[:10] or None
            context = {"macro_snapshot": _compact_macro(snapshot)} if snapshot else {}
            if not context:
                return SpecialistBrief(
                    agent, spec["title"], "当前尚未保存可用的宏观研究快照。", None, model_name,
                    status="UNKNOWN", data_gaps=("MACRO_SNAPSHOT",),
                )
        else:
            company = self._company(question, history_rows)
            if not company:
                return SpecialistBrief(
                    agent, spec["title"], "未能识别出具体公司，请补充 A 股公司名称或六位股票代码。",
                    None, model_name, status="UNKNOWN", data_gaps=("SECURITY",),
                )
            stock_code = str(company.get("code") or company.get("stock_code") or "").upper()
            stock_name = str(company.get("name") or company.get("stock_name") or stock_code)
            as_of, error = self._as_of(question)
            if error or not as_of:
                return SpecialistBrief(
                    agent, spec["title"], error or "研究日期不可用。", as_of, model_name,
                    stock_code, stock_name, status="UNAVAILABLE", data_gaps=("MARKET_CLOSE",),
                )
            if agent == "risk_researcher":
                context = self._risk_agent_context(stock_code, as_of)
            else:
                zones = dict(self.price_zone_service.get_price_zones("CN", stock_code, as_of=as_of) or {})
                historical = dict(self.historical_valuation_service.get_valuation_history("CN", stock_code, as_of=as_of) or {})
                entry = dict(self.entry_service.get_entry_research("CN", stock_code, as_of=as_of) or {})
                exit_result = dict(self.exit_service.get_exit_research("CN", stock_code, as_of=as_of) or {})
                context = {"valuation_research": _compact_valuation(zones, historical, entry, exit_result)}
        try:
            output = self._invoke(agent, question, history_rows, context)
            answer = str(output.get("answer") or "").strip()
            source_keys = tuple(str(item) for item in output.get("source_keys") or () if str(item) in context)
            data_gaps = tuple(str(item) for item in output.get("data_gaps") or ())
            if not answer or _TRADING_LANGUAGE.search(answer):
                raise ValueError("specialist answer failed safety validation")
            status = "READY" if not data_gaps else "PARTIAL"
        except Exception:
            answer = self._fallback(agent, context)
            source_keys = tuple(context)
            data_gaps = ("MODEL_EXPLANATION_UNAVAILABLE",)
            status = "PARTIAL"
        return SpecialistBrief(
            agent, spec["title"], answer, as_of, model_name, stock_code, stock_name,
            status=status, source_keys=source_keys, data_gaps=data_gaps, sources=context,
        )


_service: ResearchSpecialistChatService | None = None


def get_research_specialist_chat_service() -> ResearchSpecialistChatService:
    global _service
    if _service is None:
        _service = ResearchSpecialistChatService()
    return _service
