"""Deterministic parsers for standard A-share annual report tables.

Reads the already-extracted text from ``company_disclosure_documents.text_path``
and produces structured ``BusinessDriverEvidence`` rows.  Zero LLM calls.

Supported table types (task §6-§9):
- SEGMENT_REVENUE / SEGMENT_MARGIN: 主营业务分行业/分产品/分地区情况
- PRODUCT_VOLUME: 产销量情况分析表
- CUSTOMER: 前五名客户销售额/占比
- CAPEX_PROJECT: 在建工程/募投项目（基础事实）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.business_driver.store import BUSINESS_DRIVER_EVIDENCE_VERSION

# --- number patterns ---
_NUM = r'([\d,]+\.\d{2})'
_SIGNED = r'(-?[\d,]+\.\d{2})'

# --- table header markers ---
_SEGMENT_HEADERS = ("主营业务分行业情况", "主营业务分产品情况", "主营业务分地区情况")
_VOLUME_HEADER = "产销量情况分析表"
_CUSTOMER_PATTERN = re.compile(
    r'前五名客户销售额([\d,]+\.\d{2})万元[，,]\s*占(?:年度|报告期)销售(?:总)?(?:收入|额)(?:总额)?'
    r'(?:比例)?(?:为)?([\d.]+)%')
_CAPEX_IN_BUILDING = re.compile(r'在建工程\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})')

# --- row pattern: name + revenue + cost + margin + rev_yoy + cost_yoy ---
_SEGMENT_ROW = re.compile(
    r'([\u4e00-\u9fff][\u4e00-\u9fff\s\d]*?)\s+'
    + _NUM + r'\s+' + _NUM + r'\s+'
    + r'(\d+\.\d{2})\s+'
    + _SIGNED + r'\s+' + _SIGNED,
)

# --- volume row: product + unit + prod + sales + inv + 3 yoy ---
_VOLUME_ROW = re.compile(
    r'((?:[\u4e00-\u9fff\d]\s?)*?(?:芯片|管芯片|器件|模块|电路))\s+'
    r'((?:百万颗|万颗|万片|片|颗))\s+'
    + _NUM + r'\s+' + _NUM + r'\s+' + _NUM + r'\s+'
    + _SIGNED + r'\s+' + _SIGNED + r'\s+' + _SIGNED,
)

# noise words to strip from extracted names
_NOISE = re.compile(r'^(?:个\s*百分点|增加|减少|合计|其中\s*[:：])\s*')
_UNIT_NORMALIZERS = {"元": 1.0, "万元": 1e4, "百万元": 1e6, "亿元": 1e8}


def _clean_name(raw: str) -> str:
    """Strip PDF line-break artifacts from product/segment names."""
    name = _NOISE.sub("", raw.strip())
    name = re.sub(r'\s+', '', name)  # remove all internal whitespace
    return name.strip()


def _to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except (ValueError, TypeError):
        return None


def _detect_unit(text: str) -> str:
    """Find the unit declaration near a table (单位：元/万元/亿元)."""
    match = re.search(r'单位[：:]\s*(亿元|万元|百万元|元)', text)
    return match.group(1) if match else "元"


def parse_segment_tables(text: str, stock_code: str, document_id: str,
                          announcement_date: str, source_hash: str) -> list[dict[str, Any]]:
    """Parse 主营业务分行业/分产品/分地区 tables → SEGMENT_REVENUE + SEGMENT_MARGIN evidence."""
    results: list[dict[str, Any]] = []
    for header in _SEGMENT_HEADERS:
        idx = text.find(header)
        if idx < 0:
            continue
        chunk = text[idx:idx + 3000]
        # Collapse line breaks so rows are single-line
        cleaned = re.sub(r'\n\s*', ' ', chunk)
        # Detect unit from the surrounding context
        context = text[max(0, idx - 500):idx + 500]
        unit = _detect_unit(context)
        unit_scale = _UNIT_NORMALIZERS.get(unit, 1.0)
        dimension = "SEGMENT_REVENUE" if "分产品" in header or "分行业" in header else "REGIONAL_MIX"
        sub_type = "industry" if "分行业" in header else ("product" if "分产品" in header else "region")

        seen_names: set[str] = set()
        for m in _SEGMENT_ROW.finditer(cleaned):
            name = _clean_name(m.group(1))
            if not name or name in seen_names or len(name) < 2:
                continue
            if name in ("适用", "不适用", "上表中"):
                continue
            seen_names.add(name)
            revenue = _to_float(m.group(2))
            cost = _to_float(m.group(3))
            margin = _to_float(m.group(4))
            rev_yoy = _to_float(m.group(5))
            _cost_yoy = _to_float(m.group(6))  # noqa: F841 — stored for future margin bridge
            if revenue is None or revenue <= 0:
                continue
            results.append({
                "stock_code": stock_code,
                "dimension": dimension,
                "fact_key": f"{sub_type}:{name}",
                "raw_name": name,
                "canonical_name": name,  # V1: identity mapping
                "mapping_confidence": "HIGH",  # exact name from table
                "period": announcement_date[:7] if announcement_date else None,
                "announcement_date": announcement_date,
                "evidence_type": "QUANTIFIED_FACT",
                "revenue": revenue * unit_scale if revenue else None,
                "cost": cost * unit_scale if cost else None,
                "gross_margin": margin,
                "revenue_yoy": rev_yoy,
                "unit": unit,
                "source_document_id": document_id,
                "source_text": m.group(0)[:200],
                "source_hash": source_hash,
                "comparability_status": "COMPARABLE",  # same table structure
                "extractor_version": BUSINESS_DRIVER_EVIDENCE_VERSION,
                # store gross_margin_yoy as string since format varies
                "gross_margin_yoy": f"{sub_type}_table",
            })
    return results


def parse_product_volumes(text: str, stock_code: str, document_id: str,
                           announcement_date: str, source_hash: str) -> list[dict[str, Any]]:
    """Parse 产销量情况分析表 → PRODUCT_VOLUME evidence."""
    results: list[dict[str, Any]] = []
    idx = text.find(_VOLUME_HEADER)
    if idx < 0:
        return results
    chunk = text[idx:idx + 2000]
    cleaned = re.sub(r'\n\s*', ' ', chunk)
    for m in _VOLUME_ROW.finditer(cleaned):
        name = _clean_name(m.group(1))
        unit = m.group(2)
        prod_vol = _to_float(m.group(3))
        sales_vol = _to_float(m.group(4))
        inv_vol = _to_float(m.group(5))
        if not name or prod_vol is None or prod_vol <= 0:
            continue
        results.append({
            "stock_code": stock_code,
            "dimension": "PRODUCT_VOLUME",
            "fact_key": f"volume:{name}",
            "raw_name": name,
            "canonical_name": name,
            "mapping_confidence": "HIGH",
            "period": announcement_date[:7] if announcement_date else None,
            "announcement_date": announcement_date,
            "evidence_type": "QUANTIFIED_FACT",
            "production_volume": prod_vol,
            "sales_volume": sales_vol,
            "inventory_volume": inv_vol,
            "unit": unit,
            "source_document_id": document_id,
            "source_text": m.group(0)[:200],
            "source_hash": source_hash,
            "comparability_status": "COMPARABLE",
            "extractor_version": BUSINESS_DRIVER_EVIDENCE_VERSION,
        })
    return results


def parse_customer_concentration(text: str, stock_code: str, document_id: str,
                                  announcement_date: str, source_hash: str) -> list[dict[str, Any]]:
    """Parse 前五名客户销售额/占比 → CUSTOMER evidence."""
    results: list[dict[str, Any]] = []
    for m in _CUSTOMER_PATTERN.finditer(text):
        sales_wan = _to_float(m.group(1))
        share = _to_float(m.group(2))
        if sales_wan is None or share is None:
            continue
        results.append({
            "stock_code": stock_code,
            "dimension": "CUSTOMER",
            "fact_key": "top5_customer_share",
            "period": announcement_date[:7] if announcement_date else None,
            "announcement_date": announcement_date,
            "evidence_type": "QUANTIFIED_FACT",
            "value": sales_wan * 1e4,  # convert 万元 → 元
            "value_secondary": share,
            "customer_share": share,
            "unit": "元",
            "source_document_id": document_id,
            "source_text": m.group(0)[:200],
            "source_hash": source_hash,
            "comparability_status": "COMPARABLE",
            "extractor_version": BUSINESS_DRIVER_EVIDENCE_VERSION,
        })
    return results


def parse_capex_in_building(text: str, stock_code: str, document_id: str,
                             announcement_date: str, source_hash: str) -> list[dict[str, Any]]:
    """Parse 在建工程 balance-sheet line → CAPEX_PROJECT evidence (basic fact)."""
    results: list[dict[str, Any]] = []
    for m in _CAPEX_IN_BUILDING.finditer(text):
        current = _to_float(m.group(1))
        prior = _to_float(m.group(2))
        if current is None or current <= 0:
            continue
        results.append({
            "stock_code": stock_code,
            "dimension": "CAPEX_PROJECT",
            "fact_key": "in_building_assets",
            "period": announcement_date[:7] if announcement_date else None,
            "announcement_date": announcement_date,
            "evidence_type": "QUANTIFIED_FACT",
            "value": current,
            "value_secondary": prior,
            "unit": "元",
            "project_name": "在建工程（合计）",
            "source_document_id": document_id,
            "source_text": m.group(0)[:200],
            "source_hash": source_hash,
            "comparability_status": "COMPARABLE",
            "extractor_version": BUSINESS_DRIVER_EVIDENCE_VERSION,
        })
        break  # only take the first (balance sheet) occurrence
    return results


def _normalize_code(code: str) -> str:
    """Ensure .SH/.SZ suffix so disclosure bare codes match CIO codes."""
    code = code.strip().upper()
    if "." not in code and len(code) == 6 and code.isdigit():
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"
    return code


def extract_all(text_path: str, stock_code: str, document_id: str,
                announcement_date: str, source_hash: str) -> list[dict[str, Any]]:
    """Run all four parsers on one document's text file."""
    code = _normalize_code(stock_code)
    text = Path(text_path).read_text(encoding="utf-8", errors="replace")
    results: list[dict[str, Any]] = []
    results.extend(parse_segment_tables(text, code, document_id, announcement_date, source_hash))
    results.extend(parse_product_volumes(text, code, document_id, announcement_date, source_hash))
    results.extend(parse_customer_concentration(text, code, document_id, announcement_date, source_hash))
    results.extend(parse_capex_in_building(text, code, document_id, announcement_date, source_hash))
    return results
