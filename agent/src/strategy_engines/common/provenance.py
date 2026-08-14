"""Formula-version and provenance helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def idempotency_key(strategy_line: str, market: str, as_of: str, symbols: list[str] | None, formula_version: str) -> str:
    return stable_fingerprint({
        "strategy_line": strategy_line,
        "market": market,
        "as_of": as_of,
        "symbols": sorted(symbols or []),
        "formula_version": formula_version,
    })
