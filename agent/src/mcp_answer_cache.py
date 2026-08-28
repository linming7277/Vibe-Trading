"""Answer-level cache for the MCP ask_* specialist tools.

Phase 1 of the research-cache-first plan: a repeated Feishu question must not
re-run LLM specialists when the underlying research snapshots are unchanged.
Research results themselves stay in their own input-hash-addressed snapshot
tables; this module caches the final *tool answer* keyed by those fingerprints,
so ``same company + same trading day + same question intent + same inputs``
resolves to a pure cache hit.

Deliberately not cached (per plan §6.3): explicit re-analysis requests,
assumption scenarios, cross-company comparisons, and other free-form deep
questions.  ``input_fingerprint`` granularity is intentionally basic in this
phase (financial/business snapshot hashes + thesis version); Sprint 2's
research manifests refine it per module.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CACHE_SCHEMA_VERSION = "mcp-answer-cache-v1"

# Requests that must bypass the answer cache entirely.
_BYPASS_RE = re.compile(
    r"重新|再次|再分析|重写|重新分析|假设|假如|如果|假如说|对比|比较|换个角度|深度对比",
    re.IGNORECASE,
)
_QUESTION_NOISE_RE = re.compile(
    r"[\s，。？！?!,、:：;；'\"“”‘’()（）\[\]【】{}<>《》\-—_—…·/\\|.]+"
)


def normalize_question(question: str) -> str:
    """Collapse a question to its stable semantic skeleton for keying.

    Only punctuation/whitespace is stripped (case-folded); wording changes
    naturally produce different keys.  Company extraction is not done here —
    the company rides along via the cache key dimensions instead.
    """
    return _QUESTION_NOISE_RE.sub("", str(question or "").strip().lower())


def question_fingerprint(question: str) -> str:
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()


def bypass_cache(question: str) -> bool:
    """True when the question demands fresh reasoning rather than a cached answer."""
    return bool(_BYPASS_RE.search(str(question or "")))


def build_cache_key(
    *,
    tool_name: str,
    market: str,
    stock_code: str,
    research_as_of: str,
    q_fingerprint: str,
    input_fingerprint: str,
    prompt_version: str = "",
    model_version: str = "",
) -> str:
    raw = "|".join((
        CACHE_SCHEMA_VERSION, tool_name, market, stock_code.upper(),
        str(research_as_of)[:10], q_fingerprint, input_fingerprint,
        prompt_version, model_version,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> Path:
    from src.config.paths import get_runtime_root

    return Path(get_runtime_root()) / "research.db"


class McpAnswerCacheStore:
    """SQLite-backed answer cache shared by all MCP server processes."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_answer_cache (
                    cache_key TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL DEFAULT '',
                    research_as_of TEXT NOT NULL,
                    question_fingerprint TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    prompt_version TEXT NOT NULL DEFAULT '',
                    model_version TEXT NOT NULL DEFAULT '',
                    answer_text TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_hit_at TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mcp_answer_cache_tool_company "
                "ON mcp_answer_cache(tool_name, market, stock_code, research_as_of)"
            )

    def lookup(self, cache_key: str) -> str | None:
        """Return the cached answer text, bumping hit statistics."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT answer_text FROM mcp_answer_cache WHERE cache_key=?", (cache_key,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE mcp_answer_cache SET hit_count=hit_count+1, last_hit_at=? "
                "WHERE cache_key=?",
                (_utc_now(), cache_key),
            )
        return str(row[0])

    def save(self, *, cache_key: str, tool_name: str, market: str, stock_code: str,
             research_as_of: str, q_fingerprint: str, input_fingerprint: str,
             prompt_version: str, model_version: str, answer_text: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO mcp_answer_cache (
                    cache_key, tool_name, market, stock_code, research_as_of,
                    question_fingerprint, input_fingerprint, prompt_version,
                    model_version, answer_text, hit_count, created_at, last_hit_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,NULL)
                """,
                (cache_key, tool_name, market, stock_code, str(research_as_of)[:10],
                 q_fingerprint, input_fingerprint, prompt_version, model_version,
                 answer_text, _utc_now()),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _answer_is_cacheable(answer_text: str) -> bool:
    try:
        return json.loads(answer_text).get("status") == "ok"
    except (ValueError, TypeError):
        return False


def run_with_answer_cache(
    *,
    tool_name: str,
    question: str,
    fingerprint_fn: Callable[[str], dict[str, Any] | None],
    run_fn: Callable[[], str],
    store: McpAnswerCacheStore,
) -> str:
    """Execute an ask_* tool with the shared answer cache in front.

    ``fingerprint_fn`` returns the cache dimensions (market, stock_code,
    research_as_of, input_fingerprint, prompt_version, model_version) or
    ``None`` to skip caching for this question (e.g. no company resolved).
    """
    if bypass_cache(question):
        return run_fn()
    dims = fingerprint_fn(question)
    if not dims or not str(dims.get("input_fingerprint") or "").strip():
        return run_fn()
    q_fp = question_fingerprint(question)
    cache_key = build_cache_key(
        tool_name=tool_name,
        market=str(dims.get("market") or "CN"),
        stock_code=str(dims.get("stock_code") or ""),
        research_as_of=str(dims.get("research_as_of") or ""),
        q_fingerprint=q_fp,
        input_fingerprint=str(dims["input_fingerprint"]),
        prompt_version=str(dims.get("prompt_version") or ""),
        model_version=str(dims.get("model_version") or ""),
    )
    cached = store.lookup(cache_key)
    if cached is not None:
        return cached
    answer = run_fn()
    if _answer_is_cacheable(answer):
        store.save(
            cache_key=cache_key,
            tool_name=tool_name,
            market=str(dims.get("market") or "CN"),
            stock_code=str(dims.get("stock_code") or ""),
            research_as_of=str(dims.get("research_as_of") or ""),
            q_fingerprint=q_fp,
            input_fingerprint=str(dims["input_fingerprint"]),
            prompt_version=str(dims.get("prompt_version") or ""),
            model_version=str(dims.get("model_version") or ""),
            answer_text=answer,
        )
    return answer


_store: McpAnswerCacheStore | None = None
_store_lock = threading.Lock()


def get_mcp_answer_cache() -> McpAnswerCacheStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = McpAnswerCacheStore()
        return _store
