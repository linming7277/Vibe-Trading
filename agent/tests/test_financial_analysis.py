from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.financial_analysis_routes import register_financial_analysis_routes
from src.financial_analysis.engine import FinancialFeatureEngine, FinancialForecastEngine
from src.financial_analysis.service import (
    FINANCIAL_CLAIMS_PROMPT_VERSION,
    FinancialAnalysisService,
    _normalize_financial_markdown,
    _unsupported_business_terms,
    classify_financial_answer_mode,
    classify_financial_question,
)
from src.financial_analysis.store import FinancialAnalysisStore
from src.structured_output import StructuredOutputRuntime
from src.tdx_data.store import TdxDataStore
from src.tdx_data.financial_history import DEFAULT_FIELDS, normalize_financial_row


def test_l3_question_selects_full_research_report() -> None:
    assert classify_financial_answer_mode("L3 深度分析士兰微") == "full"
    assert classify_financial_answer_mode("详细分析600460") == "full"
    assert classify_financial_answer_mode("深度看一下老板电器") == "full"


def test_business_assertion_guard_requires_snapshot_support() -> None:
    identity = {"level1_name": "电子", "level2_name": "半导体", "level3_name": "功率半导体"}
    assert _unsupported_business_terms(
        "公司采用IDM模式。",
        question="分析这家公司",
        business_context={"main_business": "集成电路"},
        identity=identity,
    ) == ["IDM"]
    assert _unsupported_business_terms(
        "公司采用IDM模式。",
        question="分析这家公司",
        business_context={"business_model": "IDM"},
        identity=identity,
    ) == []
    assert _unsupported_business_terms(
        "当前资料无法确认是否采用IDM模式。",
        question="这家公司是IDM吗？",
        business_context={"main_business": "集成电路"},
        identity=identity,
    ) == []


def rows(*, count: int = 6, loss: bool = False) -> list[dict]:
    result = []
    for index in range(count):
        year = 2020 + index
        revenue = 10_000_000_000 * (1.1 ** index)
        profit = (-300_000_000 if index == count - 1 else 1_000_000_000 * (1.08 ** index)) if loss else 1_000_000_000 * (1.12 ** index)
        result.append({
            "symbol": "000001.SZ", "report_date": f"{year}-12-31", "announcement_date": f"{year + 1}-03-31",
            "period_type": "annual", "revenue": revenue, "net_profit": profit,
            "operating_cash_flow": profit * 1.1, "equity": 5_000_000_000 * (1.08 ** index),
            "assets": 9_000_000_000 * (1.08 ** index), "roe": 15 + index * .2,
            "gross_margin": 32 + index * .2, "net_margin": profit / revenue * 100,
            "revenue_yoy": 10, "net_profit_yoy": 12, "cash_conversion": 110,
            "debt_ratio": 44 - index * .2, "capex": revenue * .05,
            "flow_basis": "annual",
            "source": "TongDaXin professional finance / TQ", "data_as_of": f"{year + 1}-03-31",
        })
    return result


class FakeHistory:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def query(self, symbol: str, *, as_of: str | None = None, period_type: str | None = None):
        visible = [row for row in self.items if not as_of or row["announcement_date"] <= as_of]
        return {"symbol": symbol, "as_of": as_of, "items": visible, "total": len(visible), "package": {"status": "ready"}}

    def collect_incremental(self, symbols: list[str]):
        return {"status": "ready", "symbols": len(symbols)}


class FakeConfigs:
    def __init__(self, enabled: bool = True) -> None:
        self.item = {"role": "financial_analyst", "provider": "openai", "model": "test-model", "enabled": enabled, "updated_at": "now"}

    def get_config(self, role: str):
        assert role == "financial_analyst"
        return dict(self.item)

    def list_configs(self):
        return [dict(self.item)]

    def close(self):
        return None


class GoodRuntime:
    def invoke(self, **kwargs):
        payload = kwargs["payload"]
        fact = next(key for key in payload["evidence_manifest"] if key.startswith("FIN_REVENUE_"))
        forecast = next(key for key in payload["evidence_manifest"] if key.startswith("FORECAST_BASE_REVENUE_"))
        return {
            "summary": "历史经营与现金流总体保持改善。",
            "claims": [
                {"type": "FACT", "text": "历史营收保持增长", "source_keys": [fact], "confidence": "HIGH"},
                {"type": "FORECAST", "text": "Base 情景预测来自确定性模型", "source_keys": [forecast], "confidence": "MEDIUM"},
                {"type": "INFERENCE", "text": "趋势延续仍取决于利润率", "source_keys": [fact], "confidence": "MEDIUM"},
            ],
        }


class FailedRuntime:
    def invoke(self, **kwargs):
        raise RuntimeError("model unavailable")


class EmptyStructuredRuntime:
    def invoke(self, **kwargs):
        return {}


def service(tmp_path: Path, history_rows: list[dict], runtime=None) -> FinancialAnalysisService:
    result = FinancialAnalysisService(
        store=FinancialAnalysisStore(tmp_path / "research.db"), history=FakeHistory(history_rows),
        config_store=FakeConfigs(), runtime=runtime or GoodRuntime(),
    )
    result._agent_config = lambda: (FakeConfigs().item, True)  # type: ignore[method-assign]
    return result


def test_normal_history_features_forecast_and_pit() -> None:
    source = rows()
    source.append({**source[-1], "report_date": "2026-06-30", "announcement_date": "2026-08-30", "period_type": "semiannual"})
    visible = [row for row in source if row["announcement_date"] <= "2026-08-14"]
    feature = FinancialFeatureEngine().build(stock_code="000001.SZ", stock_name="测试公司", as_of="2026-08-14", rows=visible)
    forecast = FinancialForecastEngine().build(feature)
    assert feature["status"] == "READY"
    assert feature["data_quality"]["annual_period_count"] == 6
    assert all(row["announcement_date"] <= "2026-08-14" for row in feature["historical_periods"])
    assert feature["growth"]["revenue_cagr_3y"]["status"] == "READY"
    assert feature["growth"]["revenue_cagr_5y"]["status"] == "READY"
    assert forecast["status"] == "READY"
    assert set(forecast["scenarios"]) == {"BEAR", "BASE", "BULL"}
    assert all(len(item["forecast"]) == 3 for item in forecast["scenarios"].values())


def test_tdx_single_period_flows_are_summed_into_full_year() -> None:
    quarterly = []
    for year in range(2020, 2026):
        for suffix, period, revenue in (("03-31", "q1", 10.0), ("06-30", "semiannual", 20.0), ("09-30", "q3", 30.0), ("12-31", "annual", 40.0)):
            quarterly.append({
                "report_date": f"{year}-{suffix}", "announcement_date": f"{year + (suffix == '12-31')}-04-01",
                "period_type": period, "revenue": revenue, "net_profit": revenue / 10,
                "operating_cash_flow": revenue / 8, "gross_profit": revenue / 2,
                "equity": 100, "assets": 150, "roe": 10, "debt_ratio": 30,
                "flow_basis": "single_period", "source": "TDX",
            })
    feature = FinancialFeatureEngine().build(stock_code="X", stock_name="X", as_of="2026-08-14", rows=quarterly)
    latest_annual = feature["annual_periods_for_calculation"][-1]
    assert latest_annual["revenue"] == 100
    assert latest_annual["net_profit"] == 10
    assert latest_annual["gross_margin"] == 50
    assert latest_annual["annualization_status"] == "SUM_FOUR_SINGLE_PERIODS"


def test_insufficient_history_does_not_force_forecast() -> None:
    feature = FinancialFeatureEngine().build(stock_code="X", stock_name="X", as_of="2026-08-14", rows=rows(count=1))
    forecast = FinancialForecastEngine().build(feature)
    assert feature["status"] == "INSUFFICIENT_DATA"
    assert forecast["status"] == "INSUFFICIENT_DATA"
    assert forecast["scenarios"] == {}


def test_loss_company_does_not_force_profit_cagr_or_profit_forecast() -> None:
    feature = FinancialFeatureEngine().build(stock_code="X", stock_name="X", as_of="2026-08-14", rows=rows(loss=True))
    forecast = FinancialForecastEngine().build(feature)
    assert feature["growth"]["profit_cagr_3y"]["status"] == "NON_POSITIVE_ENDPOINT"
    assert forecast["status"] == "LIMITED"
    assert all(point["net_profit"] is None for scenario in forecast["scenarios"].values() for point in scenario["forecast"])


def test_financial_sector_guard_marks_metrics_and_forecast_limited() -> None:
    feature = FinancialFeatureEngine().build(stock_code="X", stock_name="银行", as_of="2026-08-14", rows=rows(), financial_sector=True)
    forecast = FinancialForecastEngine().build(feature, financial_sector=True)
    assert feature["profitability"]["gross_margin"]["status"] == "NOT_APPLICABLE"
    assert "FINANCIAL_SECTOR_METRIC_CAUTION" in feature["data_quality"]["cautions"]
    assert forecast["status"] == "LIMITED"


def test_tdx_risk_balance_sheet_fields_are_pit_normalized_and_exposed() -> None:
    assert {"FN8", "FN11", "FN17", "FN21", "FN54", "FN69", "FN327"}.issubset(DEFAULT_FIELDS)
    normalized = normalize_financial_row("000001.SZ", {
        "tag_time": "20260331", "announce_time": "20260430",
        "FN8": 100, "FN11": 80, "FN17": 20, "FN21": 300, "FN54": 150,
        "FN69": 200, "FN327": 35, "FN40": 600, "FN63": 350, "FN72": 250,
        "FN230": 400, "FN232": 40, "FN234": 30, "FN202": 30,
    }, "test")
    assert normalized is not None
    assert normalized["announcement_date"] == "2026-04-30"
    assert normalized["accounts_receivable"] == 80
    assert normalized["interest_bearing_debt_ratio"] == 35

    source = [{
        **row,
        "cash_and_equivalents": 100 + index,
        "accounts_receivable": 80 + index,
        "inventory": 20 + index,
        "current_assets": 300 + index,
        "current_liabilities": 150 + index,
        "non_current_liabilities": 200 + index,
        "interest_bearing_debt_ratio": 35,
    } for index, row in enumerate(rows())]
    feature = FinancialFeatureEngine().build(stock_code="000001.SZ", stock_name="测试公司", as_of="2026-08-14", rows=source)
    balance_sheet = feature["balance_sheet"]
    assert balance_sheet["current_ratio"][-1]["value"] == round(305 / 155, 4)
    assert balance_sheet["quick_ratio"][-1]["value"] == round((305 - 25) / 155, 4)
    assert balance_sheet["receivables_to_revenue"][-1]["value"] is not None
    assert feature["data_quality"]["risk_financial_input_status"] == "READY"

    quarterly = [{
        **source[-1], "report_date": "2026-03-31", "announcement_date": "2026-04-30",
        "period_type": "q1", "revenue": 400,
    }]
    quarter_feature = FinancialFeatureEngine().build(
        stock_code="000001.SZ", stock_name="测试公司", as_of="2026-08-14", rows=[*source, *quarterly],
    )
    assert quarter_feature["balance_sheet"]["receivables_to_revenue"][-1]["value"] is None


def test_agent_not_configured_keeps_python_ready(tmp_path: Path) -> None:
    svc = service(tmp_path, rows())
    svc._agent_config = lambda: (FakeConfigs(enabled=False).item, False)  # type: ignore[method-assign]
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["feature_status"] == "READY"
    assert result["forecast_status"] == "READY"
    assert result["analysis_status"] == "CONFIGURATION_REQUIRED"


def test_agent_failure_uses_summary_only_and_keeps_python_results(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows(), FailedRuntime())
    import src.financial_analysis.service as financial_service

    class FailedChat:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            raise RuntimeError("retry unavailable")

    monkeypatch.setattr(financial_service, "ChatLLM", FailedChat)
    svc.structured_runtime = StructuredOutputRuntime(client_factory=FailedChat)
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["analysis_status"] == "COMPLETED"
    assert result["feature_status"] == "READY" and result["forecast_status"] == "READY"
    assert result["analysis"]["analysis_metadata"]["analysis_quality_status"] == "SUMMARY_ONLY"
    assert result["analysis"]["analysis_metadata"]["evidence_ready"] is False
    assert result["analysis"]["analysis_metadata"]["fallback_failure_types"][0] == {
        "mode": "PROMPT_JSON", "type": "RuntimeError",
    }


def test_snapshot_idempotency_and_new_report(tmp_path: Path) -> None:
    history = FakeHistory(rows())
    svc = FinancialAnalysisService(
        store=FinancialAnalysisStore(tmp_path / "research.db"), history=history,
        config_store=FakeConfigs(), runtime=GoodRuntime(),
    )
    svc._agent_config = lambda: (FakeConfigs().item, True)  # type: ignore[method-assign]
    first = svc.prepare("000001.SZ", as_of="2026-08-14")
    repeated = svc.prepare("000001.SZ", as_of="2026-08-14")
    assert first["id"] == repeated["id"] and repeated["idempotent_reuse"] is True
    history.items.append({**rows()[-1], "report_date": "2026-06-30", "announcement_date": "2026-08-01", "period_type": "semiannual", "revenue": 8_000_000_000})
    updated = svc.prepare("000001.SZ", as_of="2026-08-14")
    assert updated["id"] != first["id"] and updated["source_hash"] != first["source_hash"]


def test_stale_leader_date_does_not_roll_back_financial_cutoff(tmp_path: Path) -> None:
    svc = service(tmp_path, rows())
    svc._identity = lambda stock_code, as_of: {  # type: ignore[method-assign]
        "stock_code": stock_code, "stock_name": "测试公司", "leader_as_of": "2026-08-17",
        "metric_applicability_notes": [], "data_dates": {"leader_as_of": "2026-08-17"},
    }

    result = svc.prepare("000001.SZ")

    assert result["as_of"] == date.today().isoformat()
    assert result["historical_cutoff"] == date.today().isoformat()
    assert result["identity"]["data_dates"]["leader_as_of"] == "2026-08-17"
    assert result["identity"]["data_dates"]["financial_report_date"] == "2025-12-31"


def test_get_invalidates_snapshot_when_cached_market_data_changes(tmp_path: Path) -> None:
    tdx_store = TdxDataStore(tmp_path / "tdx_data.db")
    tdx_store.upsert_records("quotes", [{
        "key": "000001.SZ", "name": "测试公司",
        "payload": {"price": 10.0, "previous_close": 9.8, "data_as_of": "2026-08-21T10:00:00+08:00"},
    }])
    tdx_store.upsert_records("fundamentals", [{
        "key": "000001.SZ", "name": "测试公司",
        "payload": {"pe_ttm": 12.0, "pb_mrq": 1.5, "dividend_yield": 2.0, "market_cap_100m": 100.0},
    }])
    svc = FinancialAnalysisService(
        store=FinancialAnalysisStore(tmp_path / "research.db"), history=FakeHistory(rows()),
        config_store=FakeConfigs(), runtime=GoodRuntime(), tdx_store=tdx_store,
    )
    svc._agent_config = lambda: (FakeConfigs().item, True)  # type: ignore[method-assign]

    first = svc.get("000001.SZ")
    tdx_store.upsert_records("quotes", [{
        "key": "000001.SZ", "name": "测试公司",
        "payload": {"price": 10.5, "previous_close": 9.8, "data_as_of": "2026-08-21T11:00:00+08:00"},
    }])
    second = svc.get("000001.SZ")

    assert second["id"] != first["id"]
    assert second["identity"]["market_quote"]["price"] == 10.5
    assert second["identity"]["data_dates"]["quote_as_of"] == "2026-08-21T11:00:00+08:00"
    assert second["identity"]["market_valuation"]["pe"] == 12.0


def test_explicit_historical_cutoff_excludes_newer_market_cache(tmp_path: Path) -> None:
    tdx_store = TdxDataStore(tmp_path / "tdx_data.db")
    tdx_store.upsert_records("quotes", [{
        "key": "000001.SZ", "name": "测试公司",
        "payload": {"price": 10.5, "data_as_of": "2026-08-21T11:00:00+08:00"},
    }])
    tdx_store.upsert_records("fundamentals", [{
        "key": "000001.SZ", "name": "测试公司", "payload": {"pe_ttm": 12.0},
    }])
    svc = FinancialAnalysisService(
        store=FinancialAnalysisStore(tmp_path / "research.db"), history=FakeHistory(rows()),
        config_store=FakeConfigs(), runtime=GoodRuntime(), tdx_store=tdx_store,
    )
    svc._agent_config = lambda: (FakeConfigs().item, True)  # type: ignore[method-assign]

    result = svc.prepare("000001.SZ", as_of="2026-08-14")

    assert result["identity"]["market_quote"] is None
    assert result["identity"]["market_valuation"] is None
    assert result["identity"]["data_dates"]["quote_as_of"] is None
    assert result["identity"]["data_dates"]["valuation_as_of"] is None


def test_financial_agent_completes_without_inventing_numbers(tmp_path: Path) -> None:
    svc = service(tmp_path, rows())
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["analysis_status"] == "COMPLETED"
    assert {claim["type"] for claim in result["analysis"]["claims"]} == {"FACT", "FORECAST", "INFERENCE"}
    assert result["analysis"]["analysis_metadata"]["evidence_ready"] is True
    assert result["analysis"]["analysis_metadata"]["prompt_version"] == FINANCIAL_CLAIMS_PROMPT_VERSION


def test_financial_agent_summary_only_when_all_capability_modes_fail(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows(), EmptyStructuredRuntime())

    class FallbackChat:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            assert "summary" in messages[0]["content"]
            payload = json.loads(messages[-1]["content"])
            key = next(item for item in payload["evidence_manifest"] if item.startswith("FIN_REVENUE_"))
            return SimpleNamespace(content=json.dumps({
                "summary": "经营趋势需要结合现金流持续核验。",
                "claims": [{"type": "FACT", "text": "历史营收已记录", "source_keys": [key], "confidence": "HIGH"}],
            }))

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FallbackChat)
    svc.structured_runtime = StructuredOutputRuntime(client_factory=FallbackChat)
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["analysis_status"] == "COMPLETED"
    assert result["analysis"]["analysis_metadata"]["fallback_path"] == "summary_only"
    assert result["analysis"]["analysis_metadata"]["structured_output_mode_used"] == "TEXT_ONLY"
    assert result["analysis"]["analysis_metadata"]["error_types"][0]["validation_error_code"] == "TOP_LEVEL_SCHEMA_INVALID"


def test_financial_chat_uses_the_role_specific_connection(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key": "role-specific-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    captured: dict = {}

    class FakeChatLLM:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def chat(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="财务快照已读取。")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)

    progress: list[tuple[str, str, dict]] = []
    result = svc.chat(
        "000001.SZ",
        as_of="2026-08-14",
        question="营收趋势如何？",
        progress=lambda stage, message, details: progress.append((stage, message, details)),
    )

    assert result["answer"] == "财务快照已读取。"
    assert captured["kwargs"] == {
        "model_name": "test-model", "provider_name": "openai",
        "base_url": connection["base_url"], "api_key": connection["api_key"],
        "timeout_seconds": 90, "max_retries": 0, "max_tokens": 4_000,
    }
    assert "财务快照" in captured["messages"][-1]["content"]
    assert [item[0] for item in progress] == [
        "financial_snapshot",
        "financial_snapshot_loaded",
        "model_analysis",
        "model_output_delta",
        "analysis_complete",
    ]
    dossier = svc.dossier("000001.SZ", as_of="2026-08-14")
    assert [entry["role"] for entry in dossier["chat_entries"]] == ["user", "assistant"]
    assert dossier["archive_summary"]["chat_entry_count"] == 2


def test_financial_chat_disables_hidden_thinking_for_ark_deepseek(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "model": "deepseek-v4-flash",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key": "role-specific-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    captured: dict = {}

    class FakeChatLLM:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def chat(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="### 一句话判断\n\n经营正在修复。", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)

    result = svc.chat("000001.SZ", as_of="2026-08-14", question="帮我分析这家公司")

    assert result["answer"].startswith("### 一句话判断")
    assert captured["kwargs"]["max_tokens"] == 6_500
    assert captured["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}
    instruction = captured["messages"][0]["content"]
    assert "### 研究结论" in instruction
    assert "### ② 经营周期与业绩" in instruction
    assert "数值项目本身默认为可复核事实" in instruction
    assert "禁止把全文挤成一个大段落" in instruction
    assert "除非用户明确询问，不展示龙头综合评分和排名" in instruction


def test_financial_detail_includes_cached_company_business_context(tmp_path: Path, monkeypatch) -> None:
    tdx_store = TdxDataStore(tmp_path / "tdx_data.db")
    tdx_store.upsert_records("fundamentals", [{
        "key": "000001.SZ", "name": "测试公司",
        "payload": {"main_business": "信号链模拟芯片", "pe_ttm": 18.0},
    }])
    # The store stamps updated_at with the real clock; pin it before the
    # test's as_of so the PIT business-context fallback stays eligible on
    # any future run date.
    tdx_store._conn.execute(
        "UPDATE records SET updated_at='2026-08-20T00:00:00+00:00' "
        "WHERE dataset='fundamentals' AND record_key='000001.SZ'"
    )
    tdx_store._conn.commit()
    svc = FinancialAnalysisService(
        store=FinancialAnalysisStore(tmp_path / "research.db"), history=FakeHistory(rows()),
        config_store=FakeConfigs(), runtime=GoodRuntime(), tdx_store=tdx_store,
    )
    svc._agent_config = lambda: ({
        **FakeConfigs().item, "base_url": "https://example.invalid/v1", "api_key": "test-key",
    }, True)  # type: ignore[method-assign]
    captured: dict = {}

    class FakeChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="### 结论\n\n现金流需要结合主营业务判断。", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)
    progress: list[tuple[str, str, dict]] = []

    result = svc.chat(
        "000001.SZ", as_of="2026-08-21", question="现金流怎么样？",
        progress=lambda stage, message, details: progress.append((stage, message, details)),
    )

    rendered = captured["messages"][-1]["content"]
    assert result["answer"].startswith("### 结论")
    assert '"main_business": "信号链模拟芯片"' in rendered
    assert '"status": "PARTIAL"' in rendered
    assert "若 business_research_snapshot 有有效主营业务" in captured["messages"][0]["content"]
    assert "不得输出 UNKNOWN、PARTIAL、READY 等内部状态码" in captured["messages"][0]["content"]
    assert "不得把 PE 通俗化为‘需要多少年回本’" in captured["messages"][0]["content"]
    assert any(stage == "business_snapshot_loaded" for stage, _message, _details in progress)


def test_financial_answer_recovers_plain_section_breaks_for_feishu() -> None:
    answer = (
        "结论先行 公司经营修复。 "
        "经营与盈利质量 收入增长，但毛利率承压。 "
        "现金流与资产负债 现金流改善，负债较低。 "
        "估值局限与后续关注 当前估值偏高。"
    )

    normalized = _normalize_financial_markdown(answer)

    assert normalized.startswith("### 结论先行\n\n")
    assert "\n\n### 经营与盈利质量\n\n" in normalized
    assert "\n\n### 现金流与资产负债\n\n" in normalized
    assert "\n\n### 估值局限与后续关注\n\n" in normalized


def test_financial_chat_uses_full_research_framework_and_current_valuation(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    svc._identity = lambda stock_code, as_of: {  # type: ignore[method-assign]
        "stock_code": stock_code, "stock_name": "测试公司", "leader_as_of": as_of,
        "level1_name": "测试", "level2_name": "测试", "level3_name": "测试行业",
        "metric_applicability_notes": [],
        "market_valuation": {
            "as_of": as_of, "pe": 18.5, "pb": 2.1, "dividend_yield": 1.2, "market_cap": 100.0,
            "source": "TongDaXin leader-score valuation snapshot", "limitations": ["仅为当前快照"],
        },
    }
    captured: dict = {}

    class FakeChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="已按研究框架整理。")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)
    result = svc.chat("000001.SZ", as_of="2026-08-14", question="请全面分析这家公司")

    assert result["answer"] == "已按研究框架整理。"
    assert "未来三年情景" in captured["messages"][0]["content"]
    assert "### ⑤ 估值与市场预期" in captured["messages"][0]["content"]
    rendered = captured["messages"][-1]["content"]
    assert '"pe": 18.5' in rendered
    assert '"market_cap": 100.0' in rendered


def test_financial_chat_streams_answer_deltas_with_bounded_model_call(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    constructor: dict[str, object] = {}

    class StreamingChatLLM:
        def __init__(self, **kwargs):
            constructor.update(kwargs)

        def chat(self, messages):
            raise AssertionError("a progress-enabled financial chat must use streaming")

        def stream_chat(self, messages, *, on_text_chunk, on_reasoning_chunk, timeout):
            assert timeout == 90
            on_reasoning_chunk("private chain of thought must not be surfaced")
            on_text_chunk("第一段财务结论。")
            on_text_chunk("第二段风险说明。")
            return SimpleNamespace(content="第一段财务结论。第二段风险说明。", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", StreamingChatLLM)
    progress: list[tuple[str, str, dict]] = []

    result = svc.chat(
        "000001.SZ", as_of="2026-08-14", question="请分析",
        progress=lambda stage, message, details: progress.append((stage, message, details)),
    )

    assert result["answer"] == "第一段财务结论。第二段风险说明。"
    assert constructor["timeout_seconds"] == 90
    assert constructor["max_retries"] == 0
    assert constructor["max_tokens"] == 6_500
    deltas = [details["text_delta"] for stage, _message, details in progress if stage == "model_output_delta"]
    assert "".join(deltas) == result["answer"]
    rendered_progress = json.dumps(progress, ensure_ascii=False)
    assert "private chain of thought" not in rendered_progress
    assert any(stage == "model_reasoning_progress" for stage, _message, _details in progress)


def test_financial_chat_uses_streamed_text_when_provider_final_content_is_empty(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {**FakeConfigs().item, "base_url": "https://example.invalid/v1", "api_key": "test-key"}
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    constructor_calls: list[dict] = []

    class StreamingChatLLM:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)

        def chat(self, messages):
            raise AssertionError("streamed text must not trigger a retry")

        def stream_chat(self, messages, *, on_text_chunk, on_reasoning_chunk, timeout):
            on_text_chunk("这是已经生成的正文。")
            return SimpleNamespace(content="", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", StreamingChatLLM)

    result = svc.chat(
        "000001.SZ", as_of="2026-08-14", question="帮我分析这家公司",
        progress=lambda *_args: None,
    )

    assert result["answer"] == "这是已经生成的正文。"
    assert result["answer_status"] == "complete"
    assert result["answer_mode"] == "overview"
    assert len(constructor_calls) == 1


def test_financial_chat_retries_when_reasoning_model_emits_no_body(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {**FakeConfigs().item, "base_url": "https://example.invalid/v1", "api_key": "test-key"}
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    constructor_calls: list[dict] = []

    class EmptyThenConciseChatLLM:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)

        def stream_chat(self, messages, *, on_text_chunk, on_reasoning_chunk, timeout):
            on_reasoning_chunk("hidden reasoning")
            return SimpleNamespace(content="", finish_reason="length")

        def chat(self, messages):
            assert "必须直接输出一份从头开始、可独立阅读的最终答案" in messages[0]["content"]
            return SimpleNamespace(content="精简重试后的有效财报结论。", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", EmptyThenConciseChatLLM)
    progress: list[tuple[str, str, dict]] = []

    result = svc.chat(
        "000001.SZ", as_of="2026-08-14", question="帮我分析这家公司",
        progress=lambda stage, message, details: progress.append((stage, message, details)),
    )

    assert result["answer"] == "精简重试后的有效财报结论。"
    assert result["answer_status"] == "retried"
    assert [item["max_tokens"] for item in constructor_calls] == [6_500, 8_000]
    assert any(stage == "model_answer_retry" for stage, _message, _details in progress)
    assert any(
        stage == "model_output_delta" and details.get("text_delta") == result["answer"]
        for stage, _message, details in progress
    )
    assert progress[-1][1] == "财报解释已完成并已保存"


def test_financial_chat_discards_partial_length_limited_stream_before_retry(
    tmp_path: Path, monkeypatch,
) -> None:
    svc = service(tmp_path, rows())
    connection = {**FakeConfigs().item, "base_url": "https://example.invalid/v1", "api_key": "test-key"}
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    constructor_calls: list[dict] = []

    class PartialThenCompleteChatLLM:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)

        def stream_chat(self, messages, *, on_text_chunk, on_reasoning_chunk, timeout):
            on_text_chunk("结论先行：收入增长、利润修复、负债")
            return SimpleNamespace(content="结论先行：收入增长、利润修复、负债", finish_reason="length")

        def chat(self, messages):
            return SimpleNamespace(content="结论先行：收入增长、利润修复，但负债与现金流仍需复核。", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", PartialThenCompleteChatLLM)
    progress: list[tuple[str, str, dict]] = []

    result = svc.chat(
        "000001.SZ", as_of="2026-08-14", question="帮我分析这家公司",
        progress=lambda stage, message, details: progress.append((stage, message, details)),
    )

    assert result["answer"] == "结论先行：收入增长、利润修复，但负债与现金流仍需复核。"
    assert result["answer_status"] == "retried"
    assert [item["max_tokens"] for item in constructor_calls] == [6_500, 8_000]
    deltas = [details["text_delta"] for stage, _message, details in progress if stage == "model_output_delta"]
    assert deltas == [result["answer"]]
    assert all("收入增长、利润修复、负债" not in delta or delta == result["answer"] for delta in deltas)
    assert any(
        stage == "model_answer_retry" and details.get("discarded_partial_chars", 0) > 0
        for stage, _message, details in progress
    )


def test_financial_chat_compacts_oversized_complete_overview_before_publishing(
    tmp_path: Path, monkeypatch,
) -> None:
    svc = service(tmp_path, rows())
    connection = {**FakeConfigs().item, "base_url": "https://example.invalid/v1", "api_key": "test-key"}
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]

    class OversizedThenConciseChatLLM:
        def __init__(self, **kwargs):
            pass

        def stream_chat(self, messages, *, on_text_chunk, on_reasoning_chunk, timeout):
            oversized = "过长草稿" * 1_200
            on_text_chunk(oversized)
            return SimpleNamespace(content=oversized, finish_reason="stop")

        def chat(self, messages):
            assert "最多3200个汉字" in messages[0]["content"]
            return SimpleNamespace(content="### 一句话判断\n\n这是精简后的完整结论。", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", OversizedThenConciseChatLLM)
    progress: list[tuple[str, str, dict]] = []

    result = svc.chat(
        "000001.SZ", as_of="2026-08-14", question="帮我分析这家公司",
        progress=lambda stage, message, details: progress.append((stage, message, details)),
    )

    assert result["answer"] == "### 一句话判断\n\n这是精简后的完整结论。"
    assert result["answer_status"] == "retried"
    assert any(
        stage == "model_answer_retry" and "正文信息过多" in message
        for stage, message, _details in progress
    )
    assert [
        details["text_delta"] for stage, _message, details in progress if stage == "model_output_delta"
    ] == [result["answer"]]


def test_financial_chat_only_reuses_history_for_an_explicit_follow_up(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {**FakeConfigs().item, "base_url": "https://example.invalid/v1", "api_key": "test-key"}
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    captured: list[list[dict[str, str]]] = []

    class FakeChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            captured.append(messages)
            return SimpleNamespace(content="有效回答。", finish_reason="stop")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)

    svc.chat("000001.SZ", as_of="2026-08-14", question="请全面分析这家公司")
    svc.chat("000001.SZ", as_of="2026-08-14", question="营收趋势如何？")
    svc.chat("000001.SZ", as_of="2026-08-14", question="那现金流呢？")

    assert len(captured[0]) == 2
    assert len(captured[1]) == 2
    assert len(captured[2]) == 4
    assert "全面分析" not in json.dumps(captured[1], ensure_ascii=False)
    assert "营收趋势如何" in json.dumps(captured[2], ensure_ascii=False)


def test_financial_workspace_chat_does_not_require_a_company(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key": "role-specific-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]

    class FakeChatLLM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def chat(self, messages):
            assert messages[0]["role"] == "system"
            assert "本地龙头池快照" in messages[-1]["content"]
            return SimpleNamespace(content="请先看营收、利润和现金流是否一致。")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)

    result = svc.chat_workspace(
        question="如何快速看一家龙头的财报？",
        candidates=[{"stock_code": "000001.SZ", "stock_name": "测试龙头", "level3_name": "测试行业", "as_of": "2026-08-14"}],
    )

    assert result["scope"] == "workspace"
    assert result["answer"] == "请先看营收、利润和现金流是否一致。"
    assert result["data_context"]["available_industries"] == [{"industry": "测试行业", "leader_count": 1}]


def test_question_router_uses_rules_before_any_model_or_leader_data(monkeypatch) -> None:
    import src.financial_analysis.service as financial_service

    # Hermetic: keep the deterministic name scanner out of this rule test.
    monkeypatch.setattr(financial_service, "_question_names_cached_security", lambda text: False)
    assert classify_financial_question("你还能分析哪些方面？") == "capability"
    assert classify_financial_question("现金流质量怎么看？") == "general_method"
    assert classify_financial_question("三级行业有哪些龙头？") == "leader_pool"
    assert classify_financial_question("分析 600519 的盈利质量") == "company_lookup"
    assert classify_financial_question("那现金流呢？") == "company_lookup"
    assert classify_financial_question("帮我研究一下宁德时代") == "ambiguous"


def test_company_name_in_question_routes_to_company_lookup(monkeypatch) -> None:
    import src.financial_analysis.service as financial_service

    monkeypatch.setattr(financial_service, "_question_names_cached_security", lambda text: True)
    # "财务表现怎么样" alone would read as general_method; an embedded
    # company name keeps it a company question (research-cache plan §5.3).
    assert classify_financial_question("同庆楼的财务表现怎么样") == "company_lookup"


def test_explicit_stock_code_wins_over_leader_pool_keywords() -> None:
    # A question naming a code is a company question even when its wording
    # also mentions leader/industry keywords: previously it was routed to
    # the leader-pool snapshot and answered about the wrong company.
    assert classify_financial_question("结合行业龙头地位深入分析600460") == "company_lookup"
    assert classify_financial_question("看看三级行业龙头池里的600460怎么样") == "company_lookup"
    assert classify_financial_question("三级行业有哪些龙头？") == "leader_pool"


def test_general_method_route_does_not_load_leader_pool(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]

    class FakeChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            assert messages[-1]["content"] == "现金流质量怎么看？"
            return SimpleNamespace(content="应比较利润、经营现金流和现金转换率。")

    import src.financial_analysis.service as financial_service
    import src.level3_leaders.service as leader_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)
    monkeypatch.setattr(
        leader_service,
        "get_level3_leader_service",
        lambda: (_ for _ in ()).throw(AssertionError("general method must not load leader data")),
    )

    result = svc.chat_current_leader_pool(question="现金流质量怎么看？", history=[])

    assert result["scope"] == "general_method"
    assert result["leader_snapshot_status"] == "not_requested"
    assert result["routing"] == {
        "intent": "general_method", "source": "rules", "confidence": 1.0, "entity": "",
    }


def test_company_code_outside_top2_uses_full_tdx_security_cache(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    calls: list[dict[str, object]] = []

    import src.financial_analysis.service as financial_service
    import src.level3_leaders.service as leader_service
    monkeypatch.setattr(
        financial_service,
        "_search_tdx_securities",
        lambda query, limit=20: [{"code": "600460.SH", "name": "士兰微", "updated_at": "2026-08-21"}]
        if "600460" in query else [],
    )
    monkeypatch.setattr(
        leader_service,
        "get_level3_leader_service",
        lambda: (_ for _ in ()).throw(AssertionError("a company lookup must not load the Top-2 leader pool")),
    )

    def fake_chat(stock_code: str, **kwargs):
        calls.append({"stock_code": stock_code, **kwargs})
        return {
            "stock_code": stock_code, "stock_name": "士兰微", "as_of": "2026-08-21",
            "answer": "已读取士兰微专业财务。",
        }

    svc.chat = fake_chat  # type: ignore[method-assign]
    progress: list[str] = []
    result = svc.chat_current_leader_pool(
        question="帮我分析下600460这只股份", history=[],
        progress=lambda stage, message, details: progress.append(stage),
    )

    assert result["scope"] == "company"
    assert result["stock_code"] == "600460.SH"
    assert result["matched_by"] == "tdx_security_cache"
    assert result["leader_snapshot_status"] == "not_requested"
    assert calls[0]["stock_code"] == "600460.SH"
    assert "security_matched" in progress
    assert "leader_pool" not in progress


def test_company_name_from_model_resolves_against_full_tdx_cache(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(
        financial_service,
        "_search_tdx_securities",
        lambda query, limit=20: [{"code": "600460.SH", "name": "士兰微"}] if query == "士兰微" else [],
    )
    monkeypatch.setattr(
        financial_service,
        "_question_names_cached_security",
        lambda text: True,  # deterministic name scan now short-circuits the model router
    )
    monkeypatch.setattr(
        financial_service.FinancialAnalysisService, "_resolve_cached_security",
        staticmethod(lambda question, entity="": (
            {"code": "600460.SH", "name": "士兰微"} if "士兰微" in question else None
        )),
    )
    svc.chat = lambda stock_code, **kwargs: {  # type: ignore[method-assign]
        "stock_code": stock_code, "stock_name": "士兰微", "as_of": "2026-08-21", "answer": "分析完成",
    }

    result = svc.chat_current_leader_pool(question="帮我研究一下士兰微", history=[])

    assert result["scope"] == "company"
    assert result["stock_code"] == "600460.SH"
    assert result["routing"]["source"] == "rules"


def test_unknown_company_code_does_not_fall_back_to_leader_pool(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    import src.financial_analysis.service as financial_service
    import src.level3_leaders.service as leader_service
    monkeypatch.setattr(financial_service, "_search_tdx_securities", lambda query, limit=20: [])
    monkeypatch.setattr(
        leader_service,
        "get_level3_leader_service",
        lambda: (_ for _ in ()).throw(AssertionError("unknown securities must not trigger a leader-pool scan")),
    )

    result = svc.chat_current_leader_pool(question="分析 600999 的财务质量", history=[])

    assert result["scope"] == "company_not_loaded"
    assert result["leader_snapshot_status"] == "not_requested"
    assert "通达信A股证券缓存" in result["answer"]


def test_company_follow_up_reuses_only_user_resolved_security(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(
        financial_service,
        "_search_tdx_securities",
        lambda query, limit=20: [{"code": "600460.SH", "name": "士兰微"}] if "600460" in query else [],
    )
    svc.chat = lambda stock_code, **kwargs: {  # type: ignore[method-assign]
        "stock_code": stock_code, "stock_name": "士兰微", "as_of": "2026-08-21", "answer": "现金流分析",
    }
    history = [
        {"role": "user", "content": "分析士兰微", "stock_code": "600460.SH", "stock_name": "士兰微"},
        {"role": "assistant", "content": "上一轮分析"},
    ]

    result = svc.chat_current_leader_pool(question="那现金流呢？", history=history)

    assert result["scope"] == "company"
    assert result["stock_code"] == "600460.SH"


def test_ambiguous_router_model_sees_only_current_message(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "model": "deepseek-v4-flash",
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    class FakeChatLLM:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def chat(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content='{"intent":"company_lookup","entity":"宁德时代","confidence":0.93,"reason":"指定公司"}')

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)

    result = svc._classify_ambiguous_question(question="帮我研究一下宁德时代")

    assert result == {
        "intent": "company_lookup", "source": "model", "confidence": 0.93, "entity": "宁德时代",
    }
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 2
    assert messages[-1] == {"role": "user", "content": "帮我研究一下宁德时代"}
    rendered = json.dumps(messages, ensure_ascii=False)
    assert "历史对话" in rendered
    assert "龙头池快照" not in rendered
    assert "财务快照" not in rendered


def test_ambiguous_router_never_uses_ollama(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    ollama = {**FakeConfigs().item, "provider": "ollama", "model": "qwen", "base_url": "http://127.0.0.1:11434/v1"}
    svc._agent_config = lambda: (ollama, True)  # type: ignore[method-assign]

    class ForbiddenChatLLM:
        def __init__(self, **kwargs):
            raise AssertionError("Ollama must not be used for intent classification")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", ForbiddenChatLLM)

    result = svc._classify_ambiguous_question(question="帮我研究一下这家公司")

    assert result["intent"] == "general_method"
    assert result["source"] == "safe_fallback"


def test_invalid_ambiguous_router_output_falls_back_without_data(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {**FakeConfigs().item, "base_url": "https://example.invalid/v1", "api_key": "test-key"}
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]

    class InvalidChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            return SimpleNamespace(content='{"intent":"company_lookup","confidence":0.2}')

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", InvalidChatLLM)

    result = svc._classify_ambiguous_question(question="看一下这个")

    assert result["intent"] == "general_method"
    assert result["source"] == "safe_fallback"


def test_capability_question_uses_deterministic_manifest_without_history_or_llm(
    tmp_path: Path, monkeypatch,
) -> None:
    svc = service(tmp_path, rows())

    class ForbiddenChatLLM:
        def __init__(self, **kwargs):
            raise AssertionError("capability questions must not call the model")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", ForbiddenChatLLM)

    result = svc.chat_workspace(
        question="你还能分析哪些方面？",
        history=[
            {"role": "user", "content": "分析太阳纸业"},
            {"role": "assistant", "content": "太阳纸业 2021—2025 年 ROE 下滑。"},
        ],
        candidates=[],
    )

    assert result["scope"] == "capability"
    assert result["deterministic"] is True
    assert "当前财报 Agent 已支持" in result["answer"]
    assert "太阳纸业" not in result["answer"]
    assert "2021" not in result["answer"]


def test_new_workspace_question_drops_previous_company_history(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]
    captured: dict = {}

    class FakeChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="经营现金流应结合利润与现金转换率判断。")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)

    svc.chat_workspace(
        question="如何判断经营现金流质量？",
        history=[
            {"role": "user", "content": "分析太阳纸业"},
            {"role": "assistant", "content": "太阳纸业 2021—2025 年 ROE 下滑。"},
        ],
        candidates=[],
    )

    rendered = "\n".join(item["content"] for item in captured["messages"])
    assert "太阳纸业" not in rendered
    assert "2021" not in rendered


def test_unavailable_metric_is_blocked_before_model_call(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())

    class ForbiddenChatLLM:
        def __init__(self, **kwargs):
            raise AssertionError("unsupported metrics must not call the model")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", ForbiddenChatLLM)

    result = svc.chat_workspace(question="帮我比较同行历史 PE 和估值敏感性", candidates=[])

    assert result["scope"] == "data_boundary"
    assert result["deterministic"] is True
    assert result["missing_capabilities"] == ["历史估值", "估值敏感性"]
    assert "暂未完整接入" in result["answer"]


def test_unknown_stock_code_requires_company_page(tmp_path: Path) -> None:
    svc = service(tmp_path, rows())

    result = svc.chat_workspace(
        question="分析 600999 的财务质量",
        candidates=[{"stock_code": "000001.SZ", "stock_name": "测试龙头", "as_of": "2026-08-14"}],
    )

    assert result["scope"] == "company_not_loaded"
    assert "公司研究页面" in result["answer"]


def test_follow_up_rebinds_only_from_a_user_selected_company(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]

    class FakeChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            assert "财务快照" in messages[-1]["content"]
            return SimpleNamespace(content="已使用真实公司快照回答。")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)
    candidate = {"stock_code": "000001.SZ", "stock_name": "测试龙头", "as_of": "2026-08-14"}

    result = svc.chat_workspace(
        question="那现金流呢？",
        history=[{"role": "user", "content": "分析测试龙头的利润质量"}],
        candidates=[candidate],
    )

    assert result["scope"] == "company"
    assert result["stock_code"] == "000001.SZ"


def test_follow_up_never_binds_a_company_introduced_only_by_model(tmp_path: Path) -> None:
    svc = service(tmp_path, rows())
    candidate = {"stock_code": "000001.SZ", "stock_name": "测试龙头", "as_of": "2026-08-14"}

    result = svc.chat_workspace(
        question="那现金流呢？",
        history=[
            {"role": "user", "content": "怎么看盈利质量？"},
            {"role": "assistant", "content": "例如测试龙头可以观察现金流。"},
        ],
        candidates=[candidate],
    )

    assert result["scope"] == "context_required"
    assert result["deterministic"] is True
    assert "不会沿用模型回答中出现的公司名称" in result["answer"]


def test_financial_workspace_chat_resolves_company_from_the_visible_leader_pool(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    connection = {
        **FakeConfigs().item,
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key": "role-specific-key",
    }
    svc._agent_config = lambda: (connection, True)  # type: ignore[method-assign]

    class FakeChatLLM:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            assert "财务快照" in messages[-1]["content"]
            return SimpleNamespace(content="该公司财务快照已加载。")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FakeChatLLM)

    result = svc.chat_workspace(
        question="测试龙头 000001 的现金流怎么样？",
        candidates=[{"stock_code": "000001.SZ", "stock_name": "测试龙头", "as_of": "2026-08-14"}],
    )

    assert result["scope"] == "company"
    assert result["stock_code"] == "000001.SZ"


def test_financial_api_returns_python_snapshot(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows())
    svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    import src.api.financial_analysis_routes as routes
    monkeypatch.setattr(routes, "get_financial_analysis_service", lambda: svc)
    app = FastAPI()
    register_financial_analysis_routes(app, lambda: True)
    client = TestClient(app)
    response = client.get("/api/value/companies/000001.SZ/financial?as_of=2026-08-14")
    assert response.status_code == 200
    assert response.json()["forecast_status"] == "READY"
    analysis = response.json()["analysis"]
    assert analysis["claims"][0]["citations"][0]["source_key"] == analysis["claims"][0]["source_keys"][0]
    assert response.json()["traceability_status"] == "COMPLETE"
    dossier = client.get("/api/value/companies/000001.SZ/financial/dossier?as_of=2026-08-14")
    assert dossier.status_code == 200
    assert dossier.json()["snapshot"]["stock_code"] == "000001.SZ"


def test_web_floating_agent_uses_full_cached_security_route(tmp_path: Path, monkeypatch) -> None:
    class FakeWebChatService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def chat_current_leader_pool(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "scope": "company", "stock_code": "600460.SH", "stock_name": "士兰微",
                "answer": "已按完整通达信证券缓存定位公司。",
            }

        def chat_workspace(self, **kwargs):
            raise AssertionError("web floating agent must not be limited to rendered leader candidates")

    import src.api.financial_analysis_routes as routes

    service = FakeWebChatService()
    monkeypatch.setattr(routes, "get_financial_analysis_service", lambda: service)
    app = FastAPI()
    register_financial_analysis_routes(app, lambda: True)
    client = TestClient(app)

    response = client.post("/api/value/financial-agent/chat", json={
        "question": "分析一下600460",
        "candidates": [{"stock_code": "000001.SZ", "stock_name": "当前页面龙头"}],
    })

    assert response.status_code == 200
    assert response.json()["stock_code"] == "600460.SH"
    assert service.calls == [{"question": "分析一下600460", "history": []}]
