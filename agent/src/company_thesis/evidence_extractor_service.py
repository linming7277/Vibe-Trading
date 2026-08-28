"""Deterministic evidence extraction from persisted Company data snapshots only."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore, normalize_market, normalize_symbol

from .evidence_rules import (
    EXTRACTOR_VERSION,
    FINANCIAL_CHANGE_RULES,
    RESEARCH_CHANGE_RULES,
    VALUATION_PERCENTILE_CHANGE,
    ChangeRule,
)
from .evidence_service import CompanyThesisEvidenceService


def _loads(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


class CompanyThesisEvidenceExtractorService:
    """Creates traceable SYSTEM evidence without changing a Thesis or a Review.

    The source reader deliberately has no network or agent dependency.  The only
    write paths are append-only evidence creation and source-revision deactivation.
    """

    def __init__(self, *, evidence_service: CompanyThesisEvidenceService | None = None,
                 db_path: Path | None = None,
                 pool_loader: Callable[[], dict[str, Any]] | None = None) -> None:
        self.evidence_service = evidence_service or CompanyThesisEvidenceService(db_path=db_path)
        self._owns_evidence_service = evidence_service is None
        self.db_path = Path(db_path or self.evidence_service.repository.db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path, seed=False)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._pool_loader = pool_loader

    def close(self) -> None:
        with self._lock:
            self._conn.close()
        if self._owns_evidence_service:
            self.evidence_service.close()

    @staticmethod
    def _result(market: str, stock_code: str, *, thesis_id: str | None = None,
                status: str = "OK") -> dict[str, Any]:
        return {
            "market": market, "stock_code": stock_code, "thesis_id": thesis_id,
            "status": status, "created": 0, "unchanged": 0, "deactivated": 0,
            "skipped": 0, "errors": [], "evidence": [],
        }

    def extract_for_company(self, market: str, stock_code: str) -> dict[str, Any]:
        market = normalize_market(market)
        stock_code = normalize_symbol(market, stock_code)
        thesis = self.evidence_service.thesis_repository.get_current_thesis(market, stock_code)
        if thesis is None:
            return self._result(market, stock_code, status="THESIS_NOT_CREATED")
        result = self._result(market, stock_code, thesis_id=thesis["thesis_id"])
        for method in (self.extract_financial_evidence, self.extract_research_snapshot_evidence,
                       self.extract_valuation_evidence):
            try:
                self._merge(result, method(thesis))
            except Exception as exc:  # one malformed source must not stop other sources
                result["errors"].append(f"{method.__name__}: {type(exc).__name__}: {exc}")
        return result

    def extract_financial_evidence(self, thesis: dict[str, Any]) -> dict[str, Any]:
        result = self._result(thesis["market"], thesis["stock_code"], thesis_id=thesis["thesis_id"])
        row = self._one(
            """SELECT * FROM company_financial_analysis_snapshots WHERE stock_code=?
               ORDER BY as_of DESC,created_at DESC LIMIT 1""", (thesis["stock_code"],),
        )
        if row is None:
            result["skipped"] += 1
            return result
        feature = _loads(row["feature_json"])
        changes = feature.get("latest_changes")
        if not isinstance(changes, list):
            result["skipped"] += 1
            return result
        source_id, source_hash = str(row["id"]), str(row["source_hash"])
        period = str(row["as_of"])
        for change in changes:
            if not isinstance(change, dict):
                continue
            rule = next((item for item in FINANCIAL_CHANGE_RULES if item.metric_name == str(change.get("metric") or "")), None)
            percent = _number(change.get("change_percent"))
            if rule is None or percent is None:
                continue
            effect = self._effect(rule, percent)
            if effect is None:
                continue
            report_date = str(change.get("report_date") or period)
            claim = f"{report_date} {rule.label}较上一期变动 {_display(percent)}%。"
            summary = self._summary(rule.label, effect)
            self._upsert_auto_evidence(result, thesis=thesis, rule=rule, effect=effect,
                                       claim=claim, summary=summary, source_type="FINANCIAL_SNAPSHOT",
                                       source_id=source_id, source_hash=source_hash, data_as_of=period,
                                       metric_name=rule.metric_name, period=report_date,
                                       comparison_period="上一期", evidence_type=rule.evidence_type)
        return result

    def extract_research_snapshot_evidence(self, thesis: dict[str, Any]) -> dict[str, Any]:
        result = self._result(thesis["market"], thesis["stock_code"], thesis_id=thesis["thesis_id"])
        row = self._one(
            """SELECT * FROM l3_company_research_snapshots WHERE stock_code=?
               ORDER BY data_as_of DESC,created_at DESC LIMIT 1""", (thesis["stock_code"],),
        )
        if row is None:
            result["skipped"] += 1
            return result
        payload = _loads(row["payload_json"])
        latest, previous = payload.get("financial_latest"), payload.get("financial_previous")
        if not isinstance(latest, dict) or not isinstance(previous, dict):
            result["skipped"] += 1
            return result
        source_id, source_hash = str(row["source_snapshot_id"]), str(row["source_hash"])
        period = str(row["data_as_of"])
        for rule in RESEARCH_CHANGE_RULES:
            current, prior = _number(latest.get(rule.metric_name)), _number(previous.get(rule.metric_name))
            if current is None or prior is None:
                continue
            delta = current - prior
            effect = self._effect(rule, delta)
            if effect is None:
                continue
            claim = f"{period} {rule.label}为 {_display(current)}%，上一快照为 {_display(prior)}%，变化 {_display(delta)} 个百分点。"
            self._upsert_auto_evidence(result, thesis=thesis, rule=rule, effect=effect,
                                       claim=claim, summary=self._summary(rule.label, effect),
                                       source_type="COMPANY_RESEARCH_SNAPSHOT", source_id=source_id,
                                       source_hash=source_hash, data_as_of=period,
                                       metric_name=rule.metric_name, period=period,
                                       comparison_period="上一研究快照", evidence_type=rule.evidence_type)
        return result

    def extract_valuation_evidence(self, thesis: dict[str, Any]) -> dict[str, Any]:
        result = self._result(thesis["market"], thesis["stock_code"], thesis_id=thesis["thesis_id"])
        rows = self._many(
            """SELECT * FROM l3_company_valuation_snapshots WHERE stock_code=?
               ORDER BY data_as_of DESC,created_at DESC LIMIT 2""", (thesis["stock_code"],),
        )
        if len(rows) < 2:
            result["skipped"] += 1
            return result
        current, previous = rows[0], rows[1]
        current_values, prior_values = _loads(current["valuation_json"]), _loads(previous["valuation_json"])
        source_id, source_hash = str(current["source_snapshot_id"]), str(current["source_hash"])
        period = str(current["data_as_of"])
        for metric, label in (("pe_percentile", "PE 历史分位"), ("pb_percentile", "PB 历史分位")):
            value, old_value = _number(current_values.get(metric)), _number(prior_values.get(metric))
            if value is None or old_value is None:
                continue
            delta = value - old_value
            threshold = 0.10 if max(abs(value), abs(old_value)) <= 1.0 else VALUATION_PERCENTILE_CHANGE
            if abs(delta) < threshold:
                continue
            scale = "" if threshold < 1 else "个百分点"
            claim = f"{period} {label}为 {_display(value)}，上一估值快照为 {_display(old_value)}，变化 {_display(delta)}{scale}。"
            rule = ChangeRule(f"valuation.{metric}.position_change", metric, "VALUATION", threshold, -threshold, label)
            self._upsert_auto_evidence(result, thesis=thesis, rule=rule, effect="NEUTRAL",
                                       claim=claim, summary=f"{label}位置发生可核验变化，需结合后续研究复核，不构成交易结论。",
                                       # Existing V1 SQLite check constraints predate a dedicated
                                       # valuation source enum.  Keep the persisted source type
                                       # valid and record the precise snapshot class in metadata.
                                       source_type="SYSTEM", source_id=source_id,
                                       source_hash=source_hash, data_as_of=period, metric_name=metric,
                                       period=period, comparison_period=str(previous["data_as_of"]),
                                       evidence_type="VALUATION")
        return result

    def extract_current_pool(self) -> dict[str, Any]:
        pool = self._pool_loader() if self._pool_loader else self._load_current_pool()
        states = pool.get("research_states") or pool.get("members") or []
        allowed = {"ACTIVE", "NEW", "REENTERED"}
        seen: set[str] = set()
        response = {"status": "OK", "pool_id": pool.get("id") or pool.get("pool_id"), "processed": 0,
                    "created": 0, "unchanged": 0, "deactivated": 0, "skipped": 0, "errors": [], "items": []}
        for item in states:
            lifecycle = str((item or {}).get("lifecycle_status") or "").upper()
            code = str((item or {}).get("stock_code") or "").strip()
            if lifecycle not in allowed:
                response["skipped"] += 1
                continue
            if not code or code in seen:
                continue
            seen.add(code)
            result = self.extract_for_company("CN", code)
            response["processed"] += 1
            response["items"].append(result)
            for key in ("created", "unchanged", "deactivated", "skipped"):
                response[key] += int(result[key])
            response["errors"].extend(result["errors"])
        return response

    def _upsert_auto_evidence(self, result: dict[str, Any], *, thesis: dict[str, Any], rule: ChangeRule,
                              effect: str, claim: str, summary: str, source_type: str, source_id: str,
                              source_hash: str, data_as_of: str, metric_name: str, period: str,
                              comparison_period: str, evidence_type: str) -> None:
        # The logical identity intentionally excludes the physical snapshot ID.
        # A corrected snapshot for the same company/rule/period must supersede the
        # prior SYSTEM evidence instead of silently leaving both active.
        identity = {"thesis_id": thesis["thesis_id"], "rule_id": rule.rule_id,
                    "metric_name": metric_name, "period": period}
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        existing = self.evidence_service.repository.find_active_evidence_by_fingerprint(thesis["thesis_id"], fingerprint)
        active = next((item for item in existing if item.get("created_by") == "SYSTEM" and
                       item.get("metadata", {}).get("extractor_version") == EXTRACTOR_VERSION), None)
        if active and active.get("metadata", {}).get("source_hash") == source_hash:
            result["unchanged"] += 1
            return
        if active:
            self.evidence_service.deactivate_evidence(active["evidence_id"], "SOURCE_REVISED", deactivated_by="SYSTEM")
            result["deactivated"] += 1
        metadata = {
            "extractor_version": EXTRACTOR_VERSION, "rule_id": rule.rule_id,
            "source_snapshot_id": source_id, "source_hash": source_hash,
            "metric_name": metric_name, "period": period, "comparison_period": comparison_period,
            "evidence_fingerprint": fingerprint,
        }
        if source_type == "SYSTEM" and evidence_type == "VALUATION":
            metadata["source_snapshot_type"] = "VALUATION_SNAPSHOT"
        try:
            evidence = self.evidence_service.create_evidence(
                thesis_id=thesis["thesis_id"], evidence_type=evidence_type, effect=effect,
                claim=claim, summary=summary, source_type=source_type, source_id=source_id,
                source_ref=source_hash, source_title=f"{source_type}:{metric_name}",
                source_date=data_as_of, data_as_of=data_as_of, confidence="HIGH", created_by="SYSTEM",
                metadata=metadata, evidence_fingerprint=fingerprint,
            )
        except sqlite3.IntegrityError:
            # Concurrent extraction of the exact same source/rule has already won.
            concurrent = self.evidence_service.repository.find_active_evidence_by_fingerprint(
                thesis["thesis_id"], fingerprint,
            )
            if any(item.get("created_by") == "SYSTEM" for item in concurrent):
                result["unchanged"] += 1
                return
            raise
        result["created"] += 1
        result["evidence"].append(evidence)

    @staticmethod
    def _effect(rule: ChangeRule, change: float) -> str | None:
        normalized_change = change * rule.improvement_direction
        if normalized_change >= rule.positive_threshold:
            return "SUPPORT"
        if normalized_change <= rule.negative_threshold:
            return "CHALLENGE"
        return None

    @staticmethod
    def _summary(label: str, effect: str) -> str:
        if effect == "SUPPORT":
            return f"{label}出现显著改善，对当前 Thesis 构成支持。"
        return f"{label}出现显著走弱，对当前 Thesis 构成挑战。"

    @staticmethod
    def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key in ("created", "unchanged", "deactivated", "skipped"):
            target[key] += int(source[key])
        target["errors"].extend(source["errors"])
        target["evidence"].extend(source["evidence"])

    def _one(self, sql: str, values: tuple[Any, ...]) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, values).fetchone()

    def _many(self, sql: str, values: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, values).fetchall()

    def _load_current_pool(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM l3_leader_pool_runs WHERE is_current=1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return {"research_states": []}
            rows = self._conn.execute(
                "SELECT stock_code,lifecycle_status FROM l3_company_research_states WHERE pool_id=?",
                (row["id"],),
            ).fetchall()
        return {"pool_id": row["id"], "research_states": [dict(item) for item in rows]}


_service: CompanyThesisEvidenceExtractorService | None = None


def get_company_thesis_evidence_extractor_service() -> CompanyThesisEvidenceExtractorService:
    global _service
    if _service is None:
        _service = CompanyThesisEvidenceExtractorService()
    return _service
