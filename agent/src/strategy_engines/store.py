"""Persistence for deterministic engine runs, snapshots, signals and decisions."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore

from .common.contracts import CommitteeDecision, DecisionStatus, FeatureSnapshot, RegimeSnapshot, ScoreResult, SignalStatus, StrategySignal, jsonable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _json(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_JSON_FIELDS = {
    "symbols_json",
    "raw_features_json",
    "normalized_features_json",
    "component_scores_json",
    "quality_flags_json",
    "evidence_ids_json",
    "missing_fields_json",
    "score_sources_json",
    "triggers_json",
    "formula_versions_json",
    "invalidation_rules_json",
    "review_triggers_json",
    "engine_run_ids_json",
}


class StrategyEngineStore:
    """A narrow store layered on the shared research SQLite database."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        # Reuse the authoritative schema initializer and keep sample seeding off.
        initializer = ResearchWorkspaceStore(self.db_path, seed=False)
        initializer.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        value = dict(row)
        for field in tuple(value):
            if field in _JSON_FIELDS:
                raw = value.pop(field)
                try:
                    value[field.removesuffix("_json")] = json.loads(raw or "[]")
                except (TypeError, json.JSONDecodeError):
                    value[field.removesuffix("_json")] = []
        if "score_sources" in value:
            value["sources"] = value.pop("score_sources")
        if "base_score" in value:
            value["score"] = value["base_score"]
        return value

    def create_or_get_run(
        self,
        *,
        idempotency_key: str,
        strategy_line: str,
        market: str,
        as_of: str,
        symbols: list[str],
        formula_version: str,
        data_snapshot_id: str | None = None,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            existing = self._conn.execute("SELECT * FROM engine_runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing and not force_refresh:
                return self._row(existing) or {}, False
            effective_key = f"{idempotency_key}:{uuid.uuid4().hex}" if force_refresh else idempotency_key
            run_id = _id("engine")
            self._conn.execute(
                """INSERT INTO engine_runs
                   (id,idempotency_key,strategy_line,market,as_of,symbols_json,formula_version,status,source_status,message,started_at,data_snapshot_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, effective_key, strategy_line, market, as_of, _json(symbols), formula_version, "running", "unavailable", "", _now(), data_snapshot_id),
            )
            self._conn.execute(
                """INSERT INTO decision_chain_runs
                   (id,engine_run_id,strategy_line,market,formula_versions_json,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (_id("chain"), run_id, strategy_line, market, _json([formula_version]), "running", _now(), _now()),
            )
            self._conn.commit()
            return self.get_run(run_id) or {}, True

    def finish_run(self, run_id: str, *, status: str, source_status: str, message: str = "") -> dict[str, Any]:
        if status not in {"completed", "failed", "insufficient_data"}:
            raise ValueError("invalid terminal engine status")
        with self._lock:
            self._conn.execute(
                "UPDATE engine_runs SET status=?,source_status=?,message=?,completed_at=? WHERE id=?",
                (status, source_status, message, _now(), run_id),
            )
            self._conn.execute(
                "UPDATE decision_chain_runs SET status=?,updated_at=? WHERE engine_run_id=?",
                (status, _now(), run_id),
            )
            self._conn.commit()
        value = self.get_run(run_id)
        if not value:
            raise KeyError("engine run not found")
        return value

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute("SELECT * FROM engine_runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self, strategy_line: str | None = None, market: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses, args = [], []
        if strategy_line:
            clauses.append("strategy_line=?")
            args.append(strategy_line)
        if market:
            clauses.append("market=?")
            args.append(market)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM engine_runs {where} ORDER BY started_at DESC LIMIT ?", (*args, limit)).fetchall()  # noqa: S608
        return [self._row(row) or {} for row in rows]

    def save_score(self, result: ScoreResult) -> dict[str, Any]:
        value = jsonable(result)
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO score_snapshots(
                       id,engine_run_id,engine,formula_version,strategy_line,market,
                       subject_type,subject_id,data_as_of,available_at,raw_features_json,
                       normalized_features_json,component_scores_json,base_score,coverage,status,
                       quality_flags_json,evidence_ids_json,confidence,missing_fields_json,
                       score_sources_json,provenance_key,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value["id"], value["engine_run_id"], value["engine"], value["formula_version"],
                    value["strategy_line"], value["market"], value["subject_type"], value["subject_id"],
                    value["data_as_of"], value["available_at"], _json(value["raw_features"]),
                    _json(value["normalized_features"]), _json(value["component_scores"]), value["base_score"],
                    value["coverage"], value["status"], _json(value["quality_flags"]), _json(value["evidence_ids"]),
                    value["confidence"], _json(value["missing_fields"]), _json(value["sources"]),
                    value["provenance_key"],
                    value["created_at"] or _now(),
                ),
            )
            chain_field = "sector_score_id" if value["subject_type"] == "sector" else "candidate_score_id" if value["subject_type"] == "security" else None
            if chain_field:
                self._conn.execute(
                    f"UPDATE decision_chain_runs SET {chain_field}=?,updated_at=? WHERE engine_run_id=?",  # noqa: S608
                    (value["id"], _now(), value["engine_run_id"]),
                )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM score_snapshots WHERE id=?", (value["id"],)).fetchone()
        return self._row(row) or {}

    def save_feature(self, snapshot: FeatureSnapshot) -> dict[str, Any]:
        value = jsonable(snapshot)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO feature_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value["id"], value["engine_run_id"], value["market"], value["subject_type"],
                    value["subject_id"], value["data_as_of"], value["available_at"], _json(value["features"]),
                    _json(value["sources"]), _json(value["quality_flags"]), value["created_at"] or _now(),
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM feature_snapshots WHERE id=?", (value["id"],)).fetchone()
        return self._row(row) or {}

    def save_regime(self, result: RegimeSnapshot) -> dict[str, Any]:
        value = jsonable(result)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO regime_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value["id"], value["engine_run_id"], value["strategy_line"], value["market"],
                    value["regime"], value["previous_regime"], value["score"], value["confidence"],
                    value["coverage"], _json(value["triggers"]), value["data_as_of"], value["available_at"],
                    value["formula_version"], value["changed_at"], value["created_at"] or _now(),
                ),
            )
            if value["strategy_line"] == "value":
                self._conn.execute(
                    "UPDATE decision_chain_runs SET macro_snapshot_id=?,updated_at=? WHERE engine_run_id=?",
                    (value["id"], _now(), value["engine_run_id"]),
                )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM regime_snapshots WHERE id=?", (value["id"],)).fetchone()
        return self._row(row) or {}

    def latest_regime(self, strategy_line: str, market: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM regime_snapshots WHERE strategy_line=? AND market=? ORDER BY data_as_of DESC,created_at DESC LIMIT 1",
                (strategy_line, market),
            ).fetchone()
        return self._row(row)

    def save_signal(self, signal: StrategySignal) -> dict[str, Any]:
        value = jsonable(signal)
        now = value["created_at"] or _now()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO strategy_signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value["id"], value["engine_run_id"], value["strategy_line"], value["horizon"], value["market"],
                    value["symbol"], value["data_as_of"], value["valid_from"], value["valid_until"], value["direction"],
                    value["base_score"], value["entry_low"], value["entry_high"], value["stop_price"], value["target_low"],
                    value["target_high"], value["position_cap"], value["coverage"], _json(value["formula_versions"]),
                    _json(value["evidence_ids"]), value["status"], _json(value["invalidation_rules"]), now, now,
                ),
            )
            self._conn.execute(
                "UPDATE decision_chain_runs SET timing_signal_id=?,status='proposed',updated_at=? WHERE engine_run_id=?",
                (value["id"], _now(), value["engine_run_id"]),
            )
            self._conn.commit()
        return self.get_signal(value["id"]) or {}

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute("SELECT * FROM strategy_signals WHERE id=?", (signal_id,)).fetchone())

    def list_signals(self, *, strategy_line: str | None = None, market: str | None = None, horizon: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses, args = [], []
        for field, value in (("strategy_line", strategy_line), ("market", market), ("horizon", horizon)):
            if value:
                clauses.append(f"{field}=?")
                args.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM strategy_signals {where} ORDER BY data_as_of DESC,base_score DESC LIMIT ?", (*args, limit)).fetchall()  # noqa: S608
        return [self._row(row) or {} for row in rows]

    def transition_signal(self, signal_id: str, status: SignalStatus) -> dict[str, Any]:
        current = self.get_signal(signal_id)
        if not current:
            raise KeyError("signal not found")
        allowed = {
            "observed": {"eligible", "invalidated"},
            "eligible": {"proposed", "invalidated", "expired"},
            "proposed": {"approved", "invalidated", "expired"},
            "approved": {"paper_submitted", "invalidated", "expired"},
            "paper_submitted": {"filled", "invalidated", "expired"},
            "filled": set(), "expired": set(), "invalidated": set(),
        }
        if status.value not in allowed.get(str(current["status"]), set()):
            raise ValueError(f"invalid signal transition {current['status']} -> {status.value}")
        with self._lock:
            self._conn.execute("UPDATE strategy_signals SET status=?,updated_at=? WHERE id=?", (status.value, _now(), signal_id))
            self._conn.commit()
        return self.get_signal(signal_id) or {}

    def get_decision_chain(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute("SELECT * FROM decision_chain_runs WHERE engine_run_id=? OR id=?", (run_id, run_id)).fetchone())

    def publish_decision(self, decision: CommitteeDecision) -> dict[str, Any]:
        value = jsonable(decision)
        decision_status = value["status"]
        signal = self.get_signal(value["signal_id"])
        if not signal:
            raise ValueError("signal not found")
        if signal["strategy_line"] != value["strategy_line"]:
            raise ValueError("decision strategy line does not match signal")
        if signal["engine_run_id"] not in value["engine_run_ids"]:
            raise ValueError("decision must reference the signal engine run")
        if value["engine_run_ids"]:
            placeholders = ",".join("?" for _ in value["engine_run_ids"])
            count = self._conn.execute(
                f"SELECT COUNT(*) FROM engine_runs WHERE id IN ({placeholders})",  # noqa: S608
                value["engine_run_ids"],
            ).fetchone()[0]
            if count != len(set(value["engine_run_ids"])):
                raise ValueError("decision references unknown engine runs")
        if value["position_cap"] < 0 or value["position_cap"] > signal["position_cap"]:
            raise ValueError("committee cannot increase the engine position cap")
        if not 0 <= value["confidence"] <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if decision_status == DecisionStatus.APPROVE.value:
            if signal["status"] != SignalStatus.PROPOSED.value:
                raise ValueError("only proposed signals can be approved")
            if not value["evidence_ids"]:
                raise ValueError("approved decisions require evidence")
            if value["direction"] != signal["direction"]:
                raise ValueError("committee cannot change signal direction")
            for field in ("entry_low", "entry_high", "stop_price"):
                if signal.get(field) is not None and value.get(field) is None:
                    raise ValueError(f"approved decision must preserve {field}")
            if signal.get("entry_low") is not None and value["entry_low"] < signal["entry_low"]:
                raise ValueError("committee cannot widen the engine entry zone")
            if signal.get("entry_high") is not None and value["entry_high"] > signal["entry_high"]:
                raise ValueError("committee cannot widen the engine entry zone")
            placeholders = ",".join("?" for _ in value["evidence_ids"])
            count = self._conn.execute(f"SELECT COUNT(*) FROM research_evidence WHERE id IN ({placeholders})", value["evidence_ids"]).fetchone()[0]  # noqa: S608
            if count != len(set(value["evidence_ids"])):
                raise ValueError("decision references unknown evidence")
            engine_stop = signal.get("stop_price")
            if engine_stop is not None and value.get("stop_price") is not None:
                if signal["direction"] == "buy" and value["stop_price"] < engine_stop:
                    raise ValueError("committee cannot loosen the engine stop")
                if signal["direction"] == "sell" and value["stop_price"] > engine_stop:
                    raise ValueError("committee cannot loosen the engine stop")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO structured_committee_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value["id"], value["committee_id"], value["signal_id"], value["strategy_line"],
                    decision_status, value["direction"], value["position_cap"], value["entry_low"],
                    value["entry_high"], value["stop_price"], value["target_low"], value["target_high"],
                    value["holding_period"], value["confidence"], value["summary"], _json(value["review_triggers"]),
                    _json(value["evidence_ids"]), _json(value["engine_run_ids"]), value["created_at"] or _now(),
                ),
            )
            self._conn.execute(
                "UPDATE decision_chain_runs SET committee_id=?,status=?,updated_at=? WHERE engine_run_id=?",
                (value["committee_id"], decision_status, _now(), signal["engine_run_id"]),
            )
            self._conn.commit()
        if decision_status == DecisionStatus.APPROVE.value:
            self.transition_signal(value["signal_id"], SignalStatus.APPROVED)
        row = self._conn.execute("SELECT * FROM structured_committee_decisions WHERE id=?", (value["id"],)).fetchone()
        return self._row(row) or {}

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM structured_committee_decisions WHERE id=?",
                (decision_id,),
            ).fetchone()
        return self._row(row)

    def list_scores(
        self,
        strategy_line: str,
        market: str,
        *,
        engine: str | None = None,
        subject_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return scores from the latest run without one engine hiding another.

        The old dashboard query sorted all engines together and applied one global
        limit. A large leader universe could therefore hide every sector score.
        """
        runs = self.list_runs(strategy_line, market, limit=1)
        if not runs:
            return []
        clauses = ["engine_run_id=?"]
        args: list[Any] = [runs[0]["id"]]
        if engine:
            clauses.append("engine=?")
            args.append(engine)
        if subject_id:
            clauses.append("subject_id=?")
            args.append(subject_id)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM score_snapshots WHERE {' AND '.join(clauses)}
                    ORDER BY CASE WHEN base_score IS NULL THEN 1 ELSE 0 END,
                             base_score DESC, subject_id ASC LIMIT ?""",  # noqa: S608
                (*args, max(1, min(limit, 1000))),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def dashboard(self, strategy_line: str, market: str) -> dict[str, Any]:
        runs = self.list_runs(strategy_line, market, limit=1)
        regime = self.latest_regime(strategy_line, market)
        signals = self.list_signals(strategy_line=strategy_line, market=market, limit=20)
        scores: list[dict[str, Any]] = []
        if runs:
            with self._lock:
                engines = self._conn.execute(
                    "SELECT DISTINCT engine FROM score_snapshots WHERE engine_run_id=? ORDER BY engine",
                    (runs[0]["id"],),
                ).fetchall()
            for row in engines:
                scores.extend(self.list_scores(strategy_line, market, engine=row[0], limit=50))
        return {"strategy_line": strategy_line, "market": market, "latest_run": runs[0] if runs else None, "regime": regime, "scores": scores, "signals": signals}
