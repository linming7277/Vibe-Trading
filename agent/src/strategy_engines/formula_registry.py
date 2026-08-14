"""Versioned public manifest for the two deterministic strategy lines.

The executable modules remain the source of truth.  This manifest imports
their exact constants and mirrors each immutable version into the existing
Strategy Store so formula governance, backtests and decay monitoring share a
single version system.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.strategy_store.models import Artifact, ArtifactStatus, ArtifactType, ModelTier
from src.strategy_store.sqlite_store import SqliteStrategyStore

from .emotion.emotion_score import FORMULA_VERSION as EMOTION_MARKET_VERSION, WEIGHTS as EMOTION_MARKET_WEIGHTS
from .emotion.emotion_regime import FORMULA_VERSION as EMOTION_REGIME_VERSION
from .emotion.sector_heat import FORMULA_VERSION as EMOTION_SECTOR_VERSION, WEIGHTS as EMOTION_SECTOR_WEIGHTS
from .emotion.short_candidate import FORMULA_VERSION as EMOTION_SHORT_VERSION, WEIGHTS as EMOTION_SHORT_WEIGHTS
from .emotion.swing_candidate import FORMULA_VERSION as EMOTION_SWING_VERSION, WEIGHTS as EMOTION_SWING_WEIGHTS
from .emotion.timing import FORMULA_VERSION as EMOTION_TIMING_VERSION
from .value.fundamental_quality import FORMULA_VERSION as VALUE_QUALITY_VERSION, WEIGHTS as VALUE_QUALITY_WEIGHTS
from .value.leader_score import FORMULA_VERSION as VALUE_LEADER_VERSION, WEIGHTS as VALUE_LEADER_WEIGHTS
from .value.macro_regime import FORMULA_VERSION as VALUE_MACRO_VERSION, WEIGHTS as VALUE_MACRO_WEIGHTS
from .value.macro_sector import FORMULA_VERSION as VALUE_MACRO_SECTOR_VERSION
from .value.sector_score import FORMULA_VERSION as VALUE_SECTOR_VERSION, WEIGHTS as VALUE_SECTOR_WEIGHTS
from .value.timing import FORMULA_VERSION as VALUE_TIMING_VERSION, WEIGHTS as VALUE_TIMING_WEIGHTS
from .value.valuation import FORMULA_VERSION as VALUE_VALUATION_VERSION
from .value.leader_score_v2 import FORMULA_VERSION as VALUE_LEADER_V2_VERSION, WEIGHTS as VALUE_LEADER_V2_WEIGHTS
from .value.macro_regime_v2 import FORMULA_VERSION as VALUE_MACRO_V2_VERSION
from .value.macro_sector_v2 import FORMULA_VERSION as VALUE_MACRO_SECTOR_V2_VERSION
from .value.sector_score_v2 import FORMULA_VERSION as VALUE_SECTOR_V2_VERSION, WEIGHTS as VALUE_SECTOR_V2_WEIGHTS


FORMULAS: tuple[dict[str, Any], ...] = (
    {"id": "value_macro_v1", "strategy_line": "value", "name": "价值宏观状态", "version": VALUE_MACRO_VERSION, "weights": VALUE_MACRO_WEIGHTS, "engine_path": "src.strategy_engines.value.macro_regime", "universe": "CN,HK", "minimum_coverage": .70},
    {"id": "value_macro_sector_v1", "strategy_line": "value", "name": "宏观赛道匹配", "version": VALUE_MACRO_SECTOR_VERSION, "weights": {}, "engine_path": "src.strategy_engines.value.macro_sector", "universe": "CN,HK", "minimum_coverage": None},
    {"id": "value_sector_v1", "strategy_line": "value", "name": "价值赛道评分", "version": VALUE_SECTOR_VERSION, "weights": VALUE_SECTOR_WEIGHTS, "engine_path": "src.strategy_engines.value.sector_score", "universe": "CN,HK", "minimum_coverage": .70},
    {"id": "value_leader_v1", "strategy_line": "value", "name": "行业龙头评分", "version": VALUE_LEADER_VERSION, "weights": VALUE_LEADER_WEIGHTS, "engine_path": "src.strategy_engines.value.leader_score", "universe": "CN,HK", "minimum_coverage": .80},
    {"id": "value_quality_v1", "strategy_line": "value", "name": "财务质量评分", "version": VALUE_QUALITY_VERSION, "weights": VALUE_QUALITY_WEIGHTS, "engine_path": "src.strategy_engines.value.fundamental_quality", "universe": "CN,HK", "minimum_coverage": .70},
    {"id": "value_valuation_v1", "strategy_line": "value", "name": "估值与安全边际", "version": VALUE_VALUATION_VERSION, "weights": {}, "engine_path": "src.strategy_engines.value.valuation", "universe": "CN,HK", "minimum_coverage": None},
    {"id": "value_timing_v1", "strategy_line": "value", "name": "价值入场时机", "version": VALUE_TIMING_VERSION, "weights": VALUE_TIMING_WEIGHTS, "engine_path": "src.strategy_engines.value.timing", "universe": "CN,HK", "minimum_coverage": .80},
    {"id": "value_macro_v2", "strategy_line": "value", "name": "价值线 V2 宏观五维", "version": VALUE_MACRO_V2_VERSION, "weights": {}, "engine_path": "src.strategy_engines.value.macro_regime_v2", "universe": "CN", "minimum_coverage": .60},
    {"id": "value_macro_sector_v2", "strategy_line": "value", "name": "价值线 V2 宏观行业矩阵", "version": VALUE_MACRO_SECTOR_V2_VERSION, "weights": {}, "engine_path": "src.strategy_engines.value.macro_sector_v2", "universe": "CN:TDX-881", "minimum_coverage": None},
    {"id": "value_sector_v2", "strategy_line": "value", "name": "价值线 V2 行业评分", "version": VALUE_SECTOR_V2_VERSION, "weights": VALUE_SECTOR_V2_WEIGHTS, "engine_path": "src.strategy_engines.value.sector_score_v2", "universe": "CN:TDX-881", "minimum_coverage": .80},
    {"id": "value_leader_v2", "strategy_line": "value", "name": "价值线 V2 龙头评分", "version": VALUE_LEADER_V2_VERSION, "weights": VALUE_LEADER_V2_WEIGHTS, "engine_path": "src.strategy_engines.value.leader_score_v2", "universe": "CN:TDX-881", "minimum_coverage": .80},
    {"id": "emotion_market_v1", "strategy_line": "emotion", "name": "市场情绪评分", "version": EMOTION_MARKET_VERSION, "weights": EMOTION_MARKET_WEIGHTS, "engine_path": "src.strategy_engines.emotion.emotion_score", "universe": "CN,HK", "minimum_coverage": .70},
    {"id": "emotion_regime_v1", "strategy_line": "emotion", "name": "情绪周期状态机", "version": EMOTION_REGIME_VERSION, "weights": {}, "engine_path": "src.strategy_engines.emotion.emotion_regime", "universe": "CN,HK", "minimum_coverage": None},
    {"id": "emotion_sector_v1", "strategy_line": "emotion", "name": "情绪板块热度", "version": EMOTION_SECTOR_VERSION, "weights": EMOTION_SECTOR_WEIGHTS, "engine_path": "src.strategy_engines.emotion.sector_heat", "universe": "CN,HK", "minimum_coverage": .70},
    {"id": "emotion_short_v1", "strategy_line": "emotion", "name": "情绪短线候选", "version": EMOTION_SHORT_VERSION, "weights": EMOTION_SHORT_WEIGHTS, "engine_path": "src.strategy_engines.emotion.short_candidate", "universe": "CN,HK", "minimum_coverage": .80},
    {"id": "emotion_swing_v1", "strategy_line": "emotion", "name": "情绪波段候选", "version": EMOTION_SWING_VERSION, "weights": EMOTION_SWING_WEIGHTS, "engine_path": "src.strategy_engines.emotion.swing_candidate", "universe": "CN,HK", "minimum_coverage": .80},
    {"id": "emotion_timing_v1", "strategy_line": "emotion", "name": "情绪可交易性过滤", "version": EMOTION_TIMING_VERSION, "weights": {}, "engine_path": "src.strategy_engines.emotion.timing", "universe": "CN,HK", "minimum_coverage": None},
)


def formula_manifest() -> list[dict[str, Any]]:
    """Return a detached, JSON-safe copy of the immutable formula manifest."""
    items = json.loads(json.dumps(FORMULAS, ensure_ascii=False, sort_keys=True))
    for item in items:
        if item["strategy_line"] == "value":
            item["lifecycle"] = "legacy" if "-v1." in item["version"] else "standard"
            item["is_default"] = item["id"] in {"value_macro_v2", "value_macro_sector_v2", "value_sector_v2", "value_leader_v2"}
    return items


def sync_formula_artifacts(db_path: Path | None = None) -> list[str]:
    """Idempotently register formula versions in the existing Strategy Store."""
    store = SqliteStrategyStore(db_path or (get_runtime_root() / "strategy_store.db"))
    registered: list[str] = []
    try:
        for item in FORMULAS:
            artifact_id = f"dual_line_{item['id']}"
            existing = store.get_artifact(artifact_id)
            definition = json.dumps(
                {"weights": item["weights"], "minimum_coverage": item["minimum_coverage"]},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            if existing:
                if existing.artifact_version != item["version"] or existing.signal_definition != definition:
                    raise ValueError(f"immutable formula artifact conflict: {artifact_id}")
                registered.append(artifact_id)
                continue
            store.register_artifact(Artifact(
                id=artifact_id,
                type=ArtifactType.STRATEGY,
                name=f"{item['name']} / {item['version']}",
                universe=item["universe"],
                signal_definition=definition,
                entry_rules="Only deterministic engine output with required PIT coverage may emit a signal.",
                exit_rules="Use the signal stop, invalidation rules and immutable valid-until timestamp.",
                position_sizing="Committee may reduce but never exceed the engine position cap.",
                signal_engine_path=item["engine_path"],
                status=ArtifactStatus.CREATED,
                developer="deterministic-strategy-engine",
                owner="research-platform",
                model_version=item["version"],
                artifact_version=item["version"],
                model_tier=ModelTier.TIER_1_CRITICAL,
                intended_use="A/H-share research, backtest and internal paper validation only.",
                limitations="Not approved for live trading; unavailable inputs remain missing and may suppress scores.",
            ))
            registered.append(artifact_id)
        return registered
    finally:
        store.close()
