from __future__ import annotations

from src.financial_analysis.citations import FinancialClaimCitationResolver


MANIFEST = {
    "FIN_NET_PROFIT_2025": {
        "source_type": "PIT_FINANCIAL_HISTORY", "source": "TongDaXin professional finance / TQ",
        "metric": "net_profit", "period": "2025", "value": 123456789.0, "unit": "CNY",
        "data_as_of": "2026-08-17", "source_snapshot_id": "financial-1", "source_hash": "hash-financial-1",
    },
    "FIN_OCF_2025": {
        "source_type": "PIT_FINANCIAL_HISTORY", "metric": "operating_cash_flow", "period": "2025",
        "value": 100.0, "unit": "CNY", "data_as_of": "2026-08-17",
        "source_snapshot_id": "financial-1", "source_hash": "hash-financial-1",
    },
    "FORECAST_BASE_REVENUE_2027": {
        "source_type": "DETERMINISTIC_FORECAST", "metric": "revenue", "period": "2027", "value": 150.0,
        "unit": "CNY", "data_as_of": "2026-08-17", "source_snapshot_id": "financial-1",
        "source_hash": "hash-financial-1", "scenario": "BASE", "forecast_year": "2027E", "forecast_version": "forecast-v1",
    },
}


def snapshot(*claims: dict, manifest: dict = MANIFEST) -> dict:
    return {
        "id": "financial-1", "stock_code": "000001.SZ", "source_hash": "hash-financial-1",
        "analysis": {
            "claims": list(claims),
            "analysis_metadata": {"evidence_manifest": manifest},
        },
    }


def claim(kind: str, keys: list[str]) -> dict:
    return {"type": kind, "statement": "已验证的声明", "source_keys": keys, "confidence": "HIGH"}


def test_resolver_maps_fact_inference_forecast_and_unknown() -> None:
    result = FinancialClaimCitationResolver().resolve_snapshot(snapshot(
        claim("FACT", ["FIN_NET_PROFIT_2025"]),
        claim("INFERENCE", ["FIN_NET_PROFIT_2025", "FIN_OCF_2025"]),
        claim("FORECAST", ["FORECAST_BASE_REVENUE_2027"]),
        claim("UNKNOWN", []),
    ))
    claims = result["analysis"]["claims"]
    fact = claims[0]["citations"][0]
    assert fact == {
        "source_key": "FIN_NET_PROFIT_2025", "status": "RESOLVED", "source_type": "FINANCIAL_HISTORY",
        "source": "TongDaXin professional finance / TQ", "metric": "net_profit", "period": "2025",
        "value": 123456789.0, "unit": "CNY", "data_as_of": "2026-08-17",
        "source_snapshot_id": "financial-1", "source_hash": "hash-financial-1",
    }
    assert len(claims[1]["citations"]) == 2
    forecast = claims[2]["citations"][0]
    assert forecast["source_type"] == "DETERMINISTIC_FORECAST"
    assert {"scenario", "forecast_year", "forecast_version"} <= set(forecast)
    assert claims[3]["citations"] == []
    assert result["traceability_status"] == "COMPLETE"
    assert result["citation_stats"] == {
        "claims_total": 4, "claims_with_citations": 3,
        "resolved_source_keys": 4, "unresolved_source_keys": 0,
    }


def test_resolver_marks_missing_source_key_partial_without_throwing() -> None:
    result = FinancialClaimCitationResolver().resolve_snapshot(snapshot(
        claim("FACT", ["FIN_NET_PROFIT_2025"]),
        claim("INFERENCE", ["MISSING_KEY"]),
    ))
    missing = result["analysis"]["claims"][1]["citations"]
    assert missing == [{"source_key": "MISSING_KEY", "status": "UNRESOLVED"}]
    assert result["traceability_status"] == "PARTIAL"
    assert result["citation_stats"]["unresolved_source_keys"] == 1
    assert result["analysis"]["analysis_metadata"]["citation_diagnostics"] == [
        {"claim_index": 1, "source_key": "MISSING_KEY", "status": "UNRESOLVED"},
    ]


def test_resolver_marks_unresolvable_historical_claims_and_summary_only() -> None:
    resolver = FinancialClaimCitationResolver()
    unresolved = resolver.resolve_snapshot(snapshot(claim("FACT", ["MISSING_KEY"]), manifest={}))
    assert unresolved["traceability_status"] == "UNRESOLVED"
    summary_only = resolver.resolve_snapshot(snapshot())
    assert summary_only["traceability_status"] == "NOT_APPLICABLE"
    assert summary_only["citation_stats"]["claims_total"] == 0


def test_resolver_only_exposes_allowlisted_citation_fields() -> None:
    manifest = {**MANIFEST, "FIN_SECRET_2025": {
        **MANIFEST["FIN_NET_PROFIT_2025"], "api_key": "must-not-leak", "prompt": "must-not-leak",
        "base_url": "https://internal.invalid", "source": "C:\\internal\\financial.json",
    }}
    result = FinancialClaimCitationResolver().resolve_snapshot(snapshot(claim("FACT", ["FIN_SECRET_2025"]), manifest=manifest))
    citation = result["analysis"]["claims"][0]["citations"][0]
    assert citation["source"] == "TDX PIT 财务"
    assert "api_key" not in citation and "prompt" not in citation and "base_url" not in citation
