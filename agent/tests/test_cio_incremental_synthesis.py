"""Incremental per-section CIO synthesis: small calls, cache, per-section fallback.

在模型层打桩（替换 ProviderModelRuntime.invoke_with_connection），保留
_invoke_section_polish 的全部校验（长度/交易表述/表格行守卫）。
规则：每节独立小请求 + 底稿哈希缓存 + 单节失败只降级该节；全部节失败
才抛错，由重试包装降级为整篇确定性模板。
"""

from __future__ import annotations

import pytest

from src.cio_report.service import (
    SYNTHESIS_LLM_COMPLETED,
    SYNTHESIS_LLM_PARTIAL,
    SYNTHESIS_TEMPLATE_FALLBACK,
    CioReportService,
)
from src.cio_report.store import CioReportStore
from src.research_tasks.service import ProviderModelRuntime

TEMPLATE_MD = (
    "投研主管深度研究报告 · 测试（600460.SH）\n"
    "## 1. 投研主管结论\n结论底稿：公司处于价值修复早期，综合判断继续观察，"
    "等待下一个财报周期验证收入质量与现金流匹配度后再升级研究优先级。\n"
    "## 2. 过去五年发生了什么\n五年底稿：收入持续增长。\n\n"
    "| 年度 | 营收 |\n| --- | --- |\n| 2025 | 10亿 |\n"
    "## 3. 最终研究判断\n判断底稿：重点研究。公司盈利结构改善、负债率下降，"
    "但估值分位仍处中位，需结合行业景气度与同行对比持续复核。\n"
)


def _service(tmp_path) -> CioReportService:
    return CioReportService(store=CioReportStore(tmp_path / "research.db"))


def _patch_model(monkeypatch, handler) -> list[dict]:
    calls: list[dict] = []

    def fake_invoke(self, **kwargs):
        payload = dict(kwargs["payload"])
        calls.append(payload)
        return handler(payload)

    monkeypatch.setattr(ProviderModelRuntime, "invoke_with_connection", fake_invoke)
    return calls


def test_all_sections_polished_cached_and_assembled(tmp_path, monkeypatch) -> None:
    def handler(payload):
        title, draft = payload["section_title"], payload["draft"]
        return {"text": f"[润色]{title} {draft}"}

    calls = _patch_model(monkeypatch, handler)
    svc = _service(tmp_path)
    report_md, model_name, status = svc._synthesize("600460.SH", "2026-09-08", [], TEMPLATE_MD)
    assert status == SYNTHESIS_LLM_COMPLETED
    assert len(calls) == 3
    assert "[润色]投研主管结论" in report_md and "[润色]最终研究判断" in report_md
    assert "| 2025 | 10亿 |" in report_md  # 表格原样保留
    import sqlite3

    conn = sqlite3.connect(tmp_path / "research.db")
    cached_rows = conn.execute("SELECT COUNT(*) FROM cio_section_narrative_cache").fetchone()[0]
    conn.close()
    assert cached_rows == 3
    # 第二次：底稿未变 → 全部命中缓存，零模型调用，报告一致
    calls.clear()
    report_md2, _, status2 = svc._synthesize("600460.SH", "2026-09-08", [], TEMPLATE_MD)
    assert calls == [] and status2 == SYNTHESIS_LLM_COMPLETED
    assert report_md2 == report_md


def test_partial_failure_degrades_only_that_section(tmp_path, monkeypatch) -> None:
    def handler(payload):
        title, draft = payload["section_title"], payload["draft"]
        if title == "过去五年发生了什么":
            raise ValueError("section polish failed safety validation")
        return {"text": f"[润色]{title} {draft}"}

    _patch_model(monkeypatch, handler)
    svc = _service(tmp_path)
    report_md, model_name, status = svc._synthesize("600460.SH", "2026-09-08", [], TEMPLATE_MD)
    assert status == SYNTHESIS_LLM_PARTIAL
    assert "节降级底稿" in model_name
    assert "[润色]投研主管结论" in report_md
    assert "五年底稿：收入持续增长。" in report_md  # 失败节回退确定性底稿
    assert "| 2025 | 10亿 |" in report_md


def test_all_failed_raises_and_wrapper_falls_back_to_template(tmp_path, monkeypatch) -> None:
    def handler(payload):
        raise ValueError("section polish failed safety validation")

    _patch_model(monkeypatch, handler)
    svc = _service(tmp_path)
    with pytest.raises(RuntimeError, match="all section polish failed"):
        svc._synthesize("600460.SH", "2026-09-08", [], TEMPLATE_MD)
    narrative, model_name, status = svc._synthesize_with_retry(
        "600460.SH", "2026-09-08", [], TEMPLATE_MD)
    assert status == SYNTHESIS_TEMPLATE_FALLBACK
    assert narrative == TEMPLATE_MD and model_name == ""


def test_table_drop_falls_back_to_draft(tmp_path, monkeypatch) -> None:
    def handler(payload):
        title, draft = payload["section_title"], payload["draft"]
        if title == "过去五年发生了什么":
            # 丢掉表格行（但长度足够，触发表格守卫而非长度守卫）
            filler = "本节叙述：" + "收入逐年变化。" * 8
            return {"text": f"[润色]{title} {filler}{draft.splitlines()[0]}"}
        return {"text": f"[润色]{title} {draft}"}

    _patch_model(monkeypatch, handler)
    svc = _service(tmp_path)
    report_md, _, status = svc._synthesize("600460.SH", "2026-09-08", [], TEMPLATE_MD)
    assert status == SYNTHESIS_LLM_PARTIAL
    assert "| 2025 | 10亿 |" in report_md  # 底稿表格仍在，未被模型丢掉


def test_trading_language_falls_back_to_draft(tmp_path, monkeypatch) -> None:
    def handler(payload):
        title, draft = payload["section_title"], payload["draft"]
        if title == "最终研究判断":
            filler = "建议买入并加仓。" + "趋势判断。" * 10
            return {"text": f"[润色]{title} {filler}{draft}"}
        return {"text": f"[润色]{title} {draft}"}

    _patch_model(monkeypatch, handler)
    svc = _service(tmp_path)
    report_md, _, status = svc._synthesize("600460.SH", "2026-09-08", [], TEMPLATE_MD)
    assert status == SYNTHESIS_LLM_PARTIAL
    assert "买入" not in report_md and "加仓" not in report_md


def test_transient_section_error_retries_once(tmp_path, monkeypatch) -> None:
    calls: list[dict] = []

    def handler(payload):
        calls.append(payload)
        if len(calls) == 1:
            raise TimeoutError("Request timed out.")
        title, draft = payload["section_title"], payload["draft"]
        return {"text": f"[润色]{title} {draft}"}

    _patch_model(monkeypatch, handler)
    svc = _service(tmp_path)
    _, _, status = svc._synthesize("600460.SH", "2026-09-08", [], TEMPLATE_MD)
    assert status == SYNTHESIS_LLM_COMPLETED
    assert len(calls) == 4  # 第一节瞬态失败重试一次（2 次）+ 后两节各 1 次
