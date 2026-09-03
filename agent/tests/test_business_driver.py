"""Business Driver Evidence extraction contracts (task §23)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from src.business_driver.parser import (
    extract_all,
    parse_customer_concentration,
    parse_product_volumes,
    parse_segment_tables,
)
from src.business_driver.profile import BusinessDriverProfileService
from src.business_driver.store import BusinessDriverEvidenceStore

# --- realistic PDF text snippets (from actual 600460 annual report) ---
_SEGMENT_TEXT = """
主营业务分产品情况
分产品 营业收入 营业成本 毛利率（%）营业收入比上年增减（%）营业成本比上年增减（%）毛利率比上年增减（%）
集成电路 4,923,542,448.73 3,368,867,617.40 31.58 19.93 18.42 增加 0.87 个百分点
分立器件产品 6,379,424,388.17 5,599,664,756.72 12.22 17.32 18.73 减少 1.04 个百分点
发光二极管产品 765,222,428.71 750,625,184.63 1.91 -0.41 4.50 增加 0.05 个百分点
"""

_REGION_TEXT = """
主营业务分地区情况
分地区 营业收入 营业成本 毛利率（%）营业收入比上年增减（%）
境内 12,556,945,746.52 10,125,375,467.65 19.36 17.08 16.95
境外 498,000,000.00 420,000,000.00 15.66 12.00 10.00
"""

_VOLUME_TEXT = """
产销量情况分析表
主要产品 单位 生产量 销售量 库存量 生产量比上年增减（%）销售量比上年增减（%）库存量比上年增减（%）
集成电路和分立器件5吋、6吋芯片 万片 277.10 277.10 30.70 17.84 17.84 -9.76
集成电路和分立器件8吋芯片 万片 68.02 68.02 17.74 4.02 4.02 4.60
发光二极管芯片 百万颗 505,516.83 507,028.03 143,904.18 -6.02 -0.05 7.45
"""

_CUSTOMER_TEXT = """
前五名客户销售额276,183.17万元，占年度销售总额21.16%；其中前五名客户销售额中关联方销售额32,848.77万元，占年度销售总额2.52%。
"""

_MOCK_DOC = {"document_id": "test_doc", "announcement_date": "2026-04-24", "source_hash": "test_sha"}


def _parse(text: str, parser_fn) -> list[dict[str, Any]]:
    return parser_fn(text, "600460.SH", _MOCK_DOC["document_id"],
                     _MOCK_DOC["announcement_date"], _MOCK_DOC["source_hash"])


# --- 1-3: segment table parsing ---
def test_segment_revenue_and_margin_parsed() -> None:
    results = _parse(_SEGMENT_TEXT, parse_segment_tables)
    by_name = {r["raw_name"]: r for r in results}
    assert "集成电路" in by_name
    ic = by_name["集成电路"]
    assert ic["revenue"] == pytest.approx(4_923_542_448.73)
    assert ic["gross_margin"] == pytest.approx(31.58)
    assert ic["revenue_yoy"] == pytest.approx(19.93)
    assert ic["evidence_type"] == "QUANTIFIED_FACT"
    assert "分立器件产品" in by_name
    assert by_name["分立器件产品"]["revenue"] == pytest.approx(6_379_424_388.17)


# --- 4: region parsing ---
def test_region_parsed() -> None:
    results = _parse(_REGION_TEXT, parse_segment_tables)
    regional = [r for r in results if r["dimension"] == "REGIONAL_MIX"]
    assert any(r["raw_name"] == "境内" for r in regional)


# --- 5: unit normalization (元/万元) ---
def test_unit_normalization_from_context() -> None:
    text = "单位：万元\n" + _SEGMENT_TEXT
    results = _parse(text, parse_segment_tables)
    ic = next(r for r in results if r["raw_name"] == "集成电路")
    # revenue stored in 元 regardless of declared unit
    assert ic["revenue"] == pytest.approx(4_923_542_448.73 * 1e4)


# --- 7: product volume parsing ---
def test_product_volume_parsed() -> None:
    results = _parse(_VOLUME_TEXT, parse_product_volumes)
    assert len(results) >= 2
    by_name = {r["raw_name"]: r for r in results}
    chip5 = next((v for k, v in by_name.items() if "6吋" in k), None)
    if chip5:
        assert chip5["production_volume"] == pytest.approx(277.10)
        assert chip5["sales_volume"] == pytest.approx(277.10)
        assert chip5["unit"] == "万片"


# --- 8: customer concentration ---
def test_customer_concentration_parsed() -> None:
    results = _parse(_CUSTOMER_TEXT, parse_customer_concentration)
    assert len(results) == 1
    c = results[0]
    assert c["customer_share"] == pytest.approx(21.16)
    assert c["value"] == pytest.approx(276_183_1700.0)  # 万元 → 元


# --- 10: idempotent fingerprint ---
def test_idempotent_fingerprint(tmp_path: Path) -> None:
    store = BusinessDriverEvidenceStore(tmp_path / "test.db")
    evidence = _parse(_SEGMENT_TEXT, parse_segment_tables)
    first = [store.save(e) for e in evidence]
    assert all(r["created"] for r in first)
    second = [store.save(e) for e in evidence]
    assert all(not r["created"] for r in second)  # all reused


# --- 12: canonical fallback (no forced mapping) ---
def test_canonical_fallback() -> None:
    results = _parse(_SEGMENT_TEXT, parse_segment_tables)
    for r in results:
        assert r["canonical_name"] == r["raw_name"]  # V1 identity mapping
        assert r["mapping_confidence"] == "HIGH"


# --- 14: missing fields remain null ---
def test_missing_fields_null() -> None:
    results = _parse(_VOLUME_TEXT, parse_product_volumes)
    for r in results:
        assert r.get("revenue") is None  # volume rows don't have revenue
        assert r.get("gross_margin") is None


# --- 15: no unsupported IGBT inference ---
def test_no_unsupported_igbt_inference() -> None:
    """IGBT is not in the table → must NOT appear in parsed evidence."""
    results = _parse(_SEGMENT_TEXT, parse_segment_tables)
    names = {r["raw_name"] for r in results}
    assert "IGBT" not in names
    # Also confirm actual products from table are present
    assert "集成电路" in names
    assert "分立器件产品" in names


# --- 16: service company → manufacturing fields not applicable ---
def test_service_company_no_volume_table() -> None:
    """A service company's report without 产销量 table → zero volume evidence."""
    service_text = "主营业务：餐饮服务\n前五名客户销售额100.00万元，占年度销售总额5.00%。"
    volumes = _parse(service_text, parse_product_volumes)
    assert volumes == []  # no volume table → empty
    customers = _parse(service_text, parse_customer_concentration)
    assert len(customers) == 1  # but customer data still works


# --- 17: parser failure doesn't corrupt ---
def test_parser_failure_returns_partial_not_wrong() -> None:
    garbage = "主营业务分产品情况\nrandom text without numbers\nmore garbage"
    results = _parse(garbage, parse_segment_tables)
    # Should return zero results (no false positives) rather than wrong data
    assert results == []


# --- 18: zero LLM (structural check: no LLM imports) ---
def test_parser_has_no_llm_import() -> None:
    """Structural check: the deterministic parser must not depend on any LLM runtime."""
    import src.business_driver.parser as parser_mod
    source = open(parser_mod.__file__, encoding="utf-8").read()
    assert "ChatLLM" not in source
    assert "ProviderModelRuntime" not in source
    assert "invoke" not in source


# --- 20: no trading language in evidence ---
def test_no_trading_language() -> None:
    results = _parse(_SEGMENT_TEXT, parse_segment_tables)
    trading = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|建仓")
    for r in results:
        assert not trading.search(str(r.get("source_text") or ""))


# --- profile service integration ---
def test_profile_service_aggregates(tmp_path: Path) -> None:
    store = BusinessDriverEvidenceStore(tmp_path / "test.db")
    for e in _parse(_SEGMENT_TEXT, parse_segment_tables):
        store.save(e)
    for e in _parse(_CUSTOMER_TEXT, parse_customer_concentration):
        store.save(e)
    profile = BusinessDriverProfileService(store).profile("600460.SH")
    assert profile["status"] == "READY"
    assert len(profile["products"]) >= 2
    assert profile["customer_concentration"]["top5_share"] == pytest.approx(21.16)


def test_profile_missing_returns_honest_message(tmp_path: Path) -> None:
    store = BusinessDriverEvidenceStore(tmp_path / "empty.db")
    profile = BusinessDriverProfileService(store).profile("999999.SH")
    assert profile["status"] == "MISSING"
    assert "尚未" in profile["boss_message"]
