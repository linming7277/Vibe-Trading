from __future__ import annotations

from src.disclosure_materials.service import CninfoClient, DisclosureMaterialService, _report_metadata
from src.disclosure_materials.store import DisclosureMaterialStore


def test_report_metadata_supports_cninfo_short_quarter_titles() -> None:
    assert _report_metadata("2026年一季度报告") == ("Q1", "2026-03-31")
    assert _report_metadata("2025年三季度报告") == ("Q3", "2025-09-30")


def test_periodic_report_query_uses_the_company_exchange(monkeypatch) -> None:
    client = CninfoClient()
    calls: list[dict[str, str]] = []

    def fake_post(_url: str, data: dict[str, str]):
        calls.append(data)
        return {"announcements": [], "hasMore": False}

    monkeypatch.setattr(client, "_post", fake_post)
    assert client.periodic_reports({"stock_code": "000544", "org_id": "gssz0000544"}) == []
    assert len(calls) == 4
    assert all(call["plate"] == "sz" and call["column"] == "szse" for call in calls)


def test_extracts_disclosure_materials_as_source_excerpts_only() -> None:
    pages = [
        "前五名客户销售额占比及分产品的营业收入。应收账款按账龄组合计提坏账准备。",
        "PPP 项目按特许经营安排收取可用性服务费；一年内到期的借款及对外担保余额详见附注。",
    ]
    materials = {item["material_type"]: item for item in DisclosureMaterialService.extract_materials(pages)}
    assert materials["CUSTOMER_CONCENTRATION"]["status"] == "FOUND"
    assert materials["BUSINESS_PRODUCT_STRUCTURE"]["status"] == "FOUND"
    assert materials["ACCOUNTS_RECEIVABLE_AGEING"]["status"] == "FOUND"
    assert materials["RECEIVABLES_IMPAIRMENT"]["status"] == "FOUND"
    assert materials["PPP_COLLECTION"]["status"] == "FOUND"
    assert materials["DEBT_MATURITY"]["status"] == "FOUND"
    assert materials["GUARANTEES_CONTINGENCIES"]["status"] == "FOUND"
    # This layer stores where a disclosure mentions a subject.  It must not
    # silently turn a keyword hit into a risk conclusion.
    assert "risk" not in str(materials).lower()


def test_store_reads_a_share_suffix_using_the_cninfo_six_digit_key(tmp_path) -> None:
    store = DisclosureMaterialStore(tmp_path / "research.db")
    document = store.save_document({
        "stock_code": "000544", "company_name": "测试", "org_id": "gssz0000544", "announcement_id": "a1",
        "report_kind": "ANNUAL", "report_period": "2025-12-31", "announcement_date": "2026-04-02",
        "title": "2025年年度报告", "source_url": "https://official.example/a1.pdf", "pdf_path": None,
        "pdf_sha256": None, "text_path": None, "text_sha256": "text-hash", "page_count": 1,
        "extraction_status": "READY", "extraction_error": "",
    })
    store.save_materials(document["id"], "000544", [{
        "material_type": "CUSTOMER_CONCENTRATION", "status": "FOUND", "keywords": ["前五名客户"],
        "excerpts": [{"page": 1, "keyword": "前五名客户", "text": "前五名客户资料"}],
    }])
    assert len(store.list_materials("000544.SZ")) == 1
    store.close()
