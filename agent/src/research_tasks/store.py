"""SQLite persistence for Research Task + Multi-Agent V1.

This module deliberately shares the Research Workspace ``research.db``.
Per-researcher credentials are project-local and are never returned by public
configuration APIs.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.accessor import get_env_config
from src.config.paths import get_runtime_root

AGENT_ROLES = (
    "research_lead", "macro_policy", "industry", "company", "valuation", "risk",
    # Standalone Value Line Financial Analyst; it is never routed into the
    # paused Multi-Agent research committee.
    "financial_analyst",
)
TASK_STATUSES = ("CREATED", "RESEARCHING", "REVIEWING", "COMPLETED", "FAILED", "BLOCKED")
PARTICIPANT_STATUSES = ("PENDING", "RUNNING", "COMPLETED", "FAILED")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class ResearchTaskStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._init_db()
        self._seed_configs()

    def close(self) -> None:
        self._conn.close()

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_model_configs (
                    role TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    base_url TEXT NOT NULL DEFAULT '',
                    api_key TEXT NOT NULL DEFAULT '',
                    capabilities_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_tasks (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    selected_agents_json TEXT NOT NULL DEFAULT '[]',
                    trigger_context_json TEXT NOT NULL DEFAULT '{}',
                    result_summary_json TEXT,
                    status_history_json TEXT NOT NULL DEFAULT '[]',
                    review_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS research_task_participants (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
                    agent_role TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    instruction TEXT NOT NULL DEFAULT '',
                    output_json TEXT,
                    error TEXT,
                    duration_ms INTEGER,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_research_task_participants_task
                    ON research_task_participants(task_id);
            """)
            columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(agent_model_configs)").fetchall()
            }
            if "base_url" not in columns:
                self._conn.execute("ALTER TABLE agent_model_configs ADD COLUMN base_url TEXT NOT NULL DEFAULT ''")
            if "api_key" not in columns:
                self._conn.execute("ALTER TABLE agent_model_configs ADD COLUMN api_key TEXT NOT NULL DEFAULT ''")
            if "capabilities_json" not in columns:
                self._conn.execute("ALTER TABLE agent_model_configs ADD COLUMN capabilities_json TEXT NOT NULL DEFAULT '{}'")

    def _seed_configs(self) -> None:
        cfg = get_env_config().llm
        provider = cfg.langchain_provider.strip().lower() or "openai"
        model = cfg.langchain_model_name.strip()
        now = _now()
        with self._lock, self._conn:
            for role in AGENT_ROLES:
                self._conn.execute(
                    "INSERT OR IGNORE INTO agent_model_configs(role, provider, model, enabled, updated_at) VALUES(?,?,?,?,?)",
                    (role, provider, model, 1, now),
                )

    @staticmethod
    def _config(row: sqlite3.Row) -> dict[str, Any]:
        result = {"role": row["role"], "provider": row["provider"], "model": row["model"],
                  "base_url": row["base_url"], "api_key_configured": bool(row["api_key"]),
                  "enabled": bool(row["enabled"]), "updated_at": row["updated_at"]}
        capability_config = _loads(row["capabilities_json"], {})
        structured_output = capability_config.get("structured_output", {})
        if structured_output:
            result["structured_output"] = structured_output
        request_extra_body = capability_config.get("request_extra_body", {})
        if isinstance(request_extra_body, dict) and request_extra_body:
            result["request_extra_body"] = request_extra_body
        return result

    def list_configs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM agent_model_configs").fetchall()
        by_role = {row["role"]: self._config(row) for row in rows}
        return [by_role[role] for role in AGENT_ROLES]

    def get_config(self, role: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM agent_model_configs WHERE role=?", (role,)).fetchone()
        if not row:
            raise KeyError(role)
        return self._config(row)

    def get_runtime_config(self, role: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM agent_model_configs WHERE role=?", (role,)).fetchone()
        if not row:
            raise KeyError(role)
        return {**self._config(row), "api_key": row["api_key"]}

    def update_config(self, role: str, provider: str, model: str, enabled: bool) -> dict[str, Any]:
        if role not in AGENT_ROLES:
            raise ValueError("unknown agent role")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE agent_model_configs SET provider=?, model=?, enabled=?, updated_at=? WHERE role=?",
                (provider.strip().lower(), model.strip(), int(enabled), _now(), role),
            )
        return self.get_config(role)

    def update_connection(self, role: str, *, base_url: str, model: str,
                          api_key: str | None, clear_api_key: bool,
                          enabled: bool) -> dict[str, Any]:
        if role not in AGENT_ROLES:
            raise ValueError("unknown agent role")
        current = self.get_runtime_config(role)
        next_key = "" if clear_api_key else (api_key.strip() if api_key is not None else current["api_key"])
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE agent_model_configs
                   SET provider='openai', model=?, base_url=?, api_key=?, enabled=?, updated_at=?
                   WHERE role=?""",
                (model.strip(), base_url.strip().rstrip("/"), next_key, int(enabled), _now(), role),
            )
        return self.get_config(role)

    def update_structured_output_capabilities(
        self, role: str, capabilities: dict[str, Any], *, request_extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a model-instance capability override without inferring model behaviour."""
        if role not in AGENT_ROLES:
            raise ValueError("unknown agent role")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE agent_model_configs SET capabilities_json=?, updated_at=? WHERE role=?",
                (json.dumps({
                    "structured_output": capabilities,
                    **({"request_extra_body": request_extra_body} if request_extra_body else {}),
                }, ensure_ascii=False), _now(), role),
            )
        return self.get_config(role)

    def create_task(self, *, source: str, scope_type: str, scope_id: str, title: str,
                    question: str, requested_by: str, trigger_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if source not in {"SYSTEM", "BOSS", "AGENT_ESCALATION"}:
            raise ValueError("invalid source")
        if scope_type not in {"INDUSTRY", "COMPANY"}:
            raise ValueError("invalid scope_type")
        task_id, now = str(uuid.uuid4()), _now()
        history = [{"status": "CREATED", "at": now}]
        with self._lock, self._conn:
            self._conn.execute("""
                INSERT INTO research_tasks(id,source,scope_type,scope_id,title,question,status,requested_by,
                    trigger_context_json,status_history_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (task_id, source, scope_type, scope_id, title.strip(), question.strip(), "CREATED",
                  requested_by.strip(), json.dumps(trigger_context or {}, ensure_ascii=False),
                  json.dumps(history, ensure_ascii=False), now))
        return self.get_task(task_id)

    def set_task_status(self, task_id: str, status: str, *, error: str | None = None) -> dict[str, Any]:
        if status not in TASK_STATUSES:
            raise ValueError("invalid task status")
        task = self.get_task(task_id)
        history = task["status_history"]
        now = _now()
        if not history or history[-1]["status"] != status:
            history.append({"status": status, "at": now})
        started = task["started_at"] or (now if status == "RESEARCHING" else None)
        completed = now if status in {"COMPLETED", "FAILED", "BLOCKED"} else None
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE research_tasks SET status=?, status_history_json=?, started_at=?, completed_at=?, error=? WHERE id=?
            """, (status, json.dumps(history, ensure_ascii=False), started, completed, error, task_id))
        return self.get_task(task_id)

    def set_selected_agents(self, task_id: str, roles: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE research_tasks SET selected_agents_json=? WHERE id=?",
                               (json.dumps(roles, ensure_ascii=False), task_id))

    def set_result(self, task_id: str, result: dict[str, Any], review_count: int) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute("UPDATE research_tasks SET result_summary_json=?, review_count=? WHERE id=?",
                               (json.dumps(result, ensure_ascii=False), review_count, task_id))
        return self.set_task_status(task_id, "COMPLETED")

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM research_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError(task_id)
        item = dict(row)
        for target, source, fallback in (
            ("selected_agents", "selected_agents_json", []),
            ("trigger_context", "trigger_context_json", {}),
            ("result_summary", "result_summary_json", None),
            ("status_history", "status_history_json", []),
        ):
            item[target] = _loads(item.pop(source), fallback)
        return item

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id FROM research_tasks ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)
        ).fetchall()
        return [self.get_task(row["id"]) for row in rows]

    def create_participant(self, task_id: str, *, role: str, phase: str, provider: str,
                           model: str, instruction: str) -> dict[str, Any]:
        participant_id = str(uuid.uuid4())
        with self._lock, self._conn:
            self._conn.execute("""
                INSERT INTO research_task_participants(id,task_id,agent_role,phase,provider,model,status,instruction)
                VALUES(?,?,?,?,?,?,?,?)
            """, (participant_id, task_id, role, phase, provider, model, "PENDING", instruction))
        return self.get_participant(participant_id)

    def get_participant(self, participant_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM research_task_participants WHERE id=?", (participant_id,)).fetchone()
        if not row:
            raise KeyError(participant_id)
        item = dict(row)
        item["output"] = _loads(item.pop("output_json"), None)
        return item

    def update_participant(self, participant_id: str, status: str, *, output: dict[str, Any] | None = None,
                           error: str | None = None, duration_ms: int | None = None) -> dict[str, Any]:
        if status not in PARTICIPANT_STATUSES:
            raise ValueError("invalid participant status")
        now = _now()
        current = self.get_participant(participant_id)
        started = current["started_at"] or (now if status == "RUNNING" else None)
        completed = now if status in {"COMPLETED", "FAILED"} else None
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE research_task_participants SET status=?, output_json=?, error=?, duration_ms=?, started_at=?, completed_at=? WHERE id=?
            """, (status, json.dumps(output, ensure_ascii=False) if output is not None else None,
                  error, duration_ms, started, completed, participant_id))
        return self.get_participant(participant_id)

    def list_participants(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT id FROM research_task_participants WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall()
        return [self.get_participant(row["id"]) for row in rows]
