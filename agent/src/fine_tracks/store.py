"""Fine Track V1 persistence in the existing Research Workspace database."""

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

from .models import confidence_level, review_status, stable_hash, track_semantic_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class FineTrackStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        # The central store owns schema versioning. Opening it applies v11 to
        # both fresh and existing databases before this focused store is used.
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

    def upsert_profiles(self, profiles: list[dict[str, Any]]) -> int:
        rows = [(
            p["third_level_industry_code"], p["third_level_industry_name"], p["stock_code"],
            p["stock_name"], p.get("business_scope", ""), p.get("main_business", ""),
            p.get("company_description", ""), p.get("main_products", ""),
            json.dumps(p.get("source") or [], ensure_ascii=False, sort_keys=True), p["data_status"],
            p["source_hash"], p["updated_at"],
        ) for p in profiles]
        with self._lock, self._conn:
            self._conn.executemany("""
                INSERT INTO company_business_profiles(
                    parent_industry_code,parent_industry_name,stock_code,stock_name,business_scope,
                    main_business,company_description,main_products,source_json,data_status,source_hash,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(parent_industry_code,stock_code) DO UPDATE SET
                    parent_industry_name=excluded.parent_industry_name,stock_name=excluded.stock_name,
                    business_scope=excluded.business_scope,main_business=excluded.main_business,
                    company_description=excluded.company_description,main_products=excluded.main_products,
                    source_json=excluded.source_json,data_status=excluded.data_status,
                    source_hash=excluded.source_hash,updated_at=excluded.updated_at
            """, rows)
        return len(rows)

    @staticmethod
    def _profile(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["third_level_industry_code"] = item.pop("parent_industry_code")
        item["third_level_industry_name"] = item.pop("parent_industry_name")
        item["source"] = _loads(item.pop("source_json"), [])
        return item

    def list_profiles(self, industry_code: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM company_business_profiles WHERE parent_industry_code=? ORDER BY stock_code",
            (industry_code,),
        ).fetchall()
        return [self._profile(row) for row in rows]

    def get_completed_run(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM fine_track_classification_runs WHERE idempotency_key=? AND status='COMPLETED'",
            (idempotency_key,),
        ).fetchone()
        return self._run(row) if row else None

    def start_run(self, *, idempotency_key: str, industry: dict[str, Any], profile_hash: str,
                  version: str, provider: str, model: str, company_count: int) -> dict[str, Any]:
        prior = self._conn.execute(
            "SELECT run_id FROM fine_track_classification_runs WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        run_id, now = (prior[0] if prior else f"fine_{uuid.uuid4().hex[:16]}"), _now()
        with self._lock, self._conn:
            if prior:
                self._conn.execute("""
                    UPDATE fine_track_classification_runs SET status='RUNNING',provider=?,model=?,error='',
                        output_json='{}',started_at=?,completed_at=NULL WHERE run_id=?
                """, (provider, model, now, run_id))
            else:
                self._conn.execute("""
                    INSERT INTO fine_track_classification_runs(
                        run_id,idempotency_key,parent_industry_code,parent_industry_name,profile_hash,
                        classification_version,provider,model,status,company_count,created_at,started_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (run_id, idempotency_key, industry["industry_code"], industry["industry_name"],
                      profile_hash, version, provider, model, "RUNNING", company_count, now, now))
        return self.get_run(run_id)

    def finish_run(self, run_id: str, *, status: str, classified_count: int = 0,
                   unclassified_count: int = 0, output: dict[str, Any] | None = None,
                   error: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE fine_track_classification_runs SET status=?,classified_count=?,unclassified_count=?,
                    output_json=?,error=?,completed_at=? WHERE run_id=?
            """, (status, classified_count, unclassified_count,
                  json.dumps(output or {}, ensure_ascii=False, sort_keys=True), error, _now(), run_id))
        return self.get_run(run_id)

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["output"] = _loads(item.pop("output_json"), {})
        return item

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM fine_track_classification_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return self._run(row)

    def list_tracks(self, industry_code: str) -> list[dict[str, Any]]:
        tracks = [dict(row) for row in self._conn.execute(
            "SELECT * FROM fine_grained_tracks WHERE parent_industry_code=? AND status='ACTIVE' ORDER BY track_name", (industry_code,)
        ).fetchall()]
        memberships = [dict(row) for row in self._conn.execute("""
            SELECT m.*,t.track_name,t.description FROM company_track_memberships m
            JOIN fine_grained_tracks t ON t.track_id=m.track_id
            WHERE m.parent_industry_code=?
            ORDER BY t.track_name, CASE m.membership_type WHEN 'PRIMARY' THEN 0 ELSE 1 END, m.stock_code
        """, (industry_code,)).fetchall()]
        by_track: dict[str, list[dict[str, Any]]] = {}
        for row in memberships:
            by_track.setdefault(row["track_id"], []).append(row)
        for track in tracks:
            track["companies"] = by_track.get(track["track_id"], [])
            track["company_count"] = len(track["companies"])
        return tracks

    def track_catalog(self, industry_code: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM fine_grained_tracks WHERE parent_industry_code=? AND status='ACTIVE' ORDER BY track_name",
            (industry_code,),
        ).fetchall()]

    def unclassified(self, industry_code: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM fine_track_unclassified WHERE parent_industry_code=? ORDER BY stock_code", (industry_code,)
        ).fetchall()]

    def suggestions(self, industry_code: str) -> list[dict[str, Any]]:
        result = []
        for row in self._conn.execute(
            "SELECT * FROM company_track_suggestions WHERE parent_industry_code=? ORDER BY created_at DESC", (industry_code,)
        ).fetchall():
            item = dict(row)
            item["suggestion"] = _loads(item.pop("suggestion_json"), {})
            result.append(item)
        return result

    def apply_classification(self, *, industry: dict[str, Any], profiles: list[dict[str, Any]],
                             result: dict[str, Any], version: str, profile_hash: str,
                             classification_source: str) -> dict[str, int]:
        """Atomically replace auto results while protecting manual decisions."""
        code = industry["industry_code"]
        profile_names = {p["stock_code"]: p["stock_name"] for p in profiles}
        existing = self.track_catalog(code)
        canonical: dict[str, dict[str, Any]] = {track_semantic_key(row["track_name"]): row for row in existing}
        now = _now()
        generated_tracks: dict[str, dict[str, Any]] = {}
        proposed: dict[str, list[dict[str, Any]]] = {}
        for raw_track in result.get("tracks", []):
            name = str(raw_track["track_name"]).strip()
            key = track_semantic_key(name)
            track = canonical.get(key)
            if not track:
                track_id = f"track_{stable_hash([code, key])[:16]}"
                track = {
                    "track_id": track_id, "track_name": name, "normalized_name": key,
                    "parent_industry_code": code, "parent_industry_name": industry["industry_name"],
                    "description": str(raw_track["description"]).strip(), "status": "ACTIVE",
                    "classification_version": version, "created_at": now, "updated_at": now,
                }
                canonical[key] = track
            else:
                track = {
                    **track,
                    "parent_industry_name": industry["industry_name"],
                    "status": "ACTIVE",
                    "classification_version": version,
                    "updated_at": now,
                }
                canonical[key] = track
            generated_tracks[track["track_id"]] = track
            for company in raw_track.get("companies", []):
                proposed.setdefault(company["stock_code"], []).append({**company, "track_id": track["track_id"]})

        # Name normalization may merge two model/cluster labels into one
        # persisted track. Collapse duplicate company->track suggestions before
        # the database uniqueness constraint, preferring PRIMARY and the
        # stronger evidence.
        for symbol, memberships in list(proposed.items()):
            by_track: dict[str, dict[str, Any]] = {}
            for membership in memberships:
                prior = by_track.get(membership["track_id"])
                if prior is None:
                    by_track[membership["track_id"]] = membership
                    continue
                if membership["membership_type"] == "PRIMARY":
                    prior["membership_type"] = "PRIMARY"
                if float(membership["confidence"]) > float(prior["confidence"]):
                    prior["confidence"] = membership["confidence"]
                    prior["reason"] = membership["reason"]
            proposed[symbol] = list(by_track.values())

        unclassified = {row["stock_code"]: row for row in result.get("unclassified", [])}
        manual_symbols = {row[0] for row in self._conn.execute(
            "SELECT DISTINCT stock_code FROM company_track_memberships WHERE parent_industry_code=? AND review_status='MANUAL_CONFIRMED'",
            (code,),
        ).fetchall()}
        classified_count = 0
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE fine_grained_tracks SET status='INACTIVE',updated_at=? WHERE parent_industry_code=?",
                (now, code),
            )
            for track in generated_tracks.values():
                self._conn.execute("""
                    INSERT INTO fine_grained_tracks(track_id,track_name,normalized_name,parent_industry_code,
                        parent_industry_name,description,status,classification_version,created_at,updated_at)
                    VALUES(:track_id,:track_name,:normalized_name,:parent_industry_code,:parent_industry_name,
                        :description,:status,:classification_version,:created_at,:updated_at)
                    ON CONFLICT(parent_industry_code,normalized_name) DO UPDATE SET
                        description=CASE WHEN fine_grained_tracks.description='' THEN excluded.description ELSE fine_grained_tracks.description END,
                        parent_industry_name=excluded.parent_industry_name,
                        status='ACTIVE',classification_version=excluded.classification_version,
                        updated_at=excluded.updated_at
                """, track)
            self._conn.execute("""
                UPDATE fine_grained_tracks SET status='ACTIVE',updated_at=?
                WHERE track_id IN (
                    SELECT track_id FROM company_track_memberships
                    WHERE parent_industry_code=? AND review_status='MANUAL_CONFIRMED'
                )
            """, (now, code))
            all_symbols = set(profile_names)
            for symbol in sorted(all_symbols):
                memberships = proposed.get(symbol, [])
                if symbol in manual_symbols:
                    if memberships:
                        suggestion = {"stock_code": symbol, "memberships": memberships}
                        self._conn.execute("""
                            INSERT OR IGNORE INTO company_track_suggestions(
                                suggestion_id,parent_industry_code,stock_code,source_hash,classification_version,
                                suggestion_json,status,created_at) VALUES(?,?,?,?,?,?,?,?)
                        """, (f"suggest_{stable_hash([code,symbol,profile_hash,version])[:16]}", code, symbol,
                              profile_hash, version, json.dumps(suggestion, ensure_ascii=False, sort_keys=True),
                              "PENDING", now))
                    continue
                self._conn.execute(
                    "DELETE FROM company_track_memberships WHERE parent_industry_code=? AND stock_code=?",
                    (code, symbol),
                )
                if memberships:
                    for membership in memberships:
                        confidence = max(0.0, min(1.0, float(membership["confidence"])))
                        self._conn.execute("""
                            INSERT INTO company_track_memberships(
                                membership_id,stock_code,stock_name,track_id,parent_industry_code,membership_type,
                                classification_reason,confidence,confidence_level,classification_source,
                                review_status,source_hash,classification_version,created_at,updated_at
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (f"member_{stable_hash([code,symbol,membership['track_id']])[:16]}", symbol,
                              profile_names.get(symbol, ""), membership["track_id"], code,
                              membership["membership_type"], membership["reason"], confidence,
                              confidence_level(confidence), classification_source, review_status(confidence),
                              profile_hash, version, now, now))
                    classified_count += 1
                    self._conn.execute(
                        "DELETE FROM fine_track_unclassified WHERE parent_industry_code=? AND stock_code=?", (code, symbol)
                    )
                else:
                    reason = unclassified.get(symbol, {}).get("reason") or "模型未返回可验证的业务分类"
                    status = unclassified.get(symbol, {}).get("classification_status") or "UNCLASSIFIED"
                    self._conn.execute("""
                        INSERT INTO fine_track_unclassified(parent_industry_code,stock_code,stock_name,
                            classification_status,reason,source_hash,classification_version,updated_at)
                        VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(parent_industry_code,stock_code) DO UPDATE SET
                            stock_name=excluded.stock_name,classification_status=excluded.classification_status,
                            reason=excluded.reason,source_hash=excluded.source_hash,
                            classification_version=excluded.classification_version,updated_at=excluded.updated_at
                    """, (code, symbol, profile_names[symbol], status, reason, profile_hash, version, now))
        return {
            "classified": classified_count,
            "unclassified": len(profile_names) - classified_count - len(manual_symbols),
            "manual_protected": len(manual_symbols),
            "tracks": len(generated_tracks),
        }
