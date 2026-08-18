"""Application service for deterministic finance plus one bounded analyst role."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Callable, Protocol

from src.research_tasks.providers import safe_provider_catalog
from src.research_tasks.service import ProviderModelRuntime
from src.research_tasks.store import ResearchTaskStore
from src.providers.chat import ChatLLM
from src.strategy_engines.common.provenance import stable_fingerprint
from src.tdx_data.financial_history import FinancialHistoryService

from .engine import FINANCIAL_FEATURE_VERSION, FORECAST_VERSION, FinancialFeatureEngine, FinancialForecastEngine
from .store import FinancialAnalysisStore

ANALYSIS_FIELDS = {
    "stock_code", "stock_name", "executive_summary", "historical_performance",
    "latest_changes", "financial_strengths", "financial_risks", "forecast_analysis",
    "key_metrics_to_monitor", "confidence", "data_gaps", "claims",
}
PROHIBITED_ACTIONS = re.compile(r"建议买入|建议卖出|买入|卖出|目标价|目标仓位|止损|加仓|减仓")
FinancialProgress = Callable[[str, str, dict[str, Any]], None]
CAPABILITY_QUESTION = re.compile(
    r"(?:还能|还可以|目前|现在)?分析(?:哪些|什么|哪方面|哪些方面|什么方面)|"
    r"(?:目前|现在)?支持(?:哪些|什么)(?:能力|功能|分析)?|"
    r"有哪些(?:能力|功能|分析)|(?:能做|可以做)(?:哪些|什么)|能力范围|功能范围",
)
FOLLOW_UP_QUESTION = re.compile(
    r"^\s*(?:那|那么|再看|继续|这个|该公司|它|上述|前面|刚才|进一步|另外|还有呢|然后)",
)
GENERAL_METHOD_QUESTION = re.compile(
    r"(?:如何|怎么|怎样|怎么看|如何判断|怎么判断|如何分析|分析方法|是什么意思|怎么理解|为什么|"
    r"应该关注什么|看哪些指标|有哪些指标|你是谁|你好|谢谢)",
)
LEADER_DATA_QUESTION = re.compile(
    r"(?:龙头池|候选龙头|三级行业|细分赛道|赛道排名|行业排名|行业龙头|哪些龙头|哪些公司)",
)
UNAVAILABLE_METRICS: dict[str, tuple[str, ...]] = {
    "流动比率": ("流动比率",),
    "速动比率": ("速动比率",),
    "利息保障倍数": ("利息保障倍数",),
    "借款期限结构": ("短期借款", "长期借款", "借款结构"),
    "营运资本周转": ("应收账款周转", "存货周转", "应付账款周转", "营运资本周转"),
    "非经常性损益": ("非经常性损益", "扣非"),
    "期间费用率": ("销售费用率", "管理费用率", "研发费用率"),
    "分产品/分地区毛利": ("分产品毛利", "分地区毛利", "产品毛利率", "地区毛利率"),
    "产能利用率": ("产能利用率",),
    "市场份额": ("市场份额", "市占率"),
    "行业价格变量": ("木浆", "纸价", "商品价格变量"),
    "历史估值": ("历史PE", "历史 PE", "历史PB", "历史 PB", "历史股息率"),
    "同行自动比较": ("同行比较", "同行业公司对比", "可比公司"),
    "估值敏感性": ("估值敏感性", "DCF敏感性", "DCF 敏感性"),
}
CAPABILITY_RESPONSE = (
    "当前财报 Agent 已支持：财务趋势、盈利质量、现金流、资产负债、资本开支、"
    "三情景未来三年营收/利润推演、当前 PE/PB/股息率/市值快照、数据质量检查、证据追踪和财报问答。\n\n"
    "目前暂未完整接入：营运资本周转、非经常性损益、分产品/分地区毛利、"
    "产能利用率、市场份额、行业价格变量、同行估值比较和估值敏感性分析。\n\n"
    "公司级分析请进入公司研究页面选择具体股票，或在公司详情页使用“完整财务分析”。"
)


def _progress(
    callback: FinancialProgress | None,
    stage: str,
    message: str,
    **details: Any,
) -> None:
    if callback is not None:
        callback(stage, message, details)


def _requested_unavailable_metrics(question: str) -> list[str]:
    return [
        label
        for label, aliases in UNAVAILABLE_METRICS.items()
        if any(alias.lower() in question.lower() for alias in aliases)
    ]


def classify_financial_question(question: str) -> str:
    """Route before loading any heavyweight company or leader-pool data."""
    text = question.strip()
    if CAPABILITY_QUESTION.search(text):
        return "capability"
    if _requested_unavailable_metrics(text):
        return "data_boundary"
    if LEADER_DATA_QUESTION.search(text):
        return "leader_pool"
    if re.search(r"(?<!\d)\d{6}(?:\.(?:SH|SZ|BJ))?(?!\d)", text, re.IGNORECASE):
        return "company_lookup"
    if FOLLOW_UP_QUESTION.search(text):
        return "company_lookup"
    if GENERAL_METHOD_QUESTION.search(text):
        return "general_method"
    return "ambiguous"


class FinancialRuntime(Protocol):
    def invoke(self, *, role: str, phase: str, provider: str, model: str,
               instruction: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def _numeric_tokens(value: Any) -> set[str]:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return set(re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text))


class FinancialAnalysisService:
    def __init__(self, *, store: FinancialAnalysisStore | None = None,
                 history: FinancialHistoryService | None = None,
                 config_store: ResearchTaskStore | None = None,
                 runtime: FinancialRuntime | None = None,
                 feature_engine: FinancialFeatureEngine | None = None,
                 forecast_engine: FinancialForecastEngine | None = None) -> None:
        self.store = store or FinancialAnalysisStore()
        self.history = history or FinancialHistoryService()
        self.config_store = config_store or ResearchTaskStore(self.store.db_path)
        self.runtime = runtime or ProviderModelRuntime()
        self.feature_engine = feature_engine or FinancialFeatureEngine()
        self.forecast_engine = forecast_engine or FinancialForecastEngine()

    def close(self) -> None:
        self.config_store.close()
        self.store.close()

    def _identity(self, stock_code: str, as_of: str | None) -> dict[str, Any]:
        leader = self.store.latest_leader(stock_code, as_of)
        if not leader:
            return {
                "stock_code": stock_code.upper(), "stock_name": stock_code.upper(),
                "level1_code": None, "level1_name": None, "level2_code": None,
                "level2_name": None, "level3_code": None, "level3_name": None,
                "leader_rank": None, "leader_score": None, "leader_formula_version": None,
                "leader_as_of": as_of, "metric_applicability_notes": [],
            }
        raw_features = dict(leader.get("raw_features") or {})
        # These are the valuation facts already used by the Level-3 leader
        # score.  Keep their source date with the financial snapshot: a
        # financial discussion may explain current PE/PB, but must never turn
        # that one observation into a fabricated valuation history or target.
        market_valuation = {
            "as_of": leader.get("as_of"),
            "pe": raw_features.get("pe"),
            "pb": raw_features.get("pb"),
            "dividend_yield": raw_features.get("dividend_yield"),
            "market_cap": raw_features.get("market_cap"),
            "source": "TongDaXin leader-score valuation snapshot",
            "limitations": [
                "仅为当前快照，不代表历史估值分位。",
                "未接入同行可比估值、DCF 敏感性或目标价格。",
            ],
        }
        return {
            key: leader.get(key) for key in (
                "stock_code", "stock_name", "level1_code", "level1_name", "level2_code", "level2_name",
                "level3_code", "level3_name", "leader_rank", "leader_score", "leader_formula_version",
                "metric_applicability_notes",
            )
        } | {"leader_as_of": leader.get("as_of"), "market_valuation": market_valuation}

    @staticmethod
    def _financial_sector(identity: dict[str, Any]) -> bool:
        if "FINANCIAL_SECTOR_METRIC_CAUTION" in (identity.get("metric_applicability_notes") or []):
            return True
        text = " ".join(str(identity.get(key) or "") for key in ("level1_name", "level2_name", "level3_name"))
        return any(token in text for token in ("银行", "保险", "证券"))

    def _agent_config(self) -> tuple[dict[str, Any], bool]:
        runtime_config = getattr(self.config_store, "get_runtime_config", self.config_store.get_config)
        config = runtime_config("financial_analyst")
        provider = next((row for row in safe_provider_catalog(self.config_store.list_configs())
                         if row["provider"] == config["provider"]), None)
        direct_ready = bool(config.get("base_url") and config["model"])
        ready = bool(config["enabled"] and config["model"]
                     and (direct_ready or (provider and provider["configured"])))
        return config, ready

    def prepare(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        symbol = stock_code.upper()
        identity = self._identity(symbol, as_of)
        cutoff = as_of or str(identity.get("leader_as_of") or date.today().isoformat())
        package = self.history.query(symbol, as_of=cutoff)
        rows = list(package.get("items") or [])
        financial_sector = self._financial_sector(identity)
        feature = self.feature_engine.build(
            stock_code=symbol, stock_name=str(identity.get("stock_name") or symbol), as_of=cutoff,
            rows=rows, financial_sector=financial_sector,
        )
        forecast = self.forecast_engine.build(feature, financial_sector=financial_sector)
        config, configured = self._agent_config()
        data_gaps = list(feature.get("data_quality", {}).get("missing_fields") or [])
        if not rows:
            data_gaps.append("financial_history")
        source_hash = stable_fingerprint({
            "stock_code": symbol, "as_of": cutoff, "identity": identity,
            "history": rows, "feature_version": FINANCIAL_FEATURE_VERSION,
            "forecast_version": FORECAST_VERSION,
            "flow_aggregation": "sum-four-tdx-single-periods-v1",
        })
        snapshot, created = self.store.save_python_snapshot({
            "stock_code": symbol, "stock_name": str(identity.get("stock_name") or symbol),
            "as_of": cutoff, "historical_cutoff": cutoff,
            "financial_feature_version": FINANCIAL_FEATURE_VERSION, "forecast_version": FORECAST_VERSION,
            "feature_status": feature["status"], "forecast_status": forecast["status"],
            "analysis_status": "NOT_RUN" if configured else "CONFIGURATION_REQUIRED",
            "agent_provider": config["provider"], "agent_model": config["model"],
            "identity": identity, "history": feature.get("historical_periods") or [],
            "feature": feature, "forecast": forecast, "data_gaps": sorted(set(data_gaps)),
            "source_hash": source_hash,
        })
        return {**snapshot, "idempotent_reuse": not created}

    def get(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        if as_of is None and (latest := self.store.latest(stock_code)):
            return {**latest, "idempotent_reuse": True}
        return self.prepare(stock_code, as_of=as_of)

    def _refresh_history(self, stock_code: str) -> str | None:
        try:
            self.history.collect_incremental([stock_code.upper()])
            return None
        except Exception as exc:  # Python analysis must remain available from cached PIT data.
            return f"refresh:{type(exc).__name__}:{exc}"

    @staticmethod
    def _evidence(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        evidence = [{
            "key": f"financial:{row.get('report_date')}:{row.get('announcement_date')}",
            "type": "FACT", "report_date": row.get("report_date"),
            "announcement_date": row.get("announcement_date"), "source": row.get("source"),
        } for row in snapshot.get("history") or []]
        for scenario, result in (snapshot.get("forecast", {}).get("scenarios") or {}).items():
            for row in result.get("forecast") or []:
                evidence.append({"key": f"forecast:{scenario}:{row.get('year')}", "type": "FORECAST"})
        return evidence

    @staticmethod
    def _validate_analysis(output: dict[str, Any], payload: dict[str, Any]) -> None:
        missing = sorted(ANALYSIS_FIELDS - set(output))
        if missing:
            raise ValueError(f"analysis schema missing: {','.join(missing)}")
        if output.get("stock_code") != payload["company_identity"]["stock_code"]:
            raise ValueError("analysis stock_code mismatch")
        if output.get("confidence") not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError("invalid confidence")
        serialized = json.dumps(output, ensure_ascii=False)
        if PROHIBITED_ACTIONS.search(serialized):
            raise ValueError("analysis contains prohibited trading action")
        evidence_keys = {item["key"] for item in payload["evidence"]}
        claims = output.get("claims")
        if not isinstance(claims, list):
            raise ValueError("claims must be a list")
        for claim in claims:
            if claim.get("type") not in {"FACT", "INFERENCE", "FORECAST", "UNKNOWN"}:
                raise ValueError("invalid claim type")
            references = set(claim.get("evidence_keys") or [])
            if claim.get("type") in {"FACT", "FORECAST"} and not references:
                raise ValueError("FACT/FORECAST requires evidence_keys")
            if references - evidence_keys:
                raise ValueError("claim references unknown evidence")
        allowed_numbers = _numeric_tokens(payload) | {"1", "2", "3", "5"}
        invented = _numeric_tokens(output) - allowed_numbers
        if invented:
            raise ValueError(f"analysis contains numbers absent from deterministic inputs: {sorted(invented)}")

    @staticmethod
    def _parse_analysis_json(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        if not text.startswith("{"):
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("analysis fallback response must be a JSON object")
        return parsed

    def _retry_analysis_without_schema(self, *, config: dict[str, Any], instruction: str,
                                       payload: dict[str, Any]) -> dict[str, Any]:
        """Fallback for OpenAI-compatible models that acknowledge schema mode but return {}."""
        retry_instruction = (
            f"{instruction} 上一次结构化输出不完整。现在必须只返回完整 JSON 对象，不要 Markdown、解释或省略字段。"
        )
        if config.get("base_url"):
            client = ChatLLM(
                model_name=config["model"], provider_name="openai",
                base_url=config["base_url"], api_key=config.get("api_key") or "",
            )
        else:
            client = ChatLLM(model_name=config["model"], provider_name=config["provider"])
        response = client.chat([
            {"role": "system", "content": retry_instruction},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ])
        if not response.content:
            raise RuntimeError("financial analyst fallback returned an empty response")
        return self._parse_analysis_json(response.content)

    def _fallback_text_analysis(self, *, config: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """Keep the dossier usable when a model cannot emit the strict analysis schema.

        The factual inputs remain deterministic.  The model only supplies a
        qualitative synthesis over a deliberately compact context, which is
        then wrapped in the application's validated analysis shape.
        """
        feature = dict(snapshot.get("feature") or {})
        forecast = dict(snapshot.get("forecast") or {})
        compact = {
            "company_identity": snapshot.get("identity"), "historical_cutoff": snapshot.get("historical_cutoff"),
            "financial_trends": feature.get("trends") or {},
            "latest_changes": feature.get("latest_changes") or [],
            "data_quality": feature.get("data_quality") or {},
            "forecast_status": forecast.get("status"), "forecast_notes": forecast.get("assumption_notes") or [],
            "data_gaps": snapshot.get("data_gaps") or [],
        }
        instruction = (
            "你是财报研究员。根据给定的确定性财务快照写一段简洁的文本研究结论，"
            "涵盖经营趋势、财务质量、主要风险、后续验证项。只写定性判断，不要写任何数字、"
            "不要给出买卖、目标价、仓位、止损或加减仓建议。数据不足必须明确标为待核实。"
        )
        if config.get("base_url"):
            client = ChatLLM(model_name=config["model"], provider_name="openai",
                             base_url=config["base_url"], api_key=config.get("api_key") or "")
        else:
            client = ChatLLM(model_name=config["model"], provider_name=config["provider"])
        response = client.chat([
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False, default=str)},
        ])
        if not (text := (response.content or "").strip()):
            raise RuntimeError("financial analyst text fallback returned an empty response")
        # The fallback is intentionally qualitative, preventing unsourced values
        # from being inserted into an otherwise deterministic dossier.
        text = re.sub(r"\d+(?:\.\d+)?", "相关", text)
        status = str(forecast.get("status") or "数据不足")
        trends = dict(feature.get("trends") or {})
        return {
            "stock_code": snapshot["stock_code"], "stock_name": snapshot["stock_name"],
            "executive_summary": f"[文本归纳模式] {text}",
            "historical_performance": {
                "growth": str(trends.get("growth_trend") or "待核实"),
                "profitability": str(trends.get("profitability_trend") or "待核实"),
                "cash_flow": str(trends.get("cash_flow_trend") or "待核实"),
                "balance_sheet": str(trends.get("balance_sheet_trend") or "待核实"),
            },
            "latest_changes": ["详见文本归纳；具体数值以财务快照为准"],
            "financial_strengths": ["需结合文本归纳与确定性快照核验"],
            "financial_risks": ["模型严格结构化输出不可用，结论需复核"],
            "forecast_analysis": {
                "bear": f"情景状态：{status}", "base": f"情景状态：{status}",
                "bull": f"情景状态：{status}", "key_assumptions": list(forecast.get("assumption_notes") or [])[:4],
            },
            "key_metrics_to_monitor": ["营收增长", "盈利能力", "经营现金流", "资产负债"],
            "confidence": "LOW", "data_gaps": list(snapshot.get("data_gaps") or []), "claims": [],
        }

    def analyze(self, stock_code: str, *, as_of: str | None = None,
                refresh: bool = True) -> dict[str, Any]:
        refresh_error = self._refresh_history(stock_code) if refresh else None
        snapshot = self.prepare(stock_code, as_of=as_of)
        if refresh_error and refresh_error not in snapshot["data_gaps"]:
            snapshot["data_gaps"].append(refresh_error)
        if snapshot["analysis_status"] == "COMPLETED":
            return {**snapshot, "idempotent_reuse": True}
        config, configured = self._agent_config()
        if not configured:
            return self.store.update_agent_result(
                snapshot["id"], status="CONFIGURATION_REQUIRED", provider=config["provider"], model=config["model"],
                error="Financial Analyst model is disabled or provider credentials are unavailable",
            )
        evidence = self._evidence(snapshot)
        payload = {
            "company_identity": snapshot["identity"],
            "financial_feature_snapshot": snapshot["feature"],
            "forecast_snapshot": snapshot["forecast"],
            "market_valuation_snapshot": dict(snapshot.get("identity") or {}).get("market_valuation") or {},
            "evidence": evidence,
            "rules": {
                "claim_types": ["FACT", "INFERENCE", "FORECAST", "UNKNOWN"],
                "forecast_numbers_are_immutable": True,
                "prohibited": ["买入", "卖出", "仓位", "目标价", "止损", "加仓", "减仓"],
            },
        }
        instruction = (
            "你是财报研究员，只解释给定的历史财务事实、当前估值快照和 Python 情景推演。"
            "必须严格区分 FACT、INFERENCE、FORECAST、UNKNOWN；FACT/FORECAST 必须引用 evidence key。"
            "executive_summary 必须综合历史经营、盈利质量、现金流、资产负债、未来三年情景和估值口径。"
            "historical_performance 需覆盖增长、盈利、现金流、资产负债；forecast_analysis 需分别解释 Bear/Base/Bull。"
            "若 market_valuation_snapshot 有 PE/PB/股息率/市值，必须在 latest_changes 或 financial_risks 中解释其口径和局限；"
            "没有历史分位、同行比较或 DCF 时必须明确不可得。"
            "不得修改、外推或新造任何数字；认为假设不合理时写入 financial_risks。"
            "禁止给出买卖、目标价、仓位、止损、加减仓建议。仅返回指定 JSON。"
        )
        try:
            # Ark's deepseek-v4-flash is reliable for normal chat but, in
            # production verification, can stall or return {} for this large
            # strict-schema payload.  Use the compact qualitative path rather
            # than making a usable dossier wait for an incompatible mode.
            if config.get("base_url") and config["model"].strip().lower() == "deepseek-v4-flash":
                output = self._fallback_text_analysis(config=config, snapshot=snapshot)
                self._validate_analysis(output, payload)
                return self.store.update_agent_result(
                    snapshot["id"], status="COMPLETED", provider=config["provider"], model=config["model"], analysis=output,
                )
            connection_invoke = getattr(self.runtime, "invoke_with_connection", None)
            if config.get("base_url") and callable(connection_invoke):
                output = connection_invoke(
                    role="financial_analyst", phase="FINANCIAL_ANALYSIS",
                    model=config["model"], base_url=config["base_url"],
                    api_key=config.get("api_key") or "", instruction=instruction, payload=payload,
                )
            else:
                output = self.runtime.invoke(
                    role="financial_analyst", phase="FINANCIAL_ANALYSIS",
                    provider=config["provider"], model=config["model"], instruction=instruction, payload=payload,
                )
            try:
                self._validate_analysis(output, payload)
            except ValueError as exc:
                if not str(exc).startswith("analysis schema missing:"):
                    raise
                output = self._fallback_text_analysis(config=config, snapshot=snapshot)
                self._validate_analysis(output, payload)
            return self.store.update_agent_result(
                snapshot["id"], status="COMPLETED", provider=config["provider"], model=config["model"], analysis=output,
            )
        except Exception as exc:
            return self.store.update_agent_result(
                snapshot["id"], status="FAILED", provider=config["provider"], model=config["model"],
                error=f"{type(exc).__name__}: {exc}",
            )

    def chat(self, stock_code: str, *, question: str, as_of: str | None = None,
             history: list[dict[str, str]] | None = None,
             progress: FinancialProgress | None = None) -> dict[str, Any]:
        """Answer one bounded question through the configured financial analyst.

        Unlike the general-purpose AgentLoop, this path deliberately uses the
        financial_analyst connection (typically the user's Ark model) and the
        deterministic financial snapshot for the selected company.
        """
        question = question.strip()
        if not question:
            raise ValueError("question is required")
        _progress(progress, "financial_snapshot", "正在读取公司财务快照", stock_code=stock_code)
        snapshot = self.get(stock_code, as_of=as_of)
        _progress(
            progress,
            "financial_snapshot_loaded",
            f"已读取 {snapshot['stock_name']} 财务快照（截至 {snapshot['as_of']}）",
            stock_code=snapshot["stock_code"],
            stock_name=snapshot["stock_name"],
            as_of=snapshot["as_of"],
            data_gap_count=len(snapshot.get("data_gaps") or []),
        )
        if CAPABILITY_QUESTION.search(question):
            _progress(progress, "capability_manifest", "已读取当前财报 Agent 能力清单")
            return {
                "stock_code": snapshot["stock_code"],
                "stock_name": snapshot["stock_name"],
                "as_of": snapshot["as_of"],
                "answer": CAPABILITY_RESPONSE,
                "scope": "capability",
                "deterministic": True,
                "capability_version": "financial-capability-v1.0.0",
            }
        unavailable = _requested_unavailable_metrics(question)
        if unavailable:
            labels = "、".join(unavailable)
            _progress(progress, "data_boundary", f"已识别暂未完整接入的指标：{labels}")
            return {
                "stock_code": snapshot["stock_code"],
                "stock_name": snapshot["stock_name"],
                "as_of": snapshot["as_of"],
                "answer": (
                    f"{snapshot['stock_name']} 当前财务快照暂未完整接入：{labels}，"
                    "因此不能基于现有数据给出可靠结论。"
                ),
                "scope": "data_boundary",
                "deterministic": True,
                "missing_capabilities": unavailable,
                "capability_version": "financial-capability-v1.0.0",
            }
        archived_history = self.store.list_chat_entries(snapshot["stock_code"], limit=12)
        model_history = archived_history or (history or [])[-8:]
        config, configured = self._agent_config()
        if not configured:
            raise RuntimeError("Financial Analyst model is disabled or provider credentials are unavailable")
        context = {
            "company_identity": snapshot.get("identity"),
            "historical_cutoff": snapshot.get("historical_cutoff"),
            "financial_feature_snapshot": snapshot.get("feature"),
            "forecast_snapshot": snapshot.get("forecast"),
            "market_valuation_snapshot": dict(snapshot.get("identity") or {}).get("market_valuation") or {
                "status": "unavailable",
                "reason": "当前财务快照生成时尚未写入估值快照；重新预建后可用。",
            },
            "data_gaps": snapshot.get("data_gaps") or [],
            "capability_manifest": {
                "supported": [
                    "营收、净利润及 CAGR", "ROE、毛利率、净利率", "经营现金流与现金转换率",
                    "资产、净资产与负债率", "资本开支", "多年度趋势", "最新财务变化",
                    "未来三年 Bear/Base/Bull 营收与净利润推演",
                    "当前 PE、PB、股息率、市值快照", "数据覆盖率、数据缺口与证据追踪",
                ],
                "not_fully_integrated": list(UNAVAILABLE_METRICS),
            },
        }
        instruction = (
            "你是价值投资研究工作台的财报研究员。只基于给定公司财务快照回答，"
            "输出应是可复核的公司研究说明，而不是泛泛摘要。\n"
            "无论用户问题是否只问一个点，只要数据可用，按下面框架组织回答（没有数据的段落保留标题并说明原因）：\n"
            "1. 数据口径与一句话结论：说明财务截止日、估值快照日、结论的事实边界。\n"
            "2. 历史经营：用快照内最近 3—5 个完整年度及最新报告期解释营收、净利润、增长持续性与波动。\n"
            "3. 盈利质量：解释 ROE、毛利率、净利率及利润增长是否相互印证。\n"
            "4. 现金流与资本开支：解释经营现金流、现金转换率、资本开支与利润匹配情况。\n"
            "5. 资产负债与财务风险：解释资产、净资产、负债率的变化及数据能覆盖到的风险。\n"
            "6. 未来三年情景：逐项列出 Bear/Base/Bull 的营收和净利润推演、增长与净利率假设；"
            "这只是确定性情景推演，不是业绩承诺。若净利润没有数值，明确说明阻断原因。\n"
            "7. 当前估值解释：只使用 market_valuation_snapshot 中的 PE、PB、股息率和市值，"
            "解释 PE 所反映的当前利润口径与适用前提；必须同时说明这不是历史分位、同行比较、DCF 或目标价。\n"
            "8. 关键风险、反证与后续跟踪：列出会推翻当前判断的事实，以及最该跟踪的 3—5 个指标。\n"
            "所有数值、年份、百分比必须逐字来自给定快照；不得自行计算、外推或补造任何数字。"
            "清楚标记【事实】【推断】【情景预测】【待核实】；如历史问答与当前快照冲突，以当前快照为准。"
            "只能把 capability_manifest.supported 和财务快照中实际存在的字段描述为现有能力。"
            "not_fully_integrated 中的指标必须明确标记为暂未完整接入。"
            "禁止给出买入、卖出、目标价、仓位、止损或加减仓建议。"
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": instruction}]
        for item in model_history:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        self.store.append_chat_entry(
            stock_code=snapshot["stock_code"], stock_name=snapshot["stock_name"], role="user", content=question,
            source_snapshot_id=snapshot.get("id"), source_hash=snapshot.get("source_hash"),
        )
        messages.append({
            "role": "user",
            "content": f"财务快照：\n{json.dumps(context, ensure_ascii=False, default=str)}\n\n用户问题：{question}",
        })
        if config.get("base_url"):
            client = ChatLLM(
                model_name=config["model"], provider_name="openai",
                base_url=config["base_url"], api_key=config.get("api_key") or "",
            )
        else:
            client = ChatLLM(model_name=config["model"], provider_name=config["provider"])
        _progress(
            progress,
            "model_analysis",
            f"正在使用 {config['model']} 解释财务事实与风险",
            model=config["model"],
        )
        response = client.chat(messages)
        if not (answer := (response.content or "").strip()):
            raise RuntimeError("Financial Analyst returned an empty response")
        assistant_entry = self.store.append_chat_entry(
            stock_code=snapshot["stock_code"], stock_name=snapshot["stock_name"], role="assistant", content=answer,
            source_snapshot_id=snapshot.get("id"), source_hash=snapshot.get("source_hash"),
        )
        _progress(progress, "analysis_complete", "财报解释已完成，正在整理结论")
        return {
            "stock_code": snapshot["stock_code"], "stock_name": snapshot["stock_name"],
            "as_of": snapshot["as_of"], "answer": answer,
            "provider": config["provider"], "model": config["model"],
            "archive_entry_id": assistant_entry["id"], "dossier_entry_count": len(archived_history) + 2,
        }

    def dossier(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Return the reusable financial snapshot and its persistent question archive."""
        snapshot = self.get(stock_code, as_of=as_of)
        entries = self.store.list_chat_entries(snapshot["stock_code"])
        return {
            "snapshot": snapshot, "chat_entries": entries,
            "archive_summary": {
                "chat_entry_count": len(entries), "latest_chat_at": entries[-1]["created_at"] if entries else None,
                "analysis_status": snapshot["analysis_status"], "source_hash": snapshot["source_hash"],
            },
        }

    @staticmethod
    def _resolve_workspace_company(question: str,
                                   candidates: list[dict[str, Any]] | None) -> dict[str, str] | None:
        """Resolve a company named in a leaders-page question against its visible pool."""
        compact_question = question.upper()
        normalized: list[dict[str, str]] = []
        for candidate in candidates or []:
            code = str(candidate.get("stock_code") or "").upper().strip()
            name = str(candidate.get("stock_name") or "").strip()
            if not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", code) or not name:
                continue
            normalized.append({
                "stock_code": code, "stock_name": name,
                "as_of": str(candidate.get("as_of") or "").strip(),
            })
        # A full exchange code is unambiguous; accept its bare six-digit form too.
        for candidate in normalized:
            if candidate["stock_code"] in compact_question or candidate["stock_code"].split(".")[0] in compact_question:
                return candidate
        # Prefer the longest name so a specific company wins over a shorter overlap.
        for candidate in sorted(normalized, key=lambda item: len(item["stock_name"]), reverse=True):
            if candidate["stock_name"] in question:
                return candidate
        return None

    @classmethod
    def _resolve_history_company(
        cls,
        history: list[dict[str, str]] | None,
        candidates: list[dict[str, Any]] | None,
    ) -> dict[str, str] | None:
        """Bind an explicit follow-up to a company actually present in the pool."""
        for item in reversed(history or []):
            if str(item.get("role") or "") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if content and (company := cls._resolve_workspace_company(content, candidates)):
                return company
        return None

    @staticmethod
    def _workspace_data_context(question: str,
                                candidates: list[dict[str, Any]] | None) -> dict[str, Any]:
        """Build bounded evidence from the locally loaded leaders-page pool."""
        records: list[dict[str, Any]] = []
        for candidate in candidates or []:
            code = str(candidate.get("stock_code") or "").upper().strip()
            name = str(candidate.get("stock_name") or "").strip()
            industry = str(candidate.get("level3_name") or "").strip()
            if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", code) and name and industry:
                records.append({
                    "stock_code": code, "stock_name": name, "industry": industry,
                    "leader_rank": candidate.get("leader_rank"),
                    "leader_score": candidate.get("leader_score"),
                    "coverage": candidate.get("coverage"), "as_of": candidate.get("as_of"),
                })
        by_industry: dict[str, list[dict[str, Any]]] = {}
        for item in records:
            by_industry.setdefault(item["industry"], []).append(item)
        matching_industries = [name for name in by_industry if name in question]
        relevant = matching_industries[:8]
        return {
            "source": "本地 value_level3_leaders 当前页面快照",
            "company_count": len(records),
            "industry_count": len(by_industry),
            "data_dates": sorted({str(item["as_of"]) for item in records if item.get("as_of")}, reverse=True)[:3],
            "average_coverage": round(
                sum(float(item["coverage"] or 0) for item in records) / len(records), 4
            ) if records else None,
            "matched_industries": [
                {
                    "industry": industry,
                    "leaders": sorted(by_industry[industry], key=lambda item: int(item["leader_rank"] or 999))[:5],
                    "note": "leader_score 只可在同一三级行业内部比较",
                }
                for industry in relevant
            ],
            "available_industries": [
                {"industry": industry, "leader_count": len(leaders)}
                for industry, leaders in sorted(by_industry.items())
            ],
            "universe_note": "该快照是当前页面已加载的三级行业龙头池，不代表全市场证券。",
        }

    def chat_workspace(self, *, question: str,
                       history: list[dict[str, str]] | None = None,
                       candidates: list[dict[str, Any]] | None = None,
                       progress: FinancialProgress | None = None) -> dict[str, Any]:
        """Answer a general question from the leaders page without forcing a company.

        A detail-drawer conversation should use :meth:`chat` above because it
        includes one company's PIT financial snapshot.  The floating launcher
        remains useful for financial concepts, screening logic, and questions
        about how to investigate a leader before a company has been selected.
        """
        question = question.strip()
        if not question:
            raise ValueError("question is required")
        if CAPABILITY_QUESTION.search(question):
            _progress(progress, "capability_manifest", "已读取当前财报 Agent 能力清单")
            return {
                "answer": CAPABILITY_RESPONSE,
                "scope": "capability",
                "deterministic": True,
                "capability_version": "financial-capability-v1.0.0",
            }

        unavailable = _requested_unavailable_metrics(question)
        if unavailable:
            labels = "、".join(unavailable)
            _progress(progress, "data_boundary", f"已识别暂未完整接入的指标：{labels}")
            return {
                "answer": (
                    f"当前确定性财务快照暂未完整接入：{labels}，因此不能基于现有数据给出可靠结论。\n\n"
                    "请进入公司研究页查看已支持的财务趋势、盈利质量、现金流、资产负债、"
                    "资本开支和三情景推演；缺失指标会明确标记为“暂未接入”。"
                ),
                "scope": "data_boundary",
                "deterministic": True,
                "missing_capabilities": unavailable,
                "capability_version": "financial-capability-v1.0.0",
            }

        _progress(progress, "company_match", "正在识别问题中的公司或股票代码")
        company = self._resolve_workspace_company(question, candidates)
        is_follow_up = bool(FOLLOW_UP_QUESTION.search(question))
        if company is None and is_follow_up:
            company = self._resolve_history_company(history, candidates)
        if company is not None:
            _progress(
                progress,
                "company_matched",
                f"已定位公司：{company['stock_name']}（{company['stock_code']}）",
                **company,
            )
            result = self.chat(
                company["stock_code"], question=question,
                as_of=company["as_of"] or None, history=history, progress=progress,
            )
            return {**result, "scope": "company", "matched_by": "leaders_page_company_name_or_code"}
        if re.search(r"(?<!\d)\d{6}(?!\d)", question):
            return {
                "answer": (
                    "当前问题中的股票代码不在本次已加载的龙头池快照中，不能仅凭代码假定已取得完整财务数据。\n\n"
                    "请先进入公司研究页面打开该股票，或在公司详情页使用“完整财务分析”。"
                ),
                "scope": "company_not_loaded",
                "deterministic": True,
            }
        if is_follow_up:
            return {
                "answer": (
                    "这条追问没有绑定到用户此前明确选择的公司，因此不会沿用模型回答中出现的公司名称或财务结论。\n\n"
                    "请明确输入龙头池中的公司名称/代码，或进入公司研究页面后继续提问。"
                ),
                "scope": "context_required",
                "deterministic": True,
            }
        config, configured = self._agent_config()
        if not configured:
            raise RuntimeError("Financial Analyst model is disabled or provider credentials are unavailable")
        local_context = self._workspace_data_context(question, candidates)
        _progress(
            progress,
            "workspace_context",
            (
                f"未锁定单家公司，已加载 {local_context['industry_count']} 个三级行业、"
                f"{local_context['company_count']} 家龙头的本地快照"
            ),
            industry_count=local_context["industry_count"],
            company_count=local_context["company_count"],
            data_dates=local_context["data_dates"],
        )
        instruction = (
            "你是价值投资工作台的财报研究员，正在龙头列表页面与用户对话。"
            "回答财报阅读、经营质量、财务风险、估值假设和研究方法问题。"
            "当前问题没有识别出龙头列表中的具体公司；不得声称掌握某家公司最新财务数据。"
            "如需公司专属数字，请让用户直接在问题中写出公司名称或股票代码。"
            "回答前必须先阅读随问题提供的本地龙头池快照；只要使用了其中的事实，就说明数据日期和口径。"
            "快照同时提供行业目录和与提问精确匹配的行业龙头；未匹配到行业不等于本地没有行业或公司信息，禁止作此类推断。"
            "当前上下文不包含任何公司的财务明细。不得从历史对话带入公司名称、股票代码、年份或财务结论。"
            "不得声称已支持流动比率、速动比率、利息保障倍数、借款期限结构、营运资本周转、"
            "非经常性损益、期间费用率、分产品或分地区毛利、产能利用率、市场份额、行业价格变量、"
            "历史估值、同行自动比较或估值敏感性；相关问题只能回答暂未完整接入。"
            "禁止给出买入、卖出、目标价、仓位、止损或加减仓建议。"
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": instruction}]
        # Workspace questions never inherit free-form history. A legitimate
        # company follow-up has already been rebound above to a real snapshot.
        messages.append({
            "role": "user",
            "content": f"本地龙头池快照：\n{json.dumps(local_context, ensure_ascii=False, default=str)}\n\n用户问题：{question}",
        })
        if config.get("base_url"):
            client = ChatLLM(
                model_name=config["model"], provider_name="openai",
                base_url=config["base_url"], api_key=config.get("api_key") or "",
            )
        else:
            client = ChatLLM(model_name=config["model"], provider_name=config["provider"])
        _progress(
            progress,
            "model_analysis",
            f"正在使用 {config['model']} 归纳本地龙头池财务信息",
            model=config["model"],
        )
        response = client.chat(messages)
        if not (answer := (response.content or "").strip()):
            raise RuntimeError("Financial Analyst returned an empty response")
        _progress(progress, "analysis_complete", "财报解释已完成，正在整理结论")
        return {
            "answer": answer, "scope": "workspace",
            "provider": config["provider"], "model": config["model"],
            "data_context": local_context,
        }

    def chat_general_method(
        self,
        *,
        question: str,
        progress: FinancialProgress | None = None,
    ) -> dict[str, Any]:
        """Answer financial-method questions without loading market/company data."""
        question = question.strip()
        if not question:
            raise ValueError("question is required")
        config, configured = self._agent_config()
        if not configured:
            raise RuntimeError("Financial Analyst model is disabled or provider credentials are unavailable")
        instruction = (
            "你是财报研究员，当前回答的是通用财报阅读方法，不包含公司、行业或龙头池数据。"
            "不得引用历史对话中的公司名称、股票代码、年份、数值或结论；不得声称已加载任何公司财务快照。"
            "当前系统已支持的确定性能力仅包括：财务趋势、盈利质量、现金流、资产负债、资本开支、"
            "三情景营收利润推演、数据质量检查、证据追踪和财报问答。"
            "营运资本周转、非经常性损益、分产品分地区毛利、产能利用率、市场份额、行业价格变量、"
            "同行估值比较和估值敏感性暂未完整接入。"
            "回答应解释方法和判断框架；如用户需要公司结论，引导其进入公司研究页面选择股票。"
            "禁止给出买入、卖出、目标价、仓位、止损或加减仓建议。"
        )
        _progress(
            progress,
            "general_method_analysis",
            f"正在使用 {config['model']} 分析通用财报方法；无需加载公司或龙头池数据",
            model=config["model"],
        )
        if config.get("base_url"):
            client = ChatLLM(
                model_name=config["model"], provider_name="openai",
                base_url=config["base_url"], api_key=config.get("api_key") or "",
            )
        else:
            client = ChatLLM(model_name=config["model"], provider_name=config["provider"])
        response = client.chat([
            {"role": "system", "content": instruction},
            {"role": "user", "content": question},
        ])
        if not (answer := (response.content or "").strip()):
            raise RuntimeError("Financial Analyst returned an empty response")
        _progress(progress, "analysis_complete", "通用财报方法解释已完成，正在整理结论")
        return {
            "answer": answer,
            "scope": "general_method",
            "provider": config["provider"],
            "model": config["model"],
            "data_context": {"source": "none", "company_data_loaded": False, "leader_pool_loaded": False},
        }

    def _classify_ambiguous_question(
        self,
        *,
        question: str,
        progress: FinancialProgress | None = None,
    ) -> dict[str, Any]:
        """Classify only the current message, without history or business data.

        Rules handle obvious requests first.  This bounded model fallback exists
        only for genuinely ambiguous wording and deliberately refuses Ollama so
        routing uses the same explicitly configured remote Financial Analyst as
        the subsequent answer.
        """
        config, configured = self._agent_config()
        provider = str(config.get("provider") or "").strip()
        base_url = str(config.get("base_url") or "").strip()
        is_ollama = provider.lower() == "ollama" or "ollama" in base_url.lower() or ":11434" in base_url
        safe_fallback = {
            "intent": "general_method",
            "source": "safe_fallback",
            "confidence": 0.0,
            "entity": "",
        }
        if not configured or is_ollama:
            _progress(
                progress,
                "intent_fallback",
                "问题意图暂无法可靠识别，按通用财报问题处理；本次不加载公司或龙头池数据",
                reason="ollama_disabled" if is_ollama else "classifier_unavailable",
            )
            return safe_fallback

        _progress(
            progress,
            "intent_model_routing",
            f"问题含义不够明确，正在使用 {config['model']} 做轻量意图识别（不加载业务数据）",
            model=config["model"],
        )
        instruction = (
            "你是财报问答的意图路由器，只分类用户当前这一句话，不回答问题。"
            "你没有历史对话，也不得假设任何公司、行业或数据库内容。"
            "只允许 intent 为 capability、data_boundary、general_method、company_lookup、leader_pool。"
            "capability=询问系统能做什么；data_boundary=询问系统是否支持某类数据；"
            "general_method=通用财报概念、分析方法或闲聊；company_lookup=指定或明显指向单家公司；"
            "leader_pool=询问行业、赛道、龙头池或多家公司比较。"
            "只返回 JSON 对象："
            '{"intent":"general_method","entity":"","confidence":0.0,"reason":""}。'
        )
        try:
            if base_url:
                client = ChatLLM(
                    model_name=config["model"], provider_name="openai",
                    base_url=base_url, api_key=config.get("api_key") or "",
                )
            else:
                client = ChatLLM(model_name=config["model"], provider_name=provider)
            response = client.chat([
                {"role": "system", "content": instruction},
                {"role": "user", "content": question.strip()},
            ])
            parsed = self._parse_analysis_json(response.content or "")
            intent = str(parsed.get("intent") or "").strip()
            if intent not in {"capability", "data_boundary", "general_method", "company_lookup", "leader_pool"}:
                raise ValueError("invalid routing intent")
            confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.0)))
            if confidence < 0.65:
                raise ValueError("routing confidence below 0.65")
            entity = str(parsed.get("entity") or "").strip()[:80]
            return {
                "intent": intent,
                "source": "model",
                "confidence": confidence,
                "entity": entity,
            }
        except Exception:
            _progress(
                progress,
                "intent_fallback",
                "意图识别结果不可靠，按通用财报问题处理；本次不加载公司或龙头池数据",
                reason="classifier_error_or_low_confidence",
            )
            return safe_fallback

    def chat_current_leader_pool(self, *, question: str,
                                 history: list[dict[str, str]] | None = None,
                                 progress: FinancialProgress | None = None) -> dict[str, Any]:
        """Chat from an IM channel using the same live leader pool as ``/value``.

        The web client normally supplies its rendered candidates to
        :meth:`chat_workspace`.  An IM channel has no browser state, so it must
        load the persisted Top-2 terminal-industry snapshot itself.  This keeps
        company matching local and ensures a named company follows the existing
        persistent company-financial-chat path.
        """
        intent = classify_financial_question(question)
        routing: dict[str, Any] = {
            "intent": intent,
            "source": "rules",
            "confidence": 1.0,
            "entity": "",
        }
        if intent == "ambiguous":
            routing = self._classify_ambiguous_question(question=question, progress=progress)
            intent = str(routing["intent"])
        intent_labels = {
            "capability": "能力咨询",
            "data_boundary": "未接入指标咨询",
            "general_method": "通用财报方法",
            "company_lookup": "公司财务分析",
            "leader_pool": "行业/赛道/龙头分析",
        }
        _progress(
            progress,
            "intent_routing",
            f"已识别问题类型：{intent_labels[intent]}，正在按需选择数据",
            intent=intent, routing_source=routing["source"], confidence=routing["confidence"],
        )
        if intent in {"capability", "data_boundary"}:
            result = self.chat_workspace(
                question=question, history=None, candidates=[], progress=progress,
            )
            return {
                **result,
                "leader_snapshot_as_of": None,
                "leader_snapshot_status": "not_requested",
                "routing": routing,
            }
        if intent == "general_method":
            result = self.chat_general_method(question=question, progress=progress)
            return {
                **result,
                "leader_snapshot_as_of": None,
                "leader_snapshot_status": "not_requested",
                "routing": routing,
            }

        from src.level3_leaders.service import get_level3_leader_service

        _progress(progress, "leader_pool", "正在读取当前三级行业龙头池")
        snapshot = get_level3_leader_service().get_all_level3_top_leaders(limit=2)
        candidates = [leader for leaders in dict(snapshot.get("items") or {}).values() for leader in leaders]
        _progress(
            progress,
            "leader_pool_loaded",
            (
                f"已加载 {len(snapshot.get('items') or {})} 个三级行业、"
                f"{len(candidates)} 家候选龙头（数据截至 {snapshot.get('as_of') or '未知'}）"
            ),
            industry_count=len(snapshot.get("items") or {}),
            company_count=len(candidates),
            as_of=snapshot.get("as_of"),
        )
        if intent == "company_lookup":
            company = self._resolve_workspace_company(question, candidates)
            if company is None and FOLLOW_UP_QUESTION.search(question):
                company = self._resolve_history_company(history, candidates)
            if company is None:
                _progress(
                    progress,
                    "company_not_loaded",
                    "未能在当前龙头池中定位该公司，已停止加载公司财务数据",
                )
                return {
                    "answer": (
                        "未能在当前龙头池中定位到这家公司，因此没有读取或推断其财务数据。\n\n"
                        "请进入公司研究页面打开该公司，或在公司详情页使用“完整财务分析”。"
                    ),
                    "scope": "company_not_loaded",
                    "deterministic": True,
                    "leader_snapshot_as_of": snapshot.get("as_of"),
                    "leader_snapshot_status": snapshot.get("snapshot_status"),
                    "routing": routing,
                }
        result = self.chat_workspace(
            question=question, history=history, candidates=candidates, progress=progress,
        )
        return {
            **result,
            "leader_snapshot_as_of": snapshot.get("as_of"),
            "leader_snapshot_status": snapshot.get("snapshot_status"),
            "routing": routing,
        }


_service: FinancialAnalysisService | None = None


def get_financial_analysis_service() -> FinancialAnalysisService:
    global _service
    if _service is None:
        _service = FinancialAnalysisService()
    return _service
