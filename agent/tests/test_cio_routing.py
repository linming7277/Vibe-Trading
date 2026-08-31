"""CIO-first runtime routing contracts (routing fix §11)."""

from __future__ import annotations

from src.cio_report.routing import (
    FULL_REPORT,
    QUICK,
    REFRESH,
    SPECIALIST_RISK,
    classify_company_question,
)
from src.mcp_answer_cache import bypass_cache


def test_deep_analysis_is_full_report_not_refresh() -> None:
    assert classify_company_question("深度分析士兰微。") == FULL_REPORT
    assert classify_company_question("完整分析士兰微") == FULL_REPORT
    assert classify_company_question("给我士兰微完整报告。") == FULL_REPORT


def test_explicit_redo_wording_is_refresh() -> None:
    assert classify_company_question("重新深度分析士兰微") == REFRESH
    assert classify_company_question("重新生成士兰微完整报告。") == REFRESH
    assert classify_company_question("用最新数据重新跑一次士兰微") == REFRESH
    assert classify_company_question("重新评估士兰微") == REFRESH


def test_ordinary_question_is_quick() -> None:
    assert classify_company_question("士兰微现在怎么看？") == QUICK
    assert classify_company_question("分析一下士兰微") == QUICK
    assert classify_company_question("同庆楼值得关注吗") == QUICK


def test_domain_deepening_is_specialist() -> None:
    assert classify_company_question("士兰微债务风险详细展开。") == SPECIALIST_RISK


def test_deep_analysis_never_hits_answer_cache_bypass() -> None:
    """深度 ≠ 重新: a deep READ must not bypass caches or trigger fan-out."""
    assert not bypass_cache("深度分析士兰微。")
    assert not bypass_cache("完整分析士兰微")
    assert not bypass_cache("给我士兰微完整报告")
    # explicit redo wording does bypass, by design
    assert bypass_cache("重新分析士兰微")


def test_general_small_talk_stays_general() -> None:
    assert classify_company_question("你是谁") == "GENERAL"
    assert classify_company_question("今天天气不错") == "GENERAL"
