"""Pure emotion-line pipeline over normalized components."""

from __future__ import annotations

from . import emotion_regime, emotion_score, sector_heat, short_candidate, swing_candidate, timing

FORMULA_VERSION = "emotion-pipeline-v1.0.0"


def run_emotion_pipeline(*, market: dict[str, object], sectors: list[dict[str, object]], candidates: list[dict[str, object]], previous_regime: str | None = None) -> dict[str, object]:
    score_result = emotion_score.calculate(dict(market.get("components") or {}))
    regime, triggers = emotion_regime.transition(
        previous=previous_regime,
        score=score_result.score,
        delta_3d=market.get("delta_3d"),
        delta_5d=market.get("delta_5d"),
        ice_confirmed=bool(market.get("ice_confirmed")),
        climax_confirmations=int(market.get("climax_confirmations") or 0),
        ebb_confirmed=bool(market.get("ebb_confirmed")),
    )
    sector_results = [{**row, "result": sector_heat.calculate(dict(row.get("components") or {}))} for row in sectors]
    candidate_results = []
    for row in candidates:
        allowed, reasons = timing.eligible(**dict(row.get("eligibility") or {}))
        horizon = str(row.get("horizon") or "short")
        result = swing_candidate.calculate(dict(row.get("components") or {})) if horizon == "swing" else short_candidate.calculate(dict(row.get("components") or {}))
        candidate_results.append({**row, "eligible": allowed, "exclusion_reasons": reasons, "result": result})
    return {"formula_version": FORMULA_VERSION, "emotion_score": score_result, "regime": regime, "regime_triggers": triggers, "sectors": sector_results, "candidates": candidate_results}
