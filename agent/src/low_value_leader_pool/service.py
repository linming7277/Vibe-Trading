"""Synchronize the persisted low-value leader pool from existing Value Line projections."""

from __future__ import annotations

from typing import Any

from src.entry_research import get_entry_research_service
from src.level3_leaders.service import Level3IndustryLeaderService, get_level3_leader_service
from src.value_price_zones import ValuePriceZoneService, get_value_price_zone_service

from .store import LowValueLeaderPoolRepository


ACTIVE_LEADER_STATES = {"NEW", "ACTIVE", "REENTERED"}
LOW_VALUE_STATES = {"UNDERVALUED", "DEEPLY_UNDERVALUED"}


class LowValueLeaderPoolService:
    """Builds a durable list without changing any upstream Value Line result."""

    def __init__(
        self,
        *,
        repository: LowValueLeaderPoolRepository | None = None,
        leader_service: Level3IndustryLeaderService | Any | None = None,
        price_zone_service: ValuePriceZoneService | Any | None = None,
        entry_research_service: Any | None = None,
        risk_snapshot_repository: Any | None = None,
        method_recorder: Any | None = None,
    ) -> None:
        self.repository = repository or LowValueLeaderPoolRepository()
        self.leader_service = leader_service or get_level3_leader_service()
        self.price_zone_service = price_zone_service or get_value_price_zone_service()
        self.entry_research_service = entry_research_service or get_entry_research_service()
        if risk_snapshot_repository is None:
            # Local import keeps the low-value pool independent from the optional
            # snapshot projection during module initialization.
            from src.low_value_risk_snapshot.store import LowValueRiskSnapshotRepository
            risk_snapshot_repository = LowValueRiskSnapshotRepository(self.repository.db_path)
        self.risk_snapshot_repository = risk_snapshot_repository
        if method_recorder is None:
            # Observational PIT provenance recorder; owns its additive tables in
            # the same research.db.  Never alters pool membership or valuation.
            from src.pit_replay.recorder import ValuationMethodRecorder
            from src.pit_replay.store import PITReplayStore
            method_recorder = ValuationMethodRecorder(PITReplayStore(self.repository.db_path))
        self.method_recorder = method_recorder

    @staticmethod
    def _primary_members(pool: dict[str, Any]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for member in pool.get("members") or []:
            if str(member.get("lifecycle_status") or "") not in ACTIVE_LEADER_STATES:
                continue
            code = str(member.get("stock_code") or "").upper()
            if code:
                grouped.setdefault(code, []).append(member)
        primary: dict[str, dict[str, Any]] = {}
        for code, memberships in grouped.items():
            memberships.sort(key=lambda item: (-float(item.get("leader_score") or 0), int(item.get("leader_rank") or 9999), str(item.get("level3_code") or "")))
            selected = dict(memberships[0])
            selected["_memberships"] = [
                {
                    "industry_code": item.get("level3_code"), "industry_name": item.get("level3_name"),
                    "leader_rank": item.get("leader_rank"), "leader_score": item.get("leader_score"),
                    "lifecycle_status": item.get("lifecycle_status"),
                }
                for item in memberships
            ]
            primary[code] = selected
        return primary

    @staticmethod
    def _support_status(zones: dict[str, Any]) -> str:
        return "AVAILABLE" if zones.get("support_zones") else "INSUFFICIENT_DATA"

    @staticmethod
    def _nearest_support(zones: dict[str, Any], current_price: Any) -> dict[str, Any] | None:
        """Pick the historical support zone closest to the current price."""
        support_zones = zones.get("support_zones") or []
        if not support_zones:
            return None
        price = float(current_price) if current_price is not None else None

        def distance(zone: dict[str, Any]) -> float:
            low = zone.get("low")
            high = zone.get("high")
            if price is None:
                return 0.0
            if low is not None and price < low:
                return (low - price) / low
            if high is not None and price > high:
                return (price - high) / high
            return 0.0

        zone = min(support_zones, key=distance)
        return {"low": zone.get("low"), "high": zone.get("high")}

    @staticmethod
    def _remove_reason(status: str) -> str:
        if status == "INSUFFICIENT_DATA":
            return "VALUATION_DATA_INSUFFICIENT"
        return "VALUATION_RECOVERED"

    def _snapshot(self, member: dict[str, Any], zones: dict[str, Any], *, pool_id: str, source_as_of: str) -> dict[str, Any]:
        valuation = dict(zones.get("valuation") or {})
        historical = dict(zones.get("historical_valuation") or {})
        valuation_methods = [
            method for method in list(valuation.get("methods") or [])
            if isinstance(method, dict)
            and str(method.get("status") or "") == "READY"
            and isinstance(method.get("fair_values"), list)
            and len(method.get("fair_values") or []) == 3
        ]
        peer_counts = [
            int(method["peer_count"])
            for method in valuation_methods
            if isinstance(method.get("peer_count"), (int, float)) and int(method["peer_count"]) > 0
        ]
        valuation_quality = {
            "method_count": len(valuation_methods),
            "min_peer_count": min(peer_counts) if peer_counts else None,
            "method_names": [str(method.get("name") or "") for method in valuation_methods],
        }
        code = str(member["stock_code"]).upper()
        entry_level: str | None = None
        entry_score: Any = None
        try:
            entry = self.entry_research_service.get_entry_research("CN", code, as_of=source_as_of)
            entry_level = entry.get("entry_level")
            entry_score = entry.get("entry_score")
        except Exception:
            # Entry Research is display-only. Its absence never changes membership.
            entry_level = None
        # Persist the full reliability verdict (status + reasons) so a replay
        # reads the day's own provenance instead of recomputing it from
        # mutable current data.  Observational only; rules live in the shared
        # versioned contract.
        from src.value_strategy import valuation_reliability
        reliability = valuation_reliability(zones)
        return {
            "market": "CN", "stock_code": code, "company_name": str(member.get("stock_name") or code),
            "industry_code": str(member.get("level3_code") or ""), "industry_name": str(member.get("level3_name") or ""),
            "leader_rank": int(member.get("leader_rank") or 0), "leader_score": float(member.get("leader_score") or 0),
            "current_price": zones.get("current_price"), "fair_value_low": valuation.get("fair_value_low"),
            "fair_value_mid": valuation.get("fair_value_mid"), "fair_value_high": valuation.get("fair_value_high"),
            "valuation_status": str(valuation.get("status") or "INSUFFICIENT_DATA"),
            "historical_valuation_status": historical.get("historical_valuation_status"),
            "support_status": self._support_status(zones), "entry_level": entry_level,
            "support_zone_low": (self._nearest_support(zones, zones.get("current_price")) or {}).get("low"),
            "support_zone_high": (self._nearest_support(zones, zones.get("current_price")) or {}).get("high"),
            "source_pool_id": pool_id, "source_as_of": source_as_of,
            "enter_reason": str(valuation.get("status") or "INSUFFICIENT_DATA"),
            "metadata": {
                "price_zone_as_of": zones.get("as_of"), "price_zone_formula_version": zones.get("formula_version"),
                "price_as_of": zones.get("price_as_of"),
                "price_source": dict((zones.get("data_quality") or {}).get("price") or {}).get("source"),
                "leader_memberships": member.get("_memberships") or [],
                "leader_state": member.get("lifecycle_status"),
                "leader_formula_version": member.get("leader_formula_version"),
                "entry_v1_level": entry_level,
                "entry_v1_score": entry_score,
                "data_quality": zones.get("data_quality") or {},
                # This is an audit snapshot of existing ValuePriceZone methods;
                # it does not recalculate or alter the low-value entry rule.
                "valuation_quality": {**valuation_quality, "as_of": zones.get("as_of")},
                "valuation_reliability": reliability,
            },
        }

    def refresh_low_value_leader_pool(self, *, as_of: str | None = None) -> dict[str, Any]:
        pool = self.leader_service.ensure_current_pool()
        # A historical rebuild can legitimately create a newer dated L3 pool
        # before the live close-snapshot scheduler catches up.  In that case a
        # resumed earlier run must refresh its own immutable pool, rather than
        # accidentally mixing its requested date with whichever pool happens
        # to be globally current.
        if as_of and str(pool.get("as_of") or "") != str(as_of):
            store = getattr(self.leader_service, "store", None)
            find_pool = getattr(store, "pool_for_as_of", None)
            dated_pool = find_pool(str(as_of)) if callable(find_pool) else None
            if dated_pool:
                load_pool = getattr(self.leader_service, "get_pool", None)
                pool = (load_pool(str(dated_pool["id"])) if callable(load_pool) else None) or dated_pool
        source_pool_id = str(pool["id"])
        source_as_of = str(as_of or pool["as_of"])
        pool_as_of = str(pool.get("as_of") or "")
        if pool_as_of != source_as_of:
            # A pool is a materialized result for one research date.  Mixing it
            # with a caller-provided date would make the EOD projection
            # unreproducible, so fail closed instead of silently using latest.
            raise ValueError(
                f"L3_AS_OF_MISMATCH: requested {source_as_of}, current L3 pool is {pool_as_of}"
            )
        candidates = self._primary_members(pool)
        eligible: dict[str, dict[str, Any]] = {}
        evaluated: dict[str, str] = {}
        errors: list[dict[str, str]] = []

        for code, member in candidates.items():
            try:
                zones = self.price_zone_service.get_price_zones("CN", code, as_of=source_as_of)
                price_quality = dict((zones.get("data_quality") or {}).get("price") or {})
                # A missing or stale same-day price must never turn into a
                # false valuation exit, nor may the previous daily close be
                # relabelled as today's price.  Keep the prior successful
                # pool projection and mark this refresh partial instead.
                if price_quality and str(price_quality.get("status") or "") != "READY":
                    errors.append({
                        "stock_code": code,
                        "error": str(price_quality.get("message") or "same-day quote is unavailable")[:500],
                    })
                    continue
                snapshot = self._snapshot(member, zones, pool_id=source_pool_id, source_as_of=source_as_of)
                status = snapshot["valuation_status"]
                evaluated[code] = status
                # PIT provenance: persist the method bundle for every evaluated
                # L3 Top1/Top2 candidate on the same zones object the pool used,
                # so a replay sees exactly the day's own peer evidence.  The
                # pool rule itself is untouched.
                try:
                    self.method_recorder.record(
                        "CN", code, research_as_of=source_as_of, zones=zones, source_pool_id=source_pool_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append({"stock_code": code, "error": f"PIT_BUNDLE_RECORD_FAILED: {type(exc).__name__}: {exc}"[:500]})
                if status in LOW_VALUE_STATES:
                    eligible[code] = snapshot
            except Exception as exc:
                # A transient projection failure must not falsely remove an existing focus period.
                errors.append({"stock_code": code, "error": f"{type(exc).__name__}: {exc}"[:500]})

        changes = self.repository.synchronize_refresh(
            eligible=eligible, current_codes=set(candidates), evaluated=evaluated,
            error_codes={item["stock_code"] for item in errors}, source_pool_id=source_pool_id,
            source_as_of=source_as_of, remove_reason=self._remove_reason,
        )

        status = "PARTIAL" if errors else "COMPLETED"
        self.repository.record_refresh(
            source_as_of=source_as_of,
            source_pool_id=source_pool_id,
            status=status,
            active_count=len(self.repository.active("CN")),
            changes=changes,
            errors=errors,
        )
        # Materialize the immutable daily snapshot on the same day it was
        # produced, so replay readiness never depends on a future EOD run
        # happening.  Idempotent; no valuation is recomputed here.
        try:
            self.repository.materialize_daily_snapshot(source_as_of, source_pool_id, market="CN")
        except Exception as exc:  # noqa: BLE001
            errors.append({"stock_code": "", "error": f"DAILY_SNAPSHOT_MATERIALIZATION_FAILED: {type(exc).__name__}: {exc}"[:500]})
            status = "PARTIAL"
            self.repository.record_refresh(
                source_as_of=source_as_of,
                source_pool_id=source_pool_id,
                status=status,
                active_count=len(self.repository.active("CN")),
                changes=changes,
                errors=errors,
            )
        self._record_freshness_manifest(source_pool_id, source_as_of)

        return {
            "source_pool_id": source_pool_id, "source_as_of": source_as_of,
            "processed": len(candidates), **changes,
            "active": len(self.repository.active("CN")), "errors": errors,
            "status": status,
        }

    def _record_freshness_manifest(self, source_pool_id: str, source_as_of: str) -> None:
        """Record the pool's input fingerprint (L3 pool id + member projection set) for freshness checks (plan §20.3).

        Best effort: manifest recording must never fail the EOD refresh.
        """
        try:
            import hashlib
            import json

            from src.research_freshness.manifests import ResearchManifestStore

            members = sorted(
                (str(item.get("stock_code") or ""), str(item.get("valuation_status") or ""),
                 str(item.get("entry_level") or ""), str(item.get("current_price") or ""))
                for item in self.repository.active("CN")
            )
            fingerprint = hashlib.sha256(json.dumps(
                {"source_pool_id": source_pool_id, "members": members},
                ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")).hexdigest()
            ResearchManifestStore().record(
                research_type="low_value_pool", market="CN", stock_code="",
                research_as_of=source_as_of, input_fingerprint=fingerprint,
                formula_version="low-value-pool-refresh-v1",
                source_hashes={"source_pool_id": source_pool_id},
            )
        except Exception:  # noqa: BLE001
            return

    def active_low_value_leaders(self) -> dict[str, Any]:
        # This is a pure join of already materialized data.  It must not invoke
        # RiskResearchService on a user GET request.
        items = self.risk_snapshot_repository.attach_to_pool_items(self.repository.active("CN"))
        latest = max((str(item.get("updated_at") or "") for item in items), default=None)
        data_as_of = max((str(item.get("source_as_of") or "") for item in items), default=None)
        return {"items": items, "total": len(items), "data_as_of": data_as_of, "last_evaluated_at": latest}

    def low_value_leader_history(self, *, stock_code: str | None = None, limit: int = 100) -> dict[str, Any]:
        items = self.repository.history("CN", stock_code, limit=limit)
        return {"items": items, "total": len(items)}

    def low_value_leader_events(self, *, limit: int = 20) -> dict[str, Any]:
        """Read the current pool's persisted change events; never refreshes it.

        A normal refresh can validly produce no events.  The UI must still show
        that current result's date and zero changes, rather than relabelling
        the last historical event as today's change.
        """
        active = self.repository.active("CN")
        source_as_of = max((str(item.get("source_as_of") or "") for item in active), default=None)
        return self.repository.event_summary("CN", limit=limit, event_date=source_as_of)


_service: LowValueLeaderPoolService | None = None


def get_low_value_leader_pool_service() -> LowValueLeaderPoolService:
    global _service
    if _service is None:
        _service = LowValueLeaderPoolService()
    return _service
