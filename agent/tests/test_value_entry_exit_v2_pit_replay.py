"""Validation tests for the read-only V2 PIT replay candidate helpers.

No test in this module opens the production database for writing or imports a
candidate evaluator into the production Value Strategy call path.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.tdx_data.store import TdxDataStore
from src.value_price_zones.service import ValuePriceZoneService
from tests.value_strategy_pit_replay import (
    PITLeakError,
    evaluate_entry_e3,
    evaluate_exit_x3,
    mass_transition,
    reliability_from_snapshot,
    require_pit,
    transition_count,
)


def _entry(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "in_value_scope": True,
        "valuation_status": "DEEPLY_UNDERVALUED",
        "historical_valuation_status": "VERY_CHEAP",
        "historical_coverage": "READY",
        "valuation_reliability": "RELIABLE",
        "thesis_status": "UNCHANGED",
        "thesis_authority": "HUMAN_CONFIRMED",
        "support_context": "NO_SIGNAL",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "HIGH_ATTENTION"),
        ({"historical_valuation_status": "NORMAL"}, "ATTENTION"),
        ({"valuation_status": "UNDERVALUED"}, "ATTENTION"),
        ({"valuation_reliability": "WEAK"}, "WATCH"),
        ({"valuation_reliability": "INSUFFICIENT"}, "WAIT"),
        ({"historical_coverage": "INSUFFICIENT"}, "WATCH"),
        ({"thesis_status": "WEAKENING"}, "WATCH"),
        ({"thesis_status": "FALSIFIED", "thesis_authority": "AI_PROVISIONAL"}, "BLOCKED"),
        ({"thesis_authority": "HUMAN_REJECTED"}, "BLOCKED"),
        ({"in_value_scope": False}, "NOT_APPLICABLE"),
    ],
)
def test_entry_e3_locked_fixtures(payload: dict[str, object], expected: str) -> None:
    assert evaluate_entry_e3(_entry(**payload))["status"] == expected


def test_ai_provisional_does_not_improve_entry_over_missing_thesis() -> None:
    provisional = evaluate_entry_e3(_entry(thesis_status="FORMING", thesis_authority="AI_PROVISIONAL"))
    missing = evaluate_entry_e3(_entry(thesis_status="MISSING", thesis_authority="MISSING"))
    assert provisional["status"] == missing["status"] == "ATTENTION"


def test_human_confirmed_thesis_status_is_not_a_price_score() -> None:
    strengthening = evaluate_entry_e3(_entry(thesis_status="STRENGTHENING"))
    unchanged = evaluate_entry_e3(_entry(thesis_status="UNCHANGED"))
    assert strengthening["status"] == unchanged["status"] == "HIGH_ATTENTION"


def test_support_context_does_not_change_entry_main_status() -> None:
    no_signal = evaluate_entry_e3(_entry(thesis_status="MISSING", thesis_authority="MISSING", support_context="NO_SIGNAL"))
    at_support = evaluate_entry_e3(_entry(thesis_status="MISSING", thesis_authority="MISSING", support_context="IN_SUPPORT_ZONE"))
    assert no_signal["status"] == at_support["status"] == "ATTENTION"


def _exit(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "valuation_status": "UNDERVALUED",
        "valuation_reliability": "RELIABLE",
        "was_in_value_scope": True,
        "thesis_status": "UNCHANGED",
        "thesis_authority": "HUMAN_CONFIRMED",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "NORMAL"),
        ({"valuation_status": "FAIR"}, "WATCH"),
        ({"valuation_status": "OVERVALUED"}, "REVIEW"),
        ({"valuation_status": "DEEPLY_OVERVALUED"}, "REVIEW"),
        ({"thesis_status": "WEAKENING"}, "REVIEW"),
        ({"thesis_status": "FALSIFIED", "thesis_authority": "HUMAN_CONFIRMED"}, "CRITICAL_REVIEW"),
        ({"thesis_status": "FALSIFIED", "thesis_authority": "AI_PROVISIONAL"}, "REVIEW"),
        ({"thesis_authority": "HUMAN_REJECTED"}, "CRITICAL_REVIEW"),
    ],
)
def test_exit_x3_locked_fixtures(payload: dict[str, object], expected: str) -> None:
    assert evaluate_exit_x3(_exit(**payload))["status"] == expected


def test_exit_weak_valuation_is_gated_to_watch() -> None:
    result = evaluate_exit_x3(_exit(valuation_status="OVERVALUED", valuation_reliability="WEAK"))
    assert result["status"] == "WATCH"
    assert result["valuation_review"] == "WATCH"


def test_exit_insufficient_valuation_does_not_create_strong_review() -> None:
    result = evaluate_exit_x3(_exit(valuation_status="OVERVALUED", valuation_reliability="INSUFFICIENT"))
    assert result["status"] == "NORMAL"
    assert "VALUATION_DATA_WEAK" in result["cautions"]


def test_pit_guard_rejects_future_business_date() -> None:
    with pytest.raises(PITLeakError):
        require_pit("financial", "2026-08-28", "2026-08-27")


def test_pit_guard_accepts_backfilled_old_business_date() -> None:
    assert require_pit("financial", "2026-03-30", "2026-08-27") == "2026-03-30"


def test_missing_reliability_provenance_is_not_reconstructed() -> None:
    state, reason = reliability_from_snapshot({"data_quality": {"peer_comparables": {"peer_count": 9}}}, "2026-08-27")
    assert state is None
    assert reason == "VALUATION_RELIABILITY_PROVENANCE"


def test_partial_valuation_quality_alone_still_lacks_provenance() -> None:
    """The pre-remediation {method_count, min_peer_count} audit cannot certify.

    It lacks per-method sample counts and the extreme-fair-value guard, so a
    replay must keep flagging the gap instead of deriving a status.
    """
    state, reason = reliability_from_snapshot(
        {"valuation_quality": {"method_count": 2, "min_peer_count": 12, "as_of": "2026-09-01"}},
        "2026-09-01",
    )
    assert state is None
    assert reason == "VALUATION_RELIABILITY_PROVENANCE"


def test_persisted_reliability_verdict_is_used_verbatim() -> None:
    state, source = reliability_from_snapshot(
        {"valuation_reliability": {"status": "RELIABLE", "as_of": "2026-09-01", "formula_version": "valuation-reliability-v1.0.0"}},
        "2026-09-01",
    )
    assert state == "RELIABLE"
    assert source == "2026-09-01"


def test_persisted_reliability_future_as_of_is_a_pit_leak() -> None:
    with pytest.raises(PITLeakError):
        reliability_from_snapshot(
            {"valuation_reliability": {"status": "RELIABLE", "as_of": "2026-09-02"}},
            "2026-09-01",
        )


def test_transition_statistics_and_mass_transition_threshold() -> None:
    order = {"WAIT": 0, "WATCH": 1, "ATTENTION": 2, "HIGH_ATTENTION": 3}
    assert transition_count(["WAIT", "WATCH", "HIGH_ATTENTION"], order) == {
        "total": 2, "one_level": 1, "two_level": 1, "three_plus": 0,
    }
    assert mass_transition(31, 100) is True
    assert mass_transition(30, 100) is False


def test_support_bars_and_coverage_metadata_are_as_of_scoped(tmp_path) -> None:
    """Bars AND their coverage metadata must stop at the requested date.

    The bars sent to the structure algorithm stop at the requested date, so
    future bars do not decide a support zone.  Since the PIT remediation the
    as-of coverage metadata is recomputed from the same truncated window, so
    ``last_date`` can no longer leak a future trading day into a replay.
    """
    store = TdxDataStore(tmp_path / "tdx.sqlite")
    start = date(2026, 1, 1)
    cutoff = start + timedelta(days=59)
    rows = []
    for offset in range(62):
        stamp = (start + timedelta(days=offset)).isoformat()
        rows.append({
            "market": "CN", "stock_code": "000001.SZ", "trade_date": stamp,
            "open": 10 + offset / 100, "high": 10.2 + offset / 100, "low": 9.8 + offset / 100,
            "close": 10 + offset / 100, "volume": 1000, "amount": 10000,
            "adjustment_type": "front", "source": "test", "source_version": "v1",
            "fetched_at": "2026-09-02T00:00:00+00:00", "source_hash": f"hash-{offset}",
        })
    store.upsert_adjusted_daily_bars(rows)
    store.refresh_adjusted_daily_bar_coverage("CN", "000001.SZ")
    zones = ValuePriceZoneService(tdx_store=store)
    bars, quality = zones._bars("000001.SZ", cutoff)  # noqa: SLF001 - explicit PIT audit
    assert bars[-1]["date"] == cutoff.isoformat()
    assert quality["last_date"] == cutoff.isoformat()
    assert quality["coverage_as_of_scoped"] is True
    latest_bars, latest_quality = zones._bars("000001.SZ", None)  # noqa: SLF001 - explicit PIT audit
    assert latest_bars[-1]["date"] == latest_quality["last_date"]
    zones.close()
    store.close()
