from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.tdx_data.service import TdxDataService
from src.tdx_data.store import TdxDataStore


class FakeTdxClient:
    available = True

    def __init__(self, home: Path) -> None:
        self.home = home
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def call(self, method: str, *args, **kwargs):
        if method == "get_stock_list":
            market = args[0]
            values = {
                "5": [{"Code": "600519.SH", "Name": "贵州茅台"}, {"Code": "000001.SZ", "Name": "平安银行"}],
                "9": [{"Code": "999999.SH", "Name": "上证指数"}],
                "23": [{"Code": "600519.SH", "Name": "贵州茅台"}],
                "24": [], "25": [], "26": [], "27": [], "28": [],
                "30": [], "31": [], "32": [], "34": [],
            }
            return values.get(market, [])
        if method == "get_pricevol":
            return {
                code: {"LastClose": "10", "Now": "11" if code.startswith("6") else "9", "Volume": "100"}
                for code in args[0]
            }
        if method == "get_sector_list":
            return [{"Code": "880001.SH", "Name": "昨日涨停"}, {"Code": "881130.SH", "Name": "酿酒"}]
        if method == "get_stock_list_in_sector":
            return [{"Code": "600519.SH", "Name": "贵州茅台"}]
        if method == "formula_get_all":
            return [{"acCode": f"F{kwargs['formula_type']}", "acName": "测试公式", "isSys": 1}]
        if method == "get_trackzs_etf_info":
            return []
        if method == "get_stock_info":
            return {"Name": "贵州茅台", "J_zgb": "100", "J_jly": "50", "J_mgsy": "2"}
        if method == "get_more_info":
            return {"DynaPE": "15", "PB_MRQ": "3", "MainBusiness": "白酒"}
        if method == "formula_process_mul":
            return {
                code: {"UP": [{"Date": "20260812", "Value": "1" if code.startswith("6") else "0"}]}
                for code in kwargs["stock_list"]
            } | {"ErrorId": "0"}
        raise AssertionError(f"unexpected fake call: {method} {args} {kwargs}")


class EmptyQuoteClient(FakeTdxClient):
    def call(self, method: str, *args, **kwargs):
        if method == "get_pricevol":
            return {}
        return super().call(method, *args, **kwargs)


class PartialQuoteClient(FakeTdxClient):
    def call(self, method: str, *args, **kwargs):
        if method == "get_pricevol":
            code = args[0][0]
            return {code: {"LastClose": "10", "Now": "11", "Volume": "100"}}
        return super().call(method, *args, **kwargs)


class ZeroQuoteClient(FakeTdxClient):
    def call(self, method: str, *args, **kwargs):
        if method == "get_pricevol":
            return {code: {"LastClose": "10", "Now": "0", "Volume": "0"} for code in args[0]}
        return super().call(method, *args, **kwargs)


class PartialFundamentalClient(FakeTdxClient):
    def call(self, method: str, *args, **kwargs):
        if method in {"get_stock_info", "get_more_info"} and args[0] == "000001.SZ":
            return {}
        return super().call(method, *args, **kwargs)


def make_service(tmp_path: Path) -> TdxDataService:
    (tmp_path / "vipdoc" / "cw").mkdir(parents=True)
    return TdxDataService(TdxDataStore(tmp_path / "tdx.db"), FakeTdxClient(tmp_path))


def test_quote_update_caches_full_market_and_breadth(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    result = service._collect_quote(lambda *_: None)
    assert result["item_count"] == 2
    assert result["metadata"] == {
        "securities": 2,
        "valid_quotes": 2,
        "up": 1,
        "down": 1,
        "flat": 0,
        "median_change_pct": 10.0,
        "up_down_ratio": 1.0,
    }
    quotes = service.store.list_records("quotes", limit=10)
    assert quotes["total"] == 2
    assert {item["payload"]["code"] for item in quotes["items"]} == {"600519.SH", "000001.SZ"}


def test_empty_quote_refresh_never_overwrites_last_successful_cache(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")
    service = TdxDataService(store, FakeTdxClient(tmp_path))
    service._collect_quote(lambda *_: None)
    before = {item["key"]: item["payload"] for item in store.list_records("quotes", limit=10)["items"]}
    service.client = EmptyQuoteClient(tmp_path)
    try:
        service._collect_quote(lambda *_: None)
        raise AssertionError("empty refresh should fail")
    except RuntimeError as exc:
        assert "拒绝覆盖" in str(exc)
    after = {item["key"]: item["payload"] for item in store.list_records("quotes", limit=10)["items"]}
    assert after == before


def test_partial_quote_refresh_below_coverage_threshold_is_rejected(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")
    service = TdxDataService(store, FakeTdxClient(tmp_path))
    service._collect_quote(lambda *_: None)
    service.client = PartialQuoteClient(tmp_path)
    try:
        service._collect_quote(lambda *_: None)
        raise AssertionError("partial refresh should fail")
    except RuntimeError as exc:
        assert "1/2" in str(exc)
    assert store.count("quotes") == 2


def test_preopen_zero_prices_are_missing_and_do_not_replace_cache(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")
    service = TdxDataService(store, FakeTdxClient(tmp_path))
    service._collect_quote(lambda *_: None)
    service.client = ZeroQuoteClient(tmp_path)
    with pytest.raises(RuntimeError, match="0/2"):
        service._collect_quote(lambda *_: None)
    quotes = store.list_records("quotes", limit=10)["items"]
    assert all(item["payload"]["price"] > 0 for item in quotes)


def test_rank_and_formula_modules_are_independently_refreshable(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service._collect_quote(lambda *_: None)
    ranks = service._collect_rank(lambda *_: None)
    formulas = service._collect_formula(lambda *_: None)
    assert ranks["metadata"]["涨幅榜"] == 2
    assert ranks["metadata"]["昨日涨停"] == 1
    assert formulas["metadata"] == {"技术指标": 1, "条件选股": 1, "专家系统": 1, "K线形态": 1}


def test_background_job_tracks_progress_and_keeps_module_state(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    job = service.start_update("quote")
    deadline = time.time() + 3
    while time.time() < deadline:
        current = service.store.get_job(job["id"])
        if current and current["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    assert current and current["status"] == "completed"
    state = {row["module"]: row for row in service.store.module_states()}["quote"]
    assert state["status"] == "ready"
    assert state["item_count"] == 2


def test_fundamental_records_include_normalized_fields_and_raw_payload(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service._collect_quote(lambda *_: None)
    result = service._collect_fundamental(lambda *_: None)
    assert result["item_count"] == 2
    record = service.store.get_record("fundamentals", "600519.SH")
    assert record is not None
    assert record["payload"]["net_profit_10k"] == 50.0
    assert record["payload"]["pe_dynamic"] == 15.0
    assert record["payload"]["main_business"] == "白酒"
    assert record["payload"]["base_raw"]["J_mgsy"] == "2"
    assert result["metadata"]["coverage_pct"] == 100.0


def test_partial_fundamental_refresh_never_overwrites_complete_cache(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service._collect_quote(lambda *_: None)
    service._collect_fundamental(lambda *_: None)
    service.client = PartialFundamentalClient(tmp_path)

    with pytest.raises(RuntimeError, match="1/2"):
        service._collect_fundamental(lambda *_: None)

    assert service.store.count("fundamentals") == 2


def test_typed_market_overview_and_ranks_aggregate_cached_data(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service._collect_quote(lambda *_: None)
    service._collect_rank(lambda *_: None)
    overview = service.market_overview()
    assert overview["breadth"] == {
        "valid": 2, "up": 1, "down": 1, "flat": 0,
        "up_down_ratio": 1.0, "median_change_pct": 0.0,
    }
    gainers = service.market_ranks(limit=1)
    assert gainers["total"] == 2
    assert gainers["items"][0]["code"] == "600519.SH"
    assert gainers["coverage"]["fundamental_pct"] == 0


def test_screener_excludes_missing_finance_instead_of_using_zero(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service._collect_quote(lambda *_: None)
    service.store.upsert_records("fundamentals", [{
        "key": "600519.SH", "name": "Kweichow Moutai",
        "payload": {"code": "600519.SH", "name": "Kweichow Moutai", "pe_ttm": 20.0, "base_raw": {}},
    }])
    result = service.screener({"max_pe": 30, "limit": 50, "offset": 0})
    assert result["total"] == 1
    assert result["items"][0]["code"] == "600519.SH"
    assert result["coverage"] == {"quotes": 2, "fundamentals": 1}


def test_formula_scan_runs_in_background_and_persists_hits(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service._collect_quote(lambda *_: None)
    scan = service.start_formula_scan({
        "formula_type": 1, "formula_code": "UPN", "formula_args": "3",
        "universe": "all", "period": "1d",
    })
    deadline = time.time() + 3
    while time.time() < deadline:
        current = service.store.get_formula_scan(scan["id"])
        if current and current["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    assert current and current["status"] == "completed"
    assert current["progress"] == current["total"] == 2
    assert [item["code"] for item in current["results"]] == ["600519.SH"]
