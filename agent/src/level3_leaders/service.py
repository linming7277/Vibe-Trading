"""Build Leader V2 rankings independently inside every terminal TDX industry."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from src.strategy_engines.common.provenance import stable_fingerprint
from src.strategy_engines.common.normalization import cross_sectional_percentiles
from src.strategy_engines.common.scoring import weighted_score
from src.strategy_engines.value.leader_score_v2 import (
    DIMENSION_LABELS,
    DIMENSION_METRIC_WEIGHTS,
    FORMULA_VERSION,
    METRIC_DEFINITIONS,
    WEIGHTS,
    formula_contract,
)
from src.strategy_engines.value_line import ValueLineService
from src.strategy_engines.value_market_history import ValueMarketHistoryService
from src.tdx_data.service import get_tdx_service
from .store import Level3LeaderStore
from .constants import (
    MAX_MARKET_DATA_STALENESS_TRADING_DAYS,
    MIN_LISTED_TRADING_DAYS,
    VALUE_LINE_LEADER_LIMIT,
)
from .business_profiles import CompanyBusinessProfileService


ELIGIBILITY_REASON_LABELS = {
    "ST_OR_DELISTING": "ST或退市风险，未参与排名",
    "LISTED_TOO_RECENTLY": "上市时间不足20个交易日",
    "MARKET_DATA_STALE": "行情缺失或超过5个交易日未更新",
    "INSUFFICIENT_FINANCIAL_HISTORY": "缺少年度专业财务历史",
    "INSUFFICIENT_LEADER_COVERAGE": "可用评分维度不足80%",
}


HISTORICAL_VALUATION_PRESENTATION = {
    "VERY_CHEAP": "DEEPLY_UNDERVALUED",
    "CHEAP": "UNDERVALUED",
    "NORMAL": "FAIR",
    "EXPENSIVE": "OVERVALUED",
    "VERY_EXPENSIVE": "DEEPLY_OVERVALUED",
}
HISTORICAL_VALUATION_FORMULA_VERSION = "historical-valuation-v1.0.0"


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
    def formula() -> dict[str, Any]:
        return {
            **formula_contract(),
            "eligibility_rules": [
                {"key": key, "label": label}
                for key, label in ELIGIBILITY_REASON_LABELS.items()
            ],
            "product_label": "三级行业量化龙头候选",
            "disclaimer": "筛选结果用于确定研究候选，不代表已确认的产业龙头、预期收益或买入建议。",
        }

    @staticmethod
    def _sample_warning(eligible_count: int) -> str | None:
        if eligible_count <= 1:
            return "唯一可评分公司，不代表已经验证为行业龙头。"
        if eligible_count == 2:
            return "仅有2家可评分公司，同行分位容易两极化。"
        if eligible_count < 5:
            return "可评分公司少于5家，排名属于小样本结果。"
        return None

    @classmethod
    def _enrich_industry_rows(cls, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        eligible = [
            row for row in rows
            if row.get("eligibility_status") == "eligible" and row.get("leader_rank") is not None
        ]
        directions = {
            key: bool(value["higher_is_better"])
            for key, value in METRIC_DEFINITIONS.items()
        }
        normalized_rows = cross_sectional_percentiles(
            [dict(row.get("raw_features") or {}) for row in eligible], directions,
        ) if eligible else []
        eligible_count = len(eligible)
        member_count = len(rows)
        result: list[dict[str, Any]] = []
        for index, row in enumerate(eligible):
            normalized = normalized_rows[index]
            component_scores = dict(row.get("component_scores") or {})
            overall = weighted_score(component_scores, WEIGHTS, minimum_coverage=.80)
            components: list[dict[str, Any]] = []
            for dimension_key, metric_weights in DIMENSION_METRIC_WEIGHTS.items():
                dimension_result = weighted_score(normalized, metric_weights, minimum_coverage=.50)
                dimension_score = component_scores.get(dimension_key)
                components.append({
                    "key": dimension_key,
                    "label": DIMENSION_LABELS[dimension_key],
                    "weight": WEIGHTS[dimension_key],
                    "score": dimension_score,
                    "coverage": dimension_result.coverage,
                    "status": dimension_result.status,
                    "contribution": (
                        round(float(dimension_score) * overall.used_weights.get(dimension_key, 0), 4)
                        if dimension_score is not None and overall.used_weights else None
                    ),
                    "reweighted": bool(
                        dimension_result.used_weights
                        and any(abs(dimension_result.used_weights.get(key, 0) - weight) > 1e-9
                                for key, weight in metric_weights.items())
                    ),
                    "metrics": [{
                        "key": metric_key,
                        "label": METRIC_DEFINITIONS[metric_key]["label"],
                        "description": METRIC_DEFINITIONS[metric_key]["description"],
                        "unit": METRIC_DEFINITIONS[metric_key]["unit"],
                        "higher_is_better": METRIC_DEFINITIONS[metric_key]["higher_is_better"],
                        "weight": metric_weight,
                        "raw_value": dict(row.get("raw_features") or {}).get(metric_key),
                        "percentile": normalized.get(metric_key),
                        "status": "available" if normalized.get(metric_key) is not None else "missing",
                    } for metric_key, metric_weight in metric_weights.items()],
                })
            available_metrics = sum(
                metric["status"] == "available"
                for component in components for metric in component["metrics"]
            )
            total_metrics = sum(len(component["metrics"]) for component in components)
            scored_components = [item for item in components if item["score"] is not None]
            strongest = sorted(scored_components, key=lambda item: float(item["score"]), reverse=True)[:2]
            weakest = sorted(scored_components, key=lambda item: float(item["score"]))[:2]
            rank = int(row["leader_rank"])
            selected = rank <= VALUE_LINE_LEADER_LIMIT
            missing_dimensions = [
                {"key": key, "label": DIMENSION_LABELS[key]}
                for key in WEIGHTS if component_scores.get(key) is None
            ]
            overall_reweighted = bool(
                overall.used_weights
                and any(abs(overall.used_weights.get(key, 0) - weight) > 1e-9
                        for key, weight in WEIGHTS.items())
            )
            summary = (
                f"在{eligible_count}家可评分公司中排名第{rank}，"
                f"{'进入行业前2量化候选' if selected else '未进入行业前2量化候选'}。"
            )
            if strongest:
                summary += f"相对优势主要来自{'、'.join(item['label'] for item in strongest)}。"
            if weakest:
                summary += f"{'、'.join(item['label'] for item in weakest)}需要重点复核。"
            result.append({
                **row,
                "normalized_features": normalized,
                "components": components,
                "raw_metric_coverage": round(available_metrics / total_metrics, 4) if total_metrics else 0,
                "raw_metric_available": available_metrics,
                "raw_metric_total": total_metrics,
                "confidence": "HIGH" if row.get("coverage", 0) >= .85 else "MEDIUM",
                "explanation": {
                    "summary": summary,
                    "selected": selected,
                    "comparison_scope": f"仅与{row.get('level3_name')}行业内可评分公司比较",
                    "member_count": member_count,
                    "eligible_count": eligible_count,
                    "excluded_count": member_count - eligible_count,
                    "rank": rank,
                    "top_percent": round(rank / eligible_count * 100, 1) if eligible_count else None,
                    "strongest": [{"key": item["key"], "label": item["label"], "score": item["score"]} for item in strongest],
                    "weakest": [{"key": item["key"], "label": item["label"], "score": item["score"]} for item in weakest],
                    "sample_warning": cls._sample_warning(eligible_count),
                    "overall_reweighted": overall_reweighted,
                    "missing_dimensions": missing_dimensions,
                    "score_interpretation": "行业内加权相对分，不是绝对质量分，也不可跨行业比较。",
                },
            })
        excluded = []
        for row in rows:
            if row.get("eligibility_status") == "eligible" and row.get("leader_rank") is not None:
                continue
            reasons = list(row.get("eligibility_reasons") or ["INSUFFICIENT_LEADER_COVERAGE"])
            excluded.append({
                **row,
                "eligibility_reason_labels": [ELIGIBILITY_REASON_LABELS.get(reason, reason) for reason in reasons],
            })
        summary = {
            "member_count": member_count,
            "eligible_count": eligible_count,
            "excluded_count": member_count - eligible_count,
            "selected_count": min(VALUE_LINE_LEADER_LIMIT, eligible_count),
            "sample_warning": cls._sample_warning(eligible_count),
            "excluded_items": excluded,
        }
        return result, summary

    def _enrich_pool(self, pool: dict[str, Any]) -> dict[str, Any]:
        source_run_id = str(pool.get("source_leader_run_id") or "")
        if not source_run_id:
            return {**pool, "formula": self.formula(), "industry_summaries": {}}
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in self.store.all_rows(source_run_id):
            groups.setdefault(str(row["level3_code"]), []).append(row)
        enriched_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        industry_summaries: dict[str, dict[str, Any]] = {}
        for industry_code, rows in groups.items():
            enriched, summary = self._enrich_industry_rows(rows)
            industry_summaries[industry_code] = {key: value for key, value in summary.items() if key != "excluded_items"}
            enriched_by_key.update({(industry_code, str(row["stock_code"])): row for row in enriched})
        members = []
        for member in pool.get("members") or []:
            key = (str(member.get("level3_code") or ""), str(member.get("stock_code") or ""))
            explanation = enriched_by_key.get(key)
            members.append({**member, **(explanation or {})})
        return {
            **pool,
            "members": members,
            "formula": self.formula(),
            "industry_summaries": industry_summaries,
        }

    def refresh_leader_valuation_snapshot(self, run_id: str) -> dict[str, Any]:
        """Persist the complete historical-valuation state for Top2 leaders.

        This is a refresh-task concern, not a page concern: the page can then
        filter all leaders from one immutable result without issuing a request
        per company.  Every lookup is explicitly bounded by the leader run's
        ``as_of`` date to preserve point-in-time semantics.
        """
        run = self.store.get_run(run_id)
        symbols = sorted({
            str(row["stock_code"]).upper()
            for row in self.store.all_rows(run_id)
            if row.get("eligibility_status") == "eligible"
            and row.get("leader_rank") is not None
            and int(row["leader_rank"]) <= VALUE_LINE_LEADER_LIMIT
        })
        if not symbols:
            return {**self.store.valuation_snapshot(run_id), "expected": 0, "failed": 0}

        # Imported lazily: historical valuation also exposes a backfill service
        # which imports this module for the current leader universe.
        from src.historical_valuation.service import HistoricalValuationService

        history_service = HistoricalValuationService(tdx_store=self.tdx_store)
        rows: list[dict[str, Any]] = []
        failures = 0
        for symbol in symbols:
            try:
                history = history_service.get_valuation_history("CN", symbol, as_of=str(run["as_of"]))
                raw_status = str(history.get("historical_valuation_status") or "INSUFFICIENT_DATA")
                coverage = dict(history.get("coverage") or {})
                rows.append({
                    "stock_code": symbol,
                    "historical_valuation_status": raw_status,
                    "presentation_status": HISTORICAL_VALUATION_PRESENTATION.get(raw_status, "INSUFFICIENT_DATA"),
                    "coverage_status": str(coverage.get("coverage_status") or "INSUFFICIENT"),
                    "data_as_of": coverage.get("last_date") or history.get("as_of"),
                    "formula_version": HISTORICAL_VALUATION_FORMULA_VERSION,
                    "source": "TongDaXin historical PE/PB/dividend-yield cache",
                })
            except Exception:
                # A single unavailable series is a valid, visible data-gap; it
                # must not leave a partially filtered page or abort other rows.
                failures += 1
                rows.append({
                    "stock_code": symbol,
                    "historical_valuation_status": "INSUFFICIENT_DATA",
                    "presentation_status": "INSUFFICIENT_DATA",
                    "coverage_status": "INSUFFICIENT",
                    "data_as_of": None,
                    "formula_version": HISTORICAL_VALUATION_FORMULA_VERSION,
                    "source": "TongDaXin historical PE/PB/dividend-yield cache",
                })
        self.store.replace_valuation_snapshot(run_id, rows)
        return {**self.store.valuation_snapshot(run_id), "expected": len(symbols), "failed": failures}

    def _valuation_snapshot_for_run(self, run_id: str) -> dict[str, Any]:
        snapshot = self.store.valuation_snapshot(run_id)
        expected = sum(
            1 for row in self.store.all_rows(run_id)
            if row.get("eligibility_status") == "eligible"
            and row.get("leader_rank") is not None
            and int(row["leader_rank"]) <= VALUE_LINE_LEADER_LIMIT
        )
        return {
            **snapshot,
            "expected": expected,
            "is_complete": bool(expected) and snapshot["total"] == expected,
        }

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
            pool, pool_created = self.store.materialize_pool(
                completed["id"], leader_limit=VALUE_LINE_LEADER_LIMIT,
            )
            valuation_snapshot = self.refresh_leader_valuation_snapshot(completed["id"])
            return {
                **completed, "idempotent_reuse": True,
                "leader_pool": pool, "pool_created": pool_created,
                "valuation_snapshot": valuation_snapshot,
            }
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
            pool, pool_created = self.store.materialize_pool(result["id"], leader_limit=VALUE_LINE_LEADER_LIMIT)
            valuation_snapshot = self.refresh_leader_valuation_snapshot(result["id"])
            return {
                **result, "idempotent_reuse": False,
                "leader_pool": pool, "pool_created": pool_created,
                "valuation_snapshot": valuation_snapshot,
            }
        except Exception as exc:
            self.store.fail_run(run["id"], f"{type(exc).__name__}: {exc}")
            raise

    def get_level3_leaders(self, industry_code: str, *, as_of: str | None = None,
                           limit: int = VALUE_LINE_LEADER_LIMIT) -> dict[str, Any]:
        industry = next((row for row in self.industries()["items"] if row["level3_code"] == industry_code), None)
        if not industry:
            raise KeyError(industry_code)
        run = self.store.latest_run(as_of)
        rows = self.store.industry_rows(run["id"], industry_code) if run else []
        eligible, summary = self._enrich_industry_rows(rows)
        return {
            "industry": industry,
            "as_of": run["as_of"] if run else as_of,
            "formula_version": FORMULA_VERSION,
            "company_count": len(rows) if run else int(industry.get("member_count") or 0),
            "eligible_count": len(eligible),
            "items": eligible[:max(0, min(int(limit), 100))],
            "total_ranked": len(eligible),
            "excluded_items": summary["excluded_items"],
            "quality": {key: value for key, value in summary.items() if key != "excluded_items"},
            "formula": self.formula(),
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
        return {
            "as_of": run["as_of"], "items": by_industry, "total": len(by_industry),
            "snapshot_status": "ready", "valuation_snapshot": self._valuation_snapshot_for_run(run["id"]),
        }

    def get_pool(self, pool_id: str, *, include_inactive: bool = True) -> dict[str, Any] | None:
        pool = self.store.get_pool(pool_id, include_inactive=include_inactive)
        return self._enrich_pool(pool) if pool else None

    def ensure_current_pool(self) -> dict[str, Any]:
        run = self.store.latest_run()
        if not run:
            raise KeyError("no completed level3 leader run")
        pool, _created = self.store.materialize_pool(run["id"], leader_limit=VALUE_LINE_LEADER_LIMIT)
        return {
            **self._enrich_pool(pool),
            "valuation_snapshot": self._valuation_snapshot_for_run(run["id"]),
        }


_service: Level3IndustryLeaderService | None = None


def get_level3_leader_service() -> Level3IndustryLeaderService:
    global _service
    if _service is None:
        _service = Level3IndustryLeaderService()
    return _service
