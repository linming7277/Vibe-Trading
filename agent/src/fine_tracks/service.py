"""Industry-sequential, idempotent Fine Track V1 application service."""

from __future__ import annotations

from typing import Any

from src.research_tasks.providers import validate_provider_model
from src.research_tasks.store import ResearchTaskStore

from .classifier import (
    CLASSIFIER_INSTRUCTION,
    ProviderTrackClassifierRuntime,
    TrackClassifierRuntime,
    eligible_profile,
    merge_batch_result,
    validate_batch_result,
)
from .database_classifier import classify_profiles
from .models import (
    DATABASE_TRACK_CLASSIFICATION_VERSION,
    TDX_TERMINAL_INDUSTRY_SOURCE,
    TRACK_CLASSIFICATION_VERSION,
    stable_hash,
)
from .profiles import CompanyBusinessProfileService
from .store import FineTrackStore


class FineTrackService:
    def __init__(self, *, store: FineTrackStore | None = None,
                 profiles: CompanyBusinessProfileService | None = None,
                 runtime: TrackClassifierRuntime | None = None,
                 agent_store: ResearchTaskStore | None = None,
                 batch_size: int = 50) -> None:
        self.store = store or FineTrackStore()
        self.profiles = profiles or CompanyBusinessProfileService()
        self.runtime = runtime or ProviderTrackClassifierRuntime()
        self.agent_store = agent_store or ResearchTaskStore(self.store.db_path)
        self.batch_size = max(1, min(100, batch_size))

    def close(self) -> None:
        self.agent_store.close()
        self.store.close()

    def industries(self) -> dict[str, Any]:
        items = [{
            **row,
            "level3_code": row["industry_code"],
            "level3_name": row["industry_name"],
            "terminal_level": int(row.get("level") or 3),
        } for row in self.profiles.industries()]
        return {
            "items": items,
            "total": len(items),
            "level1_total": len({row.get("level1_code") for row in items if row.get("level1_code")}),
            "level2_total": len({row.get("level2_code") for row in items if row.get("level2_code")}),
            "level3_total": len(items),
            "level2_leaf_total": sum(row.get("raw_industry_level") == "TDX_RESEARCH_LEVEL_2_LEAF" for row in items),
            "source": TDX_TERMINAL_INDUSTRY_SOURCE,
        }

    def companies(self, industry_code: str) -> dict[str, Any]:
        industry = self.profiles.industry(industry_code)
        profiles = self.profiles.profiles(industry_code)
        self.store.upsert_profiles(profiles)
        counts = {status: sum(row["data_status"] == status for row in profiles) for status in ("REAL", "PARTIAL", "MISSING")}
        return {"industry": industry, "items": profiles, "total": len(profiles), "data_status_counts": counts}

    def tracks(self, industry_code: str) -> dict[str, Any]:
        industry = self.profiles.industry(industry_code)
        tracks = self.store.list_tracks(industry["industry_code"])
        return {
            "industry": industry, "items": tracks, "total": len(tracks),
            "unclassified": self.store.unclassified(industry["industry_code"]),
            "new_suggestions": self.store.suggestions(industry["industry_code"]),
            "classification_version": DATABASE_TRACK_CLASSIFICATION_VERSION,
        }

    def classify_industry(self, industry_code: str) -> dict[str, Any]:
        industry = self.profiles.industry(industry_code)
        profiles = self.profiles.profiles(industry_code)
        self.store.upsert_profiles(profiles)
        profile_hash = stable_hash([{key: row[key] for key in (
            "stock_code", "source_hash", "data_status",
        )} for row in profiles])
        idempotency_key = stable_hash([
            industry["industry_code"], profile_hash, TRACK_CLASSIFICATION_VERSION,
        ])
        completed = self.store.get_completed_run(idempotency_key)
        if completed:
            return {**completed, "idempotent_reuse": True, "tracks": self.tracks(industry_code)}

        config = self.agent_store.get_runtime_config("track_classifier")
        if not config["enabled"]:
            raise ValueError("CONFIGURATION_ERROR: track_classifier is disabled")
        if not config.get("base_url") and config["provider"].strip().lower() == "ollama":
            raise ValueError("CONFIGURATION_ERROR: track_classifier 不允许使用 Ollama，请选择已配置的云端 Provider")
        if config.get("base_url"):
            if not config["model"]:
                raise ValueError("CONFIGURATION_ERROR: track_classifier model is required")
        else:
            validate_provider_model(config["provider"], config["model"], self.agent_store.list_configs())
        run = self.store.start_run(
            idempotency_key=idempotency_key, industry=industry, profile_hash=profile_hash,
            version=TRACK_CLASSIFICATION_VERSION, provider=config["provider"], model=config["model"],
            company_count=len(profiles),
        )
        insufficient = [row for row in profiles if not eligible_profile(row)]
        eligible = [row for row in profiles if eligible_profile(row)]
        aggregate: dict[str, Any] = {
            "industry_code": industry["industry_code"], "industry_name": industry["industry_name"],
            "tracks": [],
            "unclassified": [{
                "stock_code": row["stock_code"], "classification_status": "INSUFFICIENT_DATA",
                "reason": "缺少足够的真实主营业务、经营范围、公司描述或产品资料",
            } for row in insufficient],
        }
        try:
            for start in range(0, len(eligible), self.batch_size):
                batch = eligible[start:start + self.batch_size]
                existing = self.store.track_catalog(industry["industry_code"])
                discovered = [{
                    "track_name": row["track_name"], "description": row["description"],
                } for row in aggregate["tracks"]]
                catalog = [{"track_name": row["track_name"], "description": row["description"]} for row in existing]
                seen = {row["track_name"] for row in catalog}
                catalog.extend(row for row in discovered if row["track_name"] not in seen)
                payload = {
                    "industry": {"code": industry["industry_code"], "name": industry["industry_name"]},
                    "companies": [{key: row.get(key, "") for key in (
                        "stock_code", "stock_name", "business_scope", "main_business",
                        "company_description", "main_products",
                    )} for row in batch],
                    "existing_tracks": catalog,
                }
                connection_invoke = getattr(self.runtime, "invoke_with_connection", None)
                if config.get("base_url") and callable(connection_invoke):
                    raw = connection_invoke(
                        model=config["model"], base_url=config["base_url"],
                        api_key=config.get("api_key") or "",
                        instruction=CLASSIFIER_INSTRUCTION, payload=payload,
                    )
                else:
                    raw = self.runtime.invoke(
                        provider=config["provider"], model=config["model"],
                        instruction=CLASSIFIER_INSTRUCTION, payload=payload,
                    )
                clean = validate_batch_result(
                    raw, industry={"industry_code": industry["industry_code"], "industry_name": industry["industry_name"]},
                    company_codes={row["stock_code"] for row in batch},
                )
                merge_batch_result(aggregate, clean)
            classified_codes = {
                company["stock_code"] for track in aggregate["tracks"] for company in track["companies"]
            }
            returned_codes = classified_codes | {row["stock_code"] for row in aggregate["unclassified"]}
            for row in eligible:
                if row["stock_code"] not in returned_codes:
                    aggregate["unclassified"].append({
                        "stock_code": row["stock_code"], "classification_status": "UNCLASSIFIED",
                        "reason": "模型未返回该公司的可验证分类",
                    })
            counts = self.store.apply_classification(
                industry={"industry_code": industry["industry_code"], "industry_name": industry["industry_name"]},
                profiles=profiles, result=aggregate, version=TRACK_CLASSIFICATION_VERSION,
                profile_hash=profile_hash,
                classification_source=f"track_classifier:{config['provider']}:{config['model']}",
            )
            finished = self.store.finish_run(
                run["run_id"], status="COMPLETED", classified_count=counts["classified"],
                unclassified_count=counts["unclassified"], output={"counts": counts, "classification": aggregate},
            )
            return {**finished, "idempotent_reuse": False, "tracks": self.tracks(industry_code)}
        except Exception as exc:
            self.store.finish_run(run["run_id"], status="FAILED", error=f"{type(exc).__name__}: {exc}")
            raise

    def classify_industry_from_database(self, industry_code: str) -> dict[str, Any]:
        """Classify directly from existing TDX business fields, with no model call."""
        industry = self.profiles.industry(industry_code)
        profiles = self.profiles.profiles(industry_code)
        self.store.upsert_profiles(profiles)
        profile_hash = stable_hash([{key: row[key] for key in (
            "stock_code", "source_hash", "data_status",
        )} for row in profiles])
        version = DATABASE_TRACK_CLASSIFICATION_VERSION
        idempotency_key = stable_hash([industry["industry_code"], profile_hash, version])
        completed = self.store.get_completed_run(idempotency_key)
        if completed:
            return {**completed, "idempotent_reuse": True, "tracks": self.tracks(industry_code)}
        run = self.store.start_run(
            idempotency_key=idempotency_key, industry=industry, profile_hash=profile_hash,
            version=version, provider="database", model="tdx-business-text-cluster-v1",
            company_count=len(profiles),
        )
        try:
            result = classify_profiles(industry, profiles)
            counts = self.store.apply_classification(
                industry={"industry_code": industry["industry_code"], "industry_name": industry["industry_name"]},
                profiles=profiles, result=result, version=version, profile_hash=profile_hash,
                classification_source="database_business_text_cluster",
            )
            finished = self.store.finish_run(
                run["run_id"], status="COMPLETED", classified_count=counts["classified"],
                unclassified_count=counts["unclassified"], output={"counts": counts, "classification": result},
            )
            return {**finished, "idempotent_reuse": False, "tracks": self.tracks(industry_code)}
        except Exception as exc:
            self.store.finish_run(run["run_id"], status="FAILED", error=f"{type(exc).__name__}: {exc}")
            raise

    def classify_all_from_database(self) -> dict[str, Any]:
        results = []
        for industry in self.profiles.industries():
            try:
                run = self.classify_industry_from_database(industry["industry_code"])
                results.append({"industry_code": industry["industry_code"], "status": run["status"],
                                "idempotent_reuse": run.get("idempotent_reuse", False)})
            except Exception as exc:
                results.append({"industry_code": industry["industry_code"], "status": "FAILED", "error": str(exc)})
        return {"items": results, "total": len(results),
                "completed": sum(row["status"] == "COMPLETED" for row in results),
                "failed": sum(row["status"] == "FAILED" for row in results)}

    def classify_all_industries(self) -> dict[str, Any]:
        results = []
        for industry in self.profiles.industries():
            try:
                run = self.classify_industry(industry["industry_code"])
                results.append({"industry_code": industry["industry_code"], "status": run["status"],
                                "idempotent_reuse": run.get("idempotent_reuse", False)})
            except Exception as exc:
                results.append({"industry_code": industry["industry_code"], "status": "FAILED", "error": str(exc)})
        return {"items": results, "total": len(results),
                "completed": sum(row["status"] == "COMPLETED" for row in results),
                "failed": sum(row["status"] == "FAILED" for row in results)}


_service: FineTrackService | None = None


def get_fine_track_service() -> FineTrackService:
    global _service
    if _service is None:
        _service = FineTrackService()
    return _service
