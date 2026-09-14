"""_default_research_as_of 语义测试：TDX 收盘快照未就绪 → 拒绝生成、零写入。"""

from __future__ import annotations

import pytest

import src.tdx_data as tdx_module
from src.cio_report.service import CioReportService
from src.cio_report.store import CioReportStore

CODE = "003012.SZ"


class _NotReadyService:
    @staticmethod
    def latest_qualified_close_snapshot():
        return (False, "tdx bridge not ready", None)


class _ReadyService:
    @staticmethod
    def latest_qualified_close_snapshot():
        return (True, "", {"market_date": "2026-09-10"})


def test_tdx_not_ready_blocks_all_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(tdx_module, "get_tdx_service", lambda: _NotReadyService())
    svc = CioReportService(store=CioReportStore(tmp_path / "research.db"))

    build = svc.build_report("CN", CODE, as_of=None)
    assert build["status"] == "RESEARCH_DATE_UNAVAILABLE"

    ensure = svc.ensure_focus_tier_reports(as_of=None)
    assert ensure["status"] == "RESEARCH_DATE_UNAVAILABLE"

    batch = svc.refresh_cio_price_blocks(as_of=None, universe="leader_pool")
    assert batch["status"] == "RESEARCH_DATE_UNAVAILABLE"

    block = svc.refresh_cio_block("CN", CODE, "PRICE", as_of=None)
    assert block["status"] == "RESEARCH_DATE_UNAVAILABLE"

    count = svc.store._conn.execute(
        "SELECT COUNT(*) FROM company_cio_research_reports").fetchone()[0]
    assert count == 0  # 零写入


def test_tdx_ready_returns_qualified_trading_day(tmp_path, monkeypatch):
    monkeypatch.setattr(tdx_module, "get_tdx_service", lambda: _ReadyService())
    assert CioReportService._default_research_as_of() == "2026-09-10"
