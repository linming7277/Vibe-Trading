"""CIO PRICE 块刷新测试：分块按数据新鲜度更新，纯确定性、无 LLM、不触生产库。"""

from __future__ import annotations

import inspect

import pytest

from src.cio_report.blocks import block_fingerprint
from src.cio_report.builder import CioSectionBuilder
from src.cio_report.service import CioReportService
from src.cio_report.store import CioReportStore

CODE = "003012.SZ"


def _zones_fixture(close: float, as_of: str = "2026-09-11") -> dict:
    return {
        "as_of": as_of, "price_as_of": as_of, "current_price": close,
        "position_label": "低于低估关注区",
        "valuation": {"status": "DEEPLY_UNDERVALUED", "fair_value_low": 1.0,
                      "fair_value_mid": 2.0, "fair_value_high": 3.0, "methods": []},
        "confluence_zones": [{"low": 0.9, "high": 1.1}],
        "plain_summary": "夹具摘要",
    }


@pytest.fixture()
def svc(tmp_path):
    return CioReportService(store=CioReportStore(tmp_path / "research.db"))


def _seed_old_report(store: CioReportStore, code: str) -> int:
    old_price_fp = block_fingerprint(code, {
        "code": code, "close_as_of": "2026-09-10", "close": 4.0,
        "zone_low": 0.9, "zone_high": 1.1, "position_label": "低于低估关注区"})
    old_valuation = {
        "section_type": "valuation", "input_fingerprint": "oldval123",
        "freshness_status": "FRESH",
        "structured_payload": {"current_price": 4.0,
                               "block_fingerprints": {"PRICE": old_price_fp}},
        "narrative_md": "旧价格节正文", "source_refs": []}
    old_business = {
        "section_type": "business_structure", "input_fingerprint": "oldbiz456",
        "freshness_status": "FRESH", "structured_payload": {},
        "narrative_md": "旧业务原文XYZ", "source_refs": []}
    saved = store.save_report(
        market="CN", stock_code=code, research_as_of="2026-09-10",
        overall_freshness="READY", input_fingerprint="old-report-fp",
        module_hashes={"valuation": old_price_fp},
        sections=[old_valuation, old_business],
        narrative_report_md="旧全文存档正文", synthesis_source="SYNTHESIS",
        formula_version="cio-report-v1", prompt_version="p", model_version="m",
        previous_report_id=None)
    return saved["id"]


def test_price_changes_but_business_kept(svc, monkeypatch):
    report_id = _seed_old_report(svc.store, CODE)
    monkeypatch.setattr(CioSectionBuilder, "_zones", lambda self: _zones_fixture(4.56))
    monkeypatch.setattr(CioSectionBuilder, "_financial", lambda self: {})

    result = svc.refresh_cio_block("CN", CODE, "PRICE", as_of="2026-09-11")
    assert result["status"] == "REFRESHED"

    latest = svc.store.latest_report("CN", CODE, as_of=None)
    valuation = next(s for s in latest["sections"] if s["section_type"] == "valuation")
    business = next(s for s in latest["sections"] if s["section_type"] == "business_structure")
    # PRICE 变了
    assert valuation["structured_payload"]["current_price"] == 4.56
    assert valuation["structured_payload"]["position_label"] == "低于低估关注区"
    assert valuation["structured_payload"]["block_fingerprints"]["PRICE"] == result["fingerprint"]
    assert valuation["freshness_status"] == "REFRESHED"
    # 其它块保留上一份存档
    assert business["narrative_md"] == "旧业务原文XYZ"
    assert business["freshness_status"] == "FRESH"
    assert latest["narrative_report_md"] == "旧全文存档正文"
    # previous_report_id 链式保留
    assert latest["previous_report_id"] == report_id


def test_same_fingerprint_reruns_reused_no_write(svc, monkeypatch):
    _seed_old_report(svc.store, CODE)
    monkeypatch.setattr(CioSectionBuilder, "_zones",
                        lambda self: _zones_fixture(4.0, as_of="2026-09-10"))
    monkeypatch.setattr(CioSectionBuilder, "_financial", lambda self: {})

    result = svc.refresh_cio_block("CN", CODE, "PRICE", as_of="2026-09-11")
    assert result["status"] == "REUSED"

    count = svc.store._conn.execute(
        "SELECT COUNT(*) FROM company_cio_research_reports WHERE stock_code=?", (CODE,)
    ).fetchone()[0]
    assert count == 1  # 0 次无意义写入


def test_batch_updates_three_pool_stocks(svc, monkeypatch):
    import src.cio_report.blocks as blocks

    codes = ["000111.SZ", "000222.SZ", "000333.SZ"]
    for code in codes:
        _seed_old_report(svc.store, code)
    monkeypatch.setattr(blocks, "leader_pool_codes", lambda: list(codes))
    monkeypatch.setattr(CioSectionBuilder, "_zones",
                        lambda self: _zones_fixture(5.55))
    monkeypatch.setattr(CioSectionBuilder, "_financial", lambda self: {})

    result = svc.refresh_cio_price_blocks(as_of="2026-09-11", universe="leader_pool")
    assert result["count"] == 3 and result["built"] == 3 and result["failed"] == 0
    for code in codes:
        latest = svc.store.latest_report("CN", code, as_of=None)
        valuation = next(s for s in latest["sections"] if s["section_type"] == "valuation")
        assert valuation["structured_payload"]["current_price"] == 5.55


def test_refresh_path_never_calls_llm_or_business_research():
    for source in (
        inspect.getsource(CioReportService.refresh_cio_block),
        inspect.getsource(CioReportService.refresh_cio_price_blocks),
    ):
        lowered = source.lower()
        for banned in ("chatllm", "chat_llm", "analyze(", "business_research",
                       "run_macro_forecast", "_synthesize", "invoke"):
            assert banned not in lowered, banned
