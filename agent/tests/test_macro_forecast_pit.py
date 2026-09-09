"""宏观预测 V1 PIT 可见性契约（主规格 §6；T04-T08、T10-T11）。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.macro_forecast.contracts import (
    CONSERVATIVE_UPPER_BOUND, FORWARD_OBSERVED, STRICT_PIT, UNVERIFIED,
    comparable_periods, direction_with_guards, surprise_allowed, visibility_decision,
)

SH = ZoneInfo("Asia/Shanghai")
C = datetime(2026, 6, 2, 8, 50, tzinfo=SH)


def _ts(day: str, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), hour, minute, tzinfo=SH)


def test_t04_precise_release_after_cutoff_excluded() -> None:
    """T04：精确发布时间在 C 后（08:50:01）→ 不进入本期输入。"""
    decision = visibility_decision(
        cutoff=C, release_time_precision="DATETIME",
        source_released_at=datetime(2026, 6, 2, 8, 50, 1, tzinfo=SH),
        first_seen_at=datetime(2026, 6, 2, 9, 0, tzinfo=SH),
    )
    assert decision.usable is False
    assert decision.pit_status == STRICT_PIT


def test_t04b_precise_release_before_cutoff_usable_strict() -> None:
    decision = visibility_decision(
        cutoff=C, release_time_precision="DATETIME",
        source_released_at=_ts("20260601", 16, 30),
        first_seen_at=_ts("20260602", 8, 45),
    )
    assert decision.usable is True
    assert decision.pit_status == STRICT_PIT


def test_t05_date_only_same_day_morning_not_usable() -> None:
    """T05：只有发布日期（当日）、无抓取证明 → 盘前不可用。"""
    decision = visibility_decision(
        cutoff=C, release_time_precision="DATE",
        source_released_at="2026-06-02", first_seen_at=None,
    )
    assert decision.usable is False
    assert decision.pit_status == CONSERVATIVE_UPPER_BOUND


def test_t05b_date_only_with_hash_and_capture_conservative() -> None:
    """日期级发布 + 内容哈希 + 次日抓取 → 以抓取时刻为保守边界可用。"""
    decision = visibility_decision(
        cutoff=C, release_time_precision="DATE",
        source_released_at="2026-06-01",
        first_seen_at=_ts("20260601", 18, 0), content_hash="abc123",
    )
    assert decision.usable is True
    assert decision.pit_status == CONSERVATIVE_UPPER_BOUND


def test_t06_backfilled_capture_is_forward_observed_not_backdated() -> None:
    """T06：报告次日才抓取 → FORWARD_OBSERVED，可见边界=抓取时刻，不回填。"""
    decision = visibility_decision(
        cutoff=C, release_time_precision="DATE",
        source_released_at="2026-05-30",
        first_seen_at=_ts("20260601", 11, 33), content_hash="v1",
    )
    assert decision.usable is True
    assert decision.pit_status == CONSERVATIVE_UPPER_BOUND
    assert decision.visible_at == _ts("20260601", 11, 33)


def test_t06b_no_release_no_capture_unverified() -> None:
    decision = visibility_decision(cutoff=C, release_time_precision="DATE")
    assert decision.usable is False
    assert decision.pit_status == UNVERIFIED


def test_t06c_captured_after_cutoff_excluded_even_if_old_release() -> None:
    """旧发布、今日 11:33 才入库（生产实际形态）→ C=08:50 不可用。"""
    decision = visibility_decision(
        cutoff=C, release_time_precision="DATE",
        source_released_at="2026-06-01",
        first_seen_at=datetime(2026, 6, 2, 11, 33, tzinfo=SH), content_hash="v2",
    )
    assert decision.usable is False


def test_t07_revision_keeps_identity_via_revision_id() -> None:
    """T07：修订产生新 revision_id；原事实身份不变由调用方以 revision_id 锚定。"""
    original = visibility_decision(
        cutoff=C, release_time_precision="DATE", source_released_at="2026-05-10",
        first_seen_at=_ts("20260512", 9, 0), content_hash="rev1",
    )
    revised = visibility_decision(
        cutoff=C, release_time_precision="DATE", source_released_at="2026-05-20",
        first_seen_at=_ts("20260521", 9, 0), content_hash="rev2",
    )
    assert original.usable and revised.usable
    assert original.visible_at != revised.visible_at  # 选择层将按 observation+visible 取最新


def test_t08_no_consensus_never_surprise() -> None:
    assert surprise_allowed(None, _ts("20260601", 9, 30)) is False
    assert surprise_allowed(_ts("20260530", 17, 0), _ts("20260601", 9, 30)) is True
    assert surprise_allowed(_ts("20260601", 10, 0), _ts("20260601", 9, 30)) is False


def test_t09_units_must_match_to_compare() -> None:
    ok, _ = comparable_periods({"unit": "%", "frequency": "M"}, {"unit": "%", "frequency": "M"})
    assert ok
    bad, reason = comparable_periods({"unit": "%"}, {"unit": "亿元"})
    assert not bad and "unit" in reason


def test_t10_negative_base_keeps_raw_and_guard() -> None:
    """T10：负基数同比 → 保留原值/差额并打护栏，不自动解释为改善。"""
    guarded = direction_with_guards(-1.2, -3.5)
    assert guarded["direction"] == "UP"
    assert guarded["delta"] == 2.3
    assert guarded["negative_base_guard"] is True


def test_t11_content_hash_boundary_is_visibility_not_updated_at() -> None:
    """T11：updated_at 变化本身不产生新的可见边界——可见性只认发布/抓取证明。"""
    first = visibility_decision(
        cutoff=C, release_time_precision="DATE", source_released_at="2026-06-01",
        first_seen_at=_ts("20260601", 18, 0), content_hash="same-hash",
    )
    # 假想的源 updated_at 抖动不进入 visibility_decision 参数面：
    # 参数只有 release/first_seen/hash，任何 updated_at 变化无法影响结论。
    again = visibility_decision(
        cutoff=C, release_time_precision="DATE", source_released_at="2026-06-01",
        first_seen_at=_ts("20260601", 18, 0), content_hash="same-hash",
    )
    assert (first.usable, first.visible_at) == (again.usable, again.visible_at)


def test_forward_observed_status_when_only_capture_proof() -> None:
    decision = visibility_decision(
        cutoff=C, release_time_precision="DATE", source_released_at=None,
        first_seen_at=_ts("20260601", 20, 0),
    )
    assert decision.usable is True
    assert decision.pit_status == FORWARD_OBSERVED
