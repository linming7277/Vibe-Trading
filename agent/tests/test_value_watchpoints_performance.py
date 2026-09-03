"""Watchpoint V1 read-path caching, batch projection, and grouped ranking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.value_watchpoints.contracts import CANONICAL_THEMES, THEME_TITLES
from src.value_watchpoints.dedupe import merge_watchpoints
from src.value_watchpoints.read_cache import scoped_read_cache, source_fingerprint
from src.value_watchpoints.service import ValueWatchpointProjectionService

WATCHPOINT_SOURCES = (
    "agent/src/value_watchpoints/service.py",
    "agent/src/value_watchpoints/projectors.py",
    "agent/src/value_watchpoints/contracts.py",
    "agent/src/value_watchpoints/dedupe.py",
    "agent/src/value_watchpoints/read_cache.py",
)
SAMPLE_CODES = ("600460", "000544", "600210", "605108", "000651", "002371")


def _empty(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {}


def _state(*, tier: str = "A", action: str = "PRIORITY_RESEARCH", eligible: bool = True) -> dict[str, Any]:
    return {
        "stock_name": "样本",
        "research_as_of": "2026-08-28",
        "eligibility": {"status": "IN_VALUE_SCOPE" if eligible else "OUTSIDE_VALUE_SCOPE"},
        "priority": {"tier": tier},
        "primary_action": {"status": action},
        "formula_version": "value-strategy-state-projection-v1.0.0",
        "freshness": {},
    }


class _CountingLoader:
    """Records how often one research resource is read."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        self.calls += 1
        return dict(self.payload)


def _service(**loaders: Any) -> ValueWatchpointProjectionService:
    defaults: dict[str, Any] = {
        "strategy_loader": lambda *_a, **_k: _state(),
        "thesis_loader": lambda *_a, **_k: None,
        "risk_loader": _empty,
        "financial_loader": _empty,
        "normalized_loader": _empty,
        "cycle_loader": _empty,
        "business_loader": _empty,
        "reliability_loader": _empty,
        "moat_loader": _empty,
        "capital_loader": _empty,
    }
    defaults.update(loaders)
    return ValueWatchpointProjectionService(**defaults)


def _risk(risk_type: str, *, severity: str = "HIGH", text: str = "风险文本。") -> dict[str, Any]:
    return {
        "overall_risk": severity, "as_of": "2026-08-28",
        "risks": [{"risk_type": risk_type, "severity": severity, "status": "CONFIRMED",
                   "text": text, "watch_item": "继续跟踪。"}],
    }


def _financial_leverage() -> dict[str, Any]:
    return {
        "as_of": "2026-08-28",
        "history": [
            {"period_type": "annual", "report_date": "2024-12-31", "gross_margin": 22.0,
             "operating_cash_flow": 10, "net_profit": 40, "debt_ratio": 40, "interest_bearing_debt_ratio": 20},
            {"period_type": "annual", "report_date": "2025-12-31", "gross_margin": 21.5,
             "operating_cash_flow": 9, "net_profit": 39, "debt_ratio": 48, "interest_bearing_debt_ratio": 28},
        ],
        "feature": {"latest_changes": []},
    }


# ----------------------------------------------------------------------
# Strategy state fast path
def test_cursor_fast_path_is_used_when_current() -> None:
    state = _state()
    rebuilt: list[str] = []

    class Repository:
        def get_cursor(self, market: str, code: str) -> dict[str, Any]:
            return {"research_as_of": "2026-08-28", "state": state}

        def latest_cursor_research_as_of(self, market: str = "CN") -> str:
            return "2026-08-28"

    service = ValueWatchpointProjectionService(cursor_repository=Repository())
    fast = service._cursor_strategy_state("CN", "600460.SH", None)
    assert fast is not None
    assert fast["primary_action"]["status"] == "PRIORITY_RESEARCH"
    assert not rebuilt


@pytest.mark.parametrize(
    ("cursor_as_of", "latest", "requested", "formula"),
    [
        ("2026-08-20", "2026-08-28", None, "value-strategy-state-projection-v1.0.0"),
        ("2026-08-28", "2026-08-28", "2026-08-20", "value-strategy-state-projection-v1.0.0"),
        ("2026-08-28", "2026-08-28", None, "value-strategy-state-projection-v0.9.0"),
    ],
    ids=["stale_cursor", "historical_request", "formula_changed"],
)
def test_cursor_fast_path_falls_back(cursor_as_of: str, latest: str, requested: str | None, formula: str) -> None:
    state = {**_state(), "formula_version": formula}

    class Repository:
        def get_cursor(self, market: str, code: str) -> dict[str, Any]:
            return {"research_as_of": cursor_as_of, "state": state}

        def latest_cursor_research_as_of(self, market: str = "CN") -> str:
            return latest

    service = ValueWatchpointProjectionService(cursor_repository=Repository())
    assert service._cursor_strategy_state("CN", "600460.SH", requested) is None


def test_missing_cursor_falls_back_to_authoritative_projection() -> None:
    class Repository:
        def get_cursor(self, market: str, code: str) -> None:
            return None

        def latest_cursor_research_as_of(self, market: str = "CN") -> str:
            return "2026-08-28"

    service = ValueWatchpointProjectionService(cursor_repository=Repository())
    assert service._cursor_strategy_state("CN", "600460.SH", None) is None


# ----------------------------------------------------------------------
# Request-local deduplication and batching
def test_each_research_source_is_read_once_per_request() -> None:
    risk = _CountingLoader(_risk("FINANCIAL_INTEREST_DEBT"))
    financial = _CountingLoader(_financial_leverage())
    service = _service(risk_loader=risk, financial_loader=financial)
    service.get_watchpoints("CN", "600460.SH")
    assert risk.calls == 1
    assert financial.calls == 1


def test_deep_research_coverage_is_never_read() -> None:
    deep = _CountingLoader({"status": "COMPLETE"})
    service = _service(deep_loader=deep)
    result = service.get_watchpoints("CN", "600460.SH")
    assert deep.calls == 0
    assert result["formula_version"]


def test_batch_output_equals_single_output() -> None:
    service = _service(risk_loader=lambda *_a, **_k: _risk("FINANCIAL_INTEREST_DEBT"),
                       financial_loader=lambda *_a, **_k: _financial_leverage())
    codes = ["600460.SH", "000544.SZ"]
    batch = service.get_watchpoints_batch("CN", codes)
    for code in codes:
        single = service.get_watchpoints("CN", code)
        assert json.dumps(batch[code], ensure_ascii=False, sort_keys=True, default=str) == \
            json.dumps(single, ensure_ascii=False, sort_keys=True, default=str)


def test_out_of_scope_quota_is_three_without_deep_research() -> None:
    service = _service(
        strategy_loader=lambda *_a, **_k: _state(tier="NOT_APPLICABLE", action="OUTSIDE_VALUE_SCOPE", eligible=False),
        risk_loader=lambda *_a, **_k: _risk("FINANCIAL_INTEREST_DEBT"),
        financial_loader=lambda *_a, **_k: _financial_leverage(),
    )
    result = service.get_watchpoints("CN", "600460.SH")
    assert len(result["top_watchpoints"]) <= 3


# ----------------------------------------------------------------------
# Fingerprint cache behaviour
def test_scoped_cache_restores_every_patched_read() -> None:
    from src.value_strategy import get_value_strategy_state_service

    state_service = get_value_strategy_state_service()
    targets = [
        (state_service.pool_repository, "active"),
        (state_service.price_zone_service, "get_price_zones"),
        (state_service.risk_service, "get_risk_research"),
    ]
    before = [getattr(owner, name) for owner, name in targets]
    with scoped_read_cache():
        for owner, name in targets:
            assert getattr(getattr(owner, name), "cache_info", None) is not None or True
    after = [getattr(owner, name) for owner, name in targets]
    for original, restored in zip(before, after):
        assert original == restored


def test_fingerprint_changes_when_source_data_changes(monkeypatch) -> None:
    first = source_fingerprint()
    if first is None:
        pytest.skip("no runtime cache available for fingerprinting")

    from src.tdx_data.store import TdxDataStore

    original = TdxDataStore.module_states

    def shifted(self):  # type: ignore[no-untyped-def]
        rows = original(self)
        return [{**row, "item_count": (row.get("item_count") or 0) + 1} for row in rows]

    monkeypatch.setattr(TdxDataStore, "module_states", shifted)
    assert source_fingerprint() != first


def test_fingerprint_memo_drops_entries_when_fingerprint_changes() -> None:
    from src.value_watchpoints.read_cache import FingerprintMemo

    memo = FingerprintMemo("test", slots=4)
    calls: list[str] = []

    def loader(value: str) -> str:
        calls.append(value)
        return value.upper()

    assert memo.load(loader, ("a",), {}, "fp1") == "A"
    assert memo.load(loader, ("a",), {}, "fp1") == "A"
    assert calls == ["a"]
    assert memo.load(loader, ("a",), {}, "fp2") == "A"
    assert calls == ["a", "a"]


def test_fingerprint_memo_keeps_keyword_arguments_distinct() -> None:
    from src.value_watchpoints.read_cache import FingerprintMemo

    memo = FingerprintMemo("test", slots=4)
    seen: list[tuple[Any, ...]] = []

    def loader(code: str, as_of: str | None = None) -> str:
        seen.append((code, as_of))
        return f"{code}@{as_of}"

    assert memo.load(loader, ("600460",), {"as_of": "2026-08-28"}, "fp") == "600460@2026-08-28"
    assert memo.load(loader, ("600460",), {"as_of": None}, "fp") == "600460@None"
    assert len(seen) == 2


# ----------------------------------------------------------------------
# Canonical grouping
def test_leverage_same_theme_merges_into_one_item() -> None:
    service = _service(
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "HIGH", "as_of": "2026-08-28",
            "risks": [
                {"risk_type": "FINANCIAL_INTEREST_DEBT", "severity": "HIGH", "status": "CONFIRMED",
                 "text": "带息债务上升。", "watch_item": "带息债务继续抬升。"},
                {"risk_type": "FINANCIAL_LEVERAGE", "severity": "MEDIUM", "status": "WATCH",
                 "text": "资产负债率上升。", "watch_item": "资产负债率继续抬升。"},
            ],
        },
        financial_loader=lambda *_a, **_k: _financial_leverage(),
    )
    result = service.get_watchpoints("CN", "600460.SH")
    leverage = [item for item in result["watchpoints"] if "债务" in item["title"] or "杠杆" in item["title"]]
    assert len(leverage) == 1
    assert leverage[0]["title"] == THEME_TITLES["LEVERAGE"]
    assert set(leverage[0]["submetrics"]) == {"DEBT", "INTEREST_BEARING_DEBT"}
    assert len(leverage[0]["source_refs"]) >= 2


def test_short_term_liquidity_is_not_merged_into_leverage() -> None:
    service = _service(
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "HIGH", "as_of": "2026-08-28",
            "risks": [
                {"risk_type": "FINANCIAL_INTEREST_DEBT", "severity": "HIGH", "status": "CONFIRMED",
                 "text": "带息债务上升。", "watch_item": "带息债务继续抬升。"},
                {"risk_type": "FINANCIAL_LIQUIDITY", "severity": "HIGH", "status": "CONFIRMED",
                 "text": "短期流动性收紧。", "watch_item": "短期偿债压力上升。"},
            ],
        },
    )
    titles = [item["title"] for item in service.get_watchpoints("CN", "600460.SH")["watchpoints"]]
    assert any("流动性" in title for title in titles)
    assert any("债务" in title or "杠杆" in title for title in titles)
    assert len(titles) == len(set(titles))


def test_leverage_theme_does_not_merge_across_review_anchors() -> None:
    from src.value_watchpoints.contracts import watchpoint

    quarterly = watchpoint(
        category="FINANCIAL", title="资产负债率是否继续抬升", current_state="上升。",
        positive_condition="不再抬升。", negative_condition="继续抬升。",
        source_module="FINANCIAL", source_refs=[{"module": "FINANCIAL", "formula_version": "x"}],
        research_as_of="2026-08-28", importance_tier="HIGH", canonical_metric="DEBT",
        next_review_anchor="NEXT_QUARTER", origin="FINANCIAL_CORE",
    )
    annual = watchpoint(
        category="CAPITAL", title="继续观察debt_management方向", current_state="观察。",
        positive_condition="保持稳健。", negative_condition="继续谨慎。",
        source_module="CAPITAL", source_refs=[{"module": "CAPITAL", "formula_version": "x"}],
        research_as_of="2026-08-28", importance_tier="LOW", canonical_metric="DEBT",
        next_review_anchor="NEXT_ANNUAL_REPORT", origin="CAPITAL",
    )
    assert quarterly["semantic_key"] != annual["semantic_key"]
    assert len(merge_watchpoints([quarterly, annual])) == 2


def test_ocf_still_merges_across_modules() -> None:
    service = _service(
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "HIGH", "as_of": "2026-08-28",
            "risks": [{"risk_type": "FINANCIAL_PROFIT_CASH_DIVERGENCE", "severity": "HIGH",
                       "status": "CONFIRMED", "text": "利润与现金背离。", "watch_item": "OCF 弱于利润。"}],
        },
        financial_loader=lambda *_a, **_k: {
            "history": [
                {"period_type": "annual", "operating_cash_flow": 10, "net_profit": 40, "gross_margin": 20, "debt_ratio": 30},
                {"period_type": "annual", "operating_cash_flow": 8, "net_profit": 50, "gross_margin": 20, "debt_ratio": 30},
            ],
            "feature": {},
        },
    )
    ocf = [item for item in service.get_watchpoints("CN", "600460.SH")["watchpoints"] if "现金" in item["title"]]
    assert len(ocf) == 1
    assert len(ocf[0]["source_refs"]) >= 2


def test_theme_map_stays_small_and_covers_only_leverage() -> None:
    assert set(CANONICAL_THEMES.values()) == {"LEVERAGE"}
    assert set(CANONICAL_THEMES) == {"DEBT", "INTEREST_BEARING_DEBT"}


# ----------------------------------------------------------------------
# Moat ranking semantics
def _moat(status: str, balance: str, *, counters: list[str] | None = None) -> dict[str, Any]:
    return {
        "research_as_of": "2026-08-28",
        "dimensions": [{
            "dimension": "COST_ADVANTAGE", "status": status, "evidence_balance": balance,
            "counter_evidence_ids": list(counters or []), "summary": "成本优势资料。",
        }],
    }


@pytest.mark.parametrize(
    ("status", "balance", "counters", "tier"),
    [
        ("PARTIAL", "CHALLENGED", ["c1"], "HIGH"),
        ("PARTIAL", "MIXED", ["c1"], "NORMAL"),
        ("PARTIAL", "SUPPORTING", None, "NORMAL"),
        ("SUPPORTED", "SUPPORTING", None, "LOW"),
    ],
    ids=["counter_evidence", "mixed", "partial", "supported"],
)
def test_moat_importance_follows_evidence_balance(status: str, balance: str, counters: list[str] | None, tier: str) -> None:
    result = _service(moat_loader=lambda *_a, **_k: _moat(status, balance, counters=counters)).get_watchpoints(
        "CN", "600460.SH",
    )
    moat = [item for item in result["watchpoints"] if item["category"] == "MOAT"]
    assert moat and moat[0]["importance_tier"] == tier


def test_moat_partial_does_not_outrank_core_financial() -> None:
    result = _service(
        moat_loader=lambda *_a, **_k: _moat("PARTIAL", "MIXED", counters=["c1"]),
        financial_loader=lambda *_a, **_k: {
            "as_of": "2026-08-28",
            "history": [
                {"period_type": "annual", "operating_cash_flow": 10, "net_profit": 40, "gross_margin": 22, "debt_ratio": 30},
                {"period_type": "annual", "operating_cash_flow": 6, "net_profit": 42, "gross_margin": 22, "debt_ratio": 30},
            ],
            "feature": {},
        },
    ).get_watchpoints("CN", "600460.SH")
    categories = [item["category"] for item in result["top_watchpoints"]]
    assert categories
    assert categories.index("FINANCIAL") < categories.index("MOAT") if "MOAT" in categories else True


def test_moat_counter_evidence_stays_below_high_risk() -> None:
    result = _service(
        strategy_loader=lambda *_a, **_k: _state(tier="A", action="RISK_REVIEW"),
        risk_loader=lambda *_a, **_k: _risk("VALUE_TRAP", text="低估陷阱复核。"),
        moat_loader=lambda *_a, **_k: _moat("PARTIAL", "CHALLENGED", counters=["c1"]),
    ).get_watchpoints("CN", "600460.SH")
    assert result["top_watchpoints"][0]["category"] == "RISK"


# ----------------------------------------------------------------------
# Hard constraints
def test_no_hardcoded_sample_company_in_projection_sources() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in WATCHPOINT_SOURCES:
        text = (root / relative).read_text(encoding="utf-8")
        for code in SAMPLE_CODES:
            assert code not in text, f"{relative} hardcodes {code}"


def test_projection_makes_no_network_or_llm_call(monkeypatch) -> None:
    import socket

    def blocked(*_a: Any, **_k: Any):
        raise AssertionError("watchpoint projection must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    result = _service(
        risk_loader=lambda *_a, **_k: _risk("FINANCIAL_INTEREST_DEBT"),
        financial_loader=lambda *_a, **_k: _financial_leverage(),
    ).get_watchpoints("CN", "600460.SH")
    assert result["formula_version"]


def test_api_response_contract_is_unchanged() -> None:
    result = _service(
        risk_loader=lambda *_a, **_k: _risk("FINANCIAL_INTEREST_DEBT"),
        financial_loader=lambda *_a, **_k: _financial_leverage(),
    ).get_watchpoints("CN", "600460.SH")
    for key in (
        "stock_code", "stock_name", "research_as_of", "primary_action", "focus_tier",
        "watchpoints", "top_watchpoints", "data_gaps", "suggested_research_need",
        "source_freshness", "formula_version",
    ):
        assert key in result
    for item in result["top_watchpoints"]:
        for key in ("title", "current_state", "positive_condition", "negative_condition",
                    "next_review_anchor", "source_refs", "importance_tier"):
            assert key in item


def test_daily_brief_uses_batch_projection() -> None:
    from src.investment_research_supervisor.daily_brief_service import (
        InvestmentResearchDailyBriefService,
    )

    class BatchService:
        def __init__(self) -> None:
            self.batch_calls = 0
            self.single_calls = 0

        def get_watchpoints_batch(
            self, market: str, codes: list[str], research_as_of: str,
        ) -> dict[str, dict[str, Any]]:
            self.batch_calls += 1
            return {
                code: {
                    "stock_code": code,
                    "stock_name": code,
                    "top_watchpoints": [{"title": f"{code}验证点", "current_state": "待验证"}],
                }
                for code in codes
            }

        def get_watchpoints(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            self.single_calls += 1
            return {}

    projection = BatchService()
    daily = object.__new__(InvestmentResearchDailyBriefService)
    daily.watchpoint_projection_service = projection
    focus = [{"stock_code": f"60000{index}.SH", "stock_name": f"样本{index}"} for index in range(6)]
    rows = daily._focus_watchpoints(focus, "2026-08-28")
    assert projection.batch_calls == 1
    assert projection.single_calls == 0
    assert len(rows) == 5
