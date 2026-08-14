"""Application service that runs pure engines and persists reproducible outputs."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .common.contracts import FeatureSnapshot, RegimeSnapshot, ScoreResult, SignalHorizon, SignalStatus, StrategyLine, StrategySignal
from .common.normalization import cross_sectional_percentiles
from .common.provenance import idempotency_key
from .emotion.emotion_regime import FORMULA_VERSION as EMOTION_REGIME_VERSION
from .emotion.pipeline import FORMULA_VERSION as EMOTION_PIPELINE_VERSION, run_emotion_pipeline
from .emotion.short_candidate import FORMULA_VERSION as SHORT_VERSION
from .emotion.sector_heat import FORMULA_VERSION as SECTOR_HEAT_VERSION
from .emotion.swing_candidate import FORMULA_VERSION as SWING_VERSION
from .store import StrategyEngineStore
from .value.leader_score import FORMULA_VERSION as LEADER_VERSION
from .value.macro_regime import FORMULA_VERSION as MACRO_VERSION
from .value.pipeline import FORMULA_VERSION as VALUE_PIPELINE_VERSION, run_value_pipeline
from .value.sector_score import FORMULA_VERSION as SECTOR_VERSION
from .value.timing import FORMULA_VERSION as VALUE_TIMING_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _future_day(as_of: str, days: int) -> str:
    return (date.fromisoformat(as_of) + timedelta(days=days)).isoformat()


class StrategyEngineService:
    """Run value or emotion inputs without allowing an LLM to supply weights."""

    def __init__(self, store: StrategyEngineStore | None = None) -> None:
        self.store = store or StrategyEngineStore()
        self._owns_store = store is None

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def run(
        self,
        *,
        strategy_line: str,
        market: str,
        as_of: str,
        symbols: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        run, created = self.prepare(
            strategy_line=strategy_line, market=market, as_of=as_of, symbols=symbols,
            force_refresh=force_refresh,
        )
        if not created:
            return run
        return self.execute_prepared(run, inputs=inputs)

    def prepare(
        self,
        *,
        strategy_line: str,
        market: str,
        as_of: str,
        symbols: list[str] | None = None,
        force_refresh: bool = False,
        profile_id: str | None = None,
        profile_version: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if strategy_line not in {"value", "emotion"}:
            raise ValueError("strategy_line must be value or emotion")
        if market not in {"CN", "HK"}:
            raise ValueError("v1 strategy engines support CN and HK only")
        date.fromisoformat(as_of)
        formula = VALUE_PIPELINE_VERSION if strategy_line == "value" else EMOTION_PIPELINE_VERSION
        effective_formula = f"{formula}:profile={profile_id}:v{profile_version}" if strategy_line == "value" and profile_id else formula
        key = idempotency_key(strategy_line, market, as_of, symbols, effective_formula)
        return self.store.create_or_get_run(
            idempotency_key=key,
            strategy_line=strategy_line,
            market=market,
            as_of=as_of,
            symbols=symbols or [],
            formula_version=effective_formula,
            profile_id=profile_id,
            profile_version=profile_version,
            force_refresh=force_refresh,
        )

    def execute_prepared(self, run: dict[str, Any], *, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        strategy_line = str(run["strategy_line"])
        market = str(run["market"])
        as_of = str(run["as_of"])
        try:
            effective_inputs = inputs or self._load_current_inputs(strategy_line, market)
            if strategy_line == "value":
                count = self._run_value(run["id"], market, as_of, effective_inputs)
            else:
                count = self._run_emotion(run["id"], market, as_of, effective_inputs)
            status = "completed" if count else "insufficient_data"
            return self.store.finish_run(
                run["id"],
                status=status,
                source_status=str(effective_inputs.get("source_status") or ("live" if inputs else "stale")),
                message=f"persisted {count} reproducible score/signal outputs" if count else "coverage below strategy thresholds",
            )
        except Exception as exc:
            self.store.finish_run(run["id"], status="failed", source_status="unavailable", message=str(exc))
            raise

    def _load_current_inputs(self, strategy_line: str, market: str) -> dict[str, Any]:
        if market != "CN":
            return {"source_status": "unavailable", "macro": {}, "market": {"components": {}}, "sectors": [], "leaders": [], "candidates": []}
        try:
            from src.tdx_data.service import get_tdx_service

            service = get_tdx_service()
            overview = service.market_overview()
            sectors = service.sectors(limit=500).get("items", [])
            candidates = service.screener({"limit": 200, "sort": "market_cap_100m", "direction": "desc"}).get("items", [])
        except Exception:
            return {"source_status": "unavailable", "macro": {}, "market": {"components": {}}, "sectors": [], "leaders": [], "candidates": []}
        if strategy_line == "value":
            sector_raw = [
                {"prosperity": row.get("breadth_pct"), "relative_strength": row.get("change_pct")}
                for row in sectors
            ]
            sector_normalized = cross_sectional_percentiles(
                sector_raw, {"prosperity": True, "relative_strength": True},
            )
            leader_raw = [
                {
                    "industry_position_proxy": row.get("market_cap_100m"),
                    "net_profit": row.get("net_profit_10k"),
                    "eps": row.get("eps"),
                    "pe": row.get("pe_ttm"),
                    "pb": row.get("pb_mrq"),
                }
                for row in candidates
            ]
            leader_normalized = cross_sectional_percentiles(
                leader_raw,
                {"industry_position_proxy": True, "net_profit": True, "eps": True, "pe": False, "pb": False},
            )
            return {
                "source_status": "stale" if not overview.get("as_of") else "live",
                "macro": {},
                "sectors": [
                    {
                        "id": str(row.get("code") or ""), "name": str(row.get("name") or ""),
                        "raw_features": {"change_pct": row.get("change_pct"), "breadth_pct": row.get("breadth_pct")},
                        "components": sector_normalized[index],
                    }
                    for index, row in enumerate(sectors) if row.get("code")
                ],
                "leaders": [
                    {
                        "symbol": row.get("code"), "name": row.get("name"), "price": row.get("price"),
                        "raw_features": {key: row.get(key) for key in ("market_cap_100m", "net_profit_10k", "eps", "pe_ttm", "pb_mrq", "change_pct")},
                        "components": {
                            "industry_position_proxy": leader_normalized[index]["industry_position_proxy"],
                            "profitability_quality": self._mean_available(
                                leader_normalized[index]["net_profit"], leader_normalized[index]["eps"],
                            ),
                            "growth_stability": None,
                            "valuation_margin": self._mean_available(
                                leader_normalized[index]["pe"], leader_normalized[index]["pb"],
                            ),
                            "cash_flow": None,
                            "governance_risk": 0.0 if row.get("is_st") or row.get("is_quit") else 100.0,
                        },
                        "timing": {},
                    }
                    for index, row in enumerate(candidates) if row.get("code")
                ],
            }
        breadth = overview.get("breadth") or {}
        valid = int(breadth.get("valid") or 0)
        breadth_score = (float(breadth.get("up") or 0) / valid * 100) if valid else None
        return {
            "source_status": "stale" if not overview.get("as_of") else "live",
            "market": {"components": {"breadth_limits": breadth_score}},
            "sectors": [],
            "candidates": [],
        }

    @staticmethod
    def _mean_available(*values: float | None) -> float | None:
        available = [float(value) for value in values if value is not None]
        return round(sum(available) / len(available), 4) if available else None

    def _feature(self, run_id: str, market: str, subject_type: str, subject_id: str, as_of: str, features: dict[str, Any], source_status: str) -> None:
        self.store.save_feature(FeatureSnapshot(
            id=_id("feature"), engine_run_id=run_id, market=market,
            subject_type=subject_type, subject_id=subject_id, data_as_of=as_of,
            available_at=_now(), features={key: value for key, value in features.items()},
            sources={key: source_status for key in features}, created_at=_now(),
        ))

    def _score(self, *, run_id: str, strategy_line: StrategyLine, market: str, subject_type: str, subject_id: str, as_of: str, engine: str, formula_version: str, raw: dict[str, Any], components: dict[str, Any], result: Any, evidence_ids: list[str] | None = None) -> ScoreResult:
        score = ScoreResult(
            id=_id("score"), engine_run_id=run_id, engine=engine, formula_version=formula_version,
            strategy_line=strategy_line, market=market, subject_type=subject_type, subject_id=subject_id,
            data_as_of=as_of, available_at=_now(), raw_features=raw,
            normalized_features=components, component_scores=components,
            base_score=result.score, coverage=result.coverage, status=result.status,
            evidence_ids=tuple(evidence_ids or []), created_at=_now(),
        )
        self.store.save_score(score)
        return score

    def _run_value(self, run_id: str, market: str, as_of: str, inputs: dict[str, Any]) -> int:
        output = run_value_pipeline(macro=dict(inputs.get("macro") or {}), sectors=list(inputs.get("sectors") or []), leaders=list(inputs.get("leaders") or []))
        macro = output["macro"]
        self._feature(run_id, market, "market", market, as_of, dict(inputs.get("macro") or {}), str(inputs.get("source_status") or "unknown"))
        self.store.save_regime(RegimeSnapshot(
            id=_id("regime"), engine_run_id=run_id, strategy_line=StrategyLine.VALUE,
            market=market, regime=str(macro["regime"]), previous_regime=(self.store.latest_regime("value", market) or {}).get("regime"),
            score=macro["score"], confidence=float(macro["coverage"]), coverage=float(macro["coverage"]),
            triggers=(), data_as_of=as_of, available_at=_now(), formula_version=MACRO_VERSION, created_at=_now(),
        ))
        ready = 1 if macro["status"] == "ready" else 0
        for row in output["sectors"]:
            subject = str(row.get("id") or row.get("sector_code") or "")
            if not subject:
                continue
            raw, components = dict(row.get("raw_features") or {}), dict(row.get("components") or {})
            self._feature(run_id, market, "sector", subject, as_of, raw, str(inputs.get("source_status") or "unknown"))
            self._score(run_id=run_id, strategy_line=StrategyLine.VALUE, market=market, subject_type="sector", subject_id=subject, as_of=as_of, engine="value_sector", formula_version=SECTOR_VERSION, raw=raw, components=components, result=row["result"])
            ready += int(row["result"].status == "ready")
        for row in output["leaders"]:
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            raw, components = dict(row.get("raw_features") or {}), dict(row.get("components") or {})
            self._feature(run_id, market, "security", symbol, as_of, raw, str(inputs.get("source_status") or "unknown"))
            score = self._score(run_id=run_id, strategy_line=StrategyLine.VALUE, market=market, subject_type="security", subject_id=symbol, as_of=as_of, engine="value_leader", formula_version=LEADER_VERSION, raw=raw, components=components, result=row["result"], evidence_ids=row.get("evidence_ids"))
            timing_result = row["timing_result"]
            if score.base_score is not None and score.base_score >= 70 and timing_result.score is not None and timing_result.score >= 60:
                price = float(row.get("price") or 0)
                risk = dict(row.get("risk") or {})
                signal = StrategySignal(
                    id=_id("signal"), engine_run_id=run_id, strategy_line=StrategyLine.VALUE,
                    horizon=SignalHorizon.LONG, market=market, symbol=symbol, data_as_of=as_of,
                    valid_from=_future_day(as_of, 1), valid_until=_future_day(as_of, 60), direction="buy",
                    base_score=score.base_score, entry_low=risk.get("entry_low", round(price * .95, 4) if price else None),
                    entry_high=risk.get("entry_high", price or None), stop_price=risk.get("stop_price", round(price * .88, 4) if price else None),
                    target_low=risk.get("target_low", round(price * 1.15, 4) if price else None), target_high=risk.get("target_high", round(price * 1.30, 4) if price else None),
                    position_cap=min(.10, float(risk.get("position_cap", .10))), coverage=score.coverage,
                    formula_versions=(VALUE_PIPELINE_VERSION, LEADER_VERSION, VALUE_TIMING_VERSION), evidence_ids=score.evidence_ids,
                    status=SignalStatus.PROPOSED, invalidation_rules=tuple(risk.get("invalidation_rules") or ("fundamental_thesis_break",)), created_at=_now(),
                )
                self.store.save_signal(signal)
            ready += int(row["result"].status == "ready")
        return ready

    def _run_emotion(self, run_id: str, market: str, as_of: str, inputs: dict[str, Any]) -> int:
        previous = (self.store.latest_regime("emotion", market) or {}).get("regime")
        output = run_emotion_pipeline(market=dict(inputs.get("market") or {}), sectors=list(inputs.get("sectors") or []), candidates=list(inputs.get("candidates") or []), previous_regime=previous)
        market_input = dict(inputs.get("market") or {})
        components = dict(market_input.get("components") or {})
        score_result = output["emotion_score"]
        self._feature(run_id, market, "market", market, as_of, components, str(inputs.get("source_status") or "unknown"))
        self._score(run_id=run_id, strategy_line=StrategyLine.EMOTION, market=market, subject_type="market", subject_id=market, as_of=as_of, engine="emotion_market", formula_version=EMOTION_PIPELINE_VERSION, raw=components, components=components, result=score_result)
        self.store.save_regime(RegimeSnapshot(
            id=_id("regime"), engine_run_id=run_id, strategy_line=StrategyLine.EMOTION, market=market,
            regime=str(output["regime"]), previous_regime=previous, score=score_result.score,
            confidence=score_result.coverage, coverage=score_result.coverage,
            triggers=tuple(output["regime_triggers"]), data_as_of=as_of, available_at=_now(),
            formula_version=EMOTION_REGIME_VERSION, changed_at=_now() if previous != output["regime"] else None, created_at=_now(),
        ))
        ready = int(score_result.status == "ready")
        for row in output["sectors"]:
            subject = str(row.get("id") or row.get("sector_code") or "")
            if not subject:
                continue
            raw, normalized = dict(row.get("raw_features") or {}), dict(row.get("components") or {})
            self._feature(run_id, market, "sector", subject, as_of, raw, str(inputs.get("source_status") or "unknown"))
            self._score(
                run_id=run_id, strategy_line=StrategyLine.EMOTION, market=market,
                subject_type="sector", subject_id=subject, as_of=as_of,
                engine="emotion_sector_heat", formula_version=SECTOR_HEAT_VERSION,
                raw=raw, components=normalized, result=row["result"], evidence_ids=row.get("evidence_ids"),
            )
            ready += int(row["result"].status == "ready")
        for row in output["candidates"]:
            symbol = str(row.get("symbol") or "")
            if not symbol:
                continue
            horizon = str(row.get("horizon") or "short")
            raw, normalized = dict(row.get("raw_features") or {}), dict(row.get("components") or {})
            self._feature(run_id, market, "security", symbol, as_of, raw, str(inputs.get("source_status") or "unknown"))
            formula = SWING_VERSION if horizon == "swing" else SHORT_VERSION
            score = self._score(run_id=run_id, strategy_line=StrategyLine.EMOTION, market=market, subject_type="security", subject_id=symbol, as_of=as_of, engine=f"emotion_{horizon}", formula_version=formula, raw=raw, components=normalized, result=row["result"], evidence_ids=row.get("evidence_ids"))
            threshold = 65 if horizon == "swing" else 70
            if row["eligible"] and score.base_score is not None and score.base_score >= threshold:
                price = float(row.get("price") or 0)
                risk = dict(row.get("risk") or {})
                hold_days = 60 if horizon == "swing" else 5
                cap = .12 if horizon == "swing" else .05
                signal = StrategySignal(
                    id=_id("signal"), engine_run_id=run_id, strategy_line=StrategyLine.EMOTION,
                    horizon=SignalHorizon.SWING if horizon == "swing" else SignalHorizon.SHORT,
                    market=market, symbol=symbol, data_as_of=as_of, valid_from=_future_day(as_of, 1),
                    valid_until=_future_day(as_of, hold_days), direction="buy", base_score=score.base_score,
                    entry_low=risk.get("entry_low", round(price * .98, 4) if price else None), entry_high=risk.get("entry_high", price or None),
                    stop_price=risk.get("stop_price", round(price * (.90 if horizon == "swing" else .94), 4) if price else None),
                    target_low=risk.get("target_low", round(price * (1.12 if horizon == "swing" else 1.06), 4) if price else None),
                    target_high=risk.get("target_high", round(price * (1.25 if horizon == "swing" else 1.12), 4) if price else None),
                    position_cap=min(cap, float(risk.get("position_cap", cap))), coverage=score.coverage,
                    formula_versions=(EMOTION_PIPELINE_VERSION, formula), evidence_ids=score.evidence_ids,
                    status=SignalStatus.PROPOSED, invalidation_rules=tuple(row.get("exclusion_reasons") or ("emotion_regime_deterioration",)), created_at=_now(),
                )
                self.store.save_signal(signal)
            ready += int(row["result"].status == "ready")
        return ready
