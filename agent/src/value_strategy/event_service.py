"""Deterministic state-transition detection for the Value Strategy projection."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.research_workspace.store import normalize_market, normalize_symbol

from .event_store import ValueStrategyEventRepository
from .service import ValueStrategyStateService, get_value_strategy_state_service

EVENT_FORMULA_VERSION = "value-strategy-state-events-v1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(prefix: str, value: Any, length: int = 24) -> str:
    return f"{prefix}_{hashlib.sha256(_stable(value).encode('utf-8')).hexdigest()[:length]}"


def _value(state: dict[str, Any], *path: str, fallback: str = "UNKNOWN") -> str:
    current: Any = state
    for key in path:
        if not isinstance(current, dict):
            return fallback
        current = current.get(key)
    text = str(current or fallback).strip()
    return text or fallback


def _leader_scope(state: dict[str, Any]) -> str:
    try:
        rank = int((state.get("leader") or {}).get("rank"))
    except (TypeError, ValueError):
        return "OUT_OF_TOP2"
    return "TOP1" if rank == 1 else "TOP2" if rank == 2 else "OUT_OF_TOP2"


def project_event_state(state: dict[str, Any]) -> dict[str, str]:
    """Only fields authorized by the Phase 2 event contract affect the fingerprint."""
    return {
        "eligibility": _value(state, "eligibility", "status"),
        "priority": _value(state, "priority", "tier", fallback="NOT_APPLICABLE"),
        "primary_action": _value(state, "primary_action", "status"),
        "risk": _value(state, "risk", "overall"),
        "value_trap": _value(state, "risk", "trap"),
        "thesis_status": _value(state, "thesis", "status", fallback="MISSING"),
        "thesis_authority": _value(state, "thesis", "authority", fallback="MISSING"),
        "leader_scope": _leader_scope(state),
        "valuation_reliability": _value(state, "price_attention", "valuation_reliability", "status", fallback="INSUFFICIENT"),
        "price_attention": _value(state, "price_attention", "effective_status"),
        "review_pressure": _value(state, "review_pressure", "effective_status", fallback="NORMAL"),
    }


class ValueStrategyEventService:
    """Compare two Phase 1 states without recomputing or redefining either state."""

    def __init__(
        self,
        *,
        repository: ValueStrategyEventRepository | None = None,
        state_service: ValueStrategyStateService | Any | None = None,
        pool_repository: LowValueLeaderPoolRepository | Any | None = None,
    ) -> None:
        self.repository = repository or ValueStrategyEventRepository()
        self.state_service = state_service or get_value_strategy_state_service()
        self.pool_repository = pool_repository or LowValueLeaderPoolRepository(self.repository.db_path)

    @staticmethod
    def _severity(event_type: str, before: str, after: str) -> str:
        if event_type == "THESIS_STATUS_CHANGED" and after == "FALSIFIED":
            return "CRITICAL"
        if event_type == "THESIS_AUTHORITY_CHANGED" and after == "HUMAN_REJECTED":
            return "CRITICAL"
        if event_type == "RISK_CHANGED" and after == "HIGH":
            return "HIGH"
        if event_type == "PRIORITY_CHANGED" and before == "A" and after == "C":
            return "HIGH"
        if event_type == "REVIEW_PRESSURE_CHANGED" and after == "CRITICAL_REVIEW":
            return "HIGH"
        if event_type == "VALUE_SCOPE_EXITED":
            return "MEDIUM"
        if event_type == "LEADER_SCOPE_CHANGED" and "OUT_OF_TOP2" in {before, after}:
            return "MEDIUM"
        if event_type == "VALUATION_RELIABILITY_CHANGED" and after == "INSUFFICIENT":
            return "MEDIUM"
        return "INFO"

    @staticmethod
    def _direction(event_type: str, before: str, after: str) -> str | None:
        if event_type == "PRIORITY_CHANGED":
            order = {"A": 3, "B": 2, "C": 1}
            return "UPGRADE" if order[after] > order[before] else "DOWNGRADE"
        if event_type == "RISK_CHANGED":
            if before == "UNKNOWN" and after in {"LOW", "MEDIUM", "HIGH"}:
                return "DATA_RECOVERED"
            if after == "UNKNOWN":
                return "BECAME_UNKNOWN"
            order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
            return "ESCALATED" if order.get(after, 0) > order.get(before, 0) else "EASED"
        if event_type == "LEADER_SCOPE_CHANGED":
            if before == "OUT_OF_TOP2":
                return "REENTERED"
            if after == "OUT_OF_TOP2":
                return "OUT_OF_TOP2"
            return "RANK_MOVED"
        return None

    @staticmethod
    def _source_refs(state: dict[str, Any]) -> list[dict[str, Any]]:
        freshness = dict(state.get("freshness") or {})
        refs = [{"source": "ValueStrategyStateService", "formula_version": state.get("formula_version")}]
        for key in (
            "market_price_as_of", "low_value_as_of", "focus_as_of", "historical_valuation_as_of",
            "price_structure_as_of", "risk_as_of", "thesis_as_of",
        ):
            if freshness.get(key):
                refs.append({"source": key, "as_of": freshness[key]})
        leader = dict(state.get("leader") or {})
        if leader.get("as_of"):
            refs.append({"source": "leader_projection", "as_of": leader["as_of"], "rank": leader.get("rank")})
        return refs

    def _build_event(
        self, *, state: dict[str, Any], before_state: dict[str, Any], before_projection: dict[str, str],
        after_projection: dict[str, str], event_type: str, dimension: str, batch_id: str,
        occurred_at: str,
    ) -> dict[str, Any]:
        before, after = before_projection[dimension], after_projection[dimension]
        direction = self._direction(event_type, before, after)
        event_key_data = {
            "market": state["market"], "stock_code": state["stock_code"], "event_type": event_type,
            "before": before, "after": after, "research_as_of": state.get("research_as_of"),
            "trigger_dimension": dimension,
        }
        event_key = _hash("vek", event_key_data, 40)
        return {
            "id": _hash("vse", event_key_data, 24),
            "event_key": event_key,
            "market": state["market"],
            "stock_code": state["stock_code"],
            "event_type": event_type,
            "category": dimension.upper(),
            "severity": self._severity(event_type, before, after),
            "direction": direction,
            "before_value": before,
            "after_value": after,
            "before_state": before_state,
            "after_state": state,
            "primary_reason": f"{dimension} 从 {before} 变为 {after}",
            "reasons": list(state.get("reasons") or []),
            "cautions": list(state.get("cautions") or []),
            "trigger_dimension": dimension,
            "source_refs": self._source_refs(state),
            "transition_batch_id": batch_id,
            "research_as_of": state.get("research_as_of"),
            "occurred_at": occurred_at,
        }

    def detect_events(self, before_state: dict[str, Any], after_state: dict[str, Any], *, occurred_at: str | None = None) -> list[dict[str, Any]]:
        before, after = project_event_state(before_state), project_event_state(after_state)
        before_fp, after_fp = _hash("vss", before), _hash("vss", after)
        when = occurred_at or _now()
        batch_id = _hash("vsb", {
            "date": str(after_state.get("research_as_of") or when)[:10], "market": after_state["market"],
            "stock_code": after_state["stock_code"], "before": before_fp, "after": after_fp,
        })
        specs: list[tuple[str, str]] = []
        if before["eligibility"] != after["eligibility"]:
            specs.append(("VALUE_SCOPE_ENTERED" if after["eligibility"] == "IN_VALUE_SCOPE" else "VALUE_SCOPE_EXITED", "eligibility"))
        if before["priority"] in {"A", "B", "C"} and after["priority"] in {"A", "B", "C"} and before["priority"] != after["priority"]:
            specs.append(("PRIORITY_CHANGED", "priority"))
        if before["primary_action"] != after["primary_action"]:
            specs.append(("PRIMARY_ACTION_CHANGED", "primary_action"))
        if before["risk"] != after["risk"]:
            specs.append(("RISK_CHANGED", "risk"))
        if after["eligibility"] == "IN_VALUE_SCOPE" and before["value_trap"] != after["value_trap"]:
            specs.append(("VALUE_TRAP_CHANGED", "value_trap"))
        if before["thesis_status"] != after["thesis_status"]:
            specs.append(("THESIS_STATUS_CHANGED", "thesis_status"))
        if before["thesis_authority"] != after["thesis_authority"]:
            specs.append(("THESIS_AUTHORITY_CHANGED", "thesis_authority"))
        if before["leader_scope"] != after["leader_scope"]:
            specs.append(("LEADER_SCOPE_CHANGED", "leader_scope"))
        def reliability_group(value: str) -> str:
            return "GOOD" if value in {"RELIABLE", "LIMITED"} else "BAD"
        if reliability_group(before["valuation_reliability"]) != reliability_group(after["valuation_reliability"]):
            specs.append(("VALUATION_RELIABILITY_CHANGED", "valuation_reliability"))
        if after["eligibility"] == "IN_VALUE_SCOPE" and before["price_attention"] != after["price_attention"]:
            specs.append(("PRICE_ATTENTION_CHANGED", "price_attention"))
        if before["review_pressure"] != after["review_pressure"]:
            specs.append(("REVIEW_PRESSURE_CHANGED", "review_pressure"))
        return [self._build_event(
            state=after_state, before_state=before_state, before_projection=before,
            after_projection=after, event_type=event_type, dimension=dimension,
            batch_id=batch_id, occurred_at=when,
        ) for event_type, dimension in specs]

    @staticmethod
    def _cursor(state: dict[str, Any], projection: dict[str, str], *, history_start: str, existing: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "market": state["market"], "stock_code": state["stock_code"],
            **{f"current_{key}": value for key, value in projection.items()},
            "state_fingerprint": _hash("vss", projection),
            "research_as_of": state.get("research_as_of"),
            "market_price_as_of": (state.get("freshness") or {}).get("market_price_as_of"),
            "state": state,
            "event_history_start_at": (existing or {}).get("event_history_start_at") or history_start,
            "created_at": (existing or {}).get("created_at"),
        }

    def evaluate_company(
        self, market: str, stock_code: str, *, research_as_of: str | None = None,
        dry_run: bool = False, occurred_at: str | None = None,
    ) -> dict[str, Any]:
        market = normalize_market(market)
        stock_code = normalize_symbol(market, stock_code)
        state = self.state_service.get_strategy_state(market, stock_code, research_as_of=research_as_of)
        projection = project_event_state(state)
        existing = self.repository.get_cursor(market, stock_code)
        when = occurred_at or _now()
        events = [] if existing is None else self.detect_events(existing["state"], state, occurred_at=when)
        cursor = self._cursor(state, projection, history_start=when, existing=existing)
        created_ids = [] if dry_run else self.repository.persist_evaluation(cursor, events)
        return {
            "status": "BASELINE_CREATED" if existing is None else "EVALUATED",
            "market": market, "stock_code": stock_code, "dry_run": dry_run,
            "would_create_cursor": existing is None,
            "would_create_events": len(events),
            "created_event_ids": created_ids,
            "state_fingerprint": cursor["state_fingerprint"],
        }

    def evaluation_universe(self, market: str = "CN") -> list[str]:
        current = {str(item["stock_code"]) for item in self.pool_repository.active(market)}
        return sorted(current | set(self.repository.cursor_scope(market)))

    @contextmanager
    def _batch_read_cache(self):
        """Cache immutable cross-section reads for one EOD batch only.

        Phase 1 is still called once per company and remains the sole state
        authority.  This avoids decoding the same L3 pool and financial
        cross-section hundreds of times without changing any rule or result.
        """
        targets: list[tuple[Any, str]] = []
        candidates = [
            (self.state_service, "focus_service", "get_focus_selection"),
            (self.state_service, "pool_repository", "active"),
            (self.state_service, "price_zone_service", "get_price_zones"),
            (getattr(self.state_service, "risk_service", None), None, "leader_pool_reader"),
        ]
        leader = getattr(self.state_service, "leader_service", None)
        if leader is not None:
            candidates.extend([
                (leader, None, "financial_loader"),
                (getattr(leader, "leader_store", None), None, "all_rows"),
                (getattr(leader, "leader_store", None), None, "industry_rows"),
            ])
        try:
            for owner, nested, name in candidates:
                target = getattr(owner, nested, None) if nested and owner is not None else owner
                original = getattr(target, name, None) if target is not None else None
                if original is None:
                    continue
                setattr(target, name, lru_cache(maxsize=512)(original))
                targets.append((target, name, original))
            yield
        finally:
            for target, name, original in reversed(targets):
                setattr(target, name, original)

    def evaluate_universe(self, *, market: str = "CN", research_as_of: str | None = None, dry_run: bool = False, stock_codes: Iterable[str] | None = None) -> dict[str, Any]:
        codes = list(stock_codes) if stock_codes is not None else self.evaluation_universe(market)
        results, errors = [], []
        with self._batch_read_cache():
            for code in codes:
                try:
                    results.append(self.evaluate_company(market, code, research_as_of=research_as_of, dry_run=dry_run))
                except Exception as exc:
                    errors.append({"stock_code": code, "error": f"{type(exc).__name__}: {exc}"})
        return {
            "status": "COMPLETED" if not errors else "PARTIAL", "market": market,
            "research_as_of": research_as_of, "dry_run": dry_run, "companies": len(codes),
            "would_create_cursors": sum(bool(item["would_create_cursor"]) for item in results),
            "would_create_events": sum(int(item["would_create_events"]) for item in results),
            "created_events": sum(len(item["created_event_ids"]) for item in results),
            "errors": errors,
        }


_services: dict[str, ValueStrategyEventService] = {}


def get_value_strategy_event_service(db_path: Path | None = None) -> ValueStrategyEventService:
    key = str(Path(db_path).resolve()) if db_path else "default"
    if key not in _services:
        repository = ValueStrategyEventRepository(db_path)
        if db_path:
            pool = LowValueLeaderPoolRepository(db_path)
            state = ValueStrategyStateService(pool_repository=pool)
            _services[key] = ValueStrategyEventService(repository=repository, state_service=state, pool_repository=pool)
        else:
            _services[key] = ValueStrategyEventService(repository=repository)
    return _services[key]
