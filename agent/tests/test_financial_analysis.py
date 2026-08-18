from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.financial_analysis_routes import register_financial_analysis_routes
from src.financial_analysis.engine import FinancialFeatureEngine, FinancialForecastEngine
from src.financial_analysis.service import FinancialAnalysisService, classify_financial_question
from src.financial_analysis.store import FinancialAnalysisStore


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
        fact = next(item["key"] for item in payload["evidence"] if item["type"] == "FACT")
        forecast = next(item["key"] for item in payload["evidence"] if item["type"] == "FORECAST")
        identity = payload["company_identity"]
        return {
            "stock_code": identity["stock_code"], "stock_name": identity["stock_name"],
            "executive_summary": "历史经营与现金流总体保持改善。",
            "historical_performance": {"growth": "增长稳定", "profitability": "盈利稳定", "cash_flow": "现金流匹配", "balance_sheet": "资本结构稳定"},
            "latest_changes": ["最新报告延续既有趋势"], "financial_strengths": ["现金流质量较好"],
            "financial_risks": ["情景假设仍需后续财报验证"],
            "forecast_analysis": {"bear": "谨慎情景承压", "base": "基准情景延续", "bull": "乐观情景改善", "key_assumptions": ["增长和净利率遵循 Python 输入"]},
            "key_metrics_to_monitor": ["营收", "净利润", "经营现金流"], "confidence": "MEDIUM", "data_gaps": [],
            "claims": [
                {"type": "FACT", "statement": "历史营收保持增长", "evidence_keys": [fact]},
                {"type": "FORECAST", "statement": "基准情景来自确定性模型", "evidence_keys": [forecast]},
                {"type": "INFERENCE", "statement": "趋势延续仍取决于利润率", "evidence_keys": []},
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


def test_agent_not_configured_keeps_python_ready(tmp_path: Path) -> None:
    svc = service(tmp_path, rows())
    svc._agent_config = lambda: (FakeConfigs(enabled=False).item, False)  # type: ignore[method-assign]
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["feature_status"] == "READY"
    assert result["forecast_status"] == "READY"
    assert result["analysis_status"] == "CONFIGURATION_REQUIRED"


def test_agent_failure_keeps_python_results(tmp_path: Path) -> None:
    svc = service(tmp_path, rows(), FailedRuntime())
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["analysis_status"] == "FAILED"
    assert result["feature_status"] == "READY" and result["forecast_status"] == "READY"
    assert "model unavailable" in result["agent_error"]


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


def test_financial_agent_completes_without_inventing_numbers(tmp_path: Path) -> None:
    svc = service(tmp_path, rows())
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["analysis_status"] == "COMPLETED"
    assert {claim["type"] for claim in result["analysis"]["claims"]} == {"FACT", "FORECAST", "INFERENCE"}


def test_financial_agent_retries_when_schema_mode_returns_empty_object(tmp_path: Path, monkeypatch) -> None:
    svc = service(tmp_path, rows(), EmptyStructuredRuntime())

    class FallbackChat:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            assert "文本研究结论" in messages[0]["content"]
            return SimpleNamespace(content="经营趋势需要结合现金流持续核验。")

    import src.financial_analysis.service as financial_service
    monkeypatch.setattr(financial_service, "ChatLLM", FallbackChat)
    result = svc.analyze("000001.SZ", as_of="2026-08-14", refresh=False)
    assert result["analysis_status"] == "COMPLETED"


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
    }
    assert "财务快照" in captured["messages"][-1]["content"]
    assert [item[0] for item in progress] == [
        "financial_snapshot",
        "financial_snapshot_loaded",
        "model_analysis",
        "analysis_complete",
    ]
    dossier = svc.dossier("000001.SZ", as_of="2026-08-14")
    assert [entry["role"] for entry in dossier["chat_entries"]] == ["user", "assistant"]
    assert dossier["archive_summary"]["chat_entry_count"] == 2


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
    assert "当前估值解释" in captured["messages"][0]["content"]
    rendered = captured["messages"][-1]["content"]
    assert '"pe": 18.5' in rendered
    assert '"market_cap": 100.0' in rendered


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


def test_question_router_uses_rules_before_any_model_or_leader_data() -> None:
    assert classify_financial_question("你还能分析哪些方面？") == "capability"
    assert classify_financial_question("现金流质量怎么看？") == "general_method"
    assert classify_financial_question("三级行业有哪些龙头？") == "leader_pool"
    assert classify_financial_question("分析 600519 的盈利质量") == "company_lookup"
    assert classify_financial_question("那现金流呢？") == "company_lookup"
    assert classify_financial_question("帮我研究一下宁德时代") == "ambiguous"


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
    import src.api.financial_analysis_routes as routes
    monkeypatch.setattr(routes, "get_financial_analysis_service", lambda: svc)
    app = FastAPI()
    register_financial_analysis_routes(app, lambda: True)
    client = TestClient(app)
    response = client.get("/api/value/companies/000001.SZ/financial?as_of=2026-08-14")
    assert response.status_code == 200
    assert response.json()["forecast_status"] == "READY"
    dossier = client.get("/api/value/companies/000001.SZ/financial/dossier?as_of=2026-08-14")
    assert dossier.status_code == 200
    assert dossier.json()["snapshot"]["stock_code"] == "000001.SZ"
