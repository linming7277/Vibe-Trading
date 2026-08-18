"""Reproduce the Value Line V2 Leader / Research Universe V1 audit.

Run only against an isolated ``VIBE_TRADING_HOME``.  It reads the V2 score
cache, materializes an isolated workbench snapshot, then emits JSON evidence
for the Markdown audit report.  It intentionally makes no changes to product
source code or production data.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any

from src.strategy_engines.value_line import LEADER_DATASET, get_value_line_service
from src.value_workspace.service import ValueWorkspaceService


SAMPLE_TRACKS = {
    "证券": "881394.SH",
    "贵金属": "881075.SH",
    "工业金属": "881071.SH",
    "医疗服务": "881247.SH",
    "全国性银行": "881386.SH",
    "半导体": "881319.SH",
    "调味品": "881139.SH",
}


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def score_stats(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    scores = [number(item.get("score")) for item in rows]
    values = [item for item in scores if item is not None]
    return {
        "scored": len(values),
        "top1": rounded(values[0]) if values else None,
        "top3_mean": rounded(mean(values[:3])) if len(values) >= 3 else None,
        "top5_mean": rounded(mean(values[:5])) if len(values) >= 5 else None,
        "median": rounded(median(values)) if values else None,
    }


def compact_leader(row: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    result = {
        "rank": row.get("rank"),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "leader_score": row.get("score"),
        "coverage": row.get("coverage"),
        "status": row.get("status"),
        "components": row.get("component_scores"),
        "missing_fields": row.get("missing_fields"),
    }
    if include_raw:
        result["raw_features"] = row.get("raw_features")
        result["normalized_features"] = row.get("normalized_features")
        result["growth_status"] = row.get("growth_status")
    return result


def concentration(counts: Counter[str], total: int) -> dict[str, Any]:
    shares = sorted((count / total for count in counts.values()), reverse=True) if total else []
    return {
        "industry_count": len(counts),
        "max_industry_share": rounded(shares[0]) if shares else 0,
        "top3_industry_share": rounded(sum(shares[:3])) if shares else 0,
        "hhi": rounded(sum(share * share for share in shares)) if shares else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default="2026-08-14")
    parser.add_argument("--candidate-limit", type=int, default=10)
    parser.add_argument("--leader-limit", type=int, default=5)
    args = parser.parse_args()
    if not os.environ.get("VIBE_TRADING_HOME"):
        raise SystemExit("VIBE_TRADING_HOME must point to an isolated runtime")

    value_line = get_value_line_service()
    sectors = list(value_line.sectors(args.as_of).get("items") or [])
    all_leaders: dict[str, list[dict[str, Any]]] = {}
    for sector in sectors:
        code = str(sector["sector_code"])
        cached = value_line.cache.list_records(
            LEADER_DATASET, category=f"{args.as_of}:{code}", limit=100_000,
        )["items"]
        rows = [
            dict(item["payload"])
            for item in cached
            if str(item["payload"].get("as_of") or item["payload"].get("data_as_of") or "") == args.as_of
            and number(item["payload"].get("score")) is not None
        ]
        rows.sort(key=lambda item: (int(item.get("rank") or 10_000), str(item.get("symbol") or "")))
        all_leaders[code] = rows

    sector_by_code = {str(item["sector_code"]): item for item in sectors}
    cross_industry = []
    for sector in sectors:
        code = str(sector["sector_code"])
        cross_industry.append({
            "sector_rank": sector.get("rank"), "sector_code": code,
            "sector_name": sector.get("sector_name"), **score_stats(all_leaders[code]),
        })
    cross_industry.sort(key=lambda item: int(item.get("sector_rank") or 10_000))

    samples = {}
    for label, code in SAMPLE_TRACKS.items():
        rows = all_leaders.get(code, [])
        samples[label] = {
            "sector_code": code,
            "sector_rank": sector_by_code.get(code, {}).get("rank"),
            "sector_score": sector_by_code.get(code, {}).get("score"),
            "available_scored_companies": len(rows),
            "top10": [compact_leader(row) for row in rows[:10]],
            "top3_raw_explanations": [compact_leader(row, include_raw=True) for row in rows[:3]],
        }

    workspace = ValueWorkspaceService()
    try:
        snapshot = workspace.materialize_v2_snapshot(
            "profile_value_line_v2", args.as_of, force_refresh=True,
        )
        run = dict(snapshot["run"])
        universe, created = workspace.create_research_universe(
            str(run["id"]), args.candidate_limit, args.leader_limit,
        )
        current_counts: Counter[str] = Counter(item["track_id"] for item in universe["members"])
        exclusion_counts: Counter[str] = Counter(item["track_id"] for item in universe.get("eligibility_exclusions", []))
        current_rows = []
        for code, count in sorted(current_counts.items(), key=lambda item: (int(sector_by_code[item[0]].get("rank") or 10_000), item[0])):
            sector = sector_by_code[code]
            current_rows.append({
                "sector_code": code, "sector_name": sector.get("sector_name"),
                "requested_leaders": args.leader_limit,
                "eligibility_excluded": exclusion_counts[code],
                "universe_members": count,
                "share": rounded(count / len(universe["companies"])) if universe["companies"] else 0,
                "sector_rank": sector.get("rank"), "sector_score": sector.get("score"),
                "leader_top5_mean": score_stats(all_leaders[code])["top5_mean"],
            })

        # Diagnostic alternative only: the V2 top-three per selected sector,
        # preserving all memberships even where a symbol appears in two tracks.
        selected_tracks = [item for item in sectors if int(item.get("rank") or 10_000) <= args.candidate_limit]
        quota_memberships = []
        for sector in selected_tracks:
            code = str(sector["sector_code"])
            quota_memberships.extend({**row, "track_id": code} for row in all_leaders[code][:3])
        quota_symbols = {str(item["symbol"]) for item in quota_memberships}
        quota_counts: Counter[str] = Counter(item["track_id"] for item in quota_memberships)
        quota_score_values = [number(item.get("score")) for item in quota_memberships]
        quota_sector_values = [number(sector.get("score")) for sector in selected_tracks]

        current_company_scores = []
        for company in universe["companies"]:
            scores = [number(item.get("leader_score")) for item in company.get("memberships", [])]
            current_company_scores.append(max(score for score in scores if score is not None))
        current_sector_values = [number(sector_by_code[code].get("score")) for code in current_counts]
        report = {
            "as_of": args.as_of,
            "source": "isolated VIBE_TRADING_HOME Value Line V2 cache",
            "snapshot_run": run,
            "leader_pool": {
                "sector_count": len(sectors),
                "scored_leader_rows": sum(len(rows) for rows in all_leaders.values()),
                "per_sector_stats": cross_industry,
                "samples": samples,
            },
            "universe_current": {
                "created": created, "id": universe["id"],
                "formula_version": universe["formula_version"],
                "company_count": len(universe["companies"]), "membership_count": len(universe["members"]),
                "by_sector": current_rows,
                "eligibility_exclusions": universe.get("eligibility_exclusions", []),
                "concentration": concentration(current_counts, len(universe["companies"])),
                "average_leader_score": rounded(mean(current_company_scores)) if current_company_scores else None,
                "average_sector_score": rounded(mean(value for value in current_sector_values if value is not None)) if current_sector_values else None,
            },
            "universe_quota_top3_diagnostic": {
                "company_count": len(quota_symbols), "membership_count": len(quota_memberships),
                "by_sector": [
                    {"sector_code": code, "sector_name": sector_by_code[code].get("sector_name"), "members": count,
                     "share": rounded(count / len(quota_symbols)) if quota_symbols else 0}
                    for code, count in sorted(quota_counts.items(), key=lambda item: int(sector_by_code[item[0]].get("rank") or 10_000))
                ],
                "concentration": concentration(quota_counts, len(quota_symbols)),
                "average_leader_score": rounded(mean(value for value in quota_score_values if value is not None)),
                "average_sector_score": rounded(mean(value for value in quota_sector_values if value is not None)),
            },
        }
    finally:
        workspace.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
