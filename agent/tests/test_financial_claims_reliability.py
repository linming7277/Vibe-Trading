"""Financial claims per-claim validation + reliability semantics (2026-09-03 V1).

One violating claim rejects itself, never the batch; SUMMARY_ONLY never reads
as deep-complete; transport-transient errors retry exactly once; same-source
COMPLETED/PARTIAL are terminal without force.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.financial_analysis.service import (
    FinancialAnalysisService,
)
from src.structured_output import StructuredOutputMode
from src.structured_output.runtime import StructuredOutputResult

MANIFEST = {
    "HISTORY_REVENUE_2026H1": {"value": "本期营业收入123.45亿元，上年同期100亿元", "period": "2026-06-30"},
    "HISTORY_NET_PROFIT_2026H1": {"value": "本期净利润23.45亿元", "period": "2026-06-30"},
    "FORECAST_NET_PROFIT_BASE": {"value": "基准情景预测净利润30亿元", "period": "2026-12-31"},
}


def _claim(text: str = "公司营业收入保持增长态势（一）。", ctype: str = "FACT",
           keys: list | None = None) -> dict:
    return {"type": ctype, "text": text, "source_keys": keys or ["HISTORY_REVENUE_2026H1"],
            "confidence": "HIGH"}


def _validate(claims: list[dict], summary: str = "公司收入与利润保持稳健。"):
    return FinancialAnalysisService.validate_claims({"summary": summary, "claims": claims}, MANIFEST)


# ---------------------------------------------------------------------------
# Per-claim validation (matrix 1-5)
# ---------------------------------------------------------------------------

def test_one_invalid_claim_rejects_only_itself() -> None:
    claims = [_claim() for _ in range(7)]
    claims.append(_claim(text="公司毛利率高达999.99%。"))
    out = _validate(claims)
    assert len(out["claims"]) == 7
    assert out["rejected_claims"][0]["reason_code"] == "NUMERIC_MISMATCH"
    assert out["rejected_claims"][0]["claim_index"] == 7


def test_all_invalid_claims_rejected() -> None:
    out = _validate([_claim(text="毛利率高达999.99%。") for _ in range(3)])
    assert out["claims"] == [] and len(out["rejected_claims"]) == 3


def test_unknown_source_key_rejected_per_claim() -> None:
    out = _validate([_claim(), _claim(keys=["NOT_IN_MANIFEST"])])
    assert len(out["claims"]) == 1
    assert out["rejected_claims"][0]["reason_code"] == "UNKNOWN_SOURCE_KEY"


def test_fact_using_forecast_source_rejected_per_claim() -> None:
    out = _validate([_claim(), _claim(keys=["FORECAST_NET_PROFIT_BASE"])])
    assert out["rejected_claims"][0]["reason_code"] == "FACT_USING_FORECAST_SOURCE"


def test_trading_language_rejected_per_claim_only() -> None:
    out = _validate([_claim(), _claim(text="建议买入该公司股票。")])
    assert len(out["claims"]) == 1
    assert out["rejected_claims"][0]["reason_code"] == "TRADING_LANGUAGE"
    # Trading language in the single summary field stays a whole refusal.
    try:
        _validate([_claim()], summary="建议买入。")
    except Exception as exc:
        assert getattr(exc, "code", "") == "TRADING_LANGUAGE"
    else:
        raise AssertionError("summary trading language must refuse the result")


# ---------------------------------------------------------------------------
# analyze-level semantics with stubbed runtime (matrix 6-12, 14)
# ---------------------------------------------------------------------------

_SNAPSHOT = {
    "id": "financial_test_1", "stock_code": "600001.SH", "stock_name": "测试股份",
    "analysis_status": "NOT_RUN", "analysis": None, "agent_model": None,
    "identity": {"stock_code": "600001.SH"}, "feature": {"trends": {}}, "forecast": {},
    "data_gaps": [], "source_hash": "hash-a",
}


class _StubStore:
    def __init__(self) -> None:
        self.results: dict[str, dict] = {}
        self.updates: list[tuple[str, str]] = []

    def latest(self, code: str, as_of: str | None = None) -> dict | None:
        return self.results.get(code)

    def update_agent_result(self, snapshot_id: str, *, status: str, provider: str,
                            model: str, analysis: dict | None = None, error: str = "") -> dict:
        self.updates.append((snapshot_id, status))
        row = {"id": snapshot_id, "analysis_status": status, "analysis": analysis,
               "agent_model": model, "agent_error": error}
        self.results[row.get("stock_code") or "600001.SH"] = row
        return row


class _StubConfig:
    def get_runtime_config(self, role: str = "financial_analyst") -> dict:
        return {"provider": "openai", "model": "stub", "enabled": True,
                "base_url": "http://stub.local", "api_key": "k", "structured_output": {}}

    get_config = get_runtime_config

    def list_configs(self) -> list[dict]:
        return [{"provider": "openai", "configured": True}]

    def close(self) -> None:
        return None


def _service(model_output: dict | None, *, fail_first_with: type | None = None,
             text: str | None = None, snapshot: dict | None = None) -> tuple[FinancialAnalysisService, SimpleNamespace]:
    counter = SimpleNamespace(invokes=0)
    snap = dict(snapshot or _SNAPSHOT)

    def _invoke_connection(**_: object) -> dict:
        # Transport layer stub: optional first failure, then real output or a
        # persistent transport error when no model output is configured.
        if fail_first_with is not None and counter.invokes == 0:
            counter.invokes += 1
            raise fail_first_with("transport")
        counter.invokes += 1
        if model_output is None:
            raise ConnectionError("provider unreachable")
        return dict(model_output)

    def _run(**kwargs: object) -> StructuredOutputResult:
        invoke_structured = kwargs["invoke_structured"]  # type: ignore[index]
        validate = kwargs["validate"]  # type: ignore[index]
        try:
            raw = invoke_structured(StructuredOutputMode.JSON_OBJECT, {"type": "json_object"})
            parsed = validate(raw)
            return StructuredOutputResult(
                parsed=parsed, text=None, mode_requested="JSON_OBJECT", mode_used="JSON_OBJECT",
                fallback_path=[], attempts=[{"mode": "JSON_OBJECT", "success": True}],
                error_types=[], capability_profile={}, capability_source="test",
            )
        except Exception as exc:
            return StructuredOutputResult(
                parsed=None, text=text, mode_requested="JSON_OBJECT", mode_used="JSON_OBJECT",
                fallback_path=[], attempts=[{"mode": "JSON_OBJECT", "success": False}],
                error_types=[{"mode": "JSON_OBJECT", "type": type(exc).__name__}],
                capability_profile={}, capability_source="test",
            )

    svc = FinancialAnalysisService.__new__(FinancialAnalysisService)
    store = _StubStore()
    svc.store = store
    svc.config_store = _StubConfig()
    svc.runtime = SimpleNamespace(invoke_with_connection=_invoke_connection)
    svc.structured_runtime = SimpleNamespace(run=_run)

    def _prepare(code: str, as_of: str | None = None) -> dict:
        latest = store.latest(code) or {}
        return {**snap, **{k: latest[k] for k in ("id", "analysis_status", "analysis", "agent_model") if k in latest},
                "stock_code": code}

    svc.prepare = _prepare  # type: ignore[method-assign]
    svc._agent_config = lambda: ({"provider": "openai", "model": "stub", "base_url": "http://x"}, True)  # type: ignore[method-assign]
    svc._evidence_manifest = lambda snapshot_: dict(MANIFEST)  # type: ignore[method-assign]
    return svc, counter


_GOOD = {"summary": "公司收入与利润保持稳健。",
         "claims": [_claim() for _ in range(6)]
         + [{"type": "FACT", "text": "毛利率高达999.99%。", "source_keys": ["HISTORY_REVENUE_2026H1"],
             "confidence": "HIGH"}]}


def test_summary_only_is_partial_never_completed(tmp_path: Path) -> None:
    svc, counter = _service(None, text="公司经营保持稳健，收入平稳。")
    out = svc.analyze("600001.SH", refresh=False)
    assert out["analysis_status"] == "PARTIAL"
    assert out["analysis"]["claims_status"] == "SUMMARY_ONLY"
    assert out["analysis"]["claims"] == []
    assert counter.invokes == 2  # 1 transport fail + exactly 1 retry, then text fallback


def test_all_claims_rejected_keeps_real_summary_as_partial(tmp_path: Path) -> None:
    svc, counter = _service({"summary": "真实摘要。", "claims": [
        {"type": "FACT", "text": "毛利率高达999.99%。", "source_keys": ["HISTORY_REVENUE_2026H1"], "confidence": "HIGH"}]})
    out = svc.analyze("600001.SH", refresh=False)
    assert out["analysis_status"] == "PARTIAL"
    assert out["analysis"]["claims_status"] == "SUMMARY_ONLY"
    assert out["analysis"]["executive_summary"] == "真实摘要。"
    assert len(out["analysis"]["rejected_claims"]) == 1
    assert counter.invokes == 1


def test_completed_with_valid_claims(tmp_path: Path) -> None:
    svc, _ = _service(_GOOD)
    out = svc.analyze("600001.SH", refresh=False)
    assert out["analysis_status"] == "COMPLETED"
    assert out["analysis"]["claims_status"] == "CLAIMS_READY"
    assert len(out["analysis"]["claims"]) == 6
    assert len(out["analysis"]["rejected_claims"]) == 1


def test_transport_transient_retries_exactly_once(tmp_path: Path) -> None:
    class APITimeoutError(Exception):
        pass

    svc, counter = _service(_GOOD, fail_first_with=APITimeoutError)
    out = svc.analyze("600001.SH", refresh=False)
    assert counter.invokes == 2  # 1 fail + exactly 1 retry
    assert out["analysis_status"] == "COMPLETED"


def test_validation_failure_never_reinvokes_model(tmp_path: Path) -> None:
    svc, counter = _service({"summary": "摘要。",
                             "claims": [_claim(text="毛利率高达999.99%。")]})
    out = svc.analyze("600001.SH", refresh=False)
    assert counter.invokes == 1  # per-claim rejection, no second request
    assert out["analysis_status"] == "PARTIAL"


def test_completed_same_source_reused(tmp_path: Path) -> None:
    svc, counter = _service(_GOOD)
    svc.analyze("600001.SH", refresh=False)
    again = svc.analyze("600001.SH", refresh=False)
    assert counter.invokes == 1  # only the first run
    assert again.get("idempotent_reuse") is True


def test_partial_same_source_reused_no_auto_upgrade(tmp_path: Path) -> None:
    svc, counter = _service(None, text="摘要文本。")
    first = svc.analyze("600001.SH", refresh=False)
    assert first["analysis_status"] == "PARTIAL"
    again = svc.analyze("600001.SH", refresh=False)
    assert counter.invokes == 2  # first run only (fail + retry); reuse adds none
    assert again.get("idempotent_reuse") is True


def test_force_explicit_repair_runs_again(tmp_path: Path) -> None:
    svc, counter = _service(None, text="摘要文本。")
    first = svc.analyze("600001.SH", refresh=False)
    assert first["analysis_status"] == "PARTIAL"
    svc.analyze("600001.SH", refresh=False)  # 默认复用，不重跑
    assert counter.invokes == 2
    forced = svc.analyze("600001.SH", refresh=False, force=True)  # 显式人工修复才允许
    assert counter.invokes == 4  # force 真正重新请求了模型（fail+retry 再次发生）
    assert "idempotent_reuse" not in forced


def test_changed_source_permits_new_analysis(tmp_path: Path) -> None:
    svc, counter = _service(_GOOD)
    svc.analyze("600001.SH", refresh=False)
    new_snap = dict(_SNAPSHOT, id="financial_test_2", source_hash="hash-b", analysis_status="NOT_RUN", analysis=None)
    svc.prepare = lambda code, as_of=None: dict(new_snap)  # type: ignore[method-assign]
    out = svc.analyze("600001.SH", refresh=False)
    assert counter.invokes == 2
    assert out["analysis_status"] == "COMPLETED"


def test_transport_total_failure_is_failed_not_placeholder(tmp_path: Path) -> None:
    class APIConnectionError(Exception):
        pass

    svc, counter = _service(None, fail_first_with=APIConnectionError)
    # retry also fails (model_output None → parsed None, no text)
    out = svc.analyze("600001.SH", refresh=False)
    assert counter.invokes == 2
    assert out["analysis_status"] == "FAILED"
    assert "TRANSPORT_FAILED" in str(out.get("agent_error") or "")
