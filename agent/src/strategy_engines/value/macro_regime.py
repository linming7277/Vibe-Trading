"""Value-line macro regime calculation."""

from __future__ import annotations

from ..common.scoring import weighted_score

FORMULA_VERSION = "value-macro-v1.0.0"
WEIGHTS = {"growth": 0.25, "liquidity": 0.25, "inflation": 0.15, "policy": 0.20, "risk_appetite": 0.15}


def calculate(features: dict[str, float | None]) -> dict[str, object]:
    result = weighted_score(features, WEIGHTS)
    score = result.score
    regime = "insufficient_data" if score is None else "expansion" if score >= 65 else "supportive" if score >= 50 else "slowdown" if score >= 35 else "contraction"
    return {"formula_version": FORMULA_VERSION, "regime": regime, "score": score, "coverage": result.coverage, "status": result.status}
