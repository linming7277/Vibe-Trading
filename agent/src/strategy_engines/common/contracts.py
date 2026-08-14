"""Stable contracts shared by strategy engines, APIs, backtests and paper trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StrategyLine(str, Enum):
    VALUE = "value"
    EMOTION = "emotion"


class SignalHorizon(str, Enum):
    LONG = "long"
    SHORT = "short"
    SWING = "swing"


class SignalStatus(str, Enum):
    OBSERVED = "observed"
    ELIGIBLE = "eligible"
    PROPOSED = "proposed"
    APPROVED = "approved"
    PAPER_SUBMITTED = "paper_submitted"
    FILLED = "filled"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class DecisionStatus(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    WAIT = "wait"


@dataclass(frozen=True)
class FeatureSnapshot:
    id: str
    engine_run_id: str
    market: str
    subject_type: str
    subject_id: str
    data_as_of: str
    available_at: str
    features: dict[str, float | None]
    sources: dict[str, str] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()
    created_at: str = ""


@dataclass(frozen=True)
class ScoreResult:
    id: str
    engine_run_id: str
    engine: str
    formula_version: str
    strategy_line: StrategyLine
    market: str
    subject_type: str
    subject_id: str
    data_as_of: str
    available_at: str
    raw_features: dict[str, float | None]
    normalized_features: dict[str, float | None]
    component_scores: dict[str, float | None]
    base_score: float | None
    coverage: float
    status: str
    quality_flags: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    confidence: str = "LOW"
    missing_fields: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    provenance_key: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class RegimeSnapshot:
    id: str
    engine_run_id: str
    strategy_line: StrategyLine
    market: str
    regime: str
    previous_regime: str | None
    score: float | None
    confidence: float
    coverage: float
    triggers: tuple[str, ...]
    data_as_of: str
    available_at: str
    formula_version: str
    changed_at: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class StrategySignal:
    id: str
    engine_run_id: str
    strategy_line: StrategyLine
    horizon: SignalHorizon
    market: str
    symbol: str
    data_as_of: str
    valid_from: str
    valid_until: str
    direction: str
    base_score: float
    entry_low: float | None
    entry_high: float | None
    stop_price: float | None
    target_low: float | None
    target_high: float | None
    position_cap: float
    coverage: float
    formula_versions: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    status: SignalStatus = SignalStatus.OBSERVED
    invalidation_rules: tuple[str, ...] = ()
    created_at: str = ""


@dataclass(frozen=True)
class CommitteeDecision:
    id: str
    committee_id: str
    signal_id: str
    strategy_line: StrategyLine
    status: DecisionStatus
    direction: str
    position_cap: float
    entry_low: float | None
    entry_high: float | None
    stop_price: float | None
    target_low: float | None
    target_high: float | None
    holding_period: str
    confidence: float
    summary: str
    review_triggers: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    engine_run_ids: tuple[str, ...]
    created_at: str = ""


def jsonable(value: Any) -> Any:
    """Recursively convert contracts and enums into JSON-safe primitives."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value
