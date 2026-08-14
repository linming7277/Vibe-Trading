"""Pure Value V2 pipeline over cached, normalized inputs."""

from __future__ import annotations

from . import leader_score_v2, macro_regime_v2, sector_score_v2

FORMULA_VERSION = "value-pipeline-v2.0.0"


def run_value_pipeline_v2(*, macro: dict[str, float | None], sectors: list[dict[str, object]], leaders: list[dict[str, object]]) -> dict[str, object]:
    return {
        "formula_version": FORMULA_VERSION,
        "macro": macro_regime_v2.calculate(macro),
        "sectors": [{**row, "result": sector_score_v2.calculate(dict(row.get("components") or {}))} for row in sectors],
        "leaders": [{**row, "result": leader_score_v2.calculate(dict(row.get("components") or {}))} for row in leaders],
    }
