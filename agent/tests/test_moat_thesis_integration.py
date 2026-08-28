from __future__ import annotations

from pathlib import Path

from src.company_thesis.draft_service import CompanyThesisDraftService
from src.company_thesis.service import CompanyThesisService


def _moat(dimensions: list[dict], *, status: str = "PARTIAL") -> dict:
    return {"research_as_of": "2026-08-20", "status": status, "formula_version": "moat-research-v1.0.0", "dimensions": dimensions, "moat_data_gaps": ["市场份额"]}


def _dimension(name: str, status: str, *, balance: str = "SUPPORTING", facts: int = 0, management: int = 0, counters: int = 0) -> dict:
    return {"dimension": name, "label": "渠道优势" if name == "CHANNEL" else "品牌与无形资产", "applicability": "APPLICABLE", "status": status,
            "evidence_balance": balance, "confidence": "HIGH" if status == "SUPPORTED" else "MEDIUM", "summary": "测试证据边界", "supporting_evidence_ids": ["ev_fact"] if facts else [],
            "management_claim_ids": ["ev_management"] if management else [], "counter_evidence_ids": ["ev_counter"] if counters else [],
            "data_gaps": ["同店增长"], "evidence_counts": {"quantified_fact": facts, "disclosed_fact": 0, "management_claim": management, "inference": 0, "counter": counters}}


def _draft(advantages: list[dict], *, as_of: str = "2026-08-20") -> dict:
    return {"research_as_of": as_of, "source_data_as_of": as_of, "source_snapshots": [{"domain": "MOAT_RESEARCH", "data_as_of": as_of}],
            "thesis_summary": "测试草案", "core_drivers": [{"type": "FACT", "text": "已验证事实", "source_keys": ["F1"]}],
            "competitive_advantages": advantages, "key_assumptions": [{"type": "INFERENCE", "text": "趋势保持", "source_keys": ["F1"], "factual_basis": "已保存事实"}],
            "invalid_conditions": [{"type": "INFERENCE", "condition": "经营恶化", "source_keys": ["F1"], "factual_basis": "已保存事实"}],
            "key_metrics_to_monitor": [{"type": "FACT", "text": "跟踪收入", "source_keys": ["F1"]}], "main_risks": [{"type": "UNKNOWN", "text": "资料不足", "source_keys": []}],
            "source_refs": [{"type": "FACT", "text": "来源", "source_keys": ["F1"]}], "metadata": {"moat_research": {"research_as_of": as_of}}}


def test_supported_partial_unknown_mapping_and_counter_propagation() -> None:
    supported = _dimension("CHANNEL", "SUPPORTED", facts=2)
    partial = _dimension("BRAND", "PARTIAL", balance="MIXED", management=1, counters=1)
    unknown = _dimension("TECHNOLOGY", "UNKNOWN")
    advantages, refs, assumptions, conditions, monitors = CompanyThesisDraftService._moat_context_items(_moat([supported, partial, unknown]))
    by_dimension = {item["moat_dimension"]: item for item in advantages}
    assert by_dimension["CHANNEL"]["type"] == "FACT"
    assert by_dimension["CHANNEL"]["moat_evidence_ids"] == ["ev_fact"]
    assert by_dimension["BRAND"]["type"] == "INFERENCE"
    assert "公司管理层披露认为" in by_dimension["BRAND"]["text"]
    assert by_dimension["TECHNOLOGY"]["type"] == "UNKNOWN"
    assert conditions
    assert "反向经营表现" in conditions[0]["condition"]
    assert assumptions and any(item["availability"] == "DATA_NOT_AVAILABLE" for item in monitors)
    assert any(item["domain"] == "MOAT_RESEARCH" for item in refs)


def test_provisional_validation_enforces_moat_boundaries() -> None:
    partial = CompanyThesisDraftService._moat_context_items(_moat([_dimension("BRAND", "PARTIAL", management=1)]))[0][0]
    valid, reason = CompanyThesisDraftService.validate_for_provisional(_draft([partial]), research_as_of="2026-08-20")
    assert valid and reason is None
    promoted = {**partial, "type": "FACT", "claim_type": "FACT"}
    valid, reason = CompanyThesisDraftService.validate_for_provisional(_draft([promoted]), research_as_of="2026-08-20")
    assert not valid and reason == "MOAT_PARTIAL_PROMOTED"
    unknown = CompanyThesisDraftService._moat_context_items(_moat([_dimension("TECHNOLOGY", "UNKNOWN")]))[0][0]
    invalid_unknown = {**unknown, "type": "INFERENCE", "claim_type": "INFERENCE", "source_keys": ["MOAT"]}
    valid, reason = CompanyThesisDraftService.validate_for_provisional(_draft([invalid_unknown]), research_as_of="2026-08-20")
    assert not valid and reason == "MOAT_UNKNOWN_PROMOTED"


def test_existing_thesis_is_protected_without_revision(tmp_path: Path) -> None:
    thesis = CompanyThesisService(db_path=tmp_path / "research.db")
    try:
        thesis.create_initial_thesis(market="CN", stock_code="605108.SH", title="原有逻辑", core_thesis="原有 AI 初步逻辑。", status="FORMING", confidence="LOW", invalid_conditions=[], created_by="SYSTEM", source_data_as_of="2026-08-20", authority_status="AI_PROVISIONAL")
        service = CompanyThesisDraftService(thesis_service=thesis, db_path=tmp_path / "research.db", moat_research_loader=lambda *_: _moat([]))
        try:
            result = service.generate("CN", "605108.SH", research_as_of="2026-08-20")
            assert result["status"] == "THESIS_EXISTS"
            assert len(thesis.list_thesis_versions("CN", "605108.SH")) == 1
        finally:
            service.close()
    finally:
        thesis.close()
