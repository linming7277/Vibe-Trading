"""Validation and versioning rules for Company Thesis V1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.research_workspace.store import normalize_market, normalize_symbol

from .store import CompanyThesisRepository


THESIS_STATUSES = {"FORMING", "STRENGTHENING", "UNCHANGED", "WEAKENING", "FALSIFIED"}
THESIS_CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
STEP1_ACTORS = {"HUMAN", "SYSTEM", "AGENT"}
AUTHORITY_STATUSES = {"AI_PROVISIONAL", "HUMAN_CONFIRMED", "LEGACY_UNVERIFIED", "HUMAN_REJECTED"}


class CompanyThesisService:
    def __init__(self, *, repository: CompanyThesisRepository | None = None, db_path: Path | None = None) -> None:
        self.repository = repository or CompanyThesisRepository(db_path)

    def close(self) -> None:
        self.repository.close()

    def get_current_thesis(self, market: str, stock_code: str) -> dict[str, Any] | None:
        market, stock_code = self._company_key(market, stock_code)
        return self.repository.get_current_thesis(market, stock_code)

    def list_thesis_versions(self, market: str, stock_code: str) -> list[dict[str, Any]]:
        market, stock_code = self._company_key(market, stock_code)
        return self.repository.list_thesis_versions(market, stock_code)

    def get_thesis_by_id(self, thesis_id: str) -> dict[str, Any] | None:
        return self.repository.get_thesis_by_id(thesis_id)

    def create_initial_thesis(self, *, market: str, stock_code: str, title: str,
                              core_thesis: str, status: str, confidence: str,
                              invalid_conditions: list[dict[str, Any]] | None = None,
                              created_by: str = "HUMAN", source_data_as_of: str | None = None,
                              authority_status: str | None = None, source_draft_id: str | None = None,
                              supporting_conditions: list[dict[str, Any]] | None = None,
                              key_metrics_to_monitor: list[Any] | None = None) -> dict[str, Any]:
        payload = self._payload(
            market=market, stock_code=stock_code, title=title, core_thesis=core_thesis,
            status=status, confidence=confidence, invalid_conditions=invalid_conditions,
            created_by=created_by, source_data_as_of=source_data_as_of,
            supporting_conditions=supporting_conditions, key_metrics_to_monitor=key_metrics_to_monitor,
        )
        payload["authority_status"] = self._authority(authority_status, payload["created_by"])
        payload["source_draft_id"] = str(source_draft_id or "").strip() or None
        return self.repository.create_initial_thesis(payload)

    def create_new_version(self, *, market: str, stock_code: str, title: str,
                           core_thesis: str, status: str, confidence: str,
                           invalid_conditions: list[dict[str, Any]] | None = None,
                           change_reason: str, updated_by: str = "HUMAN",
                           source_data_as_of: str | None = None,
                           evidence_ids: list[str] | None = None,
                           trigger_ref: str | None = None,
                           history_metadata: dict[str, Any] | None = None,
                           authority_status: str | None = None, source_draft_id: str | None = None,
                           supporting_conditions: list[dict[str, Any]] | None = None,
                           key_metrics_to_monitor: list[Any] | None = None) -> dict[str, Any]:
        payload = self._payload(
            market=market, stock_code=stock_code, title=title, core_thesis=core_thesis,
            status=status, confidence=confidence, invalid_conditions=invalid_conditions,
            created_by=updated_by, source_data_as_of=source_data_as_of,
            supporting_conditions=supporting_conditions, key_metrics_to_monitor=key_metrics_to_monitor,
        )
        payload["authority_status"] = self._authority(authority_status, payload["created_by"])
        payload["source_draft_id"] = str(source_draft_id or "").strip() or None
        reason = str(change_reason or "").strip()
        if not reason:
            raise ValueError("change_reason is required when creating a new thesis version")
        if evidence_ids is not None and not isinstance(evidence_ids, list):
            raise ValueError("evidence_ids must be a JSON array")
        if not isinstance(history_metadata or {}, dict):
            raise ValueError("history_metadata must be an object")
        payload["change_reason"] = reason
        payload["evidence_ids"] = [str(item or "").strip() for item in (evidence_ids or [])]
        payload["trigger_ref"] = str(trigger_ref).strip() if trigger_ref else None
        payload["history_metadata"] = history_metadata or {}
        return self.repository.create_new_version(payload)

    @staticmethod
    def _company_key(market: str, stock_code: str) -> tuple[str, str]:
        normalized_market = normalize_market(market)
        return normalized_market, normalize_symbol(normalized_market, stock_code)

    def _payload(self, *, market: str, stock_code: str, title: str, core_thesis: str,
                 status: str, confidence: str, invalid_conditions: list[dict[str, Any]] | None,
                 created_by: str, source_data_as_of: str | None,
                 supporting_conditions: list[dict[str, Any]] | None = None,
                 key_metrics_to_monitor: list[Any] | None = None) -> dict[str, Any]:
        market, stock_code = self._company_key(market, stock_code)
        title, core_thesis = str(title or "").strip(), str(core_thesis or "").strip()
        if not title:
            raise ValueError("title is required")
        if not core_thesis:
            raise ValueError("core_thesis is required")
        normalized_status, normalized_confidence = str(status or "").upper(), str(confidence or "").upper()
        if normalized_status not in THESIS_STATUSES:
            raise ValueError(f"invalid thesis status: {status}")
        if normalized_confidence not in THESIS_CONFIDENCES:
            raise ValueError(f"invalid thesis confidence: {confidence}")
        actor = str(created_by or "").upper()
        if actor not in STEP1_ACTORS:
            raise ValueError("created_by must be HUMAN or SYSTEM in Company Thesis V1 Step 1")
        return {
            "market": market, "stock_code": stock_code, "title": title,
            "core_thesis": core_thesis, "status": normalized_status,
            "confidence": normalized_confidence,
            "invalid_conditions": self._invalid_conditions(invalid_conditions),
            "supporting_conditions": None if supporting_conditions is None else self._supporting_conditions(supporting_conditions),
            "key_metrics_to_monitor": None if key_metrics_to_monitor is None else self._metrics(key_metrics_to_monitor),
            "created_by": actor, "updated_by": actor,
            "source_data_as_of": str(source_data_as_of).strip() if source_data_as_of else None,
        }

    @staticmethod
    def _authority(value: str | None, actor: str) -> str:
        authority = str(value or ("HUMAN_CONFIRMED" if actor == "HUMAN" else "AI_PROVISIONAL" if actor == "AGENT" else "LEGACY_UNVERIFIED")).upper()
        if authority not in AUTHORITY_STATUSES:
            raise ValueError(f"invalid authority_status: {value}")
        return authority

    @staticmethod
    def _invalid_conditions(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("invalid_conditions must be a JSON array")
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("each invalid condition must be an object")
            condition = str(item.get("condition") or "").strip()
            if not condition:
                raise ValueError("invalid condition requires condition")
            state = str(item.get("status") or "ACTIVE").strip().upper()
            if not state:
                raise ValueError("invalid condition status is required")
            normalized.append({"condition": condition, "status": state})
        return normalized

    @staticmethod
    def _supporting_conditions(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("supporting_conditions must be a JSON array")
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append({"condition": text, "status": "ACTIVE", "role": "SUPPORT"})
                continue
            if not isinstance(item, dict):
                raise ValueError("each supporting condition must be an object")
            condition = str(item.get("condition") or item.get("text") or "").strip()
            if not condition:
                continue
            state = str(item.get("status") or "ACTIVE").strip().upper() or "ACTIVE"
            row: dict[str, Any] = {"condition": condition, "status": state, "role": str(item.get("role") or "SUPPORT")}
            if item.get("source_keys"):
                row["source_keys"] = list(item.get("source_keys") or [])
            normalized.append(row)
        return normalized

    @staticmethod
    def _metrics(value: list[Any] | None) -> list[Any]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("key_metrics_to_monitor must be a JSON array")
        return list(value)


_service: CompanyThesisService | None = None


def get_company_thesis_service() -> CompanyThesisService:
    global _service
    if _service is None:
        _service = CompanyThesisService()
    return _service
