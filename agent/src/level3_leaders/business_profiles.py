
"""Read TDX terminal industries and real company business text from caches."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.tdx_data.service import TdxDataService, get_tdx_service

from .catalog import TdxResearchIndustryCatalog
from .constants import TDX_TERMINAL_INDUSTRY_SOURCE
from .helpers import business_text, stable_hash


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(mapping.get(key) or "").strip()
        if value and value not in {"--", "0", "0.0"}:
            return value
    return ""


class CompanyBusinessProfileService:
    def __init__(self, tdx: TdxDataService | None = None,
                 catalog: TdxResearchIndustryCatalog | None = None) -> None:
        self.tdx = tdx or get_tdx_service()
        self.catalog = catalog or TdxResearchIndustryCatalog(self.tdx.client.home)
        self._industry_cache: list[dict[str, Any]] | None = None
        self._security_cache: dict[str, dict[str, Any]] | None = None
        self._fundamental_cache: dict[str, dict[str, Any]] | None = None
        self._detail_cache: dict[str, dict[str, Any]] | None = None

    @staticmethod
    def _business_fields(fundamental: dict[str, Any], detail: dict[str, Any]) -> dict[str, str]:
        extended = fundamental.get("extended_raw") or {}
        base = fundamental.get("base_raw") or {}
        detail_extended = detail.get("extended") or {}
        merged = {**base, **extended, **detail_extended}
        return {
            "business_scope": _first(merged, ("BusinessScope", "BusinessRange", "OperationScope", "JYFW", "Jyfw")),
            "main_business": str(
                fundamental.get("main_business") or _first(merged, ("MainBusiness", "ZYYW"))
            ).strip(),
            "company_description": _first(
                merged, ("CompanyDescription", "CompanyIntroduction", "CompanyIntro", "GSJJ")
            ),
            "main_products": _first(merged, ("MainProducts", "MainProduct", "Products", "ZYCP")),
        }

    def profile(self, stock_code: str) -> dict[str, Any] | None:
        """Return one company's current TDX business profile without requiring L3 membership."""
        code = stock_code.strip().upper()
        security = self.tdx.store.get_record("securities", code) or {}
        fundamental_row = self.tdx.store.get_record("fundamentals", code) or {}
        detail_row = self.tdx.store.get_record("security_details", code) or {}
        if not security and not fundamental_row and not detail_row:
            return None
        fundamental = fundamental_row.get("payload") or {}
        detail = detail_row.get("payload") or {}
        fields = self._business_fields(fundamental, detail)
        text = business_text(fields)
        updated_values = [
            str(row.get("updated_at") or "")
            for row in (fundamental_row, detail_row, security)
            if row.get("updated_at")
        ]
        data_as_of = max(updated_values, default="") or _now()
        sources: list[dict[str, Any]] = []
        if fundamental_row:
            sources.append({
                "provider": "通达信客户端缓存",
                "dataset": "fundamentals",
                "record_key": code,
                "data_as_of": fundamental_row.get("updated_at") or data_as_of,
                "fields": [key for key, value in fields.items() if value],
            })
        if detail_row:
            detail_fields = [key for key, value in fields.items() if value and key != "main_business"]
            if detail_fields:
                sources.append({
                    "provider": "通达信客户端缓存",
                    "dataset": "security_details",
                    "record_key": code,
                    "data_as_of": detail_row.get("updated_at") or data_as_of,
                    "fields": detail_fields,
                })
        profile = {
            "stock_code": code,
            "stock_name": str(
                security.get("name") or fundamental_row.get("name") or fundamental.get("name") or code
            ),
            **fields,
            "source": sources,
            "updated_at": data_as_of,
            "data_status": "REAL" if len(text) >= 8 else "PARTIAL" if text else "MISSING",
        }
        profile["source_hash"] = stable_hash({
            "stock_code": code,
            **fields,
            "sources": sources,
        })
        return profile

    def industries(self) -> list[dict[str, Any]]:
        if self._industry_cache is not None:
            return list(self._industry_cache)
        self.catalog.sync_cache(self.tdx.store)
        self._industry_cache = [
            {**row, "source": TDX_TERMINAL_INDUSTRY_SOURCE}
            for row in self.catalog.terminal_industries()
        ]
        return list(self._industry_cache)

    def industry(self, industry_code: str) -> dict[str, Any]:
        code = industry_code.strip().upper()
        row = next((item for item in self.industries() if item["industry_code"] == code), None)
        if not row:
            raise KeyError(code)
        return row

    def profiles(self, industry_code: str) -> list[dict[str, Any]]:
        industry = self.industry(industry_code)
        member_codes = self.catalog.members(industry["industry_code"])
        if self._security_cache is None:
            self._security_cache = {row["key"]: row for row in self.tdx.store.list_records("securities", limit=10_000)["items"]}
        if self._fundamental_cache is None:
            self._fundamental_cache = {row["key"]: row for row in self.tdx.store.list_records("fundamentals", limit=10_000)["items"]}
        if self._detail_cache is None:
            self._detail_cache = {row["key"]: row for row in self.tdx.store.list_records("security_details", limit=10_000)["items"]}
        securities, fundamentals, details = self._security_cache, self._fundamental_cache, self._detail_cache
        result: list[dict[str, Any]] = []
        available_codes = set(securities) | set(fundamentals)
        for code in member_codes:
            if code not in available_codes:
                continue
            fundamental_row = fundamentals.get(code) or {}
            fundamental = fundamental_row.get("payload") or {}
            detail = (details.get(code) or {}).get("payload") or {}
            fields = self._business_fields(fundamental, detail)
            text = business_text(fields)
            data_status = "REAL" if len(text) >= 8 else "PARTIAL" if text else "MISSING"
            updated_at = fundamental_row.get("updated_at") or industry.get("as_of") or _now()
            sources = [{
                "provider": "通达信客户端", "dataset": "research_industry_hierarchy",
                "record_key": industry["industry_code"], "data_as_of": industry.get("as_of"),
                "fields": ["third_level_industry_code", "third_level_industry_name"],
            }, {
                "provider": "通达信客户端缓存", "dataset": "fundamentals",
                "record_key": code, "data_as_of": updated_at,
                "fields": [key for key, value in fields.items() if value],
            }]
            profile = {
                "stock_code": code,
                "stock_name": str((securities.get(code) or {}).get("name") or fundamental_row.get("name") or ""),
                "third_level_industry_code": industry["industry_code"],
                "third_level_industry_name": industry["industry_name"],
                "second_level_industry_code": industry.get("level2_code"),
                "second_level_industry_name": industry.get("level2_name"),
                "first_level_industry_code": industry.get("level1_code"),
                "first_level_industry_name": industry.get("level1_name"),
                **fields, "source": sources, "updated_at": updated_at, "data_status": data_status,
            }
            profile["source_hash"] = stable_hash({
                "stock_code": code, "industry_code": industry["industry_code"],
                "tdx_class_code": industry["tdx_class_code"],
                **{key: profile[key] for key in fields},
            })
            result.append(profile)
        return sorted(result, key=lambda item: item["stock_code"])

