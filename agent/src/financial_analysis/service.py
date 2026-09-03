"""Application service for deterministic finance plus one bounded analyst role."""

from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from src.research_tasks.providers import safe_provider_catalog
from src.research_tasks.service import ProviderModelRuntime
from src.research_tasks.store import ResearchTaskStore
from src.providers.chat import ChatLLM, ProviderStreamError
from src.structured_output import (
    StructuredOutputMode,
    StructuredOutputRuntime,
    resolve_structured_output_capabilities,
)
from src.strategy_engines.common.provenance import stable_fingerprint
from src.tdx_data.financial_history import FinancialHistoryService
from src.tdx_data.store import TdxDataStore
from src.business_research.store import BusinessResearchStore

from .citations import FinancialClaimCitationResolver
from .engine import FINANCIAL_FEATURE_VERSION, FORECAST_VERSION, FinancialFeatureEngine, FinancialForecastEngine
from .store import FinancialAnalysisStore

PROHIBITED_ACTIONS = re.compile(r"建议买入|建议卖出|买入|卖出|目标价|目标仓位|止损|加仓|减仓")
HIGH_RISK_BUSINESS_TERMS = (
    "IDM", "Fabless", "IGBT", "MOSFET", "MCU", "MEMS", "SiC", "GaN",
)
FINANCIAL_CLAIMS_PROMPT_VERSION = "financial-analysis-claims-v1.1.0"
# Answer-cache invalidation dimension for the chat path (research-cache plan §6).
FINANCIAL_CHAT_PROMPT_VERSION = "financial-chat-v1"
CLAIM_TYPES = {"FACT", "INFERENCE", "FORECAST", "UNKNOWN"}
CLAIM_CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
MAX_CLAIMS = 8
FINANCIAL_CHAT_TIMEOUT_SECONDS = 90
FINANCIAL_CHAT_MAX_TOKENS = 6_500
FINANCIAL_CHAT_RETRY_MAX_TOKENS = 8_000
FINANCIAL_CHAT_FINAL_RETRY_MAX_TOKENS = 10_000
FINANCIAL_CHAT_TOKEN_BUDGETS = {
    "focused": 4_000,
    "overview": FINANCIAL_CHAT_MAX_TOKENS,
    "full": 9_000,
    "continuation": 4_000,
}
FinancialProgress = Callable[[str, str, dict[str, Any]], None]
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _beijing_iso(value: Any) -> str | None:
    """Normalize stored ISO timestamps to the product's Beijing-time clock."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(BEIJING_TZ).isoformat()


def _iso_date(value: Any) -> str | None:
    normalized = _beijing_iso(value)
    return normalized[:10] if normalized else None


class ClaimValidationError(ValueError):
    """A safe, structured business-contract rejection for one Claims result."""

    def __init__(self, code: str, message: str, *, claim_index: int | None = None,
                 source_keys: list[str] | None = None, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.claim_index = claim_index
        self.source_keys = list(source_keys or [])
        self.metadata = dict(metadata or {})

    def audit_dict(self) -> dict[str, Any]:
        return {
            "validation_error_code": self.code,
            "claim_index": self.claim_index,
            "error_summary": str(self),
            "source_keys": self.source_keys,
            "metadata": self.metadata,
        }
CAPABILITY_QUESTION = re.compile(
    r"(?:还能|还可以|目前|现在)?分析(?:哪些|什么|哪方面|哪些方面|什么方面)|"
    r"(?:目前|现在)?支持(?:哪些|什么)(?:能力|功能|分析)?|"
    r"有哪些(?:能力|功能|分析)|(?:能做|可以做)(?:哪些|什么)|能力范围|功能范围",
)
FOLLOW_UP_QUESTION = re.compile(
    r"^\s*(?:那|那么|再看|继续|这个|该公司|它|上述|前面|刚才|进一步|另外|还有呢|然后)",
)
CONTINUE_OUTPUT_QUESTION = re.compile(r"^\s*(?:继续(?:输出|分析|回答)?|接着(?:说|分析|输出)?|续写|下一部分|补完)")
FULL_FINANCIAL_QUESTION = re.compile(
    r"(?:全面|完整|详细|深度|系统|全方位).{0,8}(?:分析|研究|财报)|"
    r"(?:分析|研究).{0,8}(?:全面|完整|详细|深度|系统)|完整财报|L\s*3|深度",
    re.IGNORECASE,
)
FOCUSED_FINANCIAL_QUESTION = re.compile(
    r"营收|收入|利润|盈利|毛利|净利|ROE|现金流|资本开支|负债|资产|"
    r"估值|PE|PB|股息|市值|风险|反证|情景|预测|增长|财务质量",
    re.IGNORECASE,
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


def _unsupported_business_terms(
    answer: str, *, question: str, business_context: dict[str, Any], identity: dict[str, Any],
) -> list[str]:
    """Find specific product/model claims absent from the supplied research facts.

    These terms are common in generic semiconductor prose and were observed to
    leak into company reports even when the TDX business snapshot did not name
    them.  A term explicitly asked by the user is allowed so the analyst can
    explain that it is not verified.
    """
    allowed = json.dumps(
        {
            "business": business_context,
            "industry": {
                "level1_name": identity.get("level1_name"),
                "level2_name": identity.get("level2_name"),
                "level3_name": identity.get("level3_name"),
            },
        },
        ensure_ascii=False,
        default=str,
    ).lower()
    answer_lower = answer.lower()
    question_lower = question.lower()
    return [
        term for term in HIGH_RISK_BUSINESS_TERMS
        if term.lower() in answer_lower
        and term.lower() not in allowed
        and term.lower() not in question_lower
    ]


def _question_names_cached_security(text: str) -> bool:
    """True when a cached security name (>=3 chars) appears in the question."""
    from src.tdx_data.service import get_tdx_service

    try:
        return get_tdx_service().find_security_named_in(text) is not None
    except Exception:  # noqa: BLE001 - classification must never fail on cache errors
        return False


def classify_financial_question(question: str) -> str:
    """Route before loading any heavyweight company or leader-pool data."""
    text = question.strip()
    if CAPABILITY_QUESTION.search(text):
        return "capability"
    if _requested_unavailable_metrics(text):
        return "data_boundary"
    # An explicit six-digit code always names one company; leader-pool
    # keywords around a code (e.g. "分析600460的行业龙头地位") must not
    # redirect the question to the pool snapshot.
    if re.search(r"(?<!\d)\d{6}(?:\.(?:SH|SZ|BJ))?(?!\d)", text, re.IGNORECASE):
        return "company_lookup"
    # A cached company name embedded in the question keeps it a company
    # question even when generic method wording ("财务表现怎么样") is
    # present, without paying for the model router.
    if _question_names_cached_security(text):
        return "company_lookup"
    if LEADER_DATA_QUESTION.search(text):
        return "leader_pool"
    if FOLLOW_UP_QUESTION.search(text):
        return "company_lookup"
    if GENERAL_METHOD_QUESTION.search(text):
        return "general_method"
    return "ambiguous"


def classify_financial_answer_mode(question: str) -> str:
    """Choose answer depth without another model call."""
    text = question.strip()
    if CONTINUE_OUTPUT_QUESTION.search(text):
        return "continuation"
    if FULL_FINANCIAL_QUESTION.search(text):
        return "full"
    if FOCUSED_FINANCIAL_QUESTION.search(text):
        return "focused"
    return "overview"


def _financial_answer_contract(mode: str) -> str:
    common = (
        "先直接回答用户当前问题，再给支持结论的财务事实；不要先复述任务或罗列能力清单。"
        "使用通俗中文，解释数字说明了什么，避免连续堆砌指标。只展示结论所必需的数据日期，"
        "不把行情、估值、财报和排名日期混成一个截止日。除非用户明确询问，不展示龙头综合评分和排名。"
        "必须使用 Markdown 标题、空行和项目符号，禁止把全文挤成一个大段落。"
    )
    contracts = {
        "focused": (
            "这是一个聚焦问题。全文控制在600—1000个汉字，严格使用三个三级标题："
            "### 结论、### 关键依据、### 风险与待确认。关键依据使用项目符号，不展开无关章节。"
        ),
        "overview": (
            "这是标准公司研究报告。全文控制在2200—3200个汉字，不得超过3800个汉字。"
            "严格使用以下七个三级标题：### 研究结论、### ① 公司与业务、### ② 经营周期与业绩、"
            "### ③ 财务质量、### ④ 估值与市场预期、### ⑤ 优势、风险与反证、### ⑥ 下一期验证清单。"
            "研究结论用引用块展示‘当前判断’和‘核心矛盾’；中间章节每节先用1—2句通俗解释，再列3—5个项目符号。"
            "②中必须用一张 Markdown 表格合并展示近五年营收、净利润、毛利率和经营现金流，不得把年度数据拆成长串项目符号。"
        ),
        "full": (
            "用户明确要求详细、深度或L3分析。全文控制在4200—7000个汉字，不得超过8000个汉字。"
            "严格使用以下九个三级标题：### 研究结论、### ① 公司画像与行业位置、### ② 五年经营路径与周期、"
            "### ③ 盈利与现金流质量、### ④ 资产负债与资本投入、### ⑤ 估值与市场预期、"
            "### ⑥ 情景推演、### ⑦ 核心风险与反证、### ⑧ 后续验证清单。"
            "研究结论使用引用块列出‘当前判断’、‘核心矛盾’和‘最重要的下期信号’。"
            "②必须用 Markdown 表格展示近五年营收、营收同比、净利润、净利润同比和毛利率。"
            "③必须用 Markdown 表格展示近五年经营现金流、现金转换率和净利率。"
            "如情景数值完整，⑥可再使用一张情景表；整份报告最多三张表，其他部分用短段落和项目符号。"
            "未来三年情景推演只展示给定快照中已经存在的假设和数值；如果净利润或估值区间不可用，直接说无法可靠运行。"
        ),
        "continuation": (
            "用户要求继续上一轮。只补充上一轮尚未完成的内容，不重复已经回答的段落，"
            "全文控制在800—1400个汉字，并用明确的 Markdown 标题分段。"
        ),
    }
    return common + contracts.get(mode, contracts["overview"])


_FINANCIAL_SECTION_HEADINGS = (
    "研究结论", "① 公司与业务", "② 经营周期与业绩", "③ 财务质量",
    "④ 估值与市场预期", "⑤ 优势、风险与反证", "⑥ 下一期验证清单",
    "① 公司画像与行业位置", "② 五年经营路径与周期", "③ 盈利与现金流质量",
    "④ 资产负债与资本投入", "⑤ 估值与市场预期", "⑥ 情景推演",
    "⑦ 核心风险与反证", "⑧ 后续验证清单",
    "一句话判断", "公司做什么", "业绩发生了什么", "财务质量怎么样", "估值怎么看", "接下来重点看什么",
    "核心判断", "历史经营与盈利", "现金流与资产负债", "未来三年情景", "当前估值",
    "风险与跟踪清单", "结论", "关键依据", "风险与待确认",
    # Normalize answers produced by the previous prompt during a retry or a
    # rolling deployment, so Feishu does not display them as one wall of text.
    "结论先行", "经营与盈利质量", "估值局限与后续关注",
)


def _normalize_financial_markdown(answer: str) -> str:
    """Recover section breaks when a model emits headings as plain prose."""
    text = answer.strip()
    headings = sorted(_FINANCIAL_SECTION_HEADINGS, key=len, reverse=True)

    def heading_pattern(heading: str) -> str:
        escaped = re.escape(heading)
        return rf"(?:^|[ \t\r\n]+)(?:#{{1,6}}[ \t]*)?(?:{escaped}[：:]|{escaped}(?=[ \t\r\n]|$))[ \t]*"

    matched = [heading for heading in headings if re.search(heading_pattern(heading), text)]
    if len(matched) < 2:
        return text
    for heading in matched:
        text = re.sub(heading_pattern(heading), f"\n\n### {heading}\n\n", text, count=1)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _search_tdx_securities(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search the persisted TDX security master without contacting TDX."""
    from src.tdx_data.service import get_tdx_service

    return get_tdx_service().search_securities(query, limit)


class FinancialRuntime(Protocol):
    def invoke(self, *, role: str, phase: str, provider: str, model: str,
               instruction: str, payload: dict[str, Any]) -> dict[str, Any]: ...


_NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_CNY_DISPLAY_UNIT = re.compile(r"^\s*(万亿|亿元|亿|百万元|万元|万|元)")
_CNY_DISPLAY_SCALE = {"万亿": 1e12, "亿元": 1e8, "亿": 1e8, "百万元": 1e6, "万元": 1e4, "万": 1e4, "元": 1.0}
_NEGATIVE_DIRECTION = re.compile(r"(?:下降|下滑|减少|降低|回落|负增长|亏损扩大)")


def _numeric_tokens(value: Any) -> set[str]:
    text = json.dumps(value, ensure_ascii=False, default=str)
    # A dash in a year range (for example 2026-2028) is a separator, not a
    # negative sign. Keep a real sign only when it starts a numeric token.
    return {match.group(0) for match in _NUMERIC_TOKEN.finditer(text)}


def _numeric(value: Any) -> float | None:
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _claim_numeric_values(source_entries: list[dict[str, Any]]) -> set[str]:
    """Return only numeric fields which a cited Evidence item may support."""
    values: set[str] = set()
    for entry in source_entries:
        for field in ("value", "period", "report_date", "announcement_date", "forecast_year"):
            values.update(_numeric_tokens(entry.get(field)))
    return values


def _display_scale(text: str, token_end: int, source_entries: list[dict[str, Any]]) -> float:
    """Accept an amount abbreviation only when the claim states its unit."""
    match = _CNY_DISPLAY_UNIT.match(text[token_end:])
    if not match or not any(entry.get("unit") == "CNY" for entry in source_entries):
        return 1.0
    return _CNY_DISPLAY_SCALE[match.group(1)]


def _numeric_tolerance(token: str, scale: float) -> float:
    decimals = len(token.partition(".")[2])
    # The displayed precision determines the permissible rounding interval.
    return max(1e-9, 0.5 * (10 ** -decimals) * scale)


def _numeric_is_supported(*, token: str, token_start: int, token_end: int, text: str,
                          source_entries: list[dict[str, Any]], allowed_tokens: set[str]) -> bool:
    if token in allowed_tokens:
        return True
    shown = _numeric(token)
    if shown is None:
        return False
    scale = _display_scale(text, token_end, source_entries)
    displayed_value = shown * scale
    tolerance = _numeric_tolerance(token, scale)
    directional_context = text[max(0, token_start - 16):token_end]
    for entry in source_entries:
        source_value = _numeric(entry.get("value"))
        if source_value is None:
            continue
        if math.isclose(displayed_value, source_value, rel_tol=0.0, abs_tol=tolerance):
            return True
        # "同比下降 143.273%" represents a negative source value through
        # direction. The shorthand is invalid without that nearby language.
        if source_value < 0 and shown >= 0 and _NEGATIVE_DIRECTION.search(directional_context):
            if math.isclose(-displayed_value, source_value, rel_tol=0.0, abs_tol=tolerance):
                return True
    return False


class FinancialAnalysisService:
    def __init__(self, *, store: FinancialAnalysisStore | None = None,
                 history: FinancialHistoryService | None = None,
                 config_store: ResearchTaskStore | None = None,
                 runtime: FinancialRuntime | None = None,
                 structured_runtime: StructuredOutputRuntime | None = None,
                 feature_engine: FinancialFeatureEngine | None = None,
                 forecast_engine: FinancialForecastEngine | None = None,
                 tdx_store: TdxDataStore | None = None,
                 business_store: BusinessResearchStore | None = None) -> None:
        self.store = store or FinancialAnalysisStore()
        self.history = history or FinancialHistoryService()
        self.config_store = config_store or ResearchTaskStore(self.store.db_path)
        self.runtime = runtime or ProviderModelRuntime()
        self.structured_runtime = structured_runtime or StructuredOutputRuntime()
        self.feature_engine = feature_engine or FinancialFeatureEngine()
        self.forecast_engine = forecast_engine or FinancialForecastEngine()
        self.citation_resolver = FinancialClaimCitationResolver()
        self.business_store = business_store or BusinessResearchStore(self.store.db_path)
        self._owns_business_store = business_store is None
        self._owns_tdx_store = False
        self.tdx_store = tdx_store
        if self.tdx_store is None:
            # Production keeps both databases in the same runtime directory.
            # Do not create a TDX database for isolated unit-test stores.
            tdx_path = self.store.db_path.parent / "tdx_data.db"
            if tdx_path.exists():
                self.tdx_store = TdxDataStore(tdx_path)
                self._owns_tdx_store = True

    def close(self) -> None:
        self.config_store.close()
        if self._owns_business_store:
            self.business_store.close()
        if self._owns_tdx_store and self.tdx_store is not None:
            self.tdx_store.close()
        self.store.close()

    def _cached_market_context(self, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        """Read current quote/valuation clocks from the published TDX cache.

        The leader ranking is an independent derived snapshot.  It must never
        dictate the freshness of a company's quote, valuation or financial
        history.
        """
        if self.tdx_store is None:
            return {}
        quote_row = self.tdx_store.get_record("quotes", stock_code)
        fundamental_row = self.tdx_store.get_record("fundamentals", stock_code)
        quote = dict((quote_row or {}).get("payload") or {})
        fundamental = dict((fundamental_row or {}).get("payload") or {})
        quote_as_of = _beijing_iso(quote.get("data_as_of") or (quote_row or {}).get("updated_at"))
        valuation_as_of = _beijing_iso((fundamental_row or {}).get("updated_at"))
        # A current cache must not leak into an explicitly historical PIT
        # request.  In that case the dated leader snapshot remains the only
        # eligible valuation input.
        if as_of and _iso_date(quote_as_of) and str(_iso_date(quote_as_of)) > as_of:
            quote_row, quote, quote_as_of = None, {}, None
        if as_of and _iso_date(valuation_as_of) and str(_iso_date(valuation_as_of)) > as_of:
            fundamental_row, fundamental, valuation_as_of = None, {}, None
        return {
            "quote": {
                "as_of": quote_as_of,
                "price": quote.get("price"),
                "previous_close": quote.get("last_close"),
                "source": "通达信实时行情缓存",
            } if quote_row else None,
            "valuation": {
                "as_of": valuation_as_of,
                "pe": fundamental.get("pe_ttm"),
                "pb": fundamental.get("pb_mrq"),
                "dividend_yield": fundamental.get("dividend_yield"),
                "market_cap": fundamental.get("market_cap_100m"),
                "source": "通达信基础财务与估值缓存",
                "limitations": [
                    "仅为当前快照，不代表历史估值分位。",
                    "未接入同行可比估值、DCF 敏感性或目标价格。",
                ],
            } if fundamental_row else None,
            "quote_as_of": quote_as_of,
            "valuation_as_of": valuation_as_of,
            "fundamental_report_date": fundamental.get("report_date"),
        }

    def _cached_business_context(self, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        """Read persisted Business Research, with a TDX main-business fallback."""
        row = self.business_store.latest(stock_code)
        snapshot = dict((row or {}).get("snapshot") or {})
        data_as_of = _beijing_iso(snapshot.get("data_as_of"))
        if row and (not as_of or not _iso_date(data_as_of) or str(_iso_date(data_as_of)) <= as_of):
            analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
            return {
                "status": str((snapshot.get("data_quality") or {}).get("status") or "PARTIAL"),
                "data_as_of": data_as_of,
                "main_business": snapshot.get("main_business"),
                "products": snapshot.get("products") or [],
                "product_note": snapshot.get("product_note"),
                "business_model": snapshot.get("business_model"),
                "business_changes": snapshot.get("business_changes") or [],
                "claims": analysis.get("claims") or [],
                "source": "已保存的公司经营研究快照",
            }
        if self.tdx_store is None:
            return {"status": "UNKNOWN", "main_business": None, "products": [], "source": None}
        fundamental_row = self.tdx_store.get_record("fundamentals", stock_code) or {}
        fundamental = dict(fundamental_row.get("payload") or {})
        fallback_as_of = _beijing_iso(fundamental_row.get("updated_at"))
        if as_of and _iso_date(fallback_as_of) and str(_iso_date(fallback_as_of)) > as_of:
            return {"status": "UNKNOWN", "main_business": None, "products": [], "source": None}
        main_business = str(fundamental.get("main_business") or "").strip()
        return {
            "status": "PARTIAL" if main_business else "UNKNOWN",
            "data_as_of": fallback_as_of,
            "main_business": main_business or None,
            "products": [],
            "product_note": "仅有通达信主营业务原文，尚未形成完整经营研究。" if main_business else None,
            "business_model": None,
            "business_changes": [],
            "claims": [],
            "source": "通达信基础财务缓存 main_business" if main_business else None,
        }

    def _identity(self, stock_code: str, as_of: str | None) -> dict[str, Any]:
        leader = self.store.latest_leader(stock_code, as_of)
        market = self._cached_market_context(stock_code, as_of)
        if not leader:
            security = self._resolve_cached_security(stock_code)
            return {
                "stock_code": stock_code.upper(),
                "stock_name": str((security or {}).get("name") or stock_code.upper()),
                "level1_code": None, "level1_name": None, "level2_code": None,
                "level2_name": None, "level3_code": None, "level3_name": None,
                "leader_rank": None, "leader_score": None, "leader_formula_version": None,
                "leader_as_of": None, "metric_applicability_notes": [],
                "market_quote": market.get("quote"),
                "market_valuation": market.get("valuation"),
                "data_dates": {
                    "quote_as_of": market.get("quote_as_of"),
                    "valuation_as_of": market.get("valuation_as_of"),
                    "fundamental_report_date": market.get("fundamental_report_date"),
                    "leader_as_of": None,
                },
            }
        raw_features = dict(leader.get("raw_features") or {})
        # These are the valuation facts already used by the Level-3 leader
        # score.  Keep their source date with the financial snapshot: a
        # financial discussion may explain current PE/PB, but must never turn
        # that one observation into a fabricated valuation history or target.
        leader_valuation = {
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
        market_valuation = market.get("valuation") or leader_valuation
        return {
            key: leader.get(key) for key in (
                "stock_code", "stock_name", "level1_code", "level1_name", "level2_code", "level2_name",
                "level3_code", "level3_name", "leader_rank", "leader_score", "leader_formula_version",
                "metric_applicability_notes",
            )
        } | {
            "leader_as_of": leader.get("as_of"),
            "market_quote": market.get("quote"),
            "market_valuation": market_valuation,
            "data_dates": {
                "quote_as_of": market.get("quote_as_of"),
                "valuation_as_of": market.get("valuation_as_of") or leader.get("as_of"),
                "fundamental_report_date": market.get("fundamental_report_date"),
                "leader_as_of": leader.get("as_of"),
            },
        }

    @staticmethod
    def _resolve_cached_security(question: str, entity: str = "") -> dict[str, Any] | None:
        """Resolve an explicit company against the full cached A-share master.

        The Level-3 Top-2 pool is a ranking view, not a security whitelist.  A
        company question therefore resolves through the complete TDX security
        cache before any leader-pool data is considered.
        """
        text = question.strip()
        code_match = re.search(
            r"(?<!\d)(\d{6})(?:\.(SH|SZ|BJ))?(?!\d)", text, re.IGNORECASE,
        )
        queries: list[str] = []
        if code_match:
            queries.append(code_match.group(1))
        cleaned_entity = entity.strip()
        if cleaned_entity and cleaned_entity not in queries:
            queries.append(cleaned_entity)
        for query in queries:
            matches = _search_tdx_securities(query, 20)
            if code_match:
                digits = code_match.group(1)
                suffix = str(code_match.group(2) or "").upper()
                expected = f"{digits}.{suffix}" if suffix else ""
                exact = next((item for item in matches if str(item.get("code") or "").upper() == expected), None)
                if exact:
                    return exact
                bare = next(
                    (item for item in matches if str(item.get("code") or "").upper().split(".")[0] == digits),
                    None,
                )
                if bare:
                    return bare
            if cleaned_entity:
                exact_name = next(
                    (item for item in matches if str(item.get("name") or "").strip() == cleaned_entity),
                    None,
                )
                if exact_name:
                    return exact_name
                named = [item for item in matches if str(item.get("name") or "") in text]
                if named:
                    return max(named, key=lambda item: len(str(item.get("name") or "")))
        # No code and no router entity: still allow deterministic name-only
        # questions ("同庆楼的财务表现怎么样") to resolve without the model
        # router.  Only names of 3+ characters count, so generic two-character
        # words can never match by accident.
        from src.tdx_data.service import get_tdx_service

        return get_tdx_service().find_security_named_in(text)

    @classmethod
    def _resolve_history_security(cls, history: list[dict[str, str]] | None) -> dict[str, Any] | None:
        """Reuse only a company explicitly selected in an earlier user turn."""
        for item in reversed(history or []):
            if str(item.get("role") or "") != "user":
                continue
            stock_code = str(item.get("stock_code") or "").strip()
            stock_name = str(item.get("stock_name") or "").strip()
            content = str(item.get("content") or "").strip()
            if stock_code and (security := cls._resolve_cached_security(stock_code, stock_name)):
                return security
            if re.search(r"(?<!\d)\d{6}(?:\.(?:SH|SZ|BJ))?(?!\d)", content, re.IGNORECASE):
                if security := cls._resolve_cached_security(content):
                    return security
        return None

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

    def _source_fingerprint(self, symbol: str, identity: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        """Input fingerprint shared by prepare() and freshness classification."""
        fingerprint_identity = dict(identity)
        fingerprint_dates = dict(fingerprint_identity.get("data_dates") or {})
        fingerprint_dates.pop("analysis_as_of", None)
        fingerprint_identity["data_dates"] = fingerprint_dates
        return stable_fingerprint({
            "stock_code": symbol, "identity": fingerprint_identity,
            "history": rows, "feature_version": FINANCIAL_FEATURE_VERSION,
            "forecast_version": FORECAST_VERSION,
            "flow_aggregation": "sum-four-tdx-single-periods-v1",
        })

    def input_fingerprint(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        """Recompute the current input fingerprint without any write.

        ResearchFreshnessService compares this against the persisted
        snapshot's ``source_hash`` to classify freshness (plan §7/§9).
        """
        try:
            symbol = stock_code.upper()
            cutoff = as_of or date.today().isoformat()
            identity = self._identity(symbol, as_of)
            rows = list((self.history.query(symbol, as_of=cutoff) or {}).get("items") or [])
            return {"source_hash": self._source_fingerprint(symbol, identity, rows), "as_of": cutoff}
        except Exception:  # noqa: BLE001 - classification must degrade, not fail
            return None

    def prepare(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        symbol = stock_code.upper()
        identity = self._identity(symbol, as_of)
        # ``as_of`` is the point-in-time visibility cutoff for financial
        # reports.  A stale leader run must not roll this clock backwards.
        cutoff = as_of or date.today().isoformat()
        package = self.history.query(symbol, as_of=cutoff)
        rows = list(package.get("items") or [])
        data_dates = dict(identity.get("data_dates") or {})
        visible_reports = [row for row in rows if row.get("report_date")]
        visible_announcements = [row for row in rows if row.get("announcement_date")]
        data_dates.update({
            "analysis_as_of": cutoff,
            "financial_report_date": max((str(row["report_date"]) for row in visible_reports), default=None),
            "financial_announcement_date": max(
                (str(row["announcement_date"]) for row in visible_announcements), default=None,
            ),
        })
        identity["data_dates"] = data_dates
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
        # research-cache plan §20.1: the input fingerprint must NOT contain the
        # research clock.  ``cutoff`` (and identity.data_dates.analysis_as_of,
        # which mirrors it) is a PIT visibility label, not an input — visible
        # rows already carry the real change signal.  Otherwise every new day
        # mints a fresh snapshot even when nothing changed.
        source_hash = self._source_fingerprint(symbol, identity, rows)
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
        # Preparing is local and idempotent.  Recompute the input fingerprint
        # so a newer TDX quote/fundamental cache invalidates an older company
        # snapshot instead of reusing it forever.
        return self.prepare(stock_code, as_of=as_of)

    def get_saved_resolved_analysis(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Read the newest saved financial snapshot without preparing research."""
        snapshot = self.store.latest(stock_code.upper(), as_of=as_of)
        return self.resolve_citations(snapshot) if snapshot else {}

    def resolve_citations(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Decorate an API response with deterministic Claim citations only."""
        return self.citation_resolver.resolve_snapshot(
            snapshot,
            fallback_manifest=self._evidence_manifest(snapshot),
        )

    def get_resolved_analysis(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        return self.resolve_citations(self.get(stock_code, as_of=as_of))

    def _refresh_history(self, stock_code: str) -> str | None:
        try:
            self.history.collect_incremental([stock_code.upper()])
            return None
        except Exception as exc:  # Python analysis must remain available from cached PIT data.
            return f"refresh:{type(exc).__name__}:{exc}"

    @staticmethod
    def _manifest_period(report_date: Any) -> str:
        text = str(report_date or "").strip()
        if text.endswith("-12-31"):
            return text[:4]
        if text.endswith("-06-30"):
            return f"{text[:4]}H1"
        if text.endswith("-03-31"):
            return f"{text[:4]}Q1"
        if text.endswith("-09-30"):
            return f"{text[:4]}Q3"
        return re.sub(r"[^A-Za-z0-9]", "", text) or "UNKNOWN"

    @classmethod
    def _evidence_manifest(cls, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Build stable, reconstructible citation keys from deterministic inputs only."""
        manifest: dict[str, dict[str, Any]] = {}
        base = {
            "source_snapshot_id": snapshot["id"], "source_hash": snapshot["source_hash"],
            "data_as_of": snapshot["as_of"],
        }
        history_metrics = {
            "revenue": ("REVENUE", "CNY"), "net_profit": ("NET_PROFIT", "CNY"),
            "operating_cash_flow": ("OCF", "CNY"), "roe": ("ROE", "percent"),
            "debt_ratio": ("DEBT_RATIO", "percent"), "gross_margin": ("GROSS_MARGIN", "percent"),
            "net_margin": ("NET_MARGIN", "percent"), "capex": ("CAPEX", "CNY"),
            "accounts_receivable": ("ACCOUNTS_RECEIVABLE", "CNY"),
            "inventory": ("INVENTORY", "CNY"),
            "cash_and_equivalents": ("CASH_AND_EQUIVALENTS", "CNY"),
            "current_assets": ("CURRENT_ASSETS", "CNY"),
            "current_liabilities": ("CURRENT_LIABILITIES", "CNY"),
            "non_current_liabilities": ("NON_CURRENT_LIABILITIES", "CNY"),
            "interest_bearing_debt_ratio": ("INTEREST_BEARING_DEBT_RATIO", "percent"),
        }
        for row in snapshot.get("history") or []:
            period = cls._manifest_period(row.get("report_date"))
            for field, (label, unit) in history_metrics.items():
                value = row.get(field)
                if _numeric(value) is None:
                    continue
                manifest[f"FIN_{label}_{period}"] = {
                    **base, "metric": field, "period": period, "value": value, "unit": unit,
                    "source_type": "PIT_FINANCIAL_HISTORY", "source": row.get("source"),
                    "report_date": row.get("report_date"), "announcement_date": row.get("announcement_date"),
                }
        for change in (snapshot.get("feature", {}).get("latest_changes") or []):
            if not isinstance(change, dict) or _numeric(change.get("change_percent")) is None:
                continue
            metric = str(change.get("metric") or "").upper()
            period = cls._manifest_period(change.get("report_date"))
            manifest[f"FEATURE_{metric}_CHANGE_{period}"] = {
                **base, "metric": f"{str(change.get('metric') or '')}_change_percent", "period": period,
                "value": change.get("change_percent"), "unit": "percent", "source_type": "FINANCIAL_FEATURE",
                "feature_key": "latest_changes", "report_date": change.get("report_date"),
            }
        forecast = snapshot.get("forecast") or {}
        for scenario, result in (forecast.get("scenarios") or {}).items():
            for row in result.get("forecast") or []:
                year = str(row.get("year") or "").replace("E", "")
                for field, label in (("revenue", "REVENUE"), ("net_profit", "NET_PROFIT")):
                    value = row.get(field)
                    if _numeric(value) is None:
                        continue
                    manifest[f"FORECAST_{scenario}_{label}_{year}"] = {
                        **base, "metric": field, "period": year, "value": value, "unit": "CNY",
                        "source_type": "DETERMINISTIC_FORECAST", "scenario": scenario,
                        "forecast_year": row.get("year"), "forecast_version": forecast.get("forecast_version"),
                    }
        return dict(sorted(manifest.items()))

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
            raise ValueError("claims response must be a JSON object")
        return parsed

    @staticmethod
    def _claim_instruction() -> str:
        return (
            "你是财报研究员。只基于 Evidence Manifest 输出 JSON，且只能有 summary 与 claims 两个字段。"
            "summary 为简洁研究摘要。claims 最多 8 条，宁可少写也不要猜测。"
            "每条 claim 必须且只能有 type、text、source_keys、confidence。"
            "type 仅允许 FACT、INFERENCE、FORECAST、UNKNOWN；confidence 仅 LOW/MEDIUM/HIGH。"
            "FACT、INFERENCE、FORECAST 的 source_keys 必须非空且全部来自 Manifest。"
            "FORECAST 只能引用以 FORECAST_ 开头的 key，且 text 必须明确是情景/预测；"
            "FACT 不得引用 FORECAST_ key。UNKNOWN 可用空 source_keys。"
            "数值必须保持 Manifest 的正负号和量纲；金额若换算为亿元/万元，数字后必须明确单位，"
            "不得省略负号。不得产生 Manifest 外的新具体数值，禁止买卖、目标价、仓位、止损或加减仓。"
            "只返回 JSON，不要 Markdown 或额外字段。"
        )

    @staticmethod
    def claims_contract_schema(manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Business contract; provider adapters may relax it but never replace it."""
        source_key = {"type": "string", "enum": sorted(manifest)}
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "maxLength": 600},
                "claims": {
                    "type": "array", "maxItems": MAX_CLAIMS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": sorted(CLAIM_TYPES)},
                            "text": {"type": "string", "maxLength": 240},
                            "source_keys": {"type": "array", "items": source_key, "maxItems": 6},
                            "confidence": {"type": "string", "enum": sorted(CLAIM_CONFIDENCES)},
                        },
                        "required": ["type", "text", "source_keys", "confidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["summary", "claims"], "additionalProperties": False,
        }

    @staticmethod
    def validate_claims(result: dict[str, dict[str, Any]] | dict[str, Any], manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Per-claim validation: one violating claim rejects itself, never the batch.

        Top-level structure (schema shape, summary presence, claim-count
        ceiling, trading language in the single summary field) remains a
        whole-result refusal.  Every claim-level rule — schema, type,
        confidence, source existence, FACT/FORECAST source discipline, numeric
        evidence, trading language in the claim text — is enforced
        independently per claim; rejected claims are returned as diagnostics
        alongside the accepted subset (2026-09-03 reliability fix).
        """
        if set(result) != {"summary", "claims"}:
            raise ClaimValidationError("TOP_LEVEL_SCHEMA_INVALID", "claims schema must contain only summary and claims")
        summary = str(result.get("summary") or "").strip()
        claims = result.get("claims")
        if not summary:
            raise ClaimValidationError("TOP_LEVEL_SCHEMA_INVALID", "claims summary is required")
        if not isinstance(claims, list):
            raise ClaimValidationError("TOP_LEVEL_SCHEMA_INVALID", "claims must be a list")
        if len(claims) > MAX_CLAIMS:
            raise ClaimValidationError("TOO_MANY_CLAIMS", f"claims exceeds max_claims={MAX_CLAIMS}")
        if PROHIBITED_ACTIONS.search(summary):
            raise ClaimValidationError("TRADING_LANGUAGE", "claims summary contains prohibited trading action")
        # Retain the existing small natural-language counters, while concrete
        # numbers must be supported by the claim's own cited Evidence.
        common_counters = {"1", "2", "3", "5", "8"}
        normalized: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for index, raw in enumerate(claims):
            try:
                normalized_claim = FinancialAnalysisService._validate_single_claim(
                    raw, index, manifest, common_counters,
                )
            except ClaimValidationError as exc:
                details = exc.audit_dict() if hasattr(exc, "audit_dict") else {}
                rejected.append({
                    "claim_index": index,
                    "type": str(raw.get("type")) if isinstance(raw, dict) else None,
                    "reason_code": str(details.get("validation_error_code") or "CLAIM_REJECTED"),
                    "detail": str(details.get("error_summary") or details.get("detail") or ""),
                })
                continue
            normalized.append(normalized_claim)
        return {"summary": summary, "claims": normalized, "rejected_claims": rejected}

    @staticmethod
    def _validate_single_claim(raw: Any, index: int, manifest: dict[str, dict[str, Any]],
                               common_counters: set[str]) -> dict[str, Any]:
        """Enforce every claim-level rule for one claim; raise to reject only it."""
        if not isinstance(raw, dict) or set(raw) != {"type", "text", "source_keys", "confidence"}:
            raise ClaimValidationError("TOP_LEVEL_SCHEMA_INVALID", "invalid claim schema", claim_index=index)
        claim_type = str(raw.get("type") or "").upper()
        text = str(raw.get("text") or "").strip()
        confidence = str(raw.get("confidence") or "").upper()
        keys = raw.get("source_keys")
        if claim_type not in CLAIM_TYPES:
            raise ClaimValidationError("INVALID_CLAIM_TYPE", "invalid claim type", claim_index=index)
        if confidence not in CLAIM_CONFIDENCES:
            raise ClaimValidationError("INVALID_CONFIDENCE", "invalid claim confidence", claim_index=index)
        if not text:
            raise ClaimValidationError("EMPTY_CLAIM_TEXT", "claim text is required", claim_index=index)
        if PROHIBITED_ACTIONS.search(text):
            raise ClaimValidationError("TRADING_LANGUAGE", "claim contains prohibited trading action", claim_index=index)
        if not isinstance(keys, list) or any(not isinstance(key, str) or not key.strip() for key in keys):
            raise ClaimValidationError("UNKNOWN_SOURCE_KEY", "claim source_keys must be a string array", claim_index=index)
        keys = list(dict.fromkeys(key.strip() for key in keys))
        if claim_type != "UNKNOWN" and not keys:
            codes = {"FACT": "FACT_WITHOUT_SOURCE", "INFERENCE": "INFERENCE_WITHOUT_SOURCE", "FORECAST": "FORECAST_WITHOUT_SOURCE"}
            raise ClaimValidationError(codes[claim_type], f"{claim_type} requires source_keys", claim_index=index)
        missing = sorted(set(keys) - set(manifest))
        if missing:
            raise ClaimValidationError("UNKNOWN_SOURCE_KEY", "claim references unknown source keys", claim_index=index, source_keys=missing)
        forecast_keys = [key for key in keys if key.startswith("FORECAST_")]
        if claim_type == "FACT" and forecast_keys:
            raise ClaimValidationError("FACT_USING_FORECAST_SOURCE", "FACT cannot reference forecast source keys", claim_index=index, source_keys=forecast_keys)
        if claim_type == "FORECAST" and (not keys or len(forecast_keys) != len(keys)):
            raise ClaimValidationError("FORECAST_USING_NON_FORECAST_SOURCE", "FORECAST must reference forecast source keys only", claim_index=index, source_keys=keys)
        if claim_type == "FORECAST" and not re.search(r"情景|预测|推演|forecast", text, re.I):
            raise ClaimValidationError("FORECAST_USING_NON_FORECAST_SOURCE", "FORECAST text must identify a scenario or forecast", claim_index=index, source_keys=keys)
        source_entries = [manifest[key] for key in keys]
        allowed_numbers = _claim_numeric_values(source_entries) | common_counters
        invented = sorted(
            match.group(0)
            for match in _NUMERIC_TOKEN.finditer(text)
            if not _numeric_is_supported(
                token=match.group(0), token_start=match.start(), token_end=match.end(), text=text,
                source_entries=source_entries, allowed_tokens=allowed_numbers,
            )
        )
        if invented:
            raise ClaimValidationError("NUMERIC_MISMATCH", "claim contains numbers absent from manifest", claim_index=index,
                                       source_keys=keys, metadata={"numeric_tokens": invented})
        return {"type": claim_type, "text": text, "source_keys": keys, "confidence": confidence}

    @staticmethod
    def _summary_only_claims(text: str | None) -> dict[str, Any]:
        """Adapt the capability runtime's TEXT_ONLY result without creating Evidence."""
        text = (text or "").strip()
        if not text:
            text = "模型未能生成可验证 Claims；请以确定性财务快照和数据缺口为准。"
        sanitized = re.sub(r"\d+(?:\.\d+)?", "相关", text)
        return {"summary": f"[摘要模式] {sanitized}", "claims": []}

    @staticmethod
    def _compatibility_analysis(snapshot: dict[str, Any], result: dict[str, Any], manifest: dict[str, dict[str, Any]],
                                *, quality_status: str, fallback_path: str,
                                fallback_failure_types: list[dict[str, str]] | None = None,
                                structured_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Adapt compact claims to the legacy frontend analysis shape without asking the LLM for it."""
        claims = result["claims"]
        feature, forecast = snapshot.get("feature") or {}, snapshot.get("forecast") or {}
        trends = feature.get("trends") or {}
        counts = {kind: sum(item["type"] == kind for item in claims) for kind in CLAIM_TYPES}
        evidence_ready = bool(counts["FACT"] or counts["INFERENCE"] or counts["FORECAST"])
        confidence_order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        confidence = max((item["confidence"] for item in claims), key=lambda value: confidence_order[value], default="LOW")
        legacy_claims = [{
            **claim, "statement": claim["text"], "evidence_keys": claim["source_keys"],
        } for claim in claims]
        fact_text = [item["text"] for item in claims if item["type"] == "FACT"]
        unknown_text = [item["text"] for item in claims if item["type"] == "UNKNOWN"]
        return {
            "stock_code": snapshot["stock_code"], "stock_name": snapshot["stock_name"],
            "executive_summary": result["summary"],
            "historical_performance": {
                "growth": str(trends.get("growth_trend") or "待核实"),
                "profitability": str(trends.get("profitability_trend") or "待核实"),
                "cash_flow": str(trends.get("cash_flow_trend") or "待核实"),
                "balance_sheet": str(trends.get("balance_sheet_trend") or "待核实"),
            },
            "latest_changes": fact_text[:4] or ["暂无可验证的事实 Claim"],
            "financial_strengths": [item["text"] for item in claims if item["type"] == "INFERENCE"][:4] or ["需结合结构化 Claims 复核"],
            "financial_risks": unknown_text[:4] or (["暂无模型声明的数据未知项"] if claims else ["未生成可验证 Claims，不能作为 Evidence。"]),
            "forecast_analysis": {
                "bear": "详见确定性 Forecast 快照", "base": "详见确定性 Forecast 快照",
                "bull": "详见确定性 Forecast 快照", "key_assumptions": list(forecast.get("assumption_notes") or [])[:4],
            },
            "key_metrics_to_monitor": ["营收增长", "净利润", "经营现金流", "ROE", "资产负债率"],
            "confidence": confidence, "data_gaps": list(snapshot.get("data_gaps") or []), "claims": legacy_claims,
            "analysis_metadata": {
                "prompt_version": FINANCIAL_CLAIMS_PROMPT_VERSION, "analysis_quality_status": quality_status,
                "fallback_path": fallback_path, "evidence_ready": evidence_ready,
                # Keep only exception classes: useful for a production audit
                # without persisting model responses, prompts, or credentials.
                "fallback_failure_types": list(fallback_failure_types or []),
                **dict(structured_metadata or {}),
                "claims_count": len(claims), "fact_count": counts["FACT"], "inference_count": counts["INFERENCE"],
                "forecast_count": counts["FORECAST"], "unknown_count": counts["UNKNOWN"],
                "evidence_manifest": manifest,
            },
        }

    def analyze(self, stock_code: str, *, as_of: str | None = None,
                refresh: bool = True, force: bool = False) -> dict[str, Any]:
        refresh_error = self._refresh_history(stock_code) if refresh else None
        snapshot = self.prepare(stock_code, as_of=as_of)
        if refresh_error and refresh_error not in snapshot["data_gaps"]:
            snapshot["data_gaps"].append(refresh_error)
        if snapshot["analysis_status"] in {"COMPLETED", "PARTIAL"} and not force:
            # COMPLETED and PARTIAL (SUMMARY_ONLY / all claims rejected) are
            # both terminal for one source fingerprint: re-analysis requires
            # explicit force (manual repair) or a changed fingerprint/formula
            # (which prepares a different snapshot).  Background callers can
            # never auto-upgrade a claim-less result (2026-09-03 fix).
            if snapshot["analysis_status"] == "PARTIAL":
                return {**snapshot, "idempotent_reuse": True}
            # research-cache plan §20.2: a completed narrative is reusable only
            # under the prompt/model contract that produced it.
            stored = dict(snapshot.get("analysis") or {})
            metadata = dict(stored.get("analysis_metadata") or {})
            stored_prompt = str(metadata.get("prompt_version") or "")
            stored_model = str(snapshot.get("agent_model") or "")
            _config, _configured = self._agent_config()
            if (stored_prompt == FINANCIAL_CLAIMS_PROMPT_VERSION
                    and (not _configured or stored_model == str(_config.get("model") or ""))):
                return {**snapshot, "idempotent_reuse": True}
        config, configured = self._agent_config()
        if not configured:
            return self.store.update_agent_result(
                snapshot["id"], status="CONFIGURATION_REQUIRED", provider=config["provider"], model=config["model"],
                error="Financial Analyst model is disabled or provider credentials are unavailable",
            )
        manifest = self._evidence_manifest(snapshot)
        payload = {
            "company_identity": snapshot["identity"],
            "evidence_manifest": manifest, "prompt_version": FINANCIAL_CLAIMS_PROMPT_VERSION,
            "max_claims": MAX_CLAIMS,
        }
        instruction = self._claim_instruction()
        capabilities = resolve_structured_output_capabilities(config)
        contract_schema = self.claims_contract_schema(manifest)

        def invoke_once(mode: StructuredOutputMode, response_format: dict[str, Any] | None) -> dict[str, Any]:
            connection_invoke = getattr(self.runtime, "invoke_with_connection", None)
            if config.get("base_url") and callable(connection_invoke):
                return connection_invoke(
                    role="financial_analyst", phase="FINANCIAL_ANALYSIS", model=config["model"],
                    base_url=config["base_url"], api_key=config.get("api_key") or "", instruction=instruction,
                    payload=payload, target_schema=response_format,
                )
            return self.runtime.invoke(
                role="financial_analyst", phase="FINANCIAL_ANALYSIS", provider=config["provider"],
                model=config["model"], instruction=instruction, payload=payload, target_schema=response_format,
            )

        def invoke_structured(mode: StructuredOutputMode, response_format: dict[str, Any] | None) -> dict[str, Any]:
            # Transport-transient errors (timeout / connection / 5xx / 429)
            # get exactly one extra attempt per logical analysis.  Validation
            # failures never reach this layer and are never retried.
            try:
                return invoke_once(mode, response_format)
            except Exception as exc:
                if mode is not StructuredOutputMode.TEXT_ONLY and self._transient_transport(exc):
                    return invoke_once(mode, response_format)
                raise

        try:
            outcome = self.structured_runtime.run(
                config=config, instruction=instruction, payload=payload, contract_schema=contract_schema,
                capabilities=capabilities,
                text_instruction="根据确定性财务摘要写一段不含数字、没有交易建议的研究摘要。只返回纯文本。",
                text_payload={"identity": snapshot.get("identity"), "trends": snapshot.get("feature", {}).get("trends") or {}},
                invoke_structured=invoke_structured,
                validate=lambda output: self.validate_claims(output, manifest),
            )
            structured_metadata = {
                "structured_output_mode_requested": outcome.mode_requested,
                "structured_output_mode_used": outcome.mode_used,
                "capability_profile": outcome.capability_profile,
                "capability_source": outcome.capability_source,
                "structured_attempts": outcome.attempts,
                "error_types": outcome.error_types,
                "provider": config["provider"], "model": config["model"],
            }
            rejected_claims: list[dict[str, Any]] = []
            if outcome.parsed is not None:
                claims_result = outcome.parsed
                rejected_claims = list(claims_result.get("rejected_claims") or [])
                quality_status, fallback_path = "STRUCTURED", "structured"
                if not claims_result.get("claims"):
                    # Every claim was rejected per-claim: keep the model's own
                    # summary, expose diagnostics, and mark PARTIAL — a
                    # claim-less result must never read as deep-complete.
                    quality_status, fallback_path = "SUMMARY_ONLY", "all_claims_rejected"
            elif str(outcome.text or "").strip():
                structured_metadata["structured_output_mode_used"] = StructuredOutputMode.TEXT_ONLY.value
                claims_result = self._summary_only_claims(outcome.text)
                quality_status, fallback_path = "SUMMARY_ONLY", "summary_only"
            else:
                # Structured parse failed and even the TEXT_ONLY fallback
                # produced nothing — a technical transport failure, not a
                # content verdict (2026-09-03 fix: FAILED, not placeholder).
                return self.store.update_agent_result(
                    snapshot["id"], status="FAILED", provider=config["provider"], model=config["model"],
                    error="FINANCIAL_ANALYSIS_TRANSPORT_FAILED: structured and text modes produced no output",
                )
            claims_status = "CLAIMS_READY" if quality_status == "STRUCTURED" and claims_result.get("claims") else "SUMMARY_ONLY"
            final_status = "COMPLETED" if claims_status == "CLAIMS_READY" else "PARTIAL"
            analysis = self._compatibility_analysis(snapshot, claims_result, manifest,
                                                    quality_status=quality_status, fallback_path=fallback_path,
                                                    fallback_failure_types=structured_metadata["error_types"],
                                                    structured_metadata=structured_metadata)
            analysis["claims_status"] = claims_status
            analysis["rejected_claims"] = rejected_claims
            return self.store.update_agent_result(
                snapshot["id"], status=final_status, provider=config["provider"], model=config["model"], analysis=analysis,
            )
        except Exception as exc:
            return self.store.update_agent_result(
                snapshot["id"], status="FAILED", provider=config["provider"], model=config["model"],
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _transient_transport(exc: Exception) -> bool:
        """Classify clear transport-transient failures (retry-once eligible)."""
        name = type(exc).__name__
        if name in {"APITimeoutError", "APIConnectionError", "TimeoutError", "ConnectionError",
                    "ReadTimeout", "RemoteDisconnected", "ConnectTimeout"}:
            return True
        if getattr(exc, "retryable", None) is True:
            return True
        status = getattr(exc, "status_code", None)
        return bool(status) and (status in (408, 429) or status >= 500)

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
        business_context = self._cached_business_context(snapshot["stock_code"], as_of)
        data_dates = dict(snapshot.get("identity", {}).get("data_dates") or {})
        report_date = data_dates.get("financial_report_date") or "暂无"
        valuation_date = data_dates.get("valuation_as_of") or "暂无"
        quote_date = data_dates.get("quote_as_of") or "暂无"
        _progress(
            progress,
            "financial_snapshot_loaded",
            (
                f"已读取 {snapshot['stock_name']} 财务快照（财报 {report_date}；"
                f"估值 {valuation_date}；行情 {quote_date}）"
            ),
            stock_code=snapshot["stock_code"],
            stock_name=snapshot["stock_name"],
            as_of=snapshot["as_of"],
            data_dates=data_dates,
            data_gap_count=len(snapshot.get("data_gaps") or []),
        )
        if business_context.get("status") != "UNKNOWN":
            _progress(
                progress,
                "business_snapshot_loaded",
                "已读取公司主营业务与经营资料",
                stock_code=snapshot["stock_code"],
                business_status=business_context.get("status"),
                business_as_of=business_context.get("data_as_of"),
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
        answer_mode = classify_financial_answer_mode(question)
        history_source = archived_history or (history or [])
        # A new question starts from the current snapshot. Only an explicit
        # follow-up may see the immediately preceding user/assistant pair.
        model_history = history_source[-2:] if FOLLOW_UP_QUESTION.search(question) else []
        token_budget = FINANCIAL_CHAT_TOKEN_BUDGETS[answer_mode]
        config, configured = self._agent_config()
        if not configured:
            raise RuntimeError("Financial Analyst model is disabled or provider credentials are unavailable")
        context = {
            "company_identity": snapshot.get("identity"),
            "historical_cutoff": snapshot.get("historical_cutoff"),
            "data_dates": data_dates,
            "financial_feature_snapshot": snapshot.get("feature"),
            "forecast_snapshot": snapshot.get("forecast"),
            "market_valuation_snapshot": dict(snapshot.get("identity") or {}).get("market_valuation") or {
                "status": "unavailable",
                "reason": "当前财务快照生成时尚未写入估值快照；重新预建后可用。",
            },
            "business_research_snapshot": business_context,
            "data_gaps": snapshot.get("data_gaps") or [],
            "capability_manifest": {
                "supported": [
                    "营收、净利润及 CAGR", "ROE、毛利率、净利率", "经营现金流与现金转换率",
                    "资产、净资产与负债率", "资本开支", "多年度趋势", "最新财务变化",
                    "未来三年 Bear/Base/Bull 营收与净利润推演",
                    "当前 PE、PB、股息率、市值快照", "数据覆盖率、数据缺口与证据追踪",
                    "通达信主营业务，以及已保存的产品、商业模式和经营变化研究",
                ],
                "not_fully_integrated": list(UNAVAILABLE_METRICS),
            },
        }
        instruction = (
            "你是价值投资研究工作台的财报与基本面研究员。只基于给定的公司经营、财务和估值快照回答，"
            "输出应是可复核的公司研究说明，而不是泛泛摘要。\n"
            f"回答模式：{answer_mode}。{_financial_answer_contract(answer_mode)}\n"
            "若 business_research_snapshot 有有效主营业务，必须先说明公司做什么，再解释财务变化；"
            "商业模式、产品结构和经营变化只能使用该快照中的已有内容，缺失时明确说明，不能从行业名称或财务数字猜测。"
            "不得输出 UNKNOWN、PARTIAL、READY 等内部状态码，也不得暴露 JSON 字段名、快照键或内部实现。"
            "缺失的经营资料只在直接影响当前判断时用一句自然中文说明，不得逐项罗列未接入能力。"
            "即使标记为【推断】，也不得从行业归属推断公司商业模式、客户结构或细分产品。"
            "公司画像只能使用 company_identity 中的行业层级和 business_research_snapshot 中的主营、产品、商业模式；"
            "不得用行业常识补写 IDM、Fabless、下游应用或具体产品。财务周期只描述公司数据周期，"
            "没有行业数据时不得把公司变化归因于行业供需、价格或政策。"
            "数值项目本身默认为可复核事实，不要在每句后反复堆叠【事实】。只对解释性结论标记【判断】，对关键缺口标记【待核实】。"
            "排版使用短段落、粗体关键数值、项目符号和必要的分隔线；可使用✅表示已被数据支持的优势、⚠️表示需复核的风险，不使用过多表情。"
            "Markdown 表格必须使用标准管道语法，包含表头、|---|分隔行和完整数据行；只有三个以上项目需要横向对比时才使用表格。"
            "不要为了显得专业而增加没有信息量的层级、评分或口号。"
            "所有数值、年份、百分比必须逐字来自给定快照；不得自行计算、外推或补造任何数字。"
            "不得把 PE 通俗化为‘需要多少年回本’；只说明当前盈利对应的估值倍数和预期压力。"
            "估值章节不得自行设定净利率、PE倍数、概率、目标市值或价格，也不得新算快照中没有的隐含数值。"
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
        def make_client(max_tokens: int) -> ChatLLM:
            # Financial chat explains a deterministic snapshot. On Volcengine
            # Ark, hidden reasoning and visible text share one output budget;
            # disabling deep thinking prevents the reasoning trace from using
            # the entire allowance before the reviewable answer is produced.
            extra_body = (
                {"thinking": {"type": "disabled"}}
                if "volces.com" in str(config.get("base_url") or "").lower()
                and str(config.get("model") or "").lower().startswith("deepseek")
                else None
            )
            if config.get("base_url"):
                return ChatLLM(
                    model_name=config["model"], provider_name="openai",
                    base_url=config["base_url"], api_key=config.get("api_key") or "",
                    timeout_seconds=FINANCIAL_CHAT_TIMEOUT_SECONDS,
                    max_retries=0, max_tokens=max_tokens,
                    **({"extra_body": extra_body} if extra_body else {}),
                )
            return ChatLLM(
                model_name=config["model"], provider_name=config["provider"],
                timeout_seconds=FINANCIAL_CHAT_TIMEOUT_SECONDS,
                max_retries=0, max_tokens=max_tokens,
                **({"extra_body": extra_body} if extra_body else {}),
            )

        client = make_client(token_budget)
        _progress(
            progress,
            "model_analysis",
            f"正在使用 {config['model']} 解释公司业务、财务事实与风险，完整性校验通过后输出正文",
            model=config["model"],
        )
        streamed_parts: list[str] = []

        def on_text_chunk(chunk: str) -> None:
            if not chunk:
                return
            # Keep the draft private until the provider confirms a normal
            # completion. A reasoning model can emit a few visible words and
            # then stop with finish_reason=length; forwarding those chunks
            # would leave an unrecoverable half-answer in the Feishu card.
            streamed_parts.append(chunk)

        reasoning_last_report = 0.0

        def on_reasoning_chunk(_chunk: str) -> None:
            nonlocal reasoning_last_report
            now = time.monotonic()
            if reasoning_last_report and now - reasoning_last_report < 12:
                return
            reasoning_last_report = now
            _progress(
                progress,
                "model_reasoning_progress",
                "模型正在核对财务事实与结论边界",
                stock_code=snapshot["stock_code"],
            )

        answer_status = "complete"
        stream_interrupted = False
        try:
            stream_chat = getattr(client, "stream_chat", None)
            if progress is not None and callable(stream_chat):
                response = stream_chat(
                    messages,
                    on_text_chunk=on_text_chunk,
                    on_reasoning_chunk=on_reasoning_chunk,
                    timeout=FINANCIAL_CHAT_TIMEOUT_SECONDS,
                )
            else:
                response = client.chat(messages)
        except ProviderStreamError:
            # Do not publish or save a partial stream. It may end in the middle
            # of a sentence and is not a reviewable research answer.
            answer = "".join(streamed_parts).strip()
            finish_reason = "stream_interrupted"
            stream_interrupted = True
        else:
            finish_reason = str(getattr(response, "finish_reason", "stop") or "stop")
            answer = str(getattr(response, "content", "") or "").strip()
            if not answer and streamed_parts:
                answer = "".join(streamed_parts).strip()

        # A length-limited response is incomplete even when it contains a few
        # visible words. Buffer the primary stream and retry before publishing
        # anything, so Feishu never receives a broken half-sentence.
        maximum_complete_chars = {
            "focused": 1_400,
            "overview": 4_000,
            "full": 8_000,
            "continuation": 1_800,
        }[answer_mode]
        oversized_answer = len(answer) > maximum_complete_chars
        unsupported_business = _unsupported_business_terms(
            answer,
            question=question,
            business_context=business_context,
            identity=dict(snapshot.get("identity") or {}),
        )
        needs_retry = (
            not answer or finish_reason == "length" or stream_interrupted
            or oversized_answer or bool(unsupported_business)
        )
        if needs_retry:
            retry_reason = (
                "正文被长度限制截断" if finish_reason == "length"
                else "模型连接中断" if stream_interrupted
                else "正文信息过多" if oversized_answer
                else "正文含有未被业务快照支持的经营断言" if unsupported_business
                else "模型未生成正文"
            )
            _progress(
                progress,
                "model_answer_retry",
                f"{retry_reason}，正在自动生成完整的精简回答",
                finish_reason=finish_reason,
                initial_token_budget=token_budget,
                discarded_partial_chars=len(answer),
            )
            preferred_chars = {
                "focused": (900, 650),
                "overview": (3_200, 2_400),
                "full": (6_800, 5_200),
                "continuation": (1100, 800),
            }[answer_mode]
            retry_specs = (
                (FINANCIAL_CHAT_RETRY_MAX_TOKENS, preferred_chars[0]),
                (FINANCIAL_CHAT_FINAL_RETRY_MAX_TOKENS, preferred_chars[1]),
            )
            answer = ""
            for retry_index, (retry_budget, max_chars) in enumerate(retry_specs, 1):
                retry_client = make_client(max(token_budget, retry_budget))
                retry_instruction = (
                    instruction
                    + f"\n前一轮没有形成完整正文。本次必须直接输出一份从头开始、可独立阅读的最终答案，"
                    f"不得复述任务、不得展示思考过程，最多{max_chars}个汉字。"
                    "必须保留回答模式规定的 Markdown 标题、空行和项目符号，不得压成一个大段落。"
                    "必须自然结束，不能只写标题或半句话；即使数据不足也要写出可回答部分和缺失项。"
                )
                if unsupported_business:
                    retry_instruction += (
                        "\n上一轮出现了业务快照未提供的具体经营词："
                        + "、".join(unsupported_business)
                        + "。本轮必须删除这些断言，不得换用近义词继续推测。"
                    )
                retry_response = retry_client.chat([
                    {"role": "system", "content": retry_instruction},
                    messages[-1],
                ])
                retry_answer = str(getattr(retry_response, "content", "") or "").strip()
                retry_finish = str(getattr(retry_response, "finish_reason", "stop") or "stop")
                retry_unsupported = _unsupported_business_terms(
                    retry_answer,
                    question=question,
                    business_context=business_context,
                    identity=dict(snapshot.get("identity") or {}),
                )
                if retry_answer and retry_finish != "length" and not retry_unsupported:
                    answer = retry_answer
                    finish_reason = retry_finish
                    answer_status = "retried" if retry_index == 1 else "retried_compact"
                    break
                _progress(
                    progress,
                    "model_answer_retry",
                    "精简回答仍未完整结束，正在进一步压缩后重试",
                    retry_index=retry_index,
                    finish_reason=retry_finish,
                    discarded_partial_chars=len(retry_answer),
                )
            if not answer:
                raise RuntimeError("财报研究员多次调用均未生成完整正文，本次结果未保存")

        answer = _normalize_financial_markdown(answer)
        # Publish only the final, complete answer. The reasoning/progress stages
        # remain visible, but an incomplete draft never enters the Feishu card.
        _progress(
            progress,
            "model_output_delta",
            f"已生成 {len(answer)} 字完整财报正文",
            text_delta=answer,
            stock_code=snapshot["stock_code"],
            stock_name=snapshot["stock_name"],
        )
        if not answer:
            raise RuntimeError("Financial Analyst returned an empty response")
        assistant_entry = self.store.append_chat_entry(
            stock_code=snapshot["stock_code"], stock_name=snapshot["stock_name"], role="assistant", content=answer,
            source_snapshot_id=snapshot.get("id"), source_hash=snapshot.get("source_hash"),
        )
        _progress(progress, "analysis_complete", "财报解释已完成并已保存", answer_status=answer_status)
        return {
            "stock_code": snapshot["stock_code"], "stock_name": snapshot["stock_name"],
            "as_of": snapshot["as_of"], "answer": answer,
            "data_dates": data_dates,
            "provider": config["provider"], "model": config["model"],
            "answer_mode": answer_mode, "answer_status": answer_status,
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
            "company_lookup": "公司综合分析",
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

        if intent == "company_lookup":
            _progress(progress, "security_lookup", "正在通达信A股证券缓存中定位公司")
            security = self._resolve_cached_security(question, str(routing.get("entity") or ""))
            if security is None and FOLLOW_UP_QUESTION.search(question):
                security = self._resolve_history_security(history)
            if security is None:
                _progress(
                    progress,
                    "company_not_loaded",
                    "未能在通达信A股证券缓存中定位该公司，已停止加载公司财务数据",
                )
                return {
                    "answer": (
                        "未能在当前通达信A股证券缓存中定位到这家公司，因此没有读取或推断其财务数据。\n\n"
                        "请检查股票代码或公司名称；港股、美股和已退市证券不在本查询范围内。"
                    ),
                    "scope": "company_not_loaded",
                    "deterministic": True,
                    "leader_snapshot_as_of": None,
                    "leader_snapshot_status": "not_requested",
                    "routing": routing,
                }
            code = str(security.get("code") or "").upper()
            name = str(security.get("name") or code)
            _progress(
                progress,
                "security_matched",
                f"已定位公司：{name}（{code}），正在读取该公司经营与财务数据",
                stock_code=code,
                stock_name=name,
            )
            result = self.chat(code, question=question, history=history, progress=progress)
            return {
                **result,
                "scope": "company",
                "matched_by": "tdx_security_cache",
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
