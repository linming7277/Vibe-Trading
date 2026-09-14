"""CIO 报告分块契约与按数据新鲜度的块级刷新（第一期：PRICE）。

块（block）= 报告的一组 section。PRICE 块只依赖行情/价格区输入，
刷新是纯确定性重建——**绝不调用业务研究或全文 synthesis LLM**。
其它块（FINANCIAL/BUSINESS/RISK/THESIS）本轮仅登记契约（phase=0），
不批量重算；`block_fingerprints` 里的指纹只用于「是否过期」标记。

指纹约定：块指纹 = sha256(确定性 input_keys 规范 JSON) 截 16 位，
存放在对应 section 的 `structured_payload["block_fingerprints"][block_id]`
与报告行 `valuation_hash` 列（PRICE→valuation）。两处都不存在时视为
「未登记」，首次块刷新会补登记。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 块契约（代码即规格）。phase: 1=已接通刷新；0=仅登记指纹与过期标记。
CIO_BLOCK_CONTRACT: dict[str, dict[str, Any]] = {
    "PRICE": {
        "phase": 1,
        "section_types": ("valuation",),
        "input_keys": ("code", "close_as_of", "close",
                       "zone_low", "zone_high", "position_label"),
        "hash_column": "valuation_hash",
        "source": "ValuePriceZoneService.get_price_zones（确定性，无 LLM）",
    },
    "FINANCIAL": {
        "phase": 0,
        "section_types": ("financial_path", "latest_quarter", "normalized_earnings"),
        "input_keys": ("code", "financial_source_hash", "report_period"),
        "hash_column": "financial_hash",
    },
    "BUSINESS": {
        "phase": 0,
        "section_types": ("business_structure", "company_position"),
        "input_keys": ("code", "business_snapshot_hash"),
        "hash_column": "business_hash",
    },
    "RISK": {
        "phase": 0,
        "section_types": ("quality_risk", "hidden_signals"),
        "input_keys": ("code", "risk_snapshot_hash"),
        "hash_column": "risk_hash",
    },
    "THESIS": {
        "phase": 0,
        "section_types": ("thesis_watchpoints",),
        "input_keys": ("code", "thesis_updated_at"),
        "hash_column": "thesis_hash",
    },
}

MISSING_SECTIONS_NOTE = "（本节暂缺：PRICE 块先行刷新，全文重建时补齐）"


def block_fingerprint(code: str, inputs: dict[str, Any], *, block_id: str = "PRICE") -> str:
    """对契约 input_keys 做稳定哈希（缺键记 None，键序规范化）。"""
    contract = CIO_BLOCK_CONTRACT[block_id]
    payload = {
        "block": block_id,
        "code": str(code or "").upper(),
        **{key: inputs.get(key) for key in contract["input_keys"]},
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def stored_block_fingerprint(section: dict[str, Any] | None, block_id: str = "PRICE") -> str | None:
    """读取节里登记过的块指纹；未登记返回 None。"""
    payload = (section or {}).get("structured_payload") or {}
    fingerprints = payload.get("block_fingerprints") if isinstance(payload, dict) else None
    value = (fingerprints or {}).get(block_id) if isinstance(fingerprints, dict) else None
    return str(value) if value else None


def price_block_inputs(zone: dict[str, Any], *, code: str) -> dict[str, Any]:
    """从 ValuePriceZoneService.get_price_zones 输出提取 PRICE 契约输入。"""
    confluence = [z for z in (zone.get("confluence_zones") or []) if isinstance(z, dict)]
    focus_zone = confluence[0] if confluence else {}
    return {
        "code": str(code or "").upper(),
        "close_as_of": str(zone.get("price_as_of") or zone.get("as_of") or "")[:10],
        "close": zone.get("current_price"),
        "zone_low": focus_zone.get("low"),
        "zone_high": focus_zone.get("high"),
        "position_label": zone.get("position_label"),
    }


def leader_pool_codes() -> list[str]:
    """龙头池成员（l3_leader_pool 最新 COMPLETED as_of），去重升序。"""
    from src.level3_leaders.store import Level3LeaderStore

    store = Level3LeaderStore()
    try:
        pool = store.current_pool()
        if not pool:
            return []
        members = store.get_pool(str(pool["id"])) or {}
        rows = members.get("members") or []
    finally:
        store.close()
    codes = sorted({str(m.get("stock_code") or "").upper() for m in rows if m.get("stock_code")})
    return codes
