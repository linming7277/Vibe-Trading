"""预测留档存储：不可变、official 指针、单飞、熔断状态（§36-§40 §28）。

同 (target_trade_date, input_fingerprint, formula_version, run_mode) 已存在 →
REUSED，不再调用模型；OFFICIAL 每 target 至多一行（部分唯一索引）。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

FORECAST_TABLE = "macro_market_forecasts"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ForecastStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=10000")
        with self._conn:
            self._conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS {FORECAST_TABLE} (
                    id TEXT PRIMARY KEY,
                    target_trade_date TEXT NOT NULL,
                    run_mode TEXT NOT NULL CHECK(run_mode IN ('DRAFT','SHADOW','OFFICIAL')),
                    status TEXT NOT NULL CHECK(status IN (
                        'DRAFT','SHADOW','OFFICIAL','ABSTAINED','MODEL_FAILED','INVALID_OUTPUT')),
                    input_bundle_id TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    forecast_formula_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    renderer_version TEXT NOT NULL,
                    candidate_rules_version TEXT NOT NULL,
                    model_provider TEXT,
                    model_name TEXT,
                    market_direction TEXT,
                    structured_payload_json TEXT NOT NULL,
                    narrative_md TEXT,
                    logical_calls INTEGER NOT NULL DEFAULT 0,
                    actual_requests INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    model_latency_ms INTEGER,
                    prompt_chars INTEGER,
                    output_chars INTEGER,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    UNIQUE (target_trade_date, input_fingerprint, forecast_formula_version, run_mode)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_official_per_target
                    ON {FORECAST_TABLE}(target_trade_date) WHERE status='OFFICIAL';
                CREATE INDEX IF NOT EXISTS idx_forecast_target ON {FORECAST_TABLE}(target_trade_date, created_at DESC);
                CREATE TABLE IF NOT EXISTS macro_forecast_experiment_days (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    target_trade_date TEXT NOT NULL,
                    world_fingerprint TEXT NOT NULL,
                    arm_order TEXT NOT NULL,
                    day_status TEXT NOT NULL,
                    cny_visible INTEGER NOT NULL DEFAULT 0,
                    cny_value REAL,
                    cny_obs TEXT,
                    market_same INTEGER,
                    strong_overlap REAL,
                    weak_overlap REAL,
                    cny_used INTEGER,
                    arm_a_forecast_id TEXT,
                    arm_b_forecast_id TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(experiment_id, target_trade_date)
                );
                CREATE TABLE IF NOT EXISTS forecast_run_leases (
                    lease_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS forecast_semantic_validations (
                    forecast_id TEXT NOT NULL,
                    guard_version TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    revised_narrative_md TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (forecast_id, guard_version)
                );
                CREATE TABLE IF NOT EXISTS macro_forecast_outcomes (
                    id TEXT PRIMARY KEY,
                    forecast_id TEXT NOT NULL,
                    target_trade_date TEXT NOT NULL,
                    run_mode TEXT NOT NULL,
                    benchmark_symbol TEXT NOT NULL,
                    previous_trade_date TEXT,
                    previous_close REAL,
                    actual_close REAL,
                    actual_return REAL,
                    actual_market_class TEXT,
                    predicted_market_direction TEXT,
                    market_evaluation TEXT NOT NULL,
                    industry_results_json TEXT NOT NULL DEFAULT '[]',
                    strong_count INTEGER NOT NULL DEFAULT 0,
                    strong_hits INTEGER NOT NULL DEFAULT 0,
                    weak_count INTEGER NOT NULL DEFAULT 0,
                    weak_hits INTEGER NOT NULL DEFAULT 0,
                    industry_hit_rate REAL,
                    invalidation_observed TEXT NOT NULL DEFAULT 'unknown',
                    outcome_formula_version TEXT NOT NULL,
                    source_as_of TEXT,
                    details_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (forecast_id, outcome_formula_version)
                );
                CREATE INDEX IF NOT EXISTS idx_forecast_outcomes_date
                    ON macro_forecast_outcomes(target_trade_date, run_mode);
                CREATE TABLE IF NOT EXISTS forecast_provider_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    failure_class TEXT NOT NULL,
                    detail TEXT
                );
            """)

    # -- 收盘结果评价（macro_forecast_outcomes，Phase 3） -------------------

    def save_outcome(self, outcome: dict[str, Any]) -> dict[str, Any]:
        """幂等保存正式评价：UNIQUE(forecast_id, outcome_formula_version)，不可覆盖。"""
        import json as _json

        forecast_id = outcome["forecast_id"]
        version = outcome["outcome_formula_version"]
        existing = self._conn.execute(
            "SELECT id FROM macro_forecast_outcomes WHERE forecast_id=? AND outcome_formula_version=?",
            (forecast_id, version),
        ).fetchone()
        if existing:
            return {"status": "REUSED", "id": existing["id"], "written": 0}
        row_id = f"outcome_{forecast_id.split('_')[-1]}_{version.split('-')[-1]}"
        with self._conn:
            self._conn.execute(
                """INSERT INTO macro_forecast_outcomes(
                    id, forecast_id, target_trade_date, run_mode, benchmark_symbol,
                    previous_trade_date, previous_close, actual_close, actual_return,
                    actual_market_class, predicted_market_direction, market_evaluation,
                    industry_results_json, strong_count, strong_hits,
                    weak_count, weak_hits, industry_hit_rate,
                    invalidation_observed, outcome_formula_version, source_as_of,
                    details_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row_id, forecast_id, outcome.get("target_trade_date"), outcome.get("run_mode"),
                 outcome.get("benchmark_symbol"), outcome.get("previous_trade_date"),
                 outcome.get("previous_close"), outcome.get("actual_close"), outcome.get("actual_return"),
                 outcome.get("actual_market_class"), outcome.get("predicted_market_direction"),
                 outcome.get("market_evaluation"),
                 _json.dumps(outcome.get("industry_results") or [], ensure_ascii=False, default=str),
                 int(outcome.get("strong_count") or 0), int(outcome.get("strong_hits") or 0),
                 int(outcome.get("weak_count") or 0), int(outcome.get("weak_hits") or 0),
                 outcome.get("industry_hit_rate"), outcome.get("invalidation_observed"),
                 version, outcome.get("source_as_of"),
                 _json.dumps({k: v for k, v in outcome.items()
                              if k != "industry_results"}, ensure_ascii=False, default=str),
                 _now()),
            )
        return {"status": "SAVED", "id": row_id, "written": 1}

    def load_outcome(self, forecast_id: str, *, outcome_formula_version: str) -> dict[str, Any] | None:
        import json as _json

        row = self._conn.execute(
            "SELECT * FROM macro_forecast_outcomes WHERE forecast_id=? AND outcome_formula_version=?",
            (forecast_id, outcome_formula_version),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["industry_results"] = _json.loads(result.pop("industry_results_json") or "[]")
            details = _json.loads(result.pop("details_json") or "{}")
        except (TypeError, ValueError):
            result["industry_results"], details = [], {}
        result.update({k: v for k, v in details.items() if k not in result})
        return result

    def list_outcomes(self, *, run_mode: str | None = None,
                      start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        import json as _json

        query = "SELECT * FROM macro_forecast_outcomes WHERE 1=1"
        params: list[Any] = []
        if run_mode:
            query += " AND run_mode=?"
            params.append(run_mode)
        if start_date:
            query += " AND target_trade_date>=?"
            params.append(start_date)
        if end_date:
            query += " AND target_trade_date<=?"
            params.append(end_date)
        query += " ORDER BY target_trade_date"
        rows = self._conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            try:
                item["industry_results"] = _json.loads(item.pop("industry_results_json") or "[]")
                item.pop("details_json", None)
            except (TypeError, ValueError):
                item["industry_results"] = []
            results.append(item)
        return results

    def save_semantic_validation(self, forecast_id: str, *, guard_version: str,
                                 report: dict[str, Any], revised_narrative_md: str) -> dict[str, Any]:
        import json as _json

        existing = self._conn.execute(
            "SELECT guard_version FROM forecast_semantic_validations WHERE forecast_id=? AND guard_version=?",
            (forecast_id, guard_version),
        ).fetchone()
        if existing:
            return {"status": "ALREADY_SAVED", "written": 0}
        with self._conn:
            self._conn.execute(
                "INSERT INTO forecast_semantic_validations(forecast_id, guard_version, report_json, revised_narrative_md, created_at) VALUES(?,?,?,?,?)",
                (forecast_id, guard_version, _json.dumps(report, ensure_ascii=False, default=str),
                 revised_narrative_md, _now()),
            )
        return {"status": "SAVED", "written": 1}

    def load_semantic_validation(self, forecast_id: str, *, guard_version: str | None = None) -> dict[str, Any] | None:
        import json as _json

        if guard_version:
            row = self._conn.execute(
                "SELECT * FROM forecast_semantic_validations WHERE forecast_id=? AND guard_version=?",
                (forecast_id, guard_version),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM forecast_semantic_validations WHERE forecast_id=? ORDER BY created_at DESC LIMIT 1",
                (forecast_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["report"] = _json.loads(result.pop("report_json") or "{}")
        except (TypeError, ValueError):
            result["report"] = {}
        return result

    def close(self) -> None:
        self._conn.close()

    # -- 读取（0 LLM / 0 网络） ------------------------------------------

    def existing_run(self, *, target_date: str, fingerprint: str, formula_version: str,
                     run_mode: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT * FROM {FORECAST_TABLE} WHERE target_trade_date=? AND input_fingerprint=? "
            "AND forecast_formula_version=? AND run_mode=?",
            (target_date, fingerprint, formula_version, run_mode),
        ).fetchone()
        return dict(row) if row else None

    def save_experiment_day(self, record: dict[str, Any]) -> dict[str, Any]:
        """实验日 pair 记录（同 experiment+target 幂等，不覆盖）。"""
        with self._conn:
            existing = self._conn.execute(
                "SELECT id FROM macro_forecast_experiment_days WHERE experiment_id=? AND target_trade_date=?",
                (record["experiment_id"], record["target_trade_date"])).fetchone()
            if existing:
                return {"status": "REUSED", "id": existing[0]}
            self._conn.execute(
                """INSERT INTO macro_forecast_experiment_days(
                       id, experiment_id, target_trade_date, world_fingerprint, arm_order,
                       day_status, cny_visible, cny_value, cny_obs, market_same,
                       strong_overlap, weak_overlap, cny_used, arm_a_forecast_id,
                       arm_b_forecast_id, notes, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record["id"], record["experiment_id"], record["target_trade_date"],
                 record["world_fingerprint"], record["arm_order"], record["day_status"],
                 record["cny_visible"], record.get("cny_value"), record.get("cny_obs"),
                 record.get("market_same"), record.get("strong_overlap"),
                 record.get("weak_overlap"), record.get("cny_used"),
                 record.get("arm_a_forecast_id"), record.get("arm_b_forecast_id"),
                 record.get("notes", ""), record["created_at"]))
            return {"status": "SAVED", "id": record["id"]}

    def official_for_target(self, target_date: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT * FROM {FORECAST_TABLE} WHERE target_trade_date=? AND status='OFFICIAL'",
            (target_date,),
        ).fetchone()
        return dict(row) if row else None

    def latest(self, *, run_modes: tuple[str, ...] | None = None,
               exclude_experiment_arm: bool = False) -> dict[str, Any] | None:
        """最新预测留档。exclude_experiment_arm 排除 A/B 实验臂行
        （input_fingerprint 带 ":arm=" 后缀），防止实验预测进入生产消费。"""
        conditions = []
        params: list[Any] = []
        if run_modes:
            placeholders = ",".join("?" for _ in run_modes)
            conditions.append(f"run_mode IN ({placeholders})")
            params.extend(run_modes)
        if exclude_experiment_arm:
            conditions.append("input_fingerprint NOT LIKE '%:arm=%'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        row = self._conn.execute(
            f"SELECT * FROM {FORECAST_TABLE}{where} "
            "ORDER BY target_trade_date DESC, created_at DESC LIMIT 1", params,
        ).fetchone()
        return dict(row) if row else None

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        """幂等保存；同键已存在 → REUSED（不可覆盖，§37）。"""
        existing = self.existing_run(
            target_date=record["target_trade_date"], fingerprint=record["input_fingerprint"],
            formula_version=record["forecast_formula_version"], run_mode=record["run_mode"],
        )
        if existing:
            return {"status": "REUSED", "id": existing["id"], "written": 0, "row": existing}
        row_id = record.get("id") or f"mmf_{record['target_trade_date'].replace('-', '')}_{uuid.uuid4().hex[:12]}"
        with self._conn:
            self._conn.execute(
                f"""INSERT INTO {FORECAST_TABLE}(
                    id, target_trade_date, run_mode, status, input_bundle_id, input_fingerprint,
                    forecast_formula_version, prompt_version, renderer_version, candidate_rules_version,
                    model_provider, model_name, market_direction, structured_payload_json, narrative_md,
                    logical_calls, actual_requests, retry_count, model_latency_ms,
                    prompt_chars, output_chars, created_at, published_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row_id, record["target_trade_date"], record["run_mode"], record["status"],
                    record["input_bundle_id"], record["input_fingerprint"],
                    record["forecast_formula_version"], record["prompt_version"],
                    record["renderer_version"], record["candidate_rules_version"],
                    record.get("model_provider"), record.get("model_name"),
                    record.get("market_direction"), record["structured_payload_json"],
                    record.get("narrative_md"), int(record.get("logical_calls") or 0),
                    int(record.get("actual_requests") or 0), int(record.get("retry_count") or 0),
                    record.get("model_latency_ms"), record.get("prompt_chars"),
                    record.get("output_chars"), _now(), record.get("published_at"),
                ),
            )
        saved_row = self._conn.execute(
            f"SELECT * FROM {FORECAST_TABLE} WHERE id=?", (row_id,),
        ).fetchone()
        return {"status": "SAVED", "id": row_id, "written": 1,
                "row": dict(saved_row) if saved_row else None}

    # -- 单飞租约（§40） ---------------------------------------------------

    def acquire_lease(self, lease_key: str, *, owner: str, ttl_seconds: int = 180) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._conn:
            row = self._conn.execute(
                "SELECT owner, expires_at FROM forecast_run_leases WHERE lease_key=?", (lease_key,),
            ).fetchone()
            if row:
                try:
                    expires = datetime.fromisoformat(row["expires_at"])
                except ValueError:
                    expires = now
                if expires > now:
                    return {"acquired": False, "owner": row["owner"]}
                self._conn.execute("DELETE FROM forecast_run_leases WHERE lease_key=?", (lease_key,))
            expires_at = (now.timestamp() + ttl_seconds)
            self._conn.execute(
                "INSERT INTO forecast_run_leases(lease_key, owner, acquired_at, expires_at) VALUES(?,?,?,?)",
                (lease_key, owner, now.isoformat(),
                 datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()),
            )
        return {"acquired": True, "owner": owner}

    def release_lease(self, lease_key: str, *, owner: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM forecast_run_leases WHERE lease_key=? AND owner=?", (lease_key, owner),
            )

    # -- 熔断（§28：N 连续同类瞬断 → cooldown） ---------------------------

    def record_provider_failure(self, failure_class: str, detail: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO forecast_provider_health(run_at, failure_class, detail) VALUES(?,?,?)",
                (_now(), failure_class, detail[:200]),
            )

    def record_provider_success(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM forecast_provider_health")

    def circuit_open(self, *, consecutive: int = 2, cooldown_seconds: int = 600) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT failure_class, run_at FROM forecast_provider_health ORDER BY run_at DESC LIMIT ?",
            (consecutive,),
        ).fetchall()
        if len(rows) < consecutive or len({row["failure_class"] for row in rows}) != 1:
            return {"open": False}
        try:
            last = datetime.fromisoformat(rows[0]["run_at"])
        except ValueError:
            return {"open": False}
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return {"open": elapsed < cooldown_seconds, "failure_class": rows[0]["failure_class"],
                "seconds_since_last": int(elapsed), "cooldown_seconds": cooldown_seconds}
