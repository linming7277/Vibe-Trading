"""Small normalization helpers used by the L3 industry catalog."""

from __future__ import annotations

import hashlib
import json
from typing import Any

BUSINESS_FIELDS = ("business_scope", "main_business", "company_description", "main_products")


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def business_text(profile: dict[str, Any]) -> str:
    return "\n".join(
        str(profile.get(field) or "").strip()
        for field in BUSINESS_FIELDS
        if str(profile.get(field) or "").strip()
    )
