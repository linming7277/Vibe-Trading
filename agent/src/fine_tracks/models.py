"""Stable contracts and normalization helpers for Fine Track V2."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

TRACK_CLASSIFICATION_VERSION = "fine-track-v2.0.0"
DATABASE_TRACK_CLASSIFICATION_VERSION = "fine-track-db-v2.1.0"

TDX_TERMINAL_INDUSTRY_SOURCE = {
    "source_id": "tdx-research-industry-terminal-v2",
    "raw_source_name": "通达信研究行业三级（TQ 分组 18）",
    "product_display_name": "通达信末级行业（三级口径）",
    "industry_level": "TDX_RESEARCH_TERMINAL",
    "raw_industry_level": "TDX_RESEARCH_LEVEL_3_OR_LEVEL_2_LEAF",
    "code_pattern": "881xxx.SH",
    "hierarchy": {"level1_group": "16", "level2_group": "17", "terminal_group": "18"},
}

# Kept as an import-compatible alias for the initial Fine Track V1 module.
V1_THIRD_LEVEL_INDUSTRY_SOURCE = TDX_TERMINAL_INDUSTRY_SOURCE

BUSINESS_FIELDS = ("business_scope", "main_business", "company_description", "main_products")


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_track_name(value: str) -> str:
    """Normalize typography while retaining a readable Chinese display name."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[，,。；;：:、/\\|]+$", "", text)
    return text


def track_semantic_key(value: str) -> str:
    """Small V1 duplicate key; intentionally not a general-purpose ontology."""
    text = canonical_track_name(value).casefold()
    text = text.replace("半导体制造设备", "半导体设备")
    text = text.replace("制造装备", "设备").replace("制造设备", "设备")
    text = text.replace("相关业务", "").replace("业务", "")
    return text


def canonical_track_name(value: str) -> str:
    text = normalize_track_name(value)
    aliases = {
        "乳品": "乳制品", "乳品系列": "乳制品", "液态奶": "液体乳", "液态乳": "液体乳",
    }
    text = aliases.get(text, text)
    for suffix in ("相关产品", "产品系列", "产品", "系列"):
        if text.endswith(suffix) and len(text) - len(suffix) >= 2:
            text = text[:-len(suffix)]
            break
    return aliases.get(text, text)


def confidence_level(value: float) -> str:
    return "HIGH" if value >= .8 else "MEDIUM" if value >= .6 else "LOW"


def review_status(value: float) -> str:
    return "AUTO_ACCEPTED" if value >= .8 else "NEEDS_REVIEW"


def business_text(profile: dict[str, Any]) -> str:
    return "\n".join(str(profile.get(field) or "").strip() for field in BUSINESS_FIELDS if str(profile.get(field) or "").strip())
