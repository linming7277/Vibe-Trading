"""BusinessDriverProfileService — read-only projection of evidence for CIO."""

from __future__ import annotations

from typing import Any


class BusinessDriverProfileService:
    """Aggregate Business Driver Evidence into a boss-readable projection."""

    def __init__(self, store: Any = None) -> None:
        if store is None:
            from src.business_driver.store import BusinessDriverEvidenceStore
            self.store = BusinessDriverEvidenceStore()
        else:
            self.store = store

    def profile(self, stock_code: str) -> dict[str, Any]:
        code = stock_code.upper()
        evidence = self.store.list_evidence(code)
        if not evidence:
            return {"status": "MISSING", "stock_code": code,
                    "boss_message": "尚未从年报中提取结构化经营数据。"}

        products: list[dict[str, Any]] = []
        regions: list[dict[str, Any]] = []
        volumes: list[dict[str, Any]] = []
        customer: dict[str, Any] | None = None
        capex: dict[str, Any] | None = None
        for e in evidence:
            dim = e.get("dimension")
            if dim in ("SEGMENT_REVENUE", "SEGMENT_MARGIN"):
                if str(e.get("fact_key", "")).startswith("product:"):
                    products.append({
                        "name": e.get("raw_name"),
                        "revenue": e.get("revenue"),
                        "revenue_yoy": e.get("revenue_yoy"),
                        "gross_margin": e.get("gross_margin"),
                        "period": e.get("period"),
                    })
                elif str(e.get("fact_key", "")).startswith("industry:"):
                    products.append({
                        "name": e.get("raw_name"),
                        "revenue": e.get("revenue"),
                        "revenue_yoy": e.get("revenue_yoy"),
                        "gross_margin": e.get("gross_margin"),
                        "period": e.get("period"),
                        "level": "industry",
                    })
            elif dim == "REGIONAL_MIX":
                regions.append({
                    "name": e.get("raw_name"),
                    "revenue": e.get("revenue"),
                    "gross_margin": e.get("gross_margin"),
                    "period": e.get("period"),
                })
            elif dim == "PRODUCT_VOLUME":
                volumes.append({
                    "name": e.get("raw_name"),
                    "unit": e.get("unit"),
                    "production": e.get("production_volume"),
                    "sales": e.get("sales_volume"),
                    "inventory": e.get("inventory_volume"),
                    "period": e.get("period"),
                })
            elif dim == "CUSTOMER" and customer is None:
                customer = {
                    "top5_share": e.get("customer_share"),
                    "top5_sales": e.get("value"),
                    "period": e.get("period"),
                }
            elif dim == "CAPEX_PROJECT" and capex is None:
                capex = {
                    "in_building_current": e.get("value"),
                    "in_building_prior": e.get("value_secondary"),
                    "period": e.get("period"),
                }

        periods = sorted({str(e.get("period") or "") for e in evidence if e.get("period")})
        return {
            "status": "READY" if products or volumes else "PARTIAL",
            "stock_code": code,
            "latest_period": periods[-1] if periods else None,
            "products": sorted(products, key=lambda x: -(x.get("revenue") or 0)),
            "regions": regions,
            "product_volumes": volumes,
            "customer_concentration": customer,
            "capex_summary": capex,
            "data_gaps": (
                [] if customer else ["customer_concentration"]
            ) + ([] if volumes else ["product_volume"]) + ([] if capex else ["capex_project"]),
        }


_service: BusinessDriverProfileService | None = None


def get_business_driver_profile_service() -> BusinessDriverProfileService:
    global _service
    if _service is None:
        _service = BusinessDriverProfileService()
    return _service
