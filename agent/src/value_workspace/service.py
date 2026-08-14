"""Application service for the value research workbench."""

from __future__ import annotations

import hashlib
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

from src.research_workspace.store import ResearchWorkspaceStore
from src.strategy_engines.common.normalization import cross_sectional_percentiles
from src.strategy_engines.common.scoring import weighted_score
from src.strategy_engines.store import StrategyEngineStore
from src.tdx_data.financial_history import FinancialHistoryService
from src.tdx_data.service import get_tdx_service

from .store import ValueWorkspaceStore, now


PROFILE_FORMULA_VERSION = "value-profile-v1.0.0"
RESEARCH_TEMPLATE_VERSION = "value-company-research-v1.0.0"
V2_WORKBENCH_FORMULA_VERSION = "value-workbench-v2.2.0"
UNIVERSE_RULE_VERSION = "value-research-universe-v1.0.0"
SIGNAL_RULE_VERSION = "value-monitor-rules-v1.0.0"
CANDIDATE_LIMITS = {5, 10, 20, 50}
RISK_THRESHOLDS = {
    "revenue_yoy": -20.0, "net_profit_yoy": -20.0,
    "roe_drop_pp": 3.0, "debt_ratio_rise_pp": 5.0,
}
COMPANY_SNAPSHOT_TIMEOUT_SECONDS = 45

MODEL_COMPONENT_WEIGHTS: dict[str, dict[str, float]] = {
    "policy_cycle": {"prosperity": .40, "relative_strength": .25, "activity": .20, "risk": .15},
    "economic_cycle": {"prosperity": .30, "earnings": .35, "valuation": .20, "risk": .15},
    "liquidity": {"activity": .50, "relative_strength": .30, "prosperity": .20},
    "earnings_climate": {"earnings": .50, "prosperity": .20, "valuation": .20, "risk": .10},
}
LEADER_WEIGHTS = {
    "industry_position": .25, "profitability": .25, "growth_stability": .15,
    "cash_flow": .10, "valuation": .15, "governance": .10,
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _mean(*values: Any) -> float | None:
    available = [number for value in values if (number := _number(value)) is not None]
    return round(sum(available) / len(available), 4) if available else None


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _plain_diff(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        return {"initial": True}
    before = dict(previous.get("payload") or {})
    changed: dict[str, Any] = {}
    for key in sorted(set(before) | set(current)):
        if before.get(key) != current.get(key):
            changed[key] = {"before": before.get(key), "after": current.get(key)}
    return changed


def _run_with_timeout(callback: Any, timeout_seconds: int, label: str) -> Any:
    """Bound a vendor/cache read so one company cannot stall the whole pool."""
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def invoke() -> None:
        try:
            outcome["value"] = callback()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    threading.Thread(target=invoke, daemon=True, name="value-company-source").start()
    if not done.wait(timeout_seconds):
        raise TimeoutError(f"{label} timed out after {timeout_seconds}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


class ValueWorkspaceService:
    def __init__(self, store: ValueWorkspaceStore | None = None) -> None:
        self.store = store or ValueWorkspaceStore()
        self._owns_store = store is None

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def materialize_run(self, run_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
        engine_store = StrategyEngineStore(self.store.db_path)
        try:
            run = engine_store.get_run(run_id)
        finally:
            engine_store.close()
        if not run:
            raise KeyError("strategy run not found")
        if run["strategy_line"] != "value" or run["market"] != "CN":
            raise ValueError("value workbench v1 only supports A-shares")
        tdx = get_tdx_service()
        sector_result = tdx.sectors(limit=500)
        rows = list(sector_result.get("items") or [])
        raw = [{
            "prosperity": _number(row.get("breadth_pct")),
            "relative_strength": _number(row.get("change_pct")),
            "activity": _number(row.get("amount") or row.get("turnover") or row.get("volume")),
            "earnings": None, "valuation": None,
            "risk": _number(row.get("breadth_pct")),
        } for row in rows]
        normalized = cross_sectional_percentiles(raw, {
            "prosperity": True, "relative_strength": True, "activity": True,
            "earnings": True, "valuation": False, "risk": True,
        }) if raw else []
        scored: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            model_scores: dict[str, float | None] = {}
            model_coverage: dict[str, float] = {}
            for model, weights in MODEL_COMPONENT_WEIGHTS.items():
                result = weighted_score(normalized[index], weights, minimum_coverage=.4)
                model_scores[model] = result.score
                model_coverage[model] = result.coverage
            composite = weighted_score(model_scores, profile["model_weights"], minimum_coverage=.5)
            scored.append({
                "track_id": str(row.get("code") or ""), "track_name": str(row.get("name") or row.get("code") or ""),
                "category": str(row.get("category") or row.get("type") or "通达信板块"),
                "base_score": composite.score, "coverage": composite.coverage,
                "component_scores": model_scores,
                "quality_flags": [f"{key}:coverage={model_coverage[key]:.0%}" for key in model_scores if model_scores[key] is None],
                "source_status": "live" if sector_result.get("as_of") else "stale",
                "data_as_of": str(run["as_of"]), "leaders": [],
            })
        scored.sort(key=lambda item: (item["base_score"] is None, -(item["base_score"] or 0), item["track_id"]))
        for rank, item in enumerate(scored, 1):
            item["rank"] = rank
        # Keep the workbench responsive while still persisting a broad, reproducible track pool.
        for item in scored[:60]:
            item["leaders"] = self._score_track_leaders(item["track_id"])
        self.store.replace_tracks(run_id, profile["id"], scored)
        self.store.set_run_profile(run_id, profile)
        return self.store.list_tracks(run_id)

    def materialize_v2_snapshot(self, profile_id: str | None = None, as_of: str | None = None, *, force_refresh: bool = False) -> dict[str, Any]:
        """Persist the current Value Line V2 cache as a reproducible workbench run.

        Value Line V2 owns the authoritative macro / sector / leader calculations.
        The workbench adds the user's calculation-profile view and the downstream
        research workflow.  Keeping this bridge here prevents the two products
        from silently presenting unrelated rankings.
        """
        profile = self.store.get_profile(profile_id)
        if not profile:
            raise KeyError("calculation profile not found")

        # Delayed import avoids the Value Line API depending on this workspace.
        from src.strategy_engines.value_line import get_value_line_service

        value_line = get_value_line_service()
        sector_result = value_line.sectors(as_of)
        target_as_of = str(sector_result.get("as_of") or as_of or "")
        rows = list(sector_result.get("items") or [])
        if not target_as_of or not rows:
            return {"run": None, "tracks": [], "sectors": rows, "macro": value_line.macro(as_of)}

        idempotency_key = ":".join((
            "value-v2-workbench", profile["id"], f"v{profile['version']}", target_as_of,
        ))
        engine_store = StrategyEngineStore(self.store.db_path)
        try:
            formula_version = f"{V2_WORKBENCH_FORMULA_VERSION}:{profile['id']}:v{profile['version']}"
            if not force_refresh:
                # A forced intraday recalculation receives a fresh run id.  Use
                # that newest matching snapshot for subsequent workbench reads,
                # instead of falling back to the original same-date run.
                for prior_run in engine_store.list_runs("value", "CN", limit=100):
                    if prior_run.get("as_of") != target_as_of or prior_run.get("profile_id") != profile["id"]:
                        continue
                    if prior_run.get("formula_version") != formula_version:
                        continue
                    prior_tracks = self.store.list_tracks(prior_run["id"])
                    if prior_tracks:
                        return {"run": prior_run, "tracks": prior_tracks, "sectors": rows, "macro": value_line.macro(target_as_of)}
            run, created = engine_store.create_or_get_run(
                idempotency_key=idempotency_key,
                strategy_line="value",
                market="CN",
                as_of=target_as_of,
                symbols=[],
                formula_version=formula_version,
                profile_id=profile["id"],
                profile_version=profile["version"],
                force_refresh=force_refresh,
            )
            existing_tracks = self.store.list_tracks(run["id"])
            if not created and existing_tracks:
                return {"run": run, "tracks": existing_tracks, "sectors": rows, "macro": value_line.macro(target_as_of)}

            tracks: list[dict[str, Any]] = []
            for row in rows:
                components = dict(row.get("component_scores") or {})
                model_scores, model_coverage = self._v2_model_scores(components)
                standard_profile = profile["id"] == "profile_value_line_v2"
                composite = weighted_score(model_scores, profile["model_weights"], minimum_coverage=.5)
                effective_model_coverage = sum(
                    float(weight) * model_coverage.get(model, 0.0)
                    for model, weight in profile["model_weights"].items()
                )
                base_score = _number(row.get("score")) if standard_profile else composite.score
                coverage = float(row.get("coverage") or 0) if standard_profile else min(
                    float(row.get("coverage") or 0), effective_model_coverage,
                )
                leaders = self._v2_track_leaders(value_line, str(row.get("sector_code") or ""), target_as_of)
                tracks.append({
                    "track_id": str(row.get("sector_code") or ""),
                    "track_name": str(row.get("sector_name") or row.get("sector_code") or ""),
                    "category": "通达信 881xxx 二级行业",
                    "base_score": base_score,
                    "coverage": coverage,
                    "component_scores": components if standard_profile else {**model_scores, "v2_base_score": _number(row.get("score"))},
                    "quality_flags": list(row.get("missing_fields") or []) + [
                        f"{model}:coverage={model_coverage[model]:.0%}"
                        for model in model_scores if model_coverage[model] < 1.0
                    ],
                    "source_status": "cached_ready" if row.get("status") == "ready" else str(row.get("status") or "stale"),
                    "data_as_of": str(row.get("data_as_of") or target_as_of),
                    "leaders": leaders,
                })
            tracks = [item for item in tracks if item["track_id"]]
            tracks.sort(key=lambda item: (item["base_score"] is None, -(item["base_score"] or 0), item["track_id"]))
            for rank, item in enumerate(tracks, 1):
                item["rank"] = rank
            self.store.replace_tracks(run["id"], profile["id"], tracks)
            self.store.set_run_profile(run["id"], profile)
            run = engine_store.finish_run(
                run["id"], status="completed", source_status="live",
                message=f"Value Line V2 snapshot: {len(tracks)} tracks / {target_as_of}",
            )
            return {
                "run": run, "tracks": self.store.list_tracks(run["id"]),
                "sectors": rows, "macro": value_line.macro(target_as_of),
            }
        except Exception:
            if "run" in locals():
                engine_store.finish_run(run["id"], status="failed", source_status="stale", message="Value Line V2 snapshot bridge failed")
            raise
        finally:
            engine_store.close()

    @staticmethod
    def _v2_model_scores(components: dict[str, Any]) -> tuple[dict[str, float | None], dict[str, float]]:
        """Project V2 dimensions into the four documented calculation models."""
        model_weights = {
            "policy_cycle": {"macro_fit": .40, "policy_fit": .40, "momentum": .10, "risk_quality": .10},
            "economic_cycle": {"earnings_momentum": .45, "valuation": .25, "momentum": .15, "risk_quality": .15},
            "liquidity": {"capital_flow_proxy": .50, "momentum": .30, "macro_fit": .10, "risk_quality": .10},
            "earnings_climate": {"earnings_momentum": .55, "valuation": .20, "risk_quality": .15, "momentum": .10},
        }
        scores: dict[str, float | None] = {}
        coverage: dict[str, float] = {}
        for name, weights in model_weights.items():
            result = weighted_score({key: _number(components.get(key)) for key in weights}, weights, minimum_coverage=.5)
            scores[name], coverage[name] = result.score, result.coverage
        return scores, coverage

    @staticmethod
    def _v2_track_leaders(value_line: Any, track_id: str, as_of: str) -> list[dict[str, Any]]:
        rows = list(value_line.leaders(track_id, as_of).get("items") or [])[:20]
        leaders: list[dict[str, Any]] = []
        type_map = {
            "industry_position": "规模优势", "profitability": "盈利质量", "growth_stability": "成长优势",
            "cash_flow": "现金流优势", "valuation": "估值优势", "governance_risk": "稳健治理",
        }
        for index, row in enumerate(rows, 1):
            scores = {key: _number(value) for key, value in dict(row.get("component_scores") or {}).items()}
            strongest = max(((key, value) for key, value in scores.items() if value is not None), key=lambda pair: pair[1], default=("industry_position", 0))[0]
            leaders.append({
                "symbol": str(row.get("symbol") or ""), "name": str(row.get("name") or row.get("symbol") or ""),
                "leader_type": "综合领先" if index == 1 else type_map.get(strongest, "综合领先"),
                "base_score": _number(row.get("score")), "coverage": float(row.get("coverage") or 0),
                "rank": int(row.get("rank") or index), "component_scores": scores,
                "quality_flags": list(row.get("missing_fields") or []),
            })
        return [item for item in leaders if item["symbol"]]

    def _score_track_leaders(self, track_id: str) -> list[dict[str, Any]]:
        detail = get_tdx_service().sector_detail(track_id)
        members = [] if not detail else [
            row for row in detail.get("members", [])
            if row.get("code") and "ST" not in str(row.get("name") or "").upper() and not str(row.get("code")).endswith(".BJ")
        ]
        if not members:
            return []
        raw = [{
            "industry_position": _number(row.get("market_cap_100m")),
            "profitability": _mean(row.get("net_profit_10k"), row.get("eps")),
            "growth_stability": _mean(row.get("revenue_growth"), row.get("profit_growth")),
            "cash_flow": _number(row.get("operating_cash_flow")),
            "valuation": _mean(row.get("pe_ttm"), row.get("pb_mrq")),
            "governance": 100.0,
        } for row in members]
        normalized = cross_sectional_percentiles(raw, {
            "industry_position": True, "profitability": True, "growth_stability": True,
            "cash_flow": True, "valuation": False, "governance": True,
        })
        leaders = []
        for index, member in enumerate(members):
            result = weighted_score(normalized[index], LEADER_WEIGHTS, minimum_coverage=.5)
            strongest = max(
                ((key, value) for key, value in normalized[index].items() if value is not None),
                key=lambda pair: pair[1], default=("industry_position", 0),
            )[0]
            type_map = {
                "industry_position": "规模龙头", "profitability": "质量龙头", "growth_stability": "成长龙头",
                "cash_flow": "质量龙头", "valuation": "估值龙头", "governance": "产业关键环节龙头",
            }
            leaders.append({
                "symbol": str(member["code"]), "name": str(member.get("name") or member["code"]),
                "leader_type": type_map[strongest], "base_score": result.score, "coverage": result.coverage,
                "component_scores": normalized[index], "quality_flags": [] if result.score is not None else ["数据不足"],
            })
        leaders.sort(key=lambda item: (item["base_score"] is None, -(item["base_score"] or 0), item["symbol"]))
        for rank, item in enumerate(leaders[:20], 1):
            item["rank"] = rank
            if rank == 1 and item["base_score"] is not None:
                item["leader_type"] = "综合龙头"
        return leaders[:20]

    def ensure_track_leaders(self, run_id: str, track_id: str) -> list[dict[str, Any]]:
        existing = self.store.list_leaders(run_id, track_id)
        if existing:
            return existing
        tracks = {item["track_id"]: item for item in self.store.list_tracks(run_id)}
        track = tracks.get(track_id)
        if not track:
            raise KeyError("track snapshot not found")
        leaders = self._score_track_leaders(track_id)
        # Replacing the complete run keeps snapshot writes atomic.
        all_tracks = self.store.list_tracks(run_id)
        for item in all_tracks:
            item["leaders"] = self.store.list_leaders(run_id, item["track_id"])
            if item["track_id"] == track_id:
                item["leaders"] = leaders
        self.store.replace_tracks(run_id, track["profile_id"], all_tracks)
        return self.store.list_leaders(run_id, track_id)

    def create_research_universe(self, run_id: str, candidate_limit: int, leader_limit: int = 5) -> tuple[dict[str, Any], bool]:
        if candidate_limit not in CANDIDATE_LIMITS:
            raise ValueError("candidate_limit must be one of 5, 10, 20, 50")
        if leader_limit != 5:
            raise ValueError("leader_limit is fixed at 5")
        engine = StrategyEngineStore(self.store.db_path)
        try:
            run = engine.get_run(run_id)
        finally:
            engine.close()
        if not run or run.get("strategy_line") != "value" or run.get("market") != "CN":
            raise KeyError("A-share value strategy run not found")
        tracks = [item for item in self.store.list_tracks(run_id) if int(item["rank"]) <= candidate_limit]
        if not tracks:
            raise ValueError("strategy run has no materialized candidate tracks")
        members: list[dict[str, Any]] = []
        for track in tracks:
            leaders = [
                item for item in self.ensure_track_leaders(run_id, track["track_id"])
                if item.get("base_score") is not None and float(item.get("coverage") or 0) > 0
            ][:leader_limit]
            for leader in leaders:
                members.append({
                    "track_id": track["track_id"], "track_name": track["track_name"],
                    "track_rank": int(track["rank"]), "symbol": leader["symbol"], "name": leader["name"],
                    "leader_rank": int(leader["rank"]), "leader_type": leader["leader_type"],
                    "leader_score": leader.get("base_score"), "leader_coverage": float(leader.get("coverage") or 0),
                    "inclusion_reason": f"候选赛道第{track['rank']}名 · 行业内第{leader['rank']}名",
                })
        if not members:
            raise ValueError("candidate tracks have no valid leaders")
        key = ":".join((run_id, str(candidate_limit), str(leader_limit), UNIVERSE_RULE_VERSION))
        return self.store.create_universe(
            idempotency_key=key, run_id=run_id,
            profile_id=str(run.get("profile_id") or "profile_value_line_v2"),
            candidate_limit=candidate_limit, leader_limit=leader_limit,
            data_as_of=str(run["as_of"]), formula_version=str(run["formula_version"]), members=members,
        )

    @staticmethod
    def _universe_companies(universe: dict[str, Any]) -> list[dict[str, Any]]:
        companies = []
        for company in universe.get("companies", []):
            memberships = sorted(company.get("memberships") or [], key=lambda item: (item["track_rank"], item["leader_rank"]))
            if not memberships:
                continue
            companies.append({
                "symbol": company["symbol"], "name": company["name"],
                "primary_track_id": memberships[0]["track_id"],
            })
        return companies

    def create_operation(
        self, universe_id: str, *, run_kind: str, as_of: str, trigger_kind: str = "manual",
    ) -> tuple[dict[str, Any], bool]:
        if run_kind not in {"bootstrap", "incremental"}:
            raise ValueError("run_kind must be bootstrap or incremental")
        date.fromisoformat(as_of)
        universe = self.store.get_universe(universe_id)
        if not universe:
            raise KeyError("research universe not found")
        if run_kind == "incremental" and universe["status"] != "active":
            raise ValueError("only an active research universe can run daily increments")
        companies = self._universe_companies(universe)
        if not companies:
            raise ValueError("research universe is empty")
        operation, created = self.store.create_incremental_run(
            universe_id=universe_id, run_kind=run_kind, trigger_kind=trigger_kind,
            as_of=as_of, companies=companies,
        )
        if created and run_kind == "bootstrap":
            self.store.update_universe_status(universe_id, "bootstrapping")
        return operation, created

    def run_operation(self, run_id: str) -> dict[str, Any]:
        operation = self.store.get_incremental_run(run_id)
        if not operation:
            raise KeyError("incremental run not found")
        self.store.update_incremental_run(run_id, status="running", started_at=now(), message="")
        jobs = [job for job in operation["jobs"] if job["status"] == "queued"]
        if not jobs:
            return operation

        finance_refresh_error = ""
        finance = FinancialHistoryService()
        try:
            try:
                finance.collect_incremental([job["symbol"] for job in jobs], end_time=operation["as_of"].replace("-", ""))
            except Exception as exc:
                finance_refresh_error = str(exc)
        finally:
            finance.client.close()
            finance.store.close()

        for offset in range(0, len(jobs), 20):
            current = self.store.get_incremental_run(run_id) or {}
            if current.get("cancel_requested"):
                break
            chunk = jobs[offset:offset + 20]
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="value-incremental") as executor:
                futures = {
                    executor.submit(self._run_operation_job, job, operation): job for job in chunk
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        job = futures[future]
                        self.store.update_incremental_job(
                            job["id"], status="failed", stage="failed",
                            attempts=int(job.get("attempts") or 0) + 1, message=str(exc),
                        )
                        self.store.refresh_incremental_progress(run_id)
        generated_events: list[dict[str, Any]] = []
        final = self.store.get_incremental_run(run_id) or {}
        if final.get("cancel_requested"):
            for job in final.get("jobs", []):
                if job["status"] == "queued":
                    self.store.update_incremental_job(job["id"], status="cancelled", stage="cancelled", message="operation cancelled")
            completed = sum(job["status"] in {"completed", "partial"} for job in final.get("jobs", []))
            failed = sum(job["status"] == "failed" for job in final.get("jobs", []))
            self.store.update_incremental_run(
                run_id, status="cancelled", completed=completed, failed=failed,
                coverage=completed / int(final.get("total") or 1), completed_at=now(),
            )
        else:
            failed = sum(job["status"] == "failed" for job in final.get("jobs", []))
            completed = sum(job["status"] in {"completed", "partial"} for job in final.get("jobs", []))
            status = "failed" if failed == final.get("total") else "partial" if failed or finance_refresh_error else "completed"
            coverage = completed / int(final.get("total") or 1)
            self.store.update_incremental_run(
                run_id, status=status, completed=completed, failed=failed, coverage=coverage,
                message=finance_refresh_error[:1000], completed_at=now(),
            )
            if final.get("run_kind") == "bootstrap":
                self.store.update_universe_status(final["universe_id"], "ready" if completed else "partial")
            if final.get("run_kind") == "incremental":
                generated_events = self.evaluate_signal_rules(universe_id=final["universe_id"], as_of=final["as_of"])
        result = self.store.get_incremental_run(run_id) or {}
        result["generated_events"] = generated_events
        return result

    def _run_operation_job(self, job: dict[str, Any], operation: dict[str, Any]) -> None:
        self.store.update_incremental_job(
            job["id"], status="running", stage="facts", attempts=int(job.get("attempts") or 0) + 1,
        )
        try:
            def collect_in_isolated_store() -> dict[str, Any]:
                # A timed-out vendor/cache read must never hold the operation
                # store lock.  Its own short-lived connection can finish or
                # fail independently while the main queue keeps advancing.
                job_service = ValueWorkspaceService(ValueWorkspaceStore(self.store.db_path))
                try:
                    return job_service._collect_company_snapshot(
                        operation["universe_id"], job["symbol"], operation["as_of"],
                        bootstrap=operation["run_kind"] == "bootstrap",
                    )
                finally:
                    job_service.close()

            snapshot = _run_with_timeout(
                collect_in_isolated_store,
                COMPANY_SNAPSHOT_TIMEOUT_SECONDS,
                f"company snapshot {job['symbol']}",
            )
            status = "completed" if snapshot["status"] == "ready" else "partial"
            self.store.update_incremental_job(
                job["id"], status=status, stage="review", snapshot_id=snapshot["id"],
                message="资料快照已更新" if status == "completed" else "部分资料不可用，已保留可验证结果",
            )
        finally:
            # The frontend polls this stored aggregate every five seconds.
            self.store.refresh_incremental_progress(operation["id"])

    def _collect_company_snapshot(
        self, universe_id: str, symbol: str, as_of: str, *, bootstrap: bool,
    ) -> dict[str, Any]:
        universe = self.store.get_universe(universe_id)
        if not universe:
            raise KeyError("research universe not found")
        memberships = [item for item in universe["members"] if item["symbol"] == symbol]
        overview = get_tdx_service().security_overview(symbol, include_related=False, include_history=False)
        if not overview:
            raise ValueError("通达信公司事实数据不可用")
        finance = FinancialHistoryService()
        try:
            package = finance.package_status()
            annual = finance.query(symbol, as_of=as_of, period_type="annual")
            financial_items = list(annual.get("items") or [])
        finally:
            finance.client.close()
            finance.store.close()
        latest_financial = financial_items[-1] if financial_items else None
        previous_financial = financial_items[-2] if len(financial_items) > 1 else None
        quote = dict(overview.get("quote") or {})
        fundamental = dict(overview.get("fundamental") or {})
        cache = dict(overview.get("cache") or {})
        current_sectors = list(overview.get("sectors") or [])
        frozen_memberships = [{
            "track_id": item["track_id"], "track_name": item["track_name"],
            "track_rank": item["track_rank"], "leader_rank": item["leader_rank"],
            "leader_type": item["leader_type"],
        } for item in memberships]
        payload = {
            "quote": quote, "fundamental": fundamental,
            "financial_latest": latest_financial, "financial_previous": previous_financial,
            "memberships": frozen_memberships, "current_sectors": current_sectors,
            "cache": cache, "overview_as_of": overview.get("as_of"),
            "professional_finance_status": package.get("status"),
        }
        checks = {
            "quote": _number(quote.get("price")) is not None,
            "valuation": _number(fundamental.get("pe_ttm")) is not None or _number(fundamental.get("pb_mrq")) is not None,
            "professional_finance": bool(latest_financial) and package.get("status") == "ready",
            "membership": bool(memberships),
            "sector_membership": bool(current_sectors or memberships),
        }
        missing = [key for key, valid in checks.items() if not valid]
        completeness = sum(checks.values()) / len(checks)
        evidence_ids: list[str] = []
        evidence_payloads = (
            ("quote", "TongDaXin", f"{symbol}:quote:{str(overview.get('as_of') or as_of)[:10]}", quote),
            ("valuation", "TongDaXin", f"{symbol}:valuation:{str(overview.get('as_of') or as_of)[:10]}", fundamental),
            ("professional_finance", "TongDaXin professional finance / TQ", f"{symbol}:finance:{latest_financial.get('announcement_date') if latest_financial else as_of}", latest_financial or {}),
            ("sector_membership", "TongDaXin 881xxx", f"{symbol}:sectors:{str(overview.get('as_of') or as_of)[:10]}", current_sectors),
            ("universe_membership", "Value research universe", f"{universe_id}:{symbol}:membership", frozen_memberships),
        )
        for evidence_type, source, source_id, data in evidence_payloads:
            evidence, _ = self.store.upsert_evidence({
                "symbol": symbol, "evidence_type": evidence_type, "source": source,
                "source_id": source_id, "data_as_of": str(
                    latest_financial.get("announcement_date") if evidence_type == "professional_finance" and latest_financial else overview.get("as_of") or as_of
                )[:10],
                "published_at": latest_financial.get("announcement_date") if evidence_type == "professional_finance" and latest_financial else None,
                "content_hash": _stable_hash(data), "payload": data,
                "status": "ready" if checks.get({"universe_membership": "membership"}.get(evidence_type, evidence_type), False) else "unavailable",
            })
            evidence_ids.append(evidence["id"])
        previous = self.store.latest_snapshot(universe_id, symbol)
        dossier_id = previous.get("dossier_id") if previous else None
        report_id = previous.get("report_id") if previous else None
        if bootstrap and not previous:
            workspace = ResearchWorkspaceStore(self.store.db_path, seed=False)
            try:
                dossier = workspace.upsert_tdx_dossier(overview)
                report = workspace.create_company_report("CN", symbol)
                dossier_id, report_id = dossier.get("id"), report.get("id")
            finally:
                workspace.close()
        snapshot, _ = self.store.save_snapshot({
            "universe_id": universe_id, "symbol": symbol, "data_as_of": as_of,
            "status": "ready" if not missing else "partial", "completeness": completeness,
            "source_hash": _stable_hash(payload), "payload": payload,
            "diff": _plain_diff(previous, payload), "missing_fields": missing,
            "sources": [item[1] for item in evidence_payloads], "evidence_ids": evidence_ids,
            "dossier_id": dossier_id, "report_id": report_id,
        })
        return snapshot

    def run_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.store.get_batch(batch_id)
        if not batch:
            raise KeyError("research batch not found")
        self.store.update_batch(batch_id, status="running", started_at=now())
        if batch.get("cancel_requested"):
            for job in batch["jobs"]:
                if job["status"] == "queued":
                    self.store.update_job(job["id"], status="failed", stage="cancelled", message="research batch cancelled")
            final = self.store.get_batch(batch_id) or {}
            failed = sum(job["status"] == "failed" for job in final.get("jobs", []))
            self.store.update_batch(batch_id, status="cancelled", completed=0, failed=failed, completed_at=now())
            return self.store.get_batch(batch_id) or {}
        jobs = [job for job in batch["jobs"] if job["status"] == "queued"]
        if not jobs:
            return batch
        with ThreadPoolExecutor(max_workers=batch["concurrency"], thread_name_prefix="value-research") as executor:
            futures = {executor.submit(self._run_job, job, batch): job for job in jobs}
            for future in as_completed(futures):
                current = self.store.get_batch(batch_id) or {}
                if current.get("cancel_requested"):
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    future.result()
                except Exception as exc:  # individual failures must not fail the entire batch
                    job = futures[future]
                    self.store.update_job(job["id"], status="failed", stage="failed", message=str(exc), attempts=job["attempts"] + 1)
        final = self.store.get_batch(batch_id) or {}
        completed = sum(job["status"] == "completed" for job in final.get("jobs", []))
        failed = sum(job["status"] == "failed" for job in final.get("jobs", []))
        partial = sum(job["status"] == "partial" for job in final.get("jobs", []))
        if final.get("cancel_requested"):
            for job in final.get("jobs", []):
                if job["status"] == "queued":
                    self.store.update_job(job["id"], status="failed", stage="cancelled", message="research batch cancelled")
            final = self.store.get_batch(batch_id) or {}
            failed = sum(job["status"] == "failed" for job in final.get("jobs", []))
            completed = sum(job["status"] == "completed" for job in final.get("jobs", []))
            partial = sum(job["status"] == "partial" for job in final.get("jobs", []))
            status = "cancelled"
        else:
            status = "failed" if failed == final.get("total") else "partial" if failed or partial else "completed"
        self.store.update_batch(batch_id, status=status, completed=completed + partial, failed=failed, completed_at=now())
        return self.store.get_batch(batch_id) or {}

    def retry_failed_jobs(self, batch_id: str) -> dict[str, Any]:
        batch = self.store.get_batch(batch_id)
        if not batch:
            raise KeyError("research batch not found")
        failed_jobs = [job for job in batch["jobs"] if job["status"] == "failed"]
        if not failed_jobs:
            raise ValueError("no failed research jobs to retry")
        for job in failed_jobs:
            self.store.update_job(job["id"], status="queued", stage="facts", message="queued for retry", valuation_status="queued")
        self.store.update_batch(batch_id, status="queued", failed=0, cancel_requested=0, completed_at=None)
        return self.store.get_batch(batch_id) or {}

    def _run_job(self, job: dict[str, Any], batch: dict[str, Any]) -> None:
        self.store.update_job(job["id"], status="running", stage="facts", attempts=job["attempts"] + 1)
        overview = get_tdx_service().security_overview(job["symbol"])
        if not overview:
            raise ValueError("通达信公司事实数据不可用")
        workspace = ResearchWorkspaceStore(self.store.db_path, seed=False)
        try:
            dossier = workspace.upsert_tdx_dossier(overview)
            self.store.update_job(job["id"], stage="financials", dossier_id=dossier["id"])
            report = workspace.create_company_report("CN", job["symbol"])
        finally:
            workspace.close()
        self.store.update_job(job["id"], stage="valuation", report_id=report["id"])
        detail = get_tdx_service().sector_detail(batch["track_id"]) or {}
        peers = list(detail.get("members") or [])
        pe_values = [value for row in peers if (value := _number(row.get("pe_ttm"))) is not None and value > 0]
        pb_values = [value for row in peers if (value := _number(row.get("pb_mrq"))) is not None and value > 0]
        finance = overview.get("fundamental") or {}
        current_pe, current_pb = _number(finance.get("pe_ttm")), _number(finance.get("pb_mrq"))
        comps_ready = bool((current_pe and pe_values) or (current_pb and pb_values))
        valuation = {
            "comparable": {
                "pe_ttm": current_pe, "peer_median_pe": round(statistics.median(pe_values), 4) if pe_values else None,
                "pb_mrq": current_pb, "peer_median_pb": round(statistics.median(pb_values), 4) if pb_values else None,
            },
            "dcf": {"status": "unavailable", "reason": "缺少可验证的 PIT 自由现金流与预测输入，未猜测补全"},
            "review_required": True,
        }
        status = "partial"  # DCF is deliberately blocked until verified inputs exist.
        self.store.update_job(job["id"], status=status, stage="review", valuation_status="partial" if comps_ready else "unavailable", valuation_json=valuation, message="自动底稿完成，等待人工复核")

    def create_universe_monitor(
        self, *, universe_id: str, symbol: str, conditions: dict[str, Any], channels: list[str],
        position_state: str = "watching", risk_preset: str = "balanced",
    ) -> dict[str, Any]:
        if position_state not in {"watching", "holding"}:
            raise ValueError("position_state must be watching or holding")
        universe = self.store.get_universe(universe_id)
        if not universe:
            raise KeyError("research universe not found")
        company = next((item for item in universe["companies"] if item["symbol"] == symbol), None)
        snapshot = self.store.latest_snapshot(universe_id, symbol)
        if not company or not snapshot:
            raise ValueError("company must finish bootstrap before monitoring")
        memberships = sorted(company["memberships"], key=lambda item: (item["track_rank"], item["leader_rank"]))
        primary_track = memberships[0]["track_id"]
        batch, _ = self.store.create_batch(
            run_id=universe["engine_run_id"], profile_id=universe["profile_id"], track_id=primary_track,
            companies=[{"symbol": symbol, "name": company["name"]}],
            template_version=f"{RESEARCH_TEMPLATE_VERSION}:universe", concurrency=1,
        )
        job = batch["jobs"][0]
        if job["status"] not in {"partial", "completed"}:
            fundamental = dict((snapshot.get("payload") or {}).get("fundamental") or {})
            self.store.update_job(
                job["id"], status="partial", stage="review", message="研究宇宙档案已完成，等待人工复核",
                dossier_id=snapshot.get("dossier_id"), report_id=snapshot.get("report_id"),
                valuation_status="partial" if fundamental else "unavailable",
                valuation_json={
                    "comparable": {"pe_ttm": fundamental.get("pe_ttm"), "pb_mrq": fundamental.get("pb_mrq")},
                    "dcf": {"status": "unavailable", "reason": "缺少可验证的PIT自由现金流与预测输入"},
                    "review_required": True,
                },
            )
        return self.store.create_monitor(
            job_id=job["id"], conditions=conditions, channels=channels,
            position_state=position_state, universe_id=universe_id, risk_preset=risk_preset,
        )

    @staticmethod
    def _risk_reasons(payload: dict[str, Any]) -> list[str]:
        latest = dict(payload.get("financial_latest") or {})
        previous = dict(payload.get("financial_previous") or {})
        reasons: list[str] = []
        revenue_yoy = _number(latest.get("revenue_yoy"))
        profit_yoy = _number(latest.get("net_profit_yoy"))
        if revenue_yoy is not None and revenue_yoy <= RISK_THRESHOLDS["revenue_yoy"]:
            reasons.append(f"营收同比 {revenue_yoy:.1f}% ≤ -20%")
        if profit_yoy is not None and profit_yoy <= RISK_THRESHOLDS["net_profit_yoy"]:
            reasons.append(f"净利润同比 {profit_yoy:.1f}% ≤ -20%")
        roe, previous_roe = _number(latest.get("roe")), _number(previous.get("roe"))
        if roe is not None and previous_roe is not None and previous_roe - roe >= RISK_THRESHOLDS["roe_drop_pp"]:
            reasons.append(f"ROE同比下降 {previous_roe - roe:.1f} 个百分点")
        ocf, net_profit = _number(latest.get("operating_cash_flow")), _number(latest.get("net_profit"))
        if ocf is not None and net_profit is not None and net_profit > 0 and ocf < 0:
            reasons.append("公司盈利但经营现金流为负")
        debt, previous_debt = _number(latest.get("debt_ratio")), _number(previous.get("debt_ratio"))
        if debt is not None and previous_debt is not None and debt - previous_debt >= RISK_THRESHOLDS["debt_ratio_rise_pp"]:
            reasons.append(f"负债率同比上升 {debt - previous_debt:.1f} 个百分点")
        fundamental = dict(payload.get("fundamental") or {})
        if any(bool(fundamental.get(key)) for key in ("is_st", "st", "is_delisted", "delisted")):
            reasons.append("通达信证券属性显示ST或退市风险")
        return reasons

    @staticmethod
    def _entry_checks(conditions: dict[str, Any], payload: dict[str, Any]) -> list[tuple[str, bool]]:
        quote, fundamental = dict(payload.get("quote") or {}), dict(payload.get("fundamental") or {})
        price, pe, pb = _number(quote.get("price")), _number(fundamental.get("pe_ttm")), _number(fundamental.get("pb_mrq"))
        dividend = _number(fundamental.get("dividend_yield"))
        checks: list[tuple[str, bool]] = []
        low, high = _number(conditions.get("entry_low")), _number(conditions.get("entry_high"))
        if low is not None or high is not None:
            checks.append((f"价格位于 {low:g}–{high:g}" if low is not None and high is not None else "入场价格区间无效", bool(price is not None and low is not None and high is not None and low <= price <= high)))
        max_pe = _number(conditions.get("max_pe"))
        if max_pe is not None:
            checks.append((f"PE(TTM) ≤ {max_pe:g}", bool(pe is not None and pe <= max_pe)))
        max_pb = _number(conditions.get("max_pb"))
        if max_pb is not None:
            checks.append((f"PB(MRQ) ≤ {max_pb:g}", bool(pb is not None and pb <= max_pb)))
        min_dividend = _number(conditions.get("min_dividend_yield"))
        if min_dividend is not None:
            checks.append((f"股息率 ≥ {min_dividend:g}%", bool(dividend is not None and dividend >= min_dividend)))
        return checks

    @staticmethod
    def _exit_checks(conditions: dict[str, Any], payload: dict[str, Any]) -> list[tuple[str, bool]]:
        quote, fundamental = dict(payload.get("quote") or {}), dict(payload.get("fundamental") or {})
        price, pe, pb = _number(quote.get("price")), _number(fundamental.get("pe_ttm")), _number(fundamental.get("pb_mrq"))
        checks: list[tuple[str, bool]] = []
        exit_price = _number(conditions.get("exit_price"))
        if exit_price is not None:
            checks.append((f"价格达到 {exit_price:g}", bool(price is not None and price >= exit_price)))
        exit_pe = _number(conditions.get("exit_pe"))
        if exit_pe is not None:
            checks.append((f"PE(TTM) ≥ {exit_pe:g}", bool(pe is not None and pe >= exit_pe)))
        exit_pb = _number(conditions.get("exit_pb"))
        if exit_pb is not None:
            checks.append((f"PB(MRQ) ≥ {exit_pb:g}", bool(pb is not None and pb >= exit_pb)))
        return checks

    def evaluate_signal_rules(self, *, universe_id: str | None = None, as_of: str | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        target_date = as_of or date.today().isoformat()
        for monitor in self.store.list_monitors():
            if monitor["status"] != "active" or not monitor.get("universe_id"):
                continue
            if universe_id and monitor["universe_id"] != universe_id:
                continue
            snapshot = self.store.latest_snapshot(monitor["universe_id"], monitor["symbol"])
            if not snapshot:
                continue
            payload = dict(snapshot.get("payload") or {})
            missing = list(snapshot.get("missing_fields") or [])
            stale = bool((payload.get("cache") or {}).get("stale"))
            risk_reasons = self._risk_reasons(payload)
            entry_checks = self._entry_checks(monitor.get("conditions") or {}, payload)
            exit_checks = self._exit_checks(monitor.get("conditions") or {}, payload)
            thesis_invalidated = bool(monitor.get("thesis_invalidated"))
            reasons: list[str] = []
            if thesis_invalidated:
                signal_state, reasons = "thesis_invalidated", ["用户确认投资逻辑已经失效"]
            elif "professional_finance" in missing or "quote" in missing:
                signal_state, reasons = "data_insufficient", [f"缺少关键数据：{'、'.join(missing)}"]
            elif stale:
                signal_state, reasons = "stale", ["通达信行情或财务缓存已过期"]
            elif monitor.get("position_state") == "holding" and any(result for _, result in exit_checks):
                signal_state = "exit_candidate"
                reasons = [label for label, result in exit_checks if result]
            elif monitor.get("position_state") == "holding" and risk_reasons:
                signal_state, reasons = "holding_review", risk_reasons
            elif monitor.get("position_state") == "watching" and entry_checks and all(result for _, result in entry_checks):
                signal_state = "entry_candidate"
                reasons = [label for label, _ in entry_checks]
            else:
                signal_state = "watching"
                reasons = ["当前未满足方向性条件"]
            inputs = {
                "position_state": monitor.get("position_state"),
                "quote": payload.get("quote"), "fundamental": payload.get("fundamental"),
                "financial_latest": payload.get("financial_latest"),
                "financial_previous": payload.get("financial_previous"),
                "entry_checks": entry_checks, "exit_checks": exit_checks,
            }
            previous = self.store.latest_signal_evaluation(monitor["id"])
            input_hash = _stable_hash({
                "snapshot": snapshot["source_hash"], "conditions": monitor.get("conditions"),
                "position_state": monitor.get("position_state"), "thesis_invalidated": thesis_invalidated,
                "signal_state": signal_state, "reasons": reasons,
            })
            evaluation, created = self.store.save_signal_evaluation({
                "monitor_id": monitor["id"], "snapshot_id": snapshot["id"], "as_of": target_date,
                "signal_state": signal_state, "rule_version": SIGNAL_RULE_VERSION,
                "input_hash": input_hash, "rules": {
                    "risk_preset": "balanced", "risk_thresholds": RISK_THRESHOLDS,
                    "entry_mode": "all", "exit_mode": "any",
                }, "inputs": inputs, "reasons": reasons, "missing_fields": missing,
            })
            changed = not previous or previous.get("signal_state") != signal_state or previous.get("reasons") != reasons
            latest_matching_event = next((item for item in self.store.list_events(500) if item.get("monitor_id") == monitor["id"] and item.get("event_type") == signal_state), None)
            closed_reappeared = bool(latest_matching_event and latest_matching_event.get("status") == "closed")
            if not created or signal_state == "watching" or not (changed or closed_reappeared):
                continue
            labels = {
                "entry_candidate": ("入场候选", "info"), "holding_review": ("风险复核", "warning"),
                "exit_candidate": ("退出/减仓候选", "warning"), "thesis_invalidated": ("逻辑失效", "critical"),
                "data_insufficient": ("数据不足", "warning"), "stale": ("数据过期", "warning"),
            }
            title_label, severity = labels[signal_state]
            event = self.store.add_event(
                monitor_id=monitor["id"], event_key=f"{monitor['id']}:{signal_state}:{evaluation['id']}",
                event_type=signal_state, severity=severity, title=f"{monitor['name']} · {title_label}",
                message="；".join(reasons), payload={
                    "evaluation_id": evaluation["id"], "symbol": monitor["symbol"],
                    "signal_state": signal_state, "data_as_of": snapshot["data_as_of"],
                    "rule_version": SIGNAL_RULE_VERSION, "inputs": inputs,
                }, channels=monitor["channels"],
            )
            events.append(event)
        return events

    def evaluate_monitors(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = self.evaluate_signal_rules()
        tdx = get_tdx_service()
        for monitor in self.store.list_monitors():
            if monitor["status"] != "active" or monitor.get("universe_id"):
                continue
            overview = tdx.security_overview(monitor["symbol"])
            if not overview:
                continue
            quote, finance = overview.get("quote") or {}, overview.get("fundamental") or {}
            price = _number(quote.get("price"))
            conditions = monitor["conditions"]
            triggered: list[tuple[str, str, str]] = []
            low, high = _number(conditions.get("entry_low")), _number(conditions.get("entry_high"))
            if price is not None and low is not None and high is not None and low <= price <= high:
                triggered.append(("entry_zone", "info", f"价格 {price:g} 已进入 {low:g}–{high:g} 入场区间"))
            max_pe = _number(conditions.get("max_pe"))
            pe = _number(finance.get("pe_ttm"))
            if pe is not None and max_pe is not None and pe <= max_pe:
                triggered.append(("valuation", "info", f"PE(TTM) {pe:g} 已低于监控阈值 {max_pe:g}"))
            if bool((overview.get("cache") or {}).get("stale")):
                triggered.append(("data_stale", "warning", "行情或财务缓存已过期，需要人工复核"))
            data_key = str(overview.get("as_of") or date.today().isoformat())[:10]
            for event_type, severity, message in triggered:
                event = self.store.add_event(
                    monitor_id=monitor["id"], event_key=f"{monitor['id']}:{event_type}:{data_key}", event_type=event_type,
                    severity=severity, title=f"{monitor['name']} 入场监控", message=message,
                    payload={"symbol": monitor["symbol"], "price": price, "pe_ttm": pe, "data_as_of": data_key},
                    channels=monitor["channels"],
                )
                events.append(event)
            self.store._conn.execute("UPDATE value_entry_monitors SET last_checked_at=?,updated_at=? WHERE id=?", (now(), now(), monitor["id"]))
            self.store._conn.commit()
        return events

    async def deliver_notifications(self, events: list[dict[str, Any]], manager: Any | None) -> None:
        """Deliver external notifications independently; a channel failure never rolls back events."""
        from src.channels.bus.events import OutboundMessage

        for event in events:
            for channel_name in ("feishu", "weixin"):
                channel = (getattr(manager, "channels", {}) or {}).get(channel_name) if manager else None
                if channel is None:
                    self.store.update_delivery(event["id"], channel_name, status="skipped", error="channel is not enabled")
                    continue
                config = channel.config
                targets = config.get("allow_from", config.get("allowFrom", [])) if isinstance(config, dict) else getattr(config, "allow_from", [])
                if not targets:
                    self.store.update_delivery(event["id"], channel_name, status="skipped", error="no notification recipient configured")
                    continue
                try:
                    for target in targets:
                        await channel.send(OutboundMessage(channel=channel_name, chat_id=str(target), content=f"{event['title']}\n{event['message']}"))
                    self.store.update_delivery(event["id"], channel_name, status="sent")
                except Exception as exc:  # each adapter records its own failure
                    self.store.update_delivery(event["id"], channel_name, status="failed", error=str(exc))
