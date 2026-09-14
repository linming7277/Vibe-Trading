"""PBOC 社融增量免费表抓取：解析规则、防接错表、失败降级、缺失判定。

全部用 mock HTML，不打外网。规则：只认「社会融资规模增量统计表」；
月列形如 2026.07；空单元格（未来月份）丢弃、绝不写 0；HTTP 失败返回
空列表且不阻断其它宏观序列；存量表/万亿元页面一律 0 条。
"""

from __future__ import annotations

import pytest

from src.strategy_engines import macro_data as macro_data_module
from src.strategy_engines.macro_data import MacroDataService
from src.strategy_engines.value_data_store import ValueDataStore

DIRECTORY_HTML = (
    "<html><body><ul>"
    '<li><a href="/diaochatongjisi/attachDir/2026/08/2026081417010772070.htm">'
    "社会融资规模存量统计表</a> 2026-08-14</li>"
    '<li><a href="/diaochatongjisi/attachDir/2026/08/2026081417010772070.htm">'
    "社会融资规模增量统计表</a> 2026-08-14</li>"
    "</ul></body></html>"
)
FLOW_HTM = (
    "<html><head><title>社会融资规模增量统计表</title></head><body>"
    "<table><tr><th>项目</th><th>社会融资规模增量(亿元)</th></tr>"
    "<tr><td>2026.06</td><td>41,988</td></tr>"
    "<tr><td>2026.07</td><td>14,017</td></tr>"
    "<tr><td>2026.08</td><td>&nbsp;</td></tr>"
    "</table></body></html>"
)
STOCK_HTM = (
    "<html><head><title>社会融资规模存量统计表</title></head><body>"
    "<table><tr><th>项目</th><th>社会融资规模存量(万亿元)</th></tr>"
    "<tr><td>2026.07</td><td>421.6</td></tr></table></body></html>"
)


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeClient:
    def __init__(self, pages: dict[str, str], *, fail: bool = False) -> None:
        self.pages = pages
        self.fail = fail
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        if self.fail:
            raise OSError("network unavailable")
        return FakeResponse(self.pages.get(url, ""))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _install(monkeypatch, pages: dict[str, str], *, fail: bool = False) -> FakeClient:
    client = FakeClient(pages, fail=fail)
    monkeypatch.setattr(
        macro_data_module, "direct_domestic_http_client",
        lambda **kwargs: client,
    )
    return client


def test_parses_flow_table_and_skips_blank_future_month(tmp_path, monkeypatch) -> None:
    _install(monkeypatch, {
        "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/5570903/5570885/index.html": DIRECTORY_HTML,
        "https://www.pbc.gov.cn/diaochatongjisi/attachDir/2026/08/2026081417010772070.htm": FLOW_HTM,
    })
    records = MacroDataService._fetch_pbc_social_financing_flow()
    assert len(records) == 2  # 2026.06 与 2026.07；2026.08 空月丢弃
    july = next(r for r in records if r["observation_date"] == "2026-07-01")
    assert july["value"] == 14017.0
    assert july["unit"] == "亿元" and july["source"] == "PBOC"
    assert july["release_date"] == "2026-08-14"  # 目录页日期
    assert "2026081417010772070.htm" in july["source_url"]


def test_stock_table_page_yields_zero_records(tmp_path, monkeypatch) -> None:
    # 目录只链到「存量统计表」→ 增量链接不存在 → 0 条
    stock_dir = DIRECTORY_HTML.replace("社会融资规模增量统计表", "社会融资规模存量统计表")
    _install(monkeypatch, {
        "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/5570903/5570885/index.html": stock_dir,
    })
    assert MacroDataService._fetch_pbc_social_financing_flow() == []
    # 目录给了链接但页面其实是存量/万亿元口径 → 同样 0 条
    stock_dir2 = DIRECTORY_HTML.replace(
        "/diaochatongjisi/attachDir/2026/08/2026081417010772070.htm",
        "/diaochatongjisi/attachDir/2026/08/stock.htm")
    _install(monkeypatch, {
        "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/5570903/5570885/index.html": stock_dir2,
        "https://www.pbc.gov.cn/diaochatongjisi/attachDir/2026/08/stock.htm": STOCK_HTM,
    })
    assert MacroDataService._fetch_pbc_social_financing_flow() == []


def test_http_failure_returns_empty_and_other_series_unaffected(tmp_path, monkeypatch) -> None:
    _install(monkeypatch, {}, fail=True)
    assert MacroDataService._fetch_pbc_social_financing_flow() == []
    # refresh 隔离：社融 error row 不阻断其它宏观序列入库
    db = tmp_path / "research.db"
    store = ValueDataStore(db)
    rows = [
        {"series_id": "m2_yoy", "axis": "liquidity", "higher_good": True,
         "observation_date": "2026-08-01", "release_date": "2026-09-01",
         "vintage_id": "v1", "value": 8.0, "unit": "%", "source": "test",
         "source_url": "", "release_status": "first_observed_only", "fetched_at": "2026-09-01"},
        {"error": "network down", "series_id": "social_financing_increment",
         "axis": "credit", "source": "PBOC"},
    ]
    service = MacroDataService(store=store, provider=lambda: rows)
    result = service.refresh("2026-09-11")
    assert result["status"] == "partial"
    kept = store.macro_series_as_of("2026-09-11")
    assert any(r["series_id"] == "m2_yoy" for r in kept)
    assert not any(r["series_id"] == "social_financing_increment" for r in kept)


def test_environment_missing_excludes_social_when_series_present(tmp_path, monkeypatch) -> None:
    from src.macro_line import freshness as freshness_module

    def _seed(rows):
        store = ValueDataStore(tmp_path / "research.db")
        store.replace_macro_series({"social_financing_increment"}, rows)
        return store

    fresh_rows = [{
        "series_id": "social_financing_increment", "axis": "credit", "higher_good": True,
        "observation_date": "2026-09-08", "release_date": "2026-09-10",
        "vintage_id": "v1", "value": 14017.0, "unit": "亿元", "source": "PBOC",
        "source_url": "", "release_status": "first_observed_only", "fetched_at": "2026-09-10",
    }]
    monkeypatch.setattr(freshness_module, "ValueDataStore", lambda path=None: _seed(fresh_rows))
    labels = [str(x) for x in (
        freshness_module.check_macro_source_freshness(as_of="2026-09-11").get("missing_series_labels") or [])]
    assert "社融增量" not in labels

    # 对照：序列为空时缺失标签必须出现
    monkeypatch.setattr(freshness_module, "ValueDataStore", lambda path=None: _seed([]))
    labels_empty = [str(x) for x in (
        freshness_module.check_macro_source_freshness(as_of="2026-09-11").get("missing_series_labels") or [])]
    assert "社融增量" in labels_empty
