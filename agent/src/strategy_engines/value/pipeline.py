"""Pure value-line pipeline over already normalized components."""

from __future__ import annotations

from . import leader_score, macro_regime, sector_score, timing

FORMULA_VERSION = "value-pipeline-v1.0.0"


def run_value_pipeline(*, macro: dict[str, float | None], sectors: list[dict[str, object]], leaders: list[dict[str, object]]) -> dict[str, object]:
    macro_result = macro_regime.calculate(macro)
    sector_results = [{**row, "result": sector_score.calculate(dict(row.get("components") or {}))} for row in sectors]
    leader_results = []
    for row in leaders:
        score = leader_score.calculate(dict(row.get("components") or {}))
        timing_result = timing.calculate(dict(row.get("timing") or {}))
        leader_results.append({**row, "result": score, "timing_result": timing_result})
    return {"formula_version": FORMULA_VERSION, "macro": macro_result, "sectors": sector_results, "leaders": leader_results}
