"""Application service for the value research workbench."""

from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

from src.research_workspace.store import ResearchWorkspaceStore
from src.strategy_engines.common.normalization import cross_sectional_percentiles
from src.strategy_engines.common.scoring import weighted_score
from src.strategy_engines.store import StrategyEngineStore
from src.tdx_data.service import get_tdx_service

from .store import ValueWorkspaceStore, now


PROFILE_FORMULA_VERSION = "value-profile-v1.0.0"
RESEARCH_TEMPLATE_VERSION = "value-company-research-v1.0.0"
V2_WORKBENCH_FORMULA_VERSION = "value-workbench-v2.2.0"

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

    def evaluate_monitors(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        tdx = get_tdx_service()
        for monitor in self.store.list_monitors():
            if monitor["status"] != "active":
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
