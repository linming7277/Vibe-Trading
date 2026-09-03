"""Deterministic watchpoint merge.  No embeddings, no LLM."""

from __future__ import annotations

from typing import Any

from .contracts import SOURCE_RANK, THEME_TITLES


def _origin_rank(item: dict[str, Any]) -> int:
    origin = str(item.get("origin") or item.get("source_module") or "")
    if origin in SOURCE_RANK:
        return SOURCE_RANK[origin]
    category = str(item.get("category") or "")
    if category == "RISK" and item.get("importance_tier") == "HIGH":
        return SOURCE_RANK["RISK_HIGH"]
    if category == "RISK":
        return SOURCE_RANK["RISK_MEDIUM"]
    if category == "THESIS" and not item.get("generic"):
        return SOURCE_RANK["THESIS"]
    return SOURCE_RANK.get(category, 9)


def _tier_rank(item: dict[str, Any]) -> int:
    return {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}.get(str(item.get("importance_tier") or "LOW"), 4)


def merge_watchpoints(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = str(item.get("semantic_key") or f"{item.get('category')}:{item.get('title')}")
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = dict(item)
            buckets[key]["source_refs"] = list(item.get("source_refs") or [])
            buckets[key]["submetrics"] = list(item.get("submetrics") or [])
            order.append(key)
            continue
        winner, loser = (item, existing) if _origin_rank(item) < _origin_rank(existing) else (existing, item)
        if _origin_rank(item) == _origin_rank(existing) and _tier_rank(item) < _tier_rank(existing):
            winner, loser = item, existing
        merged = dict(winner)
        refs = list(winner.get("source_refs") or [])
        for ref in loser.get("source_refs") or []:
            if ref not in refs:
                refs.append(ref)
        merged["source_refs"] = refs
        if loser.get("current_state") and loser.get("current_state") not in str(merged.get("current_state") or ""):
            merged["current_state"] = f"{merged.get('current_state') or ''}；{loser['current_state']}".strip("；")
        cautions = list(merged.get("cautions") or [])
        for caution in loser.get("cautions") or []:
            if caution not in cautions:
                cautions.append(caution)
        merged["cautions"] = cautions
        if _tier_rank(loser) < _tier_rank(merged):
            merged["importance_tier"] = loser["importance_tier"]
        submetrics = list(winner.get("submetrics") or [])
        for metric in loser.get("submetrics") or []:
            if metric not in submetrics:
                submetrics.append(metric)
        merged["submetrics"] = submetrics
        # A theme bucket that absorbed a second metric is no longer described
        # by the winning metric's title alone.
        theme = str(merged.get("theme") or "")
        if len(submetrics) > 1 and theme in THEME_TITLES:
            merged["title"] = THEME_TITLES[theme]
        buckets[key] = merged
    return [buckets[key] for key in order]
