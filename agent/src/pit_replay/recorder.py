"""EOD recorder for valuation-method provenance bundles.

Hooked immediately after the ValuePriceZone projection completes for each
candidate, this persists — observationally, without changing any rule — the
per-method PE/PB status, sample counts, median multiples, and peer code sets
that fed the day's fair-value estimate, together with the reliability verdict
and the reason it was reached.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.value_price_zones import FORMULA_VERSION as PRICE_ZONE_FORMULA_VERSION
from src.value_strategy import RELIABILITY_FORMULA_VERSION, valuation_reliability

from .store import PITReplayStore


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_peer_method_bundle(zones: dict[str, Any]) -> dict[str, Any]:
    """Assemble the per-method PE/PB provenance from one zones projection."""
    valuation = dict(zones.get("valuation") or {})
    quality = dict((zones.get("data_quality") or {}).get("peer_comparables") or {})
    methods_by_kind = {
        str(method.get("kind") or ""): method
        for method in valuation.get("methods") or []
        if isinstance(method, dict)
    }
    bundle: dict[str, Any] = {}
    for kind, code_key, sample_key in (
        ("PE", "pe_codes", "pe_peer_count"),
        ("PB", "pb_codes", "pb_peer_count"),
    ):
        method = methods_by_kind.get(kind)
        codes = sorted(str(code) for code in quality.get(code_key) or [])
        if method and str(method.get("status")) == "READY":
            entry = {
                "status": "READY",
                "peer_count": int(method.get("peer_count") or 0),
                "median_or_reference": method.get("multiple_mid"),
                "multiple_low": method.get("multiple_low"),
                "multiple_high": method.get("multiple_high"),
                "peer_codes": codes,
                "peer_codes_hash": _stable_hash(codes),
                "method_name": method.get("name"),
            }
        else:
            # The method was not usable that day.  Keep the observed peer set
            # (it explains *why* the method was skipped) but never invent a
            # count or median for a method that produced no fair values.
            entry = {
                "status": "NOT_READY",
                "peer_count": int(quality.get(sample_key) or 0),
                "median_or_reference": None,
                "peer_codes": codes,
                "peer_codes_hash": _stable_hash(codes),
            }
        bundle[kind] = entry
    return bundle


class ValuationMethodRecorder:
    """Persists immutable daily method bundles; never recomputes them."""

    def __init__(self, store: PITReplayStore | None = None) -> None:
        self.store = store or PITReplayStore()

    def close(self) -> None:
        self.store.close()

    def record(
        self,
        market: str,
        stock_code: str,
        *,
        research_as_of: str,
        zones: dict[str, Any],
        source_pool_id: str | None = None,
        snapshot_origin: str = "FORWARD_CAPTURED",
    ) -> dict[str, Any]:
        """Persist one company's method bundle for one research date.

        PIT guard: the projection handed in must itself be scoped to
        ``research_as_of``.  A zones object whose own ``as_of`` is newer than
        the requested research date would silently snapshot future evidence,
        so it is rejected outright.
        """
        normalized_as_of = str(research_as_of)[:10]
        zones_as_of = str(zones.get("as_of") or "")[:10]
        if zones_as_of and zones_as_of > normalized_as_of:
            raise ValueError(
                f"PIT violation: zones projection as_of={zones_as_of} is newer than research_as_of={normalized_as_of}"
            )
        reliability = valuation_reliability(zones)
        bundle = build_peer_method_bundle(zones)
        universe = {
            "pe_codes": bundle["PE"]["peer_codes"],
            "pb_codes": bundle["PB"]["peer_codes"],
        }
        inputs = {
            "current_price": zones.get("current_price"),
            "price_as_of": zones.get("price_as_of"),
            "fair_value_low": (zones.get("valuation") or {}).get("fair_value_low"),
            "fair_value_mid": (zones.get("valuation") or {}).get("fair_value_mid"),
            "fair_value_high": (zones.get("valuation") or {}).get("fair_value_high"),
            "historical_coverage": reliability.get("historical_coverage"),
        }
        return self.store.record_method_snapshot(
            market=market,
            stock_code=stock_code,
            research_as_of=normalized_as_of,
            valuation_formula_version=str(
                zones.get("formula_version") or PRICE_ZONE_FORMULA_VERSION
            ),
            reliability_formula_version=str(
                reliability.get("formula_version") or RELIABILITY_FORMULA_VERSION
            ),
            peer_method_bundle=bundle,
            reliability_status=str(reliability.get("status") or "INSUFFICIENT"),
            reliability_reasons=[str(reason) for reason in reliability.get("reasons") or []],
            extreme_fair_value_flagged="EXTREME_FAIR_VALUE" in set(reliability.get("flags") or []),
            universe_hash=_stable_hash(universe),
            input_fingerprint=_stable_hash(inputs),
            source_hash=_stable_hash({"bundle": bundle, "reliability": {
                "status": reliability.get("status"),
                "counts": reliability.get("peer_sample_counts"),
            }}),
            source_pool_id=source_pool_id,
            snapshot_origin=snapshot_origin,
        )


_recorder: ValuationMethodRecorder | None = None


def get_valuation_method_recorder() -> ValuationMethodRecorder:
    global _recorder
    if _recorder is None:
        _recorder = ValuationMethodRecorder()
    return _recorder
