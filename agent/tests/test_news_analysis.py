"""新闻简要分析：指纹、缓存时效、fail-closed 审查、后台调度。全 mock，零真实 LLM。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import src.investment_research_supervisor.news_analysis as na

SH = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 16, 10, 0, tzinfo=SH)


def _news(title_a="国内出台新政策", title_b="美联储释放信号"):
    return {"days": [
        {"date": "2026-09-16", "domestic": [{"title": title_a, "url": "http://e.com/1"}],
         "overseas": [{"title": title_b, "url": "http://e.com/2"}]},
    ]}


def _patch_llm(text: str | None = None, exc: Exception | None = None):
    """隔离角色配置与模型调用；返回 (角色配置 patch, 模型 patch, runtime_mock)。"""
    runtime = MagicMock()
    if exc is not None:
        runtime.invoke.side_effect = exc
    else:
        runtime.invoke.return_value = {"text": text or ""}
    store = MagicMock()
    store.return_value.get_runtime_config.return_value = {"model": "test-model", "provider": "openai"}
    ctx_store = patch("src.research_tasks.store.ResearchTaskStore", store)
    ctx_runtime = patch("src.research_tasks.service.ProviderModelRuntime", lambda: runtime)
    return ctx_store, ctx_runtime, runtime


def test_fingerprint_tracks_news_content() -> None:
    base = na.analysis_fingerprint(_news())
    same = na.analysis_fingerprint(_news())
    changed = na.analysis_fingerprint(_news(title_a="国内出台另一政策"))
    assert base == same and base != changed


def test_cache_respects_six_hour_window(tmp_path) -> None:
    db = tmp_path / "research.db"
    fp = na.analysis_fingerprint(_news())
    conn = na._conn(db)
    try:
        with conn:
            conn.execute(
                "INSERT INTO morning_news_analysis(fingerprint,status,content_md,model,days,error,generated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (fp, "ready", "- 要点", "m", 3, "", "2026-09-16T08:00:00"))
    finally:
        conn.close()
    fresh = na.cached_analysis(fp, research_db_path=db, now=NOW)
    assert fresh and fresh["content_md"] == "- 要点"
    stale = na.cached_analysis(
        fp, research_db_path=db,
        now=NOW + timedelta(hours=na.ANALYSIS_MAX_AGE_HOURS + 1))
    assert stale is None


def test_generate_stores_ready_output(tmp_path) -> None:
    ctx_store, ctx_runtime, runtime = _patch_llm("- 国内政策落地\n- 海外利率预期变化")
    with ctx_store, ctx_runtime:
        result = na.generate_news_analysis(_news(), research_db_path=tmp_path / "research.db", now=NOW)
    assert result["status"] == "ready" and "国内政策落地" in result["content_md"]
    assert runtime.invoke.call_args.kwargs["phase"] == "MACRO_NEWS_ANALYSIS"
    # 缓存可立即命中。
    cached = na.cached_analysis(na.analysis_fingerprint(_news()), research_db_path=tmp_path / "research.db", now=NOW)
    assert cached and cached["status"] == "ready"


def test_generate_discards_banned_language(tmp_path) -> None:
    ctx_store, ctx_runtime, _runtime = _patch_llm("- 建议买入科技股\n- 目标价上调")
    with ctx_store, ctx_runtime:
        result = na.generate_news_analysis(_news(), research_db_path=tmp_path / "research.db", now=NOW)
    assert result["status"] == "discarded" and result["content_md"] == ""
    assert na.cached_analysis(na.analysis_fingerprint(_news()), research_db_path=tmp_path / "research.db", now=NOW) is None


def test_generate_marks_model_failure(tmp_path) -> None:
    ctx_store, ctx_runtime, _runtime = _patch_llm(exc=RuntimeError("timeout"))
    with ctx_store, ctx_runtime:
        result = na.generate_news_analysis(_news(), research_db_path=tmp_path / "research.db", now=NOW)
    assert result["status"] == "failed" and result["content_md"] == ""


def test_get_or_schedule_hits_cache_without_thread(tmp_path) -> None:
    db = tmp_path / "research.db"
    fp = na.analysis_fingerprint(_news())
    conn = na._conn(db)
    try:
        with conn:
            conn.execute(
                "INSERT INTO morning_news_analysis(fingerprint,status,content_md,model,days,error,generated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (fp, "ready", "- 要点", "m", 3, "", "2026-09-16T09:30:00"))
    finally:
        conn.close()
    with patch("src.investment_research_supervisor.news_analysis.threading.Thread") as thread:
        result = na.get_or_schedule_analysis(_news(), research_db_path=db, now=NOW)
    assert result["status"] == "ready"
    thread.assert_not_called()


def test_get_or_schedule_spawns_background_generation(tmp_path, monkeypatch) -> None:
    db = tmp_path / "research.db"
    generated: list[dict] = []
    monkeypatch.setattr(na, "generate_news_analysis",
                        lambda news, **kwargs: generated.append(news) or {"status": "ready"})
    started = []

    class _FakeThread:
        def __init__(self, *, target=None, daemon=None, name=None):
            self._target = target
            started.append(self)

        def start(self):
            pass  # 不立即执行：验证进行中去重；目标统一在断言后手动跑

    monkeypatch.setattr(na.threading, "Thread", _FakeThread)
    result = na.get_or_schedule_analysis(_news(), research_db_path=db, now=NOW)
    assert result == {"status": "generating"}
    assert len(started) == 1
    # 同指纹进行中不重复派发。
    again = na.get_or_schedule_analysis(_news(), research_db_path=db, now=NOW)
    assert again == {"status": "generating"}
    assert len(started) == 1
    # 后台目标在退出前执行生成。
    for thread in started:
        thread._target()
    assert generated == [_news()]
