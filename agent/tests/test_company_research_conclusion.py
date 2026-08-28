from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_research_conclusion_routes
from src.api.company_research_conclusion_routes import register_company_research_conclusion_routes
from src.company_research.conclusion_service import CompanyResearchConclusionService


SYMBOL = "000001.SZ"


class FakeOverview:
    def __init__(self, value: dict) -> None:
        self.value, self.calls = value, 0

    def get_overview(self, market: str, stock_code: str) -> dict:
        self.calls += 1
        return deepcopy(self.value)


class FakeResearch:
    def __init__(self, value: dict | None) -> None:
        self.value, self.calls = value, 0

    def get_entry_research(self, market: str, stock_code: str) -> dict | None:
        self.calls += 1
        return deepcopy(self.value)

    def get_exit_research(self, market: str, stock_code: str) -> dict | None:
        self.calls += 1
        return deepcopy(self.value)


_DEFAULT_THESIS = object()


def _overview(*, thesis: dict | None | object = _DEFAULT_THESIS, support: int = 3, challenge: int = 1) -> dict:
    resolved_thesis = (
        {"status": "UNCHANGED", "status_label": "基本没有变化", "confidence": "HIGH"}
        if thesis is _DEFAULT_THESIS
        else thesis
    )
    return {
        "company": {"market": "CN", "stock_code": SYMBOL, "stock_name": "测试公司"},
        "thesis": resolved_thesis,
        "supporting_evidence": [{"evidence_id": f"s{index}"} for index in range(support)],
        "challenging_evidence": [{"evidence_id": f"c{index}"} for index in range(challenge)],
        "data_status": {"financial": "READY", "business": "READY", "thesis": "CREATED", "review": "CURRENT"},
    }


def _entry(*, level: str = "ATTENTION", gaps: list[str] | None = None) -> dict:
    return {
        "entry_level": level, "entry_level_label": {"ATTENTION": "值得关注", "WATCH": "继续观察"}.get(level, level),
        "confidence": "HIGH", "data_gaps": gaps or [],
        "focus_zones": {"fair_value": {"label": "合理价值区间", "low": 80.0, "high": 100.0, "kind": "FAIR_VALUE"},
                        "focus_zone": {"label": "重点观察区", "low": 72.0, "high": 76.0, "kind": "FOCUS"}},
    }


def _exit(*, level: str = "NORMAL", gaps: list[str] | None = None) -> dict:
    return {"exit_level": level, "exit_level_label": {"NORMAL": "暂未出现明显退出压力", "REVIEW": "建议认真检查"}.get(level, level),
            "confidence": "HIGH", "data_gaps": gaps or []}


def _service(*, overview: dict | None = None, entry: dict | None = None, exit_research: dict | None = None) -> CompanyResearchConclusionService:
    return CompanyResearchConclusionService(
        overview_service=FakeOverview(overview or _overview()), entry_service=FakeResearch(entry if entry is not None else _entry()),
        exit_service=FakeResearch(exit_research if exit_research is not None else _exit()),
    )  # type: ignore[arg-type]


def test_complete_conclusion_reuses_existing_statuses_and_is_plain_language() -> None:
    service = _service()
    result = service.get_conclusion("CN", SYMBOL)
    assert result["company"]["stock_name"] == "测试公司"
    assert result["thesis"]["label"] == "基本没有变化"
    assert result["entry"]["label"] == "值得关注"
    assert result["exit"]["label"] == "暂未出现明显退出压力"
    assert result["fair_value_range"]["low"] == 80.0 and result["focus_zone"]["high"] == 76.0
    assert result["evidence_counts"] == {"support": 3, "challenge": 1}
    assert "PE percentile" not in result["research_conclusion"] and "confluence" not in result["research_conclusion"]


def test_no_thesis_and_missing_entry_exit_are_explicit() -> None:
    service = _service(overview=_overview(thesis=None, support=0, challenge=0), entry={}, exit_research={})
    result = service.get_conclusion("CN", SYMBOL)
    assert result["thesis"] is None
    assert result["entry"]["available"] is False and result["entry"]["label"] == "入场研究数据不足"
    assert result["exit"]["available"] is False and result["exit"]["label"] == "退出研究数据不足"
    assert "尚未建立公司核心逻辑" in result["research_conclusion"]


def test_conclusion_prioritizes_thesis_weakening_and_exit_review() -> None:
    weakening = _service(overview=_overview(thesis={"status": "WEAKENING", "status_label": "逻辑正在减弱", "confidence": "MEDIUM"})).get_conclusion("CN", SYMBOL)
    review = _service(entry=_entry(level="WATCH"), exit_research=_exit(level="REVIEW")).get_conclusion("CN", SYMBOL)
    assert "核心逻辑正在减弱" in weakening["research_conclusion"]
    assert "重新检查" in review["research_conclusion"]


def test_conclusion_api_is_read_only_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    app = FastAPI(); register_company_research_conclusion_routes(app, require_auth=lambda: True)
    monkeypatch.setattr(company_research_conclusion_routes, "get_company_research_conclusion_service", lambda: service)
    response = TestClient(app).get(f"/api/value/companies/{SYMBOL}/research-conclusion?market=CN")
    assert response.status_code == 200 and response.json()["formula_version"] == "company-research-conclusion-card-v1.0.0"
