from __future__ import annotations

import pytest

from src.financial_analysis.service import ClaimValidationError, FinancialAnalysisService, MAX_CLAIMS
from src.structured_output import StructuredOutputCapabilities, StructuredOutputMode, StructuredOutputRuntime


MANIFEST = {
    "FIN_REVENUE_2025": {"metric": "revenue", "period": "2025", "value": 100.0, "source_type": "PIT_FINANCIAL_HISTORY"},
    "FIN_OCF_2025": {"metric": "operating_cash_flow", "period": "2025", "value": 80.0, "source_type": "PIT_FINANCIAL_HISTORY"},
    "FORECAST_BASE_REVENUE_2027": {"metric": "revenue", "period": "2027", "value": 130.0, "source_type": "DETERMINISTIC_FORECAST", "scenario": "BASE"},
}


def result(*claims: dict) -> dict:
    return {"summary": "基于确定性快照的研究摘要。", "claims": list(claims)}


def claim(claim_type: str, keys: list[str], *, text: str = "已记录的财务变化", confidence: str = "HIGH") -> dict:
    return {"type": claim_type, "text": text, "source_keys": keys, "confidence": confidence}


def test_valid_fact_inference_forecast_unknown_and_max_claims() -> None:
    valid = result(
        claim("FACT", ["FIN_REVENUE_2025"]),
        claim("INFERENCE", ["FIN_REVENUE_2025", "FIN_OCF_2025"]),
        claim("FORECAST", ["FORECAST_BASE_REVENUE_2027"], text="Base 情景预测的收入已列入系统推演"),
        claim("UNKNOWN", [], text="当前数据无法判断客户集中度", confidence="LOW"),
    )
    parsed = FinancialAnalysisService.validate_claims(valid, MANIFEST)
    assert len(parsed["claims"]) == 4
    too_many = result(*[claim("FACT", ["FIN_REVENUE_2025"]) for _ in range(MAX_CLAIMS + 1)])
    with pytest.raises(ValueError, match="max_claims"):
        FinancialAnalysisService.validate_claims(too_many, MANIFEST)


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (claim("FACT", []), "FACT requires"),
        (claim("INFERENCE", []), "INFERENCE requires"),
        (claim("FORECAST", ["FIN_REVENUE_2025"], text="这是情景预测"), "FORECAST must"),
        (claim("FACT", ["FORECAST_BASE_REVENUE_2027"]), "FACT cannot"),
        (claim("FACT", ["MISSING_KEY"]), "unknown source"),
        (claim("OPINION", ["FIN_REVENUE_2025"]), "invalid claim type"),
        (claim("FACT", ["FIN_REVENUE_2025"], confidence="CERTAIN"), "invalid claim confidence"),
        (claim("FACT", ["FIN_REVENUE_2025"], text="收入为 999"), "numbers absent"),
    ],
)
def test_claim_validation_rejects_invalid_sources_types_and_numbers(bad: dict, message: str) -> None:
    # Per-claim contract: the single violating claim is rejected (with the
    # same message) while the rest of the result survives.
    out = FinancialAnalysisService.validate_claims(result(bad), MANIFEST)
    assert out["claims"] == []
    assert out["rejected_claims"] and message in out["rejected_claims"][0]["detail"]


def test_claim_validation_accepts_exact_float_amount_abbreviation_and_directional_negative() -> None:
    manifest = {
        "FIN_REVENUE_2025": {"metric": "revenue", "period": "2025", "value": 39353112576.0, "unit": "CNY"},
        "FEATURE_OCF_CHANGE_2026Q1": {"metric": "operating_cash_flow_change_percent", "period": "2026Q1", "value": -143.273, "unit": "percent"},
        "FORECAST_BASE_REVENUE_2026": {"metric": "revenue", "period": "2026", "value": 51159046348.8, "unit": "CNY"},
        "FORECAST_BASE_REVENUE_2028": {"metric": "revenue", "period": "2028", "value": 80565266190.09, "unit": "CNY"},
    }
    parsed = FinancialAnalysisService.validate_claims(result(
        claim("FACT", ["FIN_REVENUE_2025"], text="2025年收入为393.53亿元"),
        claim("FACT", ["FIN_REVENUE_2025"], text="2025年收入为39,353,112,576元"),
        claim("FACT", ["FEATURE_OCF_CHANGE_2026Q1"], text="经营现金流同比下降143.273%"),
        claim("FORECAST", ["FORECAST_BASE_REVENUE_2026", "FORECAST_BASE_REVENUE_2028"], text="Base情景预测覆盖2026-2028年"),
    ), manifest)
    assert len(parsed["claims"]) == 4


def test_claim_validation_keeps_numeric_sign_and_unit_requirements() -> None:
    manifest = {
        "FEATURE_OCF_CHANGE_2026Q1": {"metric": "operating_cash_flow_change_percent", "period": "2026Q1", "value": -143.273, "unit": "percent"},
        "FIN_REVENUE_2025": {"metric": "revenue", "period": "2025", "value": 39353112576.0, "unit": "CNY"},
    }
    for invalid in (
        claim("FACT", ["FEATURE_OCF_CHANGE_2026Q1"], text="经营现金流同比增长143.273%"),
        claim("FACT", ["FIN_REVENUE_2025"], text="2025年收入为393.53"),
    ):
        out = FinancialAnalysisService.validate_claims(result(invalid), manifest)
        assert out["claims"] == []
        assert out["rejected_claims"][0]["reason_code"] == "NUMERIC_MISMATCH"


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ({"summary": "x", "claims": [], "extra": True}, "TOP_LEVEL_SCHEMA_INVALID"),
        (result(*[claim("FACT", ["FIN_REVENUE_2025"]) for _ in range(MAX_CLAIMS + 1)]), "TOO_MANY_CLAIMS"),
        (result(claim("OPINION", ["FIN_REVENUE_2025"])), "INVALID_CLAIM_TYPE"),
        (result(claim("FACT", ["FIN_REVENUE_2025"], confidence="CERTAIN")), "INVALID_CONFIDENCE"),
        (result(claim("FACT", [])), "FACT_WITHOUT_SOURCE"),
        (result(claim("INFERENCE", [])), "INFERENCE_WITHOUT_SOURCE"),
        (result(claim("FORECAST", [])), "FORECAST_WITHOUT_SOURCE"),
        (result(claim("FACT", ["UNKNOWN"])), "UNKNOWN_SOURCE_KEY"),
        (result(claim("FACT", ["FORECAST_BASE_REVENUE_2027"])), "FACT_USING_FORECAST_SOURCE"),
        (result(claim("FORECAST", ["FIN_REVENUE_2025"], text="情景预测")), "FORECAST_USING_NON_FORECAST_SOURCE"),
        (result(claim("FACT", ["FIN_REVENUE_2025"], text="收入为 999")), "NUMERIC_MISMATCH"),
        (result(claim("FACT", ["FIN_REVENUE_2025"], text="建议买入")), "TRADING_LANGUAGE"),
        (result(claim("FACT", ["FIN_REVENUE_2025"], text="")), "EMPTY_CLAIM_TEXT"),
    ],
)
def test_claim_validation_error_codes(bad: dict, code: str) -> None:
    # Per-claim contract (2026-09-03): top-level failures still raise;
    # claim-level failures reject only that claim with the same code.
    if code in {"TOP_LEVEL_SCHEMA_INVALID", "TOO_MANY_CLAIMS"}:
        with pytest.raises(ClaimValidationError) as caught:
            FinancialAnalysisService.validate_claims(bad, MANIFEST)
        assert caught.value.code == code
        assert caught.value.audit_dict()["validation_error_code"] == code
        return
    out = FinancialAnalysisService.validate_claims(bad, MANIFEST)
    assert out["claims"] == []
    assert out["rejected_claims"] and out["rejected_claims"][0]["reason_code"] == code


def test_manifest_keys_are_stable_and_include_snapshot_metadata() -> None:
    snapshot = {
        "id": "financial_test", "source_hash": "hash", "as_of": "2026-08-14",
        "history": [{"report_date": "2025-12-31", "announcement_date": "2026-03-31", "source": "TDX", "revenue": 100, "net_profit": 10, "roe": 12}],
        "feature": {"latest_changes": [{"metric": "revenue", "change_percent": 20, "report_date": "2025-12-31"}]},
        "forecast": {"forecast_version": "v1", "scenarios": {"BASE": {"forecast": [{"year": "2027E", "revenue": 130, "net_profit": 13}]}}},
    }
    manifest = FinancialAnalysisService._evidence_manifest(snapshot)
    assert "FIN_REVENUE_2025" in manifest
    assert "FEATURE_REVENUE_CHANGE_2025" in manifest
    assert "FORECAST_BASE_REVENUE_2027" in manifest
    assert manifest["FIN_REVENUE_2025"]["source_snapshot_id"] == "financial_test"
    assert manifest["FORECAST_BASE_REVENUE_2027"]["forecast_version"] == "v1"


def test_compatibility_adapter_preserves_legacy_analysis_fields() -> None:
    snapshot = {
        "id": "financial_test", "source_hash": "hash", "as_of": "2026-08-14", "stock_code": "000001.SZ", "stock_name": "测试",
        "feature": {"trends": {}, "latest_changes": []}, "forecast": {"assumption_notes": []}, "data_gaps": [], "history": [],
    }
    manifest = FinancialAnalysisService._evidence_manifest(snapshot)
    analysis = FinancialAnalysisService._compatibility_analysis(
        snapshot, result(claim("UNKNOWN", [], text="数据不足", confidence="LOW")), manifest,
        quality_status="SUMMARY_ONLY", fallback_path="summary_only",
    )
    assert {"executive_summary", "historical_performance", "forecast_analysis", "claims", "analysis_metadata"} <= set(analysis)
    assert analysis["analysis_metadata"]["evidence_ready"] is False


def test_provider_runtime_uses_compact_financial_claims_schema() -> None:
    contract = FinancialAnalysisService.claims_contract_schema(MANIFEST)
    response_format = StructuredOutputRuntime.provider_schema(
        contract, StructuredOutputMode.JSON_SCHEMA,
        StructuredOutputCapabilities(supports_json_schema=True, supports_enum=True,
                                     supports_array_constraints=True, supports_additional_properties_false=True),
    )
    assert response_format is not None
    schema = response_format["json_schema"]["schema"]
    assert set(schema["properties"]) == {"summary", "claims"}
    assert schema["properties"]["claims"]["maxItems"] == 8
    claim_schema = schema["properties"]["claims"]["items"]
    assert set(claim_schema["properties"]) == {"type", "text", "source_keys", "confidence"}
    assert claim_schema["properties"]["source_keys"]["items"]["enum"] == sorted(MANIFEST)
