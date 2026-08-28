"""Answer-cache contracts for the MCP ask_* specialist tools (plan Sprint 1)."""

from __future__ import annotations

import json
from pathlib import Path

from src.mcp_answer_cache import (
    McpAnswerCacheStore,
    bypass_cache,
    build_cache_key,
    normalize_question,
    question_fingerprint,
    run_with_answer_cache,
)


def _store(tmp_path: Path) -> McpAnswerCacheStore:
    return McpAnswerCacheStore(tmp_path / "research.db")


def _ok(payload: dict | None = None) -> str:
    return json.dumps({"status": "ok", **(payload or {})}, ensure_ascii=False)


def test_normalize_question_strips_punctuation_and_case() -> None:
    assert normalize_question("士兰微，财务怎么样？") == normalize_question("士兰微 财务怎么样")
    assert normalize_question("600460！") == "600460"


def test_bypass_cache_matches_reanalysis_and_scenario_questions() -> None:
    assert bypass_cache("重新分析一下600460")
    assert bypass_cache("如果毛利率再降5个点会怎样")
    assert bypass_cache("对比一下600460和601919")
    assert not bypass_cache("600460财务怎么样")
    assert not bypass_cache("分析一下600460")


def test_cache_key_changes_with_every_dimension() -> None:
    base = dict(
        tool_name="ask_risk_researcher", market="CN", stock_code="600460.SH",
        research_as_of="2026-08-28", q_fingerprint="q1", input_fingerprint="v1|a|b|1",
    )
    key = build_cache_key(**base)
    assert key == build_cache_key(**base)  # stable
    for changed in (
        {"tool_name": "ask_valuation_researcher"}, {"stock_code": "601919.SH"},
        {"research_as_of": "2026-08-29"}, {"q_fingerprint": "q2"},
        {"input_fingerprint": "v1|a|b|2"}, {"prompt_version": "p2"}, {"model_version": "m2"},
    ):
        assert build_cache_key(**{**base, **changed}) != key


def test_repeat_question_hits_cache_and_runs_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    calls: list[str] = []
    dims = {"market": "CN", "stock_code": "600460.SH", "research_as_of": "2026-08-28",
            "input_fingerprint": "v1|a|b|1", "prompt_version": "p", "model_version": "m"}

    def run() -> str:
        calls.append("run")
        return _ok({"answer": "风险结论。"})

    def fp(question: str):
        return dims

    first = run_with_answer_cache(tool_name="t", question="600460风险怎么样", fingerprint_fn=fp, run_fn=run, store=store)
    second = run_with_answer_cache(tool_name="t", question="600460风险怎么样", fingerprint_fn=fp, run_fn=run, store=store)
    assert calls == ["run"]
    assert first == second
    assert json.loads(second)["status"] == "ok"


def test_input_fingerprint_change_invalidates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dims = {"market": "CN", "stock_code": "600460.SH", "research_as_of": "2026-08-28",
            "input_fingerprint": "v1|a|b|1"}
    calls: list[str] = []
    state = {"fp": "v1|a|b|1"}

    def fp(question: str):
        return {**dims, "input_fingerprint": state["fp"]}

    def run() -> str:
        calls.append("run")
        return _ok({"answer": "x"})

    run_with_answer_cache(tool_name="t", question="q", fingerprint_fn=fp, run_fn=run, store=store)
    state["fp"] = "v1|a|c|1"  # new business snapshot hash
    run_with_answer_cache(tool_name="t", question="q", fingerprint_fn=fp, run_fn=run, store=store)
    assert calls == ["run", "run"]


def test_bypass_questions_never_read_or_write_cache(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dims = {"market": "CN", "stock_code": "600460.SH", "research_as_of": "2026-08-28",
            "input_fingerprint": "v1"}
    calls: list[str] = []

    def fp(question: str):
        return dims

    def run() -> str:
        calls.append("run")
        return _ok({"answer": "x"})

    run_with_answer_cache(tool_name="t", question="重新分析600460", fingerprint_fn=fp, run_fn=run, store=store)
    assert calls == ["run"]
    with store._conn:
        n = store._conn.execute("SELECT count(*) FROM mcp_answer_cache").fetchone()[0]
    assert n == 0


def test_error_answers_and_unresolved_questions_skip_cache(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def run_error() -> str:
        return json.dumps({"status": "error", "error_type": "t", "error": "boom"})

    run_with_answer_cache(
        tool_name="t", question="随便聊聊",
        fingerprint_fn=lambda q: {"market": "CN", "stock_code": "S", "research_as_of": "d", "input_fingerprint": "f"},
        run_fn=run_error, store=store,
    )
    run_with_answer_cache(
        tool_name="t", question="什么是ROE",  # no company resolved
        fingerprint_fn=lambda q: None,
        run_fn=lambda: _ok(), store=store,
    )
    with store._conn:
        n = store._conn.execute("SELECT count(*) FROM mcp_answer_cache").fetchone()[0]
    assert n == 0


def test_question_fingerprint_ignores_punctuation_only_differences() -> None:
    assert question_fingerprint("600460,风险怎么样?") == question_fingerprint("600460 风险怎么样")
    assert question_fingerprint("600460风险") != question_fingerprint("600460估值")
