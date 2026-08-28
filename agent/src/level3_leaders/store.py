"""SQLite snapshots for terminal-industry Leader V1."""

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class Level3LeaderStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["statistics"] = _loads(item.pop("statistics_json"), {})
        return item

    @staticmethod
    def _leader(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["component_scores"] = _loads(item.pop("component_scores_json"), {})
        item["eligibility_reasons"] = _loads(item.pop("eligibility_reasons_json"), [])
        item["metric_applicability_notes"] = _loads(item.pop("metric_notes_json"), [])
        item["raw_features"] = _loads(item.pop("raw_features_json"), {})
        return item

    def completed_run(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM value_level3_leader_runs WHERE idempotency_key=? AND status='COMPLETED'",
            (idempotency_key,),
        ).fetchone()
        return self._run(row) if row else None

    def start_run(self, *, idempotency_key: str, as_of: str, catalog_as_of: str,
                  formula_version: str) -> dict[str, Any]:
        prior = self._conn.execute(
            "SELECT id FROM value_level3_leader_runs WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        run_id = str(prior[0]) if prior else f"level3_{uuid.uuid4().hex[:16]}"
        with self._lock, self._conn:
            if prior:
                self._conn.execute(
                    "UPDATE value_level3_leader_runs SET status='RUNNING',statistics_json='{}',error='',completed_at=NULL WHERE id=?",
                    (run_id,),
                )
                self._conn.execute("DELETE FROM value_level3_leaders WHERE run_id=?", (run_id,))
            else:
                self._conn.execute(
                    """INSERT INTO value_level3_leader_runs(
                       id,idempotency_key,as_of,catalog_as_of,formula_version,status,created_at
                       ) VALUES(?,?,?,?,?,'RUNNING',?)""",
                    (run_id, idempotency_key, as_of, catalog_as_of, formula_version, _now()),
                )
        return self.get_run(run_id)

    def finish_run(self, run_id: str, *, rows: list[dict[str, Any]], statistics: dict[str, Any]) -> dict[str, Any]:
        created_at = _now()
        values = [(
            f"l3lead_{uuid.uuid4().hex[:20]}", run_id, row["as_of"],
            row["level1_code"], row["level1_name"], row["level2_code"], row["level2_name"],
            row["level3_code"], row["level3_name"], row["stock_code"], row["stock_name"],
            row.get("leader_rank"), row.get("leader_score"), row["leader_formula_version"],
            json.dumps(row.get("component_scores") or {}, ensure_ascii=False, sort_keys=True),
            float(row.get("coverage") or 0), row["eligibility_status"],
            json.dumps(row.get("eligibility_reasons") or [], ensure_ascii=False),
            json.dumps(row.get("metric_applicability_notes") or [], ensure_ascii=False),
            json.dumps(row.get("raw_features") or {}, ensure_ascii=False, sort_keys=True),
            str(row.get("provenance_key") or ""), created_at,
        ) for row in rows]
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO value_level3_leaders(
                   id,run_id,as_of,level1_code,level1_name,level2_code,level2_name,
                   level3_code,level3_name,stock_code,stock_name,leader_rank,leader_score,
                   leader_formula_version,component_scores_json,coverage,eligibility_status,
                   eligibility_reasons_json,metric_notes_json,raw_features_json,provenance_key,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            self._conn.execute(
                "UPDATE value_level3_leader_runs SET status='COMPLETED',statistics_json=?,completed_at=? WHERE id=?",
                (json.dumps(statistics, ensure_ascii=False, sort_keys=True), created_at, run_id),
            )
        return self.get_run(run_id)

    def fail_run(self, run_id: str, error: str) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE value_level3_leader_runs SET status='FAILED',error=?,completed_at=? WHERE id=?",
                (error, _now(), run_id),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM value_level3_leader_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return self._run(row)

    def latest_run(self, as_of: str | None = None) -> dict[str, Any] | None:
        if as_of:
            row = self._conn.execute(
                "SELECT * FROM value_level3_leader_runs WHERE status='COMPLETED' AND as_of=? ORDER BY completed_at DESC LIMIT 1",
                (as_of,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM value_level3_leader_runs WHERE status='COMPLETED' ORDER BY as_of DESC,completed_at DESC LIMIT 1"
            ).fetchone()
        return self._run(row) if row else None

    def completed_runs(self, *, through_as_of: str | None = None, limit: int = 60) -> list[dict[str, Any]]:
        """Return completed immutable L3 snapshots for read-only history views.

        This deliberately exposes run metadata only.  Consumers still obtain
        company rows through ``all_rows`` / ``industry_rows`` and must not use
        it to trigger a ranking rebuild.
        """
        clauses = ["status='COMPLETED'"]
        params: list[Any] = []
        if through_as_of:
            clauses.append("as_of<=?")
            params.append(str(through_as_of))
        params.append(max(1, min(int(limit), 365)))
        rows = self._conn.execute(
            f"SELECT * FROM value_level3_leader_runs WHERE {' AND '.join(clauses)} "
            "ORDER BY as_of ASC,completed_at ASC LIMIT ?",
            params,
        ).fetchall()
        return [self._run(row) for row in rows]

    def industry_rows(self, run_id: str, industry_code: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM value_level3_leaders WHERE run_id=? AND level3_code=?
               ORDER BY CASE WHEN leader_rank IS NULL THEN 1 ELSE 0 END,leader_rank,stock_code""",
            (run_id, industry_code),
        ).fetchall()
        return [self._leader(row) for row in rows]

    def all_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM value_level3_leaders WHERE run_id=?
               ORDER BY level3_code,CASE WHEN leader_rank IS NULL THEN 1 ELSE 0 END,leader_rank,stock_code""",
            (run_id,),
        ).fetchall()
        return [self._leader(row) for row in rows]

    def replace_valuation_snapshot(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        """Atomically replace the complete historical-valuation view of a leader run.

        This deliberately receives an already-computed list.  A failed refresh
        therefore never clears the last complete snapshot that the page reads.
        """
        if not rows:
            raise ValueError("leader valuation snapshot cannot be empty")
        values = [(
            run_id,
            str(row["stock_code"]).upper(),
            str(row.get("historical_valuation_status") or "INSUFFICIENT_DATA"),
            str(row.get("presentation_status") or "INSUFFICIENT_DATA"),
            str(row.get("coverage_status") or "INSUFFICIENT"),
            row.get("data_as_of"),
            str(row.get("formula_version") or "historical-valuation-v1.0.0"),
            str(row.get("source") or "TongDaXin historical valuation cache"),
            str(row.get("updated_at") or _now()),
        ) for row in rows]
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM value_level3_leader_valuation_snapshots WHERE run_id=?", (run_id,),
            )
            self._conn.executemany(
                """INSERT INTO value_level3_leader_valuation_snapshots(
                   run_id,stock_code,historical_valuation_status,presentation_status,coverage_status,
                   data_as_of,formula_version,source,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                values,
            )

    def valuation_snapshot(self, run_id: str) -> dict[str, Any]:
        rows = self._conn.execute(
            """SELECT stock_code,historical_valuation_status,presentation_status,coverage_status,
                      data_as_of,formula_version,source,updated_at
               FROM value_level3_leader_valuation_snapshots WHERE run_id=? ORDER BY stock_code""",
            (run_id,),
        ).fetchall()
        items = {str(row["stock_code"]): dict(row) for row in rows}
        dates = sorted({str(row["data_as_of"]) for row in rows if row["data_as_of"]})
        return {
            "run_id": run_id,
            "status": "READY" if rows else "MISSING",
            "total": len(rows),
            "data_as_of": dates[-1] if dates else None,
            "items": items,
        }

    @staticmethod
    def _pool(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["diff"] = _loads(item.pop("diff_json"), {})
        return item

    @staticmethod
    def _pool_member(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["component_scores"] = _loads(item.pop("component_scores_json"), {})
        return item

    def current_pool(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT * FROM l3_leader_pool_runs
               WHERE status='COMPLETED' ORDER BY as_of DESC,completed_at DESC LIMIT 1"""
        ).fetchone()
        return self._pool(row) if row else None

    def pool_for_as_of(self, as_of: str, *, include_inactive: bool = True) -> dict[str, Any] | None:
        """Return the completed Top2 pool for one exact research date."""
        row = self._conn.execute(
            """SELECT * FROM l3_leader_pool_runs
               WHERE status='COMPLETED' AND as_of=?
               ORDER BY completed_at DESC LIMIT 1""",
            (as_of,),
        ).fetchone()
        return self.get_pool(str(row["id"]), include_inactive=include_inactive) if row else None

    def get_pool(self, pool_id: str, *, include_inactive: bool = True) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM l3_leader_pool_runs WHERE id=?", (pool_id,),
        ).fetchone()
        if not row:
            return None
        where = "" if include_inactive else "AND lifecycle_status<>'OUT_OF_TOP2'"
        members = self._conn.execute(
            f"""SELECT * FROM l3_leader_pool_members WHERE pool_id=? {where}
                ORDER BY level1_code,level2_code,level3_code,leader_rank,stock_code""",
            (pool_id,),
        ).fetchall()
        states = self._conn.execute(
            """SELECT * FROM l3_company_research_states WHERE pool_id=?
               ORDER BY lifecycle_status,stock_code""", (pool_id,),
        ).fetchall()
        pool = self._pool(row)
        return {
            **pool,
            "members": [{
                **self._pool_member(member), "as_of": pool["as_of"],
                "eligibility_reasons": [], "metric_applicability_notes": [],
            } for member in members],
            "research_states": [dict(state) for state in states],
        }

    def list_pools(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM l3_leader_pool_runs
               WHERE status='COMPLETED' ORDER BY as_of DESC,completed_at DESC LIMIT ?""",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [self._pool(row) for row in rows]

    def pool_for_source_run(self, source_run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM l3_leader_pool_runs WHERE source_leader_run_id=? AND status='COMPLETED'",
            (source_run_id,),
        ).fetchone()
        return self._pool(row) if row else None

    def materialize_pool(self, source_run_id: str, *, leader_limit: int = 2) -> tuple[dict[str, Any], bool]:
        """Create one immutable Top-N pool and its lifecycle diff.

        Lifecycle is evaluated per terminal-industry membership.  Company
        research state is then aggregated per stock so a company that remains
        Top2 in any terminal industry stays active.
        """
        if int(leader_limit) != 2:
            raise ValueError("Value Line V1 uses a fixed Top2 leader pool")
        if existing := self.pool_for_source_run(source_run_id):
            return self.get_pool(existing["id"], include_inactive=True) or existing, False
        source = self.get_run(source_run_id)
        if source["status"] != "COMPLETED":
            raise ValueError("leader run is not completed")
        current_rows = [
            row for row in self.all_rows(source_run_id)
            if row["eligibility_status"] == "eligible"
            and row.get("leader_rank") is not None
            and int(row["leader_rank"]) <= leader_limit
        ]
        previous_pool = self.current_pool()
        previous_rows: list[dict[str, Any]] = []
        if previous_pool:
            previous_rows = (self.get_pool(previous_pool["id"], include_inactive=False) or {}).get("members", [])
        previous = {(row["level3_code"], row["stock_code"]): row for row in previous_rows}
        current = {(row["level3_code"], row["stock_code"]): row for row in current_rows}
        history_keys = {
            (row["level3_code"], row["stock_code"])
            for row in self._conn.execute(
                "SELECT DISTINCT level3_code,stock_code FROM l3_leader_pool_members"
            ).fetchall()
        }
        timestamp = _now()
        pool_id = f"l3pool_{uuid.uuid4().hex[:16]}"
        members: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        counts = {"NEW": 0, "ACTIVE": 0, "OUT_OF_TOP2": 0, "REENTERED": 0}
        for key, row in current.items():
            prior = previous.get(key)
            if prior:
                lifecycle = "ACTIVE"
                first_entered_at = str(prior.get("first_entered_at") or timestamp)
            elif key in history_keys:
                lifecycle = "REENTERED"
                first = self._conn.execute(
                    """SELECT first_entered_at FROM l3_leader_pool_members
                       WHERE level3_code=? AND stock_code=? ORDER BY created_at LIMIT 1""", key,
                ).fetchone()
                first_entered_at = str(first[0]) if first else timestamp
            else:
                lifecycle = "NEW"
                first_entered_at = timestamp
            counts[lifecycle] += 1
            members.append({
                **row, "lifecycle_status": lifecycle,
                "first_entered_at": first_entered_at, "last_seen_at": timestamp,
                "exited_at": None, "previous_pool_id": previous_pool["id"] if previous_pool else None,
            })
            events.append({
                "event_type": {"NEW": "LEADER_ENTERED", "ACTIVE": "LEADER_STAYED", "REENTERED": "LEADER_REENTERED"}[lifecycle],
                "row": row, "previous_rank": prior.get("leader_rank") if prior else None,
                "current_rank": row.get("leader_rank"), "lifecycle_status": lifecycle,
            })
        for key, prior in previous.items():
            if key in current:
                continue
            counts["OUT_OF_TOP2"] += 1
            members.append({
                **prior, "lifecycle_status": "OUT_OF_TOP2",
                "last_seen_at": str(prior.get("last_seen_at") or timestamp),
                "exited_at": timestamp, "previous_pool_id": previous_pool["id"] if previous_pool else None,
            })
            events.append({
                "event_type": "LEADER_LEFT", "row": prior,
                "previous_rank": prior.get("leader_rank"), "current_rank": None,
                "lifecycle_status": "OUT_OF_TOP2",
            })
        distinct_current = {row["stock_code"] for row in current_rows}
        terminal_count = int(
            (source.get("statistics") or {}).get("industry_count")
            or len({row["level3_code"] for row in current_rows})
        )
        diff = {
            "previous_pool_id": previous_pool["id"] if previous_pool else None,
            "entered": counts["NEW"], "stayed": counts["ACTIVE"],
            "left": counts["OUT_OF_TOP2"], "reentered": counts["REENTERED"],
        }
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO l3_leader_pool_runs(
                   id,source_leader_run_id,as_of,status,formula_version,catalog_as_of,
                   terminal_industry_count,current_membership_count,company_count,
                   new_count,active_count,out_count,reentered_count,diff_json,error,created_at,completed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pool_id, source_run_id, source["as_of"], "COMPLETED", source["formula_version"], source["catalog_as_of"],
                 terminal_count, len(current_rows), len(distinct_current), counts["NEW"], counts["ACTIVE"],
                 counts["OUT_OF_TOP2"], counts["REENTERED"], json.dumps(diff, ensure_ascii=False), "", timestamp, timestamp),
            )
            for item in members:
                self._conn.execute(
                    """INSERT INTO l3_leader_pool_members(
                       id,pool_id,stock_code,stock_name,level1_code,level1_name,level2_code,level2_name,
                       level3_code,level3_name,leader_rank,leader_score,leader_formula_version,
                       component_scores_json,coverage,eligibility_status,lifecycle_status,
                       first_entered_at,last_seen_at,exited_at,previous_pool_id,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"l3pm_{uuid.uuid4().hex[:20]}", pool_id, item["stock_code"], item["stock_name"],
                     item["level1_code"], item["level1_name"], item["level2_code"], item["level2_name"],
                     item["level3_code"], item["level3_name"], int(item["leader_rank"]), item.get("leader_score"),
                     item["leader_formula_version"], json.dumps(item.get("component_scores") or {}, ensure_ascii=False, sort_keys=True),
                     float(item.get("coverage") or 0), item["eligibility_status"], item["lifecycle_status"],
                     item["first_entered_at"], item["last_seen_at"], item.get("exited_at"),
                     item.get("previous_pool_id"), timestamp),
                )
            for event in events:
                row = event["row"]
                event_key = f"{pool_id}:{event['event_type']}:{row['level3_code']}:{row['stock_code']}"
                self._conn.execute(
                    """INSERT INTO l3_leader_pool_events(
                       id,pool_id,event_key,event_type,stock_code,stock_name,level3_code,level3_name,
                       previous_rank,current_rank,payload_json,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"l3pe_{uuid.uuid4().hex[:20]}", pool_id, event_key, event["event_type"],
                     row["stock_code"], row["stock_name"], row["level3_code"], row["level3_name"],
                     event["previous_rank"], event["current_rank"],
                     json.dumps({"lifecycle_status": event["lifecycle_status"]}, ensure_ascii=False), timestamp),
                )
            company_rows: dict[str, dict[str, Any]] = {}
            for item in members:
                symbol = item["stock_code"]
                current_item = company_rows.get(symbol)
                if current_item is None or item["lifecycle_status"] != "OUT_OF_TOP2":
                    company_rows[symbol] = item
            for symbol, item in company_rows.items():
                lifecycle = "ACTIVE" if symbol in distinct_current else "OUT_OF_TOP2"
                previous_state = self._conn.execute(
                    """SELECT * FROM l3_company_research_states WHERE stock_code=?
                       ORDER BY updated_at DESC LIMIT 1""", (symbol,),
                ).fetchone()
                research_status = str(previous_state["research_status"]) if previous_state else "PENDING"
                if lifecycle == "OUT_OF_TOP2":
                    research_status = "INACTIVE"
                elif research_status == "INACTIVE":
                    research_status = "PENDING"
                self._conn.execute(
                    """INSERT INTO l3_company_research_states(
                       id,pool_id,stock_code,stock_name,lifecycle_status,research_status,is_priority,
                       last_financial_snapshot_id,last_researched_at,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"l3rs_{uuid.uuid4().hex[:20]}", pool_id, symbol, item["stock_name"], lifecycle,
                     research_status, int(previous_state["is_priority"]) if previous_state else 0,
                     previous_state["last_financial_snapshot_id"] if previous_state else None,
                     previous_state["last_researched_at"] if previous_state else None, timestamp, timestamp),
                )
        return self.get_pool(pool_id, include_inactive=True) or {}, True

    def update_research_state(self, pool_id: str, stock_code: str, *, status: str,
                              snapshot_id: str | None = None, researched_at: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE l3_company_research_states
                   SET research_status=?,last_financial_snapshot_id=COALESCE(?,last_financial_snapshot_id),
                       last_researched_at=COALESCE(?,last_researched_at),updated_at=?
                   WHERE pool_id=? AND stock_code=?""",
                (status, snapshot_id, researched_at, _now(), pool_id, stock_code),
            )

    def get_automation(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM value_research_automation WHERE id='default'"
        ).fetchone()
        return dict(row) if row else {}

    def update_automation(self, **fields: Any) -> dict[str, Any]:
        allowed = {"enabled", "next_run_at", "last_run_id", "last_status", "last_error", "lock_owner", "lock_until"}
        values = {key: int(value) if key == "enabled" else value for key, value in fields.items() if key in allowed}
        values["updated_at"] = _now()
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE value_research_automation SET {','.join(f'{key}=?' for key in values)} WHERE id='default'",
                tuple(values.values()),
            )
        return self.get_automation()

    def acquire_automation_lock(self, owner: str, *, until: str, now_value: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT lock_owner,lock_until FROM value_research_automation WHERE id='default'"
                ).fetchone()
                if row and row["lock_owner"] and str(row["lock_until"] or "") > now_value and row["lock_owner"] != owner:
                    self._conn.rollback()
                    return False
                self._conn.execute(
                    "UPDATE value_research_automation SET lock_owner=?,lock_until=?,updated_at=? WHERE id='default'",
                    (owner, until, _now()),
                )
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    def release_automation_lock(self, owner: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE value_research_automation SET lock_owner=NULL,lock_until=NULL,updated_at=?
                   WHERE id='default' AND lock_owner=?""", (_now(), owner),
            )
