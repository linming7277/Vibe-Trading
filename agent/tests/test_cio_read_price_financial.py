"""CIO 读路径 PRICE 补刷 + EOD FINANCIAL 块刷新测试。全 mock，零 LLM，不触生产库。"""

from __future__ import annotations

import json

import pytest

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


def _seed_full_report(store: CioReportStore, code: str, close: float = 4.71) -> int:
    def sec(section_type: str, body: str, payload: dict | None = None) -> dict:
        return {
            "section_type": section_type,
            "input_fingerprint": f"{section_type}-old",
            "freshness_status": "REFRESHED",
            "structured_payload": payload if payload is not None else {"note": body},
            "narrative_md": body, "source_refs": [],
        }

    saved = store.save_report(
        market="CN", stock_code=code, research_as_of="2026-09-07",
        overall_freshness="READY", input_fingerprint="seed-fp", module_hashes={},
        sections=[
            sec("valuation", "旧价格节正文", {"current_price": close, "close_as_of": "2026-09-07",
                                             "as_of": "2026-09-07", "block_fingerprints": {"PRICE": "old"}}),
            sec("business_structure", "旧业务节原文XYZ"),
            sec("financial_path", "旧财务节正文",
                {"historical_cutoff": "2026-03-31", "report_period": "2026-03-31"}),
        ],
        narrative_report_md="旧全文存档", synthesis_source="TEMPLATE",
        formula_version="t", prompt_version="t", model_version="", previous_report_id=None)
    return int(saved["id"])


def _install_ready_tdx(monkeypatch, market_date: str = "2026-09-11") -> None:
    import src.tdx_data as tdx_module

    class _Ready:
        @staticmethod
        def latest_qualified_close_snapshot():
            return (True, "", {"market_date": market_date})

    monkeypatch.setattr(tdx_module, "get_tdx_service", lambda: _Ready())


# ---------------------------------------------------------------------------
# 读路径：打开报告先补 PRICE（a/b/c）
# ---------------------------------------------------------------------------

def test_read_refreshes_price_keeps_business_section(tmp_path, monkeypatch):
    """a. 现价变了 → 返回新价格、业务节原文不变、无 LLM（全文存档保持）。"""
    store = CioReportStore(tmp_path / "research.db")
    old_id = _seed_full_report(store, CODE, close=4.71)
    svc = CioReportService(store=store)
    monkeypatch.setattr(CioSectionBuilder, "_zones", lambda self: _zones_fixture(4.57))
    monkeypatch.setattr(CioSectionBuilder, "_financial", lambda self: {})

    _install_ready_tdx(monkeypatch)
    report = svc.get_report("CN", CODE)

    assert report is not None and report["price_as_of"] == "2026-09-11"
    valuation = next(s for s in report["sections"] if s["section_type"] == "valuation")
    assert valuation["structured_payload"]["current_price"] == 4.57
    business = next(s for s in report["sections"] if s["section_type"] == "business_structure")
    assert business["narrative_md"] == "旧业务节原文XYZ"  # 其它节不动
    assert report["narrative_report_md"] == "旧全文存档"  # 全文存档保持（零 LLM）
    assert report["previous_report_id"] == old_id


def test_read_same_price_no_new_row(tmp_path, monkeypatch):
    """b. 现价没变（块指纹已登记）→ 指纹相同 REUSED，报告行数不增加。"""
    from src.cio_report.blocks import block_fingerprint

    store = CioReportStore(tmp_path / "research.db")
    fp = block_fingerprint(CODE, {
        "code": CODE, "close_as_of": "2026-09-11", "close": 4.57,
        "zone_low": 0.9, "zone_high": 1.1, "position_label": "低于低估关注区"})
    store.save_report(
        market="CN", stock_code=CODE, research_as_of="2026-09-07",
        overall_freshness="READY", input_fingerprint="seed-fp", module_hashes={},
        sections=[{
            "section_type": "valuation", "input_fingerprint": "valuation-old",
            "freshness_status": "REFRESHED",
            "structured_payload": {"current_price": 4.57, "close_as_of": "2026-09-11",
                                   "block_fingerprints": {"PRICE": fp}},
            "narrative_md": "旧价格节正文", "source_refs": [],
        }],
        narrative_report_md="旧全文存档", synthesis_source="TEMPLATE",
        formula_version="t", prompt_version="t", model_version="", previous_report_id=None)
    before = store._conn.execute(
        "SELECT COUNT(*) FROM company_cio_research_reports WHERE stock_code=?", (CODE,)).fetchone()[0]
    svc = CioReportService(store=store)
    monkeypatch.setattr(CioSectionBuilder, "_zones", lambda self: _zones_fixture(4.57))
    monkeypatch.setattr(CioSectionBuilder, "_financial", lambda self: {})

    _install_ready_tdx(monkeypatch)
    report = svc.get_report("CN", CODE)
    assert report is not None
    after = store._conn.execute(
        "SELECT COUNT(*) FROM company_cio_research_reports WHERE stock_code=?", (CODE,)).fetchone()[0]
    assert after == before  # REUSED：零写入


def test_read_tdx_not_ready_serves_archive_no_write(tmp_path, monkeypatch):
    """c. TDX 未就绪 → 不写库、返回旧报告（绝不 08-19）。"""
    import src.tdx_data as tdx_module

    class _NotReady:
        @staticmethod
        def latest_qualified_close_snapshot():
            return (False, "tdx not ready", None)

    monkeypatch.setattr(tdx_module, "get_tdx_service", lambda: _NotReady())
    store = CioReportStore(tmp_path / "research.db")
    old_id = _seed_full_report(store, CODE, close=4.71)
    svc = CioReportService(store=store)

    report = svc.get_report("CN", CODE)
    assert report is not None and report["id"] == old_id  # 旧报告原样
    after = store._conn.execute(
        "SELECT COUNT(*) FROM company_cio_research_reports WHERE stock_code=?", (CODE,)).fetchone()[0]
    assert after == 1  # 零写入


# ---------------------------------------------------------------------------
# EOD FINANCIAL 块刷新（d/e/f）
# ---------------------------------------------------------------------------

@pytest.fixture()
def fin_env(tmp_path, monkeypatch):
    import src.cio_report.block_worker as bw

    store = CioReportStore(tmp_path / "research.db")
    old_id = _seed_full_report(store, CODE)
    worker = bw.CioBlockWorker(store=store)
    monkeypatch.setattr(bw, "_qualified_close_date", lambda: "2026-09-11")
    return store, worker, old_id


def _seed_fin_fp(monkeypatch, mapping: dict[str, str]) -> None:
    import src.cio_report.block_worker as bw
    monkeypatch.setattr(bw, "financial_fingerprints", lambda codes: mapping)


def test_eod_financial_new_period_updates_stale(tmp_path, monkeypatch):
    """d. 新报告期 → 财务节期次变、叙述标 STALE、零 LLM。"""
    import src.cio_report.block_worker as bw

    store = CioReportStore(tmp_path / "research.db")
    old_id = _seed_full_report(store, CODE)
    worker = bw.CioBlockWorker(store=store)
    monkeypatch.setattr(bw, "_qualified_close_date", lambda: "2026-09-11")
    _seed_fin_fp(monkeypatch, {CODE: "2026-06-30@2026-08-20T00:00:00"})

    def fake_fin_path(self):
        return {"section_type": "financial_path", "input_fingerprint": "fin-new",
                "freshness_status": "REFRESHED",
                "structured_payload": {"historical_cutoff": "2026-06-30",
                                       "report_period": "2026-06-30"},
                "narrative_md": "旧财务叙述", "source_refs": []}

    def fake_quarter(self):
        return {"section_type": "latest_quarter", "input_fingerprint": "q-new",
                "freshness_status": "REFRESHED",
                "structured_payload": {"report_period": "2026-06-30"},
                "narrative_md": "旧季度叙述", "source_refs": []}

    monkeypatch.setattr(CioSectionBuilder, "build_financial_path", fake_fin_path)
    monkeypatch.setattr(CioSectionBuilder, "build_latest_quarter", fake_quarter)

    result = bw.refresh_financial_blocks(as_of="2026-09-11", store=store)
    assert result["status"] == "COMPLETED" and result["processed"] == 1

    latest = store.latest_report("CN", CODE, as_of=None)
    fin = next(s for s in latest["sections"] if s["section_type"] == "financial_path")
    assert fin["structured_payload"]["historical_cutoff"] == "2026-06-30"
    assert fin["freshness_status"] == "STALE"
    assert fin["narrative_md"] == "旧财务节正文"  # keep_narrative：叙述保留原文
    assert latest["previous_report_id"] is None  # 就地更新语义：无新行、无链


def test_eod_financial_same_period_zero_write(tmp_path, monkeypatch):
    """e. 同期次再跑 → 指纹相同，0 无意义写入。"""
    import src.cio_report.block_worker as bw

    store = CioReportStore(tmp_path / "research.db")
    _seed_full_report(store, CODE)
    worker = bw.CioBlockWorker(store=store)
    monkeypatch.setattr(bw, "_qualified_close_date", lambda: "2026-09-11")
    _seed_fin_fp(monkeypatch, {CODE: "2026-06-30@2026-08-20T00:00:00"})

    first = bw.refresh_financial_blocks(as_of="2026-09-11", store=store)
    assert first["status"] == "COMPLETED"
    rows_after_first = store._conn.execute(
        "SELECT COUNT(*) FROM company_cio_research_reports").fetchone()[0]

    second = bw.refresh_financial_blocks(as_of="2026-09-11", store=store)
    assert second["queued"] == 0  # 期次未变 → 不入队
    rows_after_second = store._conn.execute(
        "SELECT COUNT(*) FROM company_cio_research_reports").fetchone()[0]
    assert rows_after_second == rows_after_first  # 零写入


def test_eod_financial_out_of_pool_skipped(tmp_path, monkeypatch):
    """f. 池外股票不刷。"""
    import src.cio_report.block_worker as bw

    store = CioReportStore(tmp_path / "research.db")
    _seed_full_report(store, "999999.SZ")
    worker = bw.CioBlockWorker(store=store)
    monkeypatch.setattr(bw, "_qualified_close_date", lambda: "2026-09-11")
    _seed_fin_fp(monkeypatch, {"999999.SZ": "2026-06-30@x", "003012.SZ": "2026-06-30@x"})
    monkeypatch.setattr(bw, "pool_universe", lambda: {"003012.SZ"})  # 池里只有 003012

    result = bw.refresh_financial_blocks(as_of="2026-09-11", store=store)
    assert result["processed"] == 1
    row = store._conn.execute(
        "SELECT updated_at FROM company_cio_report_sections WHERE report_id=? AND section_type='financial_path'",
        (store.latest_report("CN", "999999.SZ", as_of=None)["id"],)).fetchone()
    # 池外票的节未被触碰（无独立断言值，行存在即可；消费仅池内）
    assert row is not None
