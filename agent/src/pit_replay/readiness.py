"""Point-in-time replay readiness checks (read-only, no LLM).

Answers one narrow question per research date: does the persisted evidence
exist to replay that day honestly — a qualified market close, an immutable
Low Value pool snapshot, and a valuation-method bundle for every pool
company?  It never computes research outputs and never mutates anything.
"""

from __future__ import annotations

from typing import Any

from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.tdx_data.store import TdxDataStore

from .store import PITReplayStore


class PITReplayReadinessService:
    """Quick per-date evidence checks over already-persisted rows."""

    def __init__(
        self,
        *,
        pool_repository: LowValueLeaderPoolRepository | None = None,
        tdx_store: TdxDataStore | None = None,
        replay_store: PITReplayStore | None = None,
    ) -> None:
        self.pool_repository = pool_repository or LowValueLeaderPoolRepository()
        self.tdx_store = tdx_store or TdxDataStore()
        db_path = getattr(self.pool_repository, "db_path", None)
        self.replay_store = replay_store or PITReplayStore(db_path)
        self._owns = {
            "pool": pool_repository is None,
            "tdx": tdx_store is None,
            "replay": replay_store is None,
        }

    def close(self) -> None:
        if self._owns["replay"]:
            self.replay_store.close()
        if self._owns["pool"]:
            self.pool_repository.close()
        if self._owns["tdx"]:
            self.tdx_store.close()

    # ---------------------------------------------------------------- checks

    def evaluate_readiness(self, research_as_of: str, *, market: str = "CN") -> dict[str, Any]:
        day = str(research_as_of)[:10]
        checks: list[dict[str, Any]] = []

        close = self.tdx_store.market_close_qualification(day, market)
        qualification = str((close or {}).get("qualification") or "UNKNOWN")
        checks.append({
            "check": "market_close",
            "status": "PASS" if qualification == "QUALIFIED" else "WARN" if qualification == "PARTIAL" else "FAIL",
            "evidence": {"qualification": qualification, "run_status": (close or {}).get("run_status"),
                         "quotes_status": (close or {}).get("quotes_status"),
                         "quotes_item_count": (close or {}).get("quotes_item_count")},
        })

        snapshots = self.pool_repository.snapshots_for_as_of(day, market)
        low_value_count = len(snapshots)
        refresh = self.pool_repository.refresh_history(market)
        marker = next((row for row in refresh if str(row.get("source_as_of")) == day), None)
        snapshot_ok = bool(marker) and str((marker or {}).get("status") or "") in {"COMPLETED", "PARTIAL"}
        checks.append({
            "check": "low_value_snapshot",
            "status": "PASS" if snapshot_ok else "FAIL",
            "evidence": {"snapshot_rows": low_value_count,
                         "refresh_status": (marker or {}).get("status") if marker else None},
        })

        bundles = self.replay_store.method_snapshots_for_as_of(day, market)
        bundled_codes = {str(row.get("stock_code") or "") for row in bundles}
        expected_codes = {str(item.get("stock_code") or "") for item in snapshots}
        missing = sorted(expected_codes - bundled_codes)
        coverage = round(len(expected_codes & bundled_codes) / len(expected_codes), 4) if expected_codes else (1.0 if bundles else 0.0)
        if not expected_codes:
            bundle_status = "PASS"
        elif not (expected_codes & bundled_codes):
            # No company has provenance: the day cannot be replayed at all.
            bundle_status = "FAIL"
        elif missing:
            # Some companies carry provenance: replayable for those only.
            bundle_status = "WARN"
        else:
            bundle_status = "PASS"
        checks.append({
            "check": "valuation_method_bundle",
            "status": bundle_status,
            "evidence": {"expected": len(expected_codes), "bundled": len(expected_codes & bundled_codes),
                         "coverage": coverage, "missing_codes": missing[:20]},
        })

        provenance_rows = [
            row for row in bundles
            if str(row.get("stock_code") or "") in expected_codes
            and bool(row.get("reliability_status")) and bool(row.get("peer_method_bundle"))
        ]
        if not expected_codes:
            provenance_status = "PASS"
        elif not provenance_rows:
            provenance_status = "FAIL"
        elif len(provenance_rows) < len(expected_codes):
            provenance_status = "WARN"
        else:
            provenance_status = "PASS"
        checks.append({
            "check": "reliability_provenance",
            "status": provenance_status,
            "evidence": {"rows_with_status_and_reasons": len(provenance_rows)},
        })

        failed = [item["check"] for item in checks if item["status"] == "FAIL"]
        warned = [item["check"] for item in checks if item["status"] == "WARN"]
        status = "READY" if not failed and not warned else "PARTIAL" if not failed else "NOT_READY"
        return {
            "research_as_of": day,
            "status": status,
            "complete_companies": len(expected_codes),
            "low_value_count": low_value_count,
            "bundle_coverage": coverage,
            "checks": checks,
        }

    def list_ready_dates(self, *, market: str = "CN", limit: int = 20) -> list[dict[str, Any]]:
        """Evaluate the most recent candidate research dates, newest first."""
        dates: set[str] = {str(row["market_date"])[:10] for row in self.tdx_store.market_close_qualifications(market, limit=90)}
        for row in self.pool_repository.refresh_history(market):
            dates.add(str(row.get("source_as_of") or "")[:10])
        for day in self.replay_store.method_snapshot_as_of_counts(market):
            dates.add(str(day)[:10])
        ordered = sorted({day for day in dates if day}, reverse=True)[: max(1, min(int(limit), 100))]
        return [self.evaluate_readiness(day, market=market) for day in ordered]

    def trust_start_date(self, *, market: str = "CN") -> str | None:
        """Earliest research date whose evidence is fully forward-captured."""
        counts = self.replay_store.method_snapshot_as_of_counts(market)
        for day in sorted(counts):
            if counts[day] > 0:
                result = self.evaluate_readiness(day, market=market)
                if result["status"] == "READY":
                    return str(result["research_as_of"])
        return None
