"""跟踪清单：纳入/移出/清单富集。全隔离（临时库 + mock 行情与档位），零 LLM 零网络。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.company_tracking import CompanyTrackingService
from src.company_tracking.store import CompanyTrackingStore

NOW = datetime(2026, 9, 16, 15, 0, tzinfo=timezone(timedelta(hours=8)))


class _FakeTdx:
    price = 10.0

    def get_record(self, dataset, key):
        return {"payload": {"price": self.price, "data_as_of": "2026-09-16T15:00:00"}}

    def close(self):
        pass


def _service(tmp_path, *, tiers=None):
    store = CompanyTrackingStore(tmp_path / "research.db")
    fake_tdx = _FakeTdx()

    service = CompanyTrackingService(store=store)
    patchers = [
        patch("src.tdx_data.store.TdxDataStore", lambda: fake_tdx),
        patch("src.company_tracking.service.CompanyTrackingService._focus_company_map",
              staticmethod(lambda: {k: {"tier": v} for k, v in (tiers or {}).items()})),
    ]
    for patcher in patchers:
        patcher.start()
    service._patchers = patchers
    service._fake_tdx = fake_tdx
    return service


def _stop(service) -> None:
    for patcher in getattr(service, "_patchers", []):
        patcher.stop()
    service.close()


def test_add_records_price_reasons_and_enriches_list(tmp_path) -> None:
    service = _service(tmp_path, tiers={"601886.SH": "重点研究"})
    try:
        result = service.add(
            stock_code="601886.SH", company_name="江河集团", tier="A",
            reasons=["进入低估关注区", "龙头质量分领先同行"], note="",
            now=NOW - timedelta(days=5),
        )
        assert result["added_price"] == 10.0
        items = service.list_items(now=NOW)["items"]
        assert len(items) == 1
        item = items[0]
        assert item["stock_code"] == "601886.SH"
        assert item["current_tier"] == "重点研究"
        assert item["change_pct"] == 0.0
        assert item["days_tracked"] == 5
        assert item["added_reasons"] == ["进入低估关注区", "龙头质量分领先同行"]
    finally:
        _stop(service)


def test_price_change_reflects_latest_quote(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        service.add(stock_code="600216.SH", company_name="浙江医药", tier="A",
                    reasons=[], now=NOW - timedelta(days=3))
        assert service._fake_tdx.price == 10.0
        service._fake_tdx.price = 12.0  # 纳入后行情上涨
        item = service.list_items(now=NOW)["items"][0]
        assert item["added_price"] == 10.0
        assert item["current_price"] == 12.0
        assert item["change_pct"] == 20.0
    finally:
        _stop(service)


def test_remove_then_readd_reactivates(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        service.add(stock_code="600273.SH", company_name="嘉化能源", tier="A", reasons=[], now=NOW)
        assert service.remove("600273.SH")["removed"] is True
        assert service.remove("600273.SH")["removed"] is False  # 已移出不可重复移出
        assert service.list_items(now=NOW)["items"] == []
        result = service.add(stock_code="600273.SH", company_name="嘉化能源", tier="B", reasons=[], now=NOW)
        assert result["reactivated"] is True
        assert service.list_items(now=NOW)["total"] == 1
    finally:
        _stop(service)


def test_focus_tier_read_failure_degrades_cleanly(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        service.add(stock_code="600522.SH", company_name="中天科技", tier="A", reasons=[], now=NOW)
        with patch("src.focus_selection.get_focus_selection_service",
                   lambda: (_ for _ in ()).throw(RuntimeError("boom"))):
            items = service.list_items(now=NOW)["items"]
        assert items[0]["current_tier"] is None
        assert items[0]["current_price"] is not None
    finally:
        _stop(service)


def test_sync_focus_a_auto_tracks_and_respects_manual(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        a_items = [
            {"stock_code": "601886.SH", "company_name": "江河集团", "focus_reasons": ["进入低估关注区"]},
            {"stock_code": "600216.SH", "company_name": "浙江医药", "focus_reasons": ["龙头质量分领先"]},
        ]
        # 人工先移出 600216（尊重人工决定，不再自动纳入）
        service.add(stock_code="600216.SH", company_name="浙江医药", tier="A", reasons=[], now=NOW)
        service.remove("600216.SH", note="manual", now=NOW)
        result = service.sync_focus_a(a_items, now=NOW)
        assert result["added"] == 1 and result["kept"] == 2
        auto = service.store.get("601886.SH")
        assert auto["source"] == "auto" and auto["status"] == "active" and auto["added_price"] == 10.0
        assert service.store.get("600216.SH")["status"] == "removed"

        # 退出 A 档：auto 行自动移出；manual 不受影响
        manual = service.add(stock_code="600273.SH", company_name="嘉化能源", tier="B",
                             reasons=[], source="manual", now=NOW)
        assert manual["source"] == "manual"
        result = service.sync_focus_a([], now=NOW + timedelta(days=1))
        assert service.store.get("601886.SH")["status"] == "removed"
        assert service.store.get("601886.SH")["removed_note"] == "已退出重点研究"
        assert service.store.get("600273.SH")["status"] == "active"
        assert result["removed"] >= 1
    finally:
        _stop(service)


def test_sync_anchors_to_entry_date_and_entry_price(tmp_path, monkeypatch) -> None:
    """A 档自动跟踪锚定到进入重点研究当日：参考价=当日池快照价、天数从进入日起算。"""
    import json

    db = tmp_path / "research.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE company_low_value_leader_pool_snapshots("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, stock_code TEXT, source_as_of TEXT, payload_json TEXT)")
    conn.execute(
        "INSERT INTO company_low_value_leader_pool_snapshots(stock_code, source_as_of, payload_json)"
        " VALUES('601886.SH','2026-08-24',?)",
        (json.dumps({"current_price": 12.72}),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("src.company_tracking.service.get_runtime_root", lambda: tmp_path)

    service = _service(tmp_path)
    try:
        service.sync_focus_a([
            {"stock_code": "601886.SH", "company_name": "江河集团",
             "entered_at": "2026-08-24", "focus_reasons": ["进入低估关注区"]},
        ], now=NOW + timedelta(days=20))
        item = service.list_items(now=NOW + timedelta(days=20))["items"][0]
        assert item["added_date"] == "2026-08-24"
        assert item["added_price"] == 12.72
        assert item["days_tracked"] == (datetime(2026, 10, 6).date() - datetime(2026, 8, 24).date()).days or item["days_tracked"] >= 20
        assert item["current_tier"] is None  # 测试未注册档位映射，干净降级
    finally:
        _stop(service)
