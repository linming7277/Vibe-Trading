"""Build Leader V2 rankings independently inside every terminal TDX industry."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from src.fine_tracks.profiles import CompanyBusinessProfileService
from src.strategy_engines.common.provenance import stable_fingerprint
from src.strategy_engines.value.leader_score_v2 import FORMULA_VERSION
from src.strategy_engines.value_line import ValueLineService
from src.strategy_engines.value_market_history import ValueMarketHistoryService
from src.tdx_data.service import get_tdx_service
from src.value_workspace.service import MAX_MARKET_DATA_STALENESS_TRADING_DAYS, MIN_LISTED_TRADING_DAYS

from .store import Level3LeaderStore


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Level3IndustryLeaderService:
    def __init__(self, *, store: Level3LeaderStore | None = None,
                 profiles: CompanyBusinessProfileService | None = None,
                 value_line: ValueLineService | None = None,
                 tdx_store: Any | None = None,
                 market_history: ValueMarketHistoryService | None = None) -> None:
        self.store = store or Level3LeaderStore()
        self.profiles = profiles or CompanyBusinessProfileService()
        self.value_line = value_line or ValueLineService()
        self.tdx_store = tdx_store or get_tdx_service().store
        self.market_history = market_history or ValueMarketHistoryService()

    def close(self) -> None:
        self.value_line.close()
        self.store.close()

    @staticmethod
    def _public_industry(industry: dict[str, Any]) -> dict[str, Any]:
        # TDX has 36 terminal level-2 leaves. Product V1 exposes every terminal
        # node through the third selector while preserving its raw level.
        return {
            **industry,
            "level3_code": industry["industry_code"],
            "level3_name": industry["industry_name"],
            "terminal_level": int(industry.get("level") or 3),
        }

    def industries(self) -> dict[str, Any]:
        items = [self._public_industry(row) for row in self.profiles.industries()]
        return {
            "items": items,
            "total": len(items),
            "level1_total": len({row["level1_code"] for row in items}),
            "level2_total": len({row["level2_code"] for row in items}),
            "level3_total": len(items),
            "source": "TongDaXin research terminal industry catalog",
        }

    def industry_tree(self) -> dict[str, Any]:
        industries = self.industries()["items"]
        roots: dict[str, dict[str, Any]] = {}
        for row in industries:
            root = roots.setdefault(row["level1_code"], {
                "code": row["level1_code"], "name": row["level1_name"], "children": {},
            })
            branch = root["children"].setdefault(row["level2_code"], {
                "code": row["level2_code"], "name": row["level2_name"], "children": [],
            })
            branch["children"].append({
                "code": row["level3_code"], "name": row["level3_name"],
                "member_count": int(row.get("member_count") or 0),
                "terminal_level": row["terminal_level"],
            })
        items = []
        for root in roots.values():
            branches = list(root["children"].values())
            for branch in branches:
                branch["children"].sort(key=lambda item: item["code"])
            items.append({**root, "children": sorted(branches, key=lambda item: item["code"])})
        items.sort(key=lambda item: item["code"])
        return {
            "items": items, "level1_total": len(items),
            "level2_total": len({row["level2_code"] for row in industries}),
            "level3_total": len(industries),
        }

    @staticmethod
    def _eligibility(*, symbol: str, name: str, histories: dict[str, list[dict[str, Any]]],
                     annual_available: dict[str, bool], market_dates: list[str]) -> list[str]:
        reasons: list[str] = []
        if "ST" in name.upper() or "退" in name:
            reasons.append("ST_OR_DELISTING")
        rows = [row for row in histories.get(symbol, []) if (_number(row.get("close")) or 0) > 0]
        if rows and len(rows) < MIN_LISTED_TRADING_DAYS:
            reasons.append("LISTED_TOO_RECENTLY")
        if not rows:
            reasons.append("MARKET_DATA_STALE")
        elif market_dates:
            last = max(str(row.get("data_as_of") or row.get("trade_date")) for row in rows)
            if sum(day > last for day in market_dates) > MAX_MARKET_DATA_STALENESS_TRADING_DAYS:
                reasons.append("MARKET_DATA_STALE")
        if not annual_available.get(symbol, False):
            reasons.append("INSUFFICIENT_FINANCIAL_HISTORY")
        return reasons

    @staticmethod
    def _metric_notes(industry_name: str) -> list[str]:
        return ["FINANCIAL_SECTOR_METRIC_CAUTION"] if any(
            token in industry_name for token in ("银行", "保险", "证券")
        ) else []

    def build_level3_leaders(self, as_of: str, *, force: bool = False) -> dict[str, Any]:
        date.fromisoformat(as_of)
        industries = [self._public_industry(row) for row in self.profiles.industries()]
        catalog_as_of = max(str(row.get("as_of") or "") for row in industries)
        idempotency_key = stable_fingerprint({
            "as_of": as_of, "catalog_as_of": catalog_as_of,
            "formula_version": FORMULA_VERSION, "scope": "terminal-industry-v1",
        })
        if not force and (completed := self.store.completed_run(idempotency_key)):
            return {**completed, "idempotent_reuse": True}
        run = self.store.start_run(
            idempotency_key=idempotency_key, as_of=as_of,
            catalog_as_of=catalog_as_of, formula_version=FORMULA_VERSION,
        )
        try:
            memberships = self.profiles.catalog.memberships()
            symbols = sorted({symbol for values in memberships.values() for symbol in values})
            fundamentals = {row["key"]: row["payload"] for row in self.tdx_store.list_records("fundamentals", limit=10_000)["items"]}
            quotes = {row["key"]: row["payload"] for row in self.tdx_store.list_records("quotes", limit=10_000)["items"]}
            securities = {row["key"]: row for row in self.tdx_store.list_records("securities", limit=10_000)["items"]}
            financials = self.value_line._load_financials(as_of)
            histories = self.market_history.read_symbols(symbols, as_of=as_of, count=60)
            annual_available = {
                symbol: any(row.get("period_type") == "annual" for row in financials.get(symbol, []))
                for symbol in symbols
            }
            market_dates = sorted({
                str(row.get("data_as_of") or row.get("trade_date"))
                for values in histories.values() for row in values
                if row.get("data_as_of") or row.get("trade_date")
            })
            market_as_of = market_dates[-1] if market_dates else as_of
            all_rows: list[dict[str, Any]] = []
            industry_stats: list[dict[str, Any]] = []
            exclusion_counts: Counter[str] = Counter()
            for industry in industries:
                members = memberships.get(industry["industry_code"], [])
                eligible: list[str] = []
                exclusions: dict[str, list[str]] = {}
                for symbol in members:
                    name = str(
                        (securities.get(symbol) or {}).get("name")
                        or fundamentals.get(symbol, {}).get("name")
                        or quotes.get(symbol, {}).get("name") or symbol
                    )
                    reasons = self._eligibility(
                        symbol=symbol, name=name, histories=histories,
                        annual_available=annual_available, market_dates=market_dates,
                    )
                    if reasons:
                        exclusions[symbol] = reasons
                        exclusion_counts.update(reasons)
                    else:
                        eligible.append(symbol)
                scored = self.value_line._leader_rows(
                    industry["level3_code"], industry["level3_name"], eligible, as_of,
                    financials, fundamentals, quotes,
                    {"market_data_as_of": market_as_of, "market_data_status": "COMPLETE"},
                )
                scored_rows = [row for row in scored if row.get("score") is not None]
                for rank, row in enumerate(scored_rows, 1):
                    row["rank"] = rank
                scored_by_symbol = {row["symbol"]: row for row in scored_rows}
                notes = self._metric_notes(industry["level3_name"])
                for symbol in members:
                    scored_row = scored_by_symbol.get(symbol)
                    reasons = exclusions.get(symbol, [])
                    if symbol in eligible and scored_row is None:
                        reasons = ["INSUFFICIENT_LEADER_COVERAGE"]
                        exclusion_counts.update(reasons)
                    all_rows.append({
                        "as_of": as_of,
                        "level1_code": str(industry.get("level1_code") or ""),
                        "level1_name": str(industry.get("level1_name") or ""),
                        "level2_code": str(industry.get("level2_code") or industry["level3_code"]),
                        "level2_name": str(industry.get("level2_name") or industry["level3_name"]),
                        "level3_code": industry["level3_code"], "level3_name": industry["level3_name"],
                        "stock_code": symbol,
                        "stock_name": str(
                            (scored_row or {}).get("name") or (securities.get(symbol) or {}).get("name")
                            or fundamentals.get(symbol, {}).get("name") or symbol
                        ),
                        "leader_rank": (scored_row or {}).get("rank"),
                        "leader_score": (scored_row or {}).get("score"),
                        "leader_formula_version": FORMULA_VERSION,
                        "component_scores": (scored_row or {}).get("component_scores") or {},
                        "coverage": float((scored_row or {}).get("coverage") or 0),
                        "eligibility_status": "eligible" if scored_row else "ineligible",
                        "eligibility_reasons": reasons,
                        "metric_applicability_notes": notes,
                        "raw_features": (scored_row or {}).get("raw_features") or {},
                        "provenance_key": (scored_row or {}).get("provenance_key") or "",
                    })
                industry_stats.append({
                    "level3_code": industry["level3_code"], "level3_name": industry["level3_name"],
                    "company_count": len(members), "eligible_count": len(scored_rows),
                })
            eligible_counts = [row["eligible_count"] for row in industry_stats]
            company_counts = [row["company_count"] for row in industry_stats]
            statistics = {
                "industry_count": len(industries),
                "successful_industry_count": len(industry_stats),
                "zero_company_industry_count": sum(value == 0 for value in company_counts),
                "one_eligible_industry_count": sum(value == 1 for value in eligible_counts),
                "two_or_more_eligible_industry_count": sum(value >= 2 for value in eligible_counts),
                "leader_record_count": sum(value for value in eligible_counts),
                "top1_count": sum(value >= 1 for value in eligible_counts),
                "top2_count": sum(value >= 2 for value in eligible_counts),
                "company_membership_count": len(all_rows),
                "sample_size": {
                    "one_company": sum(value == 1 for value in company_counts),
                    "two_companies": sum(value == 2 for value in company_counts),
                    "three_to_five_companies": sum(3 <= value <= 5 for value in company_counts),
                    "more_than_five_companies": sum(value > 5 for value in company_counts),
                },
                "exclusion_counts": dict(exclusion_counts),
                "industry_stats": industry_stats,
            }
            result = self.store.finish_run(run["id"], rows=all_rows, statistics=statistics)
            return {**result, "idempotent_reuse": False}
        except Exception as exc:
            self.store.fail_run(run["id"], f"{type(exc).__name__}: {exc}")
            raise

    def get_level3_leaders(self, industry_code: str, *, as_of: str | None = None,
                           limit: int = 2) -> dict[str, Any]:
        industry = next((row for row in self.industries()["items"] if row["level3_code"] == industry_code), None)
        if not industry:
            raise KeyError(industry_code)
        run = self.store.latest_run(as_of)
        rows = self.store.industry_rows(run["id"], industry_code) if run else []
        eligible = [row for row in rows if row["eligibility_status"] == "eligible" and row["leader_rank"] is not None]
        return {
            "industry": industry,
            "as_of": run["as_of"] if run else as_of,
            "formula_version": FORMULA_VERSION,
            "company_count": len(rows) if run else int(industry.get("member_count") or 0),
            "eligible_count": len(eligible),
            "items": eligible[:max(0, min(int(limit), 100))],
            "total_ranked": len(eligible),
            "snapshot_status": "ready" if run else "not_built",
            "comparison_scope": "仅限当前通达信三级/末级行业内部，不可跨行业比较",
        }

    def get_all_level3_top_leaders(self, *, as_of: str | None = None, limit: int = 2) -> dict[str, Any]:
        run = self.store.latest_run(as_of)
        if not run:
            return {"as_of": as_of, "items": [], "total": 0, "snapshot_status": "not_built"}
        by_industry: dict[str, list[dict[str, Any]]] = {}
        for row in self.store.all_rows(run["id"]):
            if row["eligibility_status"] == "eligible" and row["leader_rank"] is not None and row["leader_rank"] <= limit:
                by_industry.setdefault(row["level3_code"], []).append(row)
        return {"as_of": run["as_of"], "items": by_industry, "total": len(by_industry), "snapshot_status": "ready"}


_service: Level3IndustryLeaderService | None = None


def get_level3_leader_service() -> Level3IndustryLeaderService:
    global _service
    if _service is None:
        _service = Level3IndustryLeaderService()
    return _service
