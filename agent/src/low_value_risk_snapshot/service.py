"""Generate small, auditable low-value-pool risk snapshots from Risk Research."""

from __future__ import annotations

from typing import Any

from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.risk_research import RiskResearchService, get_risk_research_service

from .store import LowValueRiskSnapshotRepository


class LowValuePoolRiskSnapshotService:
    def __init__(self, *, pool_repository: LowValueLeaderPoolRepository | None = None,
                 repository: LowValueRiskSnapshotRepository | None = None,
                 risk_research_service: RiskResearchService | Any | None = None) -> None:
        self.pool_repository = pool_repository or LowValueLeaderPoolRepository()
        db_path = self.pool_repository.db_path
        self.repository = repository or LowValueRiskSnapshotRepository(db_path)
        self.risk_research_service = risk_research_service or get_risk_research_service()
        self._owns_pool = pool_repository is None
        self._owns_repository = repository is None

    def close(self) -> None:
        if self._owns_pool:
            self.pool_repository.close()
        if self._owns_repository:
            self.repository.close()

    @staticmethod
    def _summary(result: dict[str, Any], *, high: int, medium: int) -> str:
        if str(result.get("overall_risk")) == "UNKNOWN":
            return "关键研究资料不足，暂无法完整判断风险。"
        if high:
            return f"发现 {high} 项明显风险、{medium} 项需要继续观察的问题。" if medium else f"发现 {high} 项明显风险，需要重点复核。"
        if medium:
            return f"发现 {medium} 项需要继续观察的问题。"
        return "当前未发现明显基本面风险。"

    @classmethod
    def _project(cls, result: dict[str, Any], *, market: str, stock_code: str, source_as_of: str) -> dict[str, Any]:
        risks = [dict(item) for item in result.get("risks") or [] if str(item.get("severity")) in {"HIGH", "MEDIUM"}]
        high = sum(str(item.get("severity")) == "HIGH" for item in risks)
        medium = sum(str(item.get("severity")) == "MEDIUM" for item in risks)
        quality = dict(result.get("data_quality") or {})
        return {
            "market": market, "stock_code": stock_code, "source_as_of": source_as_of,
            "overall_risk": str(result.get("overall_risk") or "UNKNOWN"),
            "value_trap_risk": str(result.get("value_trap_risk") or "UNKNOWN"),
            "material_risk_count": len(risks), "high_risk_count": high, "medium_risk_count": medium,
            "top_risk_types": [str(item.get("risk_type")) for item in risks[:3]],
            "risk_summary": cls._summary(result, high=high, medium=medium),
            "financial_status": str(quality.get("financial") or "UNKNOWN"),
            "business_status": str(quality.get("business") or "UNKNOWN"),
            "thesis_status": str(quality.get("thesis") or "UNKNOWN"),
            "formula_version": str(result.get("formula_version") or "risk-research-v1.0.0"),
        }

    @staticmethod
    def _error_projection(*, market: str, stock_code: str, source_as_of: str, error: Exception) -> dict[str, Any]:
        return {"market": market, "stock_code": stock_code, "source_as_of": source_as_of,
                "overall_risk": "UNKNOWN", "value_trap_risk": "UNKNOWN", "material_risk_count": 0,
                "high_risk_count": 0, "medium_risk_count": 0, "top_risk_types": [],
                "risk_summary": "风险资料读取失败，暂无法完整判断。", "financial_status": "UNKNOWN",
                "business_status": "UNKNOWN", "thesis_status": "UNKNOWN", "formula_version": "risk-research-v1.0.0",
                "error": f"{type(error).__name__}: {error}"[:500]}

    @staticmethod
    def _record_freshness_manifest(market: str, stock_code: str, as_of: str) -> None:
        """Record the risk-snapshot input fingerprint for freshness checks (plan §20.3).

        Best effort: manifest recording must never fail the EOD projection.
        """
        try:
            from src.research_freshness import fingerprints
            from src.research_freshness.manifests import ResearchManifestStore

            current = fingerprints.fingerprint_risk(stock_code, as_of=as_of)
            if not current or not current.get("input_fingerprint"):
                return
            ResearchManifestStore().record(
                research_type="risk_snapshot", market=market, stock_code=stock_code,
                research_as_of=as_of, input_fingerprint=str(current["input_fingerprint"]),
                formula_version="risk-snapshot-projection-v1",
            )
        except Exception:  # noqa: BLE001
            return

    def refresh_active_low_value_risk_snapshots(self, *, source_as_of: str | None = None, force: bool = False) -> dict[str, Any]:
        """Refresh only current ACTIVE low-value leaders, one company at a time.

        Each failure is persisted as an UNKNOWN snapshot and does not affect pool
        membership, leader ranking, or other companies.
        """
        active = self.pool_repository.active("CN")
        # An ACTIVE record may have a source date earlier than the scheduler's
        # run date when upstream data was deliberately held at its last complete
        # snapshot.  It is still a current pool member, so project it using its
        # own source_as_of rather than silently omitting it.
        processed = created = skipped = failed = 0
        errors: list[dict[str, str]] = []
        for item in active:
            market, code, as_of = str(item.get("market") or "CN"), str(item["stock_code"]).upper(), str(item["source_as_of"])
            existing = self.repository.get(market, code, as_of)
            # A saved error is deliberately retryable on the next resumed EOD.
            # A successful same-as-of projection remains idempotent.
            if not force and existing and not existing.get("error"):
                skipped += 1
                continue
            processed += 1
            try:
                result = self.risk_research_service.get_risk_research(market, code, as_of=as_of)
                self.repository.save(self._project(result, market=market, stock_code=code, source_as_of=as_of))
                self._record_freshness_manifest(market, code, as_of)
                created += 1
            except Exception as exc:
                self.repository.save(self._error_projection(market=market, stock_code=code, source_as_of=as_of, error=exc))
                failed += 1
                errors.append({"stock_code": code, "error": f"{type(exc).__name__}: {exc}"[:500]})
        return {"source_as_of": source_as_of, "active": len(active), "processed": processed, "created": created,
                "skipped": skipped, "failed": failed, "errors": errors, "status": "PARTIAL" if failed else "COMPLETED"}

    def refresh_company_snapshot(self, *, market: str, stock_code: str, source_as_of: str) -> dict[str, Any]:
        """Re-project one already-active company after its research preparation.

        This deliberately recalculates only the small list projection.  It
        does not alter RiskResearch rules, the low-value pool, or any source
        research; it simply prevents an EOD pre-preparation snapshot from
        remaining stale after Business/Disclosure/Thesis material is ready.
        """
        normalized_market, normalized_code = market.upper(), stock_code.upper()
        try:
            result = self.risk_research_service.get_risk_research(
                normalized_market, normalized_code, as_of=source_as_of,
            )
            snapshot = self.repository.save(self._project(
                result, market=normalized_market, stock_code=normalized_code, source_as_of=source_as_of,
            ))
            self._record_freshness_manifest(normalized_market, normalized_code, source_as_of)
            return {"status": "READY", "snapshot": snapshot}
        except Exception as exc:
            snapshot = self.repository.save(self._error_projection(
                market=normalized_market, stock_code=normalized_code, source_as_of=source_as_of, error=exc,
            ))
            return {"status": "FAILED", "snapshot": snapshot, "error": f"{type(exc).__name__}: {exc}"[:500]}

    def coverage_for_active_pool(self, *, source_as_of: str) -> dict[str, Any]:
        """Return same-date coverage without triggering Risk Research."""
        active = self.pool_repository.active("CN")
        mismatched = [item["stock_code"] for item in active if str(item.get("source_as_of") or "") != source_as_of]
        missing: list[str] = []
        errored: list[str] = []
        for item in active:
            if str(item.get("source_as_of") or "") != source_as_of:
                continue
            row = self.repository.get(str(item.get("market") or "CN"), str(item["stock_code"]), source_as_of)
            if not row:
                missing.append(str(item["stock_code"]))
            elif row.get("error"):
                errored.append(str(item["stock_code"]))
        return {
            "source_as_of": source_as_of,
            "active": len(active),
            "matched_pool_rows": len(active) - len(mismatched),
            "missing": missing,
            "errored": errored,
            "complete": not mismatched and not missing and not errored,
        }


_service: LowValuePoolRiskSnapshotService | None = None


def get_low_value_pool_risk_snapshot_service() -> LowValuePoolRiskSnapshotService:
    global _service
    if _service is None:
        _service = LowValuePoolRiskSnapshotService()
    return _service
