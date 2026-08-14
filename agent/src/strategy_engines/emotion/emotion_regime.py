"""Hysteretic five-stage emotion regime state machine."""

from __future__ import annotations

FORMULA_VERSION = "emotion-regime-v1.0.0"
REGIMES = ("ice", "repair", "fermentation", "climax", "ebb")


def transition(*, previous: str | None, score: float | None, delta_3d: float | None = None, delta_5d: float | None = None, ice_confirmed: bool = False, climax_confirmations: int = 0, ebb_confirmed: bool = False) -> tuple[str, tuple[str, ...]]:
    if score is None:
        return "insufficient_data", ("emotion_score_missing",)
    prior = previous if previous in REGIMES else None
    triggers: list[str] = []
    if prior == "climax" and ((delta_3d is not None and delta_3d <= -12) or ebb_confirmed):
        triggers.append("post_climax_deterioration")
        return "ebb", tuple(triggers)
    if score >= 75 and climax_confirmations >= 2:
        triggers.append("score_and_climax_confirmations")
        return "climax", tuple(triggers)
    if prior == "ice" and score >= 30 and delta_5d is not None and delta_5d > 0:
        triggers.append("ice_recovery")
        return "repair", tuple(triggers)
    if score <= 20 and ice_confirmed:
        triggers.append("low_score_and_loss_effect")
        return "ice", tuple(triggers)
    if 40 <= score < 75 and ((delta_5d or 0) > 0):
        triggers.append("rising_mid_range")
        return "fermentation", tuple(triggers)
    if prior == "ebb" and score < 40:
        return "ebb", ("ebb_hysteresis",)
    if prior in REGIMES:
        return prior, ("hysteresis_hold",)
    return "repair" if score < 40 else "fermentation", ("initial_classification",)
