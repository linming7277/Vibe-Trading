"""Read-only PIT replay helpers for the proposed Entry/Exit V2 rules.

This module deliberately lives under ``tests``.  It is not imported by API,
EOD, ValueStrategyState, Hermes, or any production service.  It only reads
immutable research/TDX snapshots through SQLite read-only connections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


class PITLeakError(RuntimeError):
    """Raised when a replay input is newer than its replay business date."""


def _day(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _payload(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def require_pit(name: str, data_as_of: Any, replay_as_of: str) -> str | None:
    """Validate business time, never database write time, for a replay input."""
    selected = _day(data_as_of)
    if selected and selected > replay_as_of:
        raise PITLeakError(f"{name}: data_as_of={selected} exceeds replay_as_of={replay_as_of}")
    return selected


def reliability_from_snapshot(metadata: dict[str, Any], replay_as_of: str) -> tuple[str | None, str | None]:
    """Return a persisted reliability state; never reconstruct it from today.

    Preference order:
    1. ``metadata.valuation_reliability`` — the full day-of verdict persisted
       by the pool snapshot since the PIT remediation.  Used verbatim.
    2. ``metadata.valuation_quality`` — the partial {method_count, min_peer_count}
       audit present in 09-01-era snapshots; insufficient for the V1 contract
       (no per-method counts, no extreme guard), so it stays a provenance gap.
    """
    persisted = metadata.get("valuation_reliability")
    if isinstance(persisted, dict) and persisted.get("status"):
        source_as_of = require_pit(
            "valuation_reliability", persisted.get("as_of") or replay_as_of, replay_as_of,
        )
        return str(persisted["status"]), source_as_of
    quality = metadata.get("valuation_quality")
    if not isinstance(quality, dict):
        return None, "VALUATION_RELIABILITY_PROVENANCE"
    source_as_of = require_pit("valuation_reliability", quality.get("as_of") or replay_as_of, replay_as_of)
    method_count = quality.get("method_count")
    min_peer_count = quality.get("min_peer_count")
    if method_count is None or min_peer_count is None:
        return None, "VALUATION_RELIABILITY_PROVENANCE"
    # Even with both counts present, the partial audit cannot reproduce the
    # extreme-fair-value guard or per-method sample counts, so it never
    # certifies RELIABLE/LIMITED on its own.
    return None, "VALUATION_RELIABILITY_PROVENANCE"


def evaluate_entry_e3(item: dict[str, Any]) -> dict[str, Any]:
    """Candidate E3 rules, deterministic and intentionally score-free."""
    reasons: list[str] = []
    cautions: list[str] = []
    if not bool(item.get("in_value_scope")):
        return {"status": "NOT_APPLICABLE", "confidence": "LOW", "reasons": ["OUTSIDE_VALUE_SCOPE"], "cautions": []}

    thesis = str(item.get("thesis_status") or "MISSING")
    authority = str(item.get("thesis_authority") or "MISSING")
    valuation = str(item.get("valuation_status") or "INSUFFICIENT_DATA")
    historical = str(item.get("historical_valuation_status") or "MISSING")
    coverage = str(item.get("historical_coverage") or "INSUFFICIENT")
    reliability = str(item.get("valuation_reliability") or "INSUFFICIENT")

    # The current validation specification explicitly resolves the older
    # design-note ambiguity: any FALSIFIED thesis blocks Entry; authority
    # remains visible as a caution for auditability.
    if authority == "HUMAN_REJECTED" or thesis == "FALSIFIED":
        reasons.append("THESIS_REJECTED" if authority == "HUMAN_REJECTED" else "THESIS_FALSIFIED")
        if authority != "HUMAN_CONFIRMED":
            cautions.append("THESIS_NOT_HUMAN_CONFIRMED")
        return {"status": "BLOCKED", "confidence": "LOW", "reasons": reasons, "cautions": cautions}

    if thesis == "WEAKENING":
        cautions.append("THESIS_WEAKENING")
    elif authority in {"AI_PROVISIONAL", "LEGACY_UNVERIFIED"}:
        cautions.append("THESIS_NOT_HUMAN_CONFIRMED")
    elif thesis == "MISSING":
        cautions.append("THESIS_UNAVAILABLE")

    if reliability == "INSUFFICIENT":
        return {"status": "WAIT", "confidence": "LOW", "reasons": ["VALUATION_RELIABILITY_INSUFFICIENT"], "cautions": cautions}
    if valuation not in {"DEEPLY_UNDERVALUED", "UNDERVALUED"}:
        return {"status": "WAIT", "confidence": "MEDIUM" if reliability == "LIMITED" else "HIGH", "reasons": [f"VALUATION_{valuation}"], "cautions": cautions}

    if coverage == "INSUFFICIENT" or historical == "MISSING":
        return {"status": "WATCH", "confidence": "LOW", "reasons": ["HISTORICAL_VALUATION_INSUFFICIENT"], "cautions": cautions}
    if reliability == "WEAK":
        return {"status": "WATCH", "confidence": "LOW", "reasons": [f"VALUATION_{valuation}"], "cautions": cautions + ["VALUATION_RELIABILITY_WEAK"]}

    high = (
        valuation == "DEEPLY_UNDERVALUED"
        and historical in {"VERY_CHEAP", "CHEAP"}
        and coverage == "READY"
        and reliability in {"RELIABLE", "LIMITED"}
        # A provisional Thesis may explain a result but cannot improve it
        # relative to a missing Thesis.  Human confirmation is a data-quality
        # boundary, not a positive price/valuation score.
        and authority == "HUMAN_CONFIRMED"
        and thesis not in {"WEAKENING", "MISSING"}
    )
    attention = (
        (valuation == "DEEPLY_UNDERVALUED" and historical in {"VERY_CHEAP", "CHEAP", "NORMAL"})
        or (valuation == "UNDERVALUED" and historical in {"VERY_CHEAP", "CHEAP"})
    )
    if thesis == "WEAKENING":
        return {"status": "WATCH", "confidence": "MEDIUM", "reasons": [f"VALUATION_{valuation}"], "cautions": cautions}
    if high:
        reasons = ["VALUATION_DEEPLY_UNDERVALUED", f"HISTORICAL_VALUATION_{historical}"]
        return {"status": "HIGH_ATTENTION", "confidence": "HIGH" if reliability == "RELIABLE" else "MEDIUM", "reasons": reasons, "cautions": cautions}
    if attention:
        reasons = [f"VALUATION_{valuation}", f"HISTORICAL_VALUATION_{historical}"]
        if coverage == "PARTIAL":
            cautions.append("HISTORICAL_VALUATION_PARTIAL")
        return {"status": "ATTENTION", "confidence": "MEDIUM" if coverage == "PARTIAL" or reliability == "LIMITED" else "HIGH", "reasons": reasons, "cautions": cautions}
    return {"status": "WATCH", "confidence": "MEDIUM", "reasons": [f"VALUATION_{valuation}", f"HISTORICAL_VALUATION_{historical}"], "cautions": cautions}


def evaluate_exit_x3(item: dict[str, Any]) -> dict[str, Any]:
    """Candidate X3 rules: max(valuation review, thesis review), no score."""
    order = {"NORMAL": 0, "WATCH": 1, "REVIEW": 2, "CRITICAL_REVIEW": 3}
    reasons: list[str] = []
    cautions: list[str] = []
    valuation = str(item.get("valuation_status") or "INSUFFICIENT_DATA")
    reliability = str(item.get("valuation_reliability") or "INSUFFICIENT")
    thesis = str(item.get("thesis_status") or "MISSING")
    authority = str(item.get("thesis_authority") or "MISSING")

    valuation_review = "NORMAL"
    if reliability == "INSUFFICIENT":
        cautions.append("VALUATION_DATA_WEAK")
    elif reliability == "WEAK":
        cautions.append("VALUATION_DATA_WEAK")
        if valuation in {"FAIR", "OVERVALUED", "DEEPLY_OVERVALUED"}:
            valuation_review = "WATCH"
    elif valuation == "FAIR" and bool(item.get("was_in_value_scope")):
        valuation_review = "WATCH"
        reasons.append("VALUATION_MARGIN_NARROWED")
    elif valuation in {"OVERVALUED", "DEEPLY_OVERVALUED"}:
        valuation_review = "REVIEW"
        reasons.append("VALUATION_OVERVALUED")

    thesis_review = "NORMAL"
    if authority == "HUMAN_REJECTED":
        thesis_review = "CRITICAL_REVIEW"
        reasons.append("THESIS_REJECTED")
    elif thesis == "FALSIFIED":
        if authority == "HUMAN_CONFIRMED":
            thesis_review = "CRITICAL_REVIEW"
            reasons.append("THESIS_FALSIFIED")
        else:
            thesis_review = "REVIEW"
            reasons.append("THESIS_FALSIFIED")
            cautions.append("THESIS_NOT_HUMAN_CONFIRMED")
    elif thesis == "WEAKENING":
        thesis_review = "REVIEW"
        reasons.append("THESIS_WEAKENING")
        if authority in {"AI_PROVISIONAL", "LEGACY_UNVERIFIED"}:
            cautions.append("THESIS_NOT_HUMAN_CONFIRMED")

    status = max((valuation_review, thesis_review), key=lambda state: order[state])
    confidence = "LOW" if reliability in {"WEAK", "INSUFFICIENT"} else "MEDIUM" if reliability == "LIMITED" or thesis == "MISSING" else "HIGH"
    return {
        "status": status,
        "confidence": confidence,
        "valuation_review": valuation_review,
        "thesis_review": thesis_review,
        "reasons": list(dict.fromkeys(reasons)),
        "cautions": list(dict.fromkeys(cautions)),
    }


@dataclass(frozen=True)
class ReplayInput:
    stock_code: str
    replay_as_of: str
    classification: str
    missing: tuple[str, ...]
    values: dict[str, Any]


class PITReplayReader:
    """Strictly read-only reader of snapshot bundles for audit/test use only."""

    def __init__(self, research_db: Path, tdx_db: Path) -> None:
        self.research = sqlite3.connect(f"file:{Path(research_db).as_posix()}?mode=ro", uri=True)
        self.research.row_factory = sqlite3.Row
        self.tdx = sqlite3.connect(f"file:{Path(tdx_db).as_posix()}?mode=ro", uri=True)
        self.tdx.row_factory = sqlite3.Row

    def close(self) -> None:
        self.research.close()
        self.tdx.close()

    def qualified_dates(self) -> list[str]:
        rows = self.tdx.execute(
            """SELECT DISTINCT rr.market_date FROM refresh_runs rr
               JOIN dataset_snapshots ds ON ds.snapshot_id=rr.snapshot_id
               WHERE rr.profile='market_close' AND rr.market='CN' AND rr.status='completed'
                 AND ds.dataset='quotes' AND ds.status='ready' AND ds.item_count>=5000
               ORDER BY rr.market_date"""
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _pool_row(self, code: str, replay_as_of: str) -> dict[str, Any] | None:
        row = self.research.execute(
            """SELECT payload_json FROM company_low_value_leader_pool_snapshots
               WHERE market='CN' AND stock_code=? AND source_as_of=? AND pool_status='ACTIVE'""",
            (code, replay_as_of),
        ).fetchone()
        return _payload(row[0]) if row else None

    def input_for(self, code: str, replay_as_of: str) -> ReplayInput:
        pool = self._pool_row(code, replay_as_of)
        missing: list[str] = []
        values: dict[str, Any] = {"in_value_scope": bool(pool), "was_in_value_scope": bool(pool)}
        if not pool:
            return ReplayInput(code, replay_as_of, "PIT_INSUFFICIENT", ("LOW_VALUE_SNAPSHOT",), values)
        metadata = dict(pool.get("metadata") or {})
        require_pit("low_value", pool.get("source_as_of"), replay_as_of)
        values.update({
            "valuation_status": pool.get("valuation_status"),
            "historical_valuation_status": pool.get("historical_valuation_status") or "MISSING",
            "current_price": pool.get("current_price"),
            "fair_value_low": pool.get("fair_value_low"),
            "fair_value_high": pool.get("fair_value_high"),
            "support_context": pool.get("support_status") or "NO_SIGNAL",
        })
        if not values["valuation_status"] or values["current_price"] is None or values["fair_value_low"] is None or values["fair_value_high"] is None:
            missing.append("VALUE_PRICE_ZONE")

        leader = self.research.execute(
            """SELECT id,as_of FROM value_level3_leaders
               WHERE stock_code=? AND as_of=? AND leader_rank<=2 AND eligibility_status='eligible' LIMIT 1""",
            (code, replay_as_of),
        ).fetchone()
        if leader:
            values["leader_run_id"] = str(leader[0])
            require_pit("l3_leader", leader[1], replay_as_of)
        else:
            missing.append("L3_LEADER")

        financial = self.research.execute(
            """SELECT id,as_of,historical_cutoff,history_json FROM company_financial_analysis_snapshots
               WHERE stock_code=? AND as_of<=? ORDER BY as_of DESC,created_at DESC,rowid DESC LIMIT 1""",
            (code, replay_as_of),
        ).fetchone()
        if financial:
            values["financial_snapshot_id"] = str(financial[0])
            values["financial_as_of"] = require_pit("financial", financial[1], replay_as_of)
            values["financial_cutoff"] = require_pit("financial_cutoff", financial[2], replay_as_of)
            history = json.loads(financial[3] or "[]")
            announced = [str(item.get("announcement_date") or "")[:10] for item in history if isinstance(item, dict)]
            values["financial_announcement_date"] = max((item for item in announced if item and item <= replay_as_of), default=None)
        else:
            missing.append("FINANCIAL_PIT")

        risk = self.research.execute(
            """SELECT id,source_as_of FROM company_low_value_risk_snapshots
               WHERE market='CN' AND stock_code=? AND source_as_of=? LIMIT 1""",
            (code, replay_as_of),
        ).fetchone()
        if risk:
            values["risk_snapshot_id"] = str(risk[0])
            values["risk_as_of"] = require_pit("risk", risk[1], replay_as_of)
        else:
            missing.append("RISK_SNAPSHOT")

        quote = self.tdx.execute(
            """SELECT sr.payload_json FROM snapshot_records sr JOIN refresh_runs rr ON rr.snapshot_id=sr.snapshot_id
               JOIN dataset_snapshots ds ON ds.snapshot_id=rr.snapshot_id AND ds.dataset=sr.dataset
               WHERE rr.profile='market_close' AND rr.market='CN' AND rr.market_date=? AND rr.status='completed'
                 AND sr.dataset='quotes' AND sr.record_key=? AND ds.status='ready'
               ORDER BY rr.completed_at DESC,sr.updated_at DESC LIMIT 1""",
            (replay_as_of, code),
        ).fetchone()
        if quote:
            quote_payload = _payload(quote[0])
            values["last_price_date"] = require_pit("price", quote_payload.get("data_as_of"), replay_as_of)
            if values["last_price_date"] != replay_as_of:
                missing.append("PRICE_DATE")
        else:
            missing.append("PRICE_SNAPSHOT")

        historical = self.tdx.execute(
            """SELECT trade_date,financial_data_as_of FROM historical_valuation_series
               WHERE market='CN' AND stock_code=? AND trade_date<=?
               ORDER BY trade_date DESC LIMIT 1""",
            (code, replay_as_of),
        ).fetchone()
        coverage = ((metadata.get("data_quality") or {}).get("historical_valuation") or {}).get("coverage") or {}
        values["historical_coverage"] = str(coverage.get("coverage_status") or "INSUFFICIENT")
        if historical and values["historical_coverage"] in {"READY", "PARTIAL"}:
            values["historical_valuation_as_of"] = require_pit("historical_valuation_price", historical[0], replay_as_of)
            values["historical_financial_data_as_of"] = require_pit("historical_valuation_financial", historical[1], replay_as_of)
        else:
            missing.append("HISTORICAL_VALUATION")

        daily = ((metadata.get("data_quality") or {}).get("daily_history") or {})
        values["support_last_bar_date"] = require_pit("support", daily.get("last_date"), replay_as_of)
        if daily.get("status") not in {"READY", "PARTIAL"}:
            missing.append("SUPPORT")

        # Thesis PIT gate (dual): the conclusion must have existed on the replay
        # date (valid_from, falling back to the row's own creation day) AND its
        # evidence must have been visible (source_data_as_of).  An older
        # evidence date alone must not leak a conclusion the system had not
        # yet reached.
        thesis = self.research.execute(
            """SELECT thesis_id,status,authority_status,source_data_as_of,version,created_at,valid_from
               FROM company_theses
               WHERE market='CN' AND stock_code=?
                 AND COALESCE(valid_from, substr(created_at,1,10)) <= ?
                 AND COALESCE(source_data_as_of, substr(created_at,1,10)) <= ?
               ORDER BY COALESCE(valid_from, substr(created_at,1,10)) DESC, version DESC LIMIT 1""",
            (code, replay_as_of, replay_as_of),
        ).fetchone()
        if thesis:
            values.update({"thesis_id": str(thesis[0]), "thesis_status": str(thesis[1]),
                           "thesis_authority": str(thesis[2]), "thesis_version_as_of": int(thesis[4])})
            values["thesis_data_as_of"] = require_pit("thesis", thesis[3], replay_as_of)
        else:
            # A missing thesis is an explicit candidate input, not a guessed
            # current thesis.  It will block HIGH_ATTENTION but not invent an
            # Exit review.
            values.update({"thesis_status": "MISSING", "thesis_authority": "MISSING", "thesis_version_as_of": None})

        reliability, detail = reliability_from_snapshot(metadata, replay_as_of)
        values["valuation_reliability"] = reliability or "INSUFFICIENT"
        if reliability is None:
            missing.append(detail or "VALUATION_RELIABILITY")

        # Reliability, current valuation and historical valuation are core
        # E3/X3 inputs.  Support alone is the only allowed PARTIAL condition.
        core = {"VALUE_PRICE_ZONE", "L3_LEADER", "FINANCIAL_PIT", "RISK_SNAPSHOT", "PRICE_DATE", "PRICE_SNAPSHOT", "HISTORICAL_VALUATION", "VALUATION_RELIABILITY_PROVENANCE"}
        classification = "PIT_INSUFFICIENT" if core & set(missing) else "PIT_PARTIAL" if missing else "PIT_COMPLETE"
        return ReplayInput(code, replay_as_of, classification, tuple(missing), values)


def transition_count(states: list[str], order: dict[str, int]) -> dict[str, int]:
    changes = [abs(order[right] - order[left]) for left, right in zip(states, states[1:]) if left in order and right in order and left != right]
    return {"total": len(changes), "one_level": sum(item == 1 for item in changes), "two_level": sum(item == 2 for item in changes), "three_plus": sum(item >= 3 for item in changes)}


def mass_transition(changed: int, complete: int) -> bool:
    return bool(complete and changed / complete > 0.30)
