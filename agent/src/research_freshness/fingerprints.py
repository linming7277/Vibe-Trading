"""Per-module input fingerprint computations for freshness classification.

All functions are pure reads over persisted tables (plan §9.2).  They mirror
the fingerprint each module already persists where one exists, so a direct
comparison classifies freshness; for date-keyed writers (pool / risk
snapshot / daily brief) the computed value is what gets recorded into
``research_manifests`` at write time (plan §20.3).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def fingerprint_financial(stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
    """Current financial snapshot input fingerprint (clock-free, plan §20.1)."""
    from src.financial_analysis.service import get_financial_analysis_service

    return get_financial_analysis_service().input_fingerprint(stock_code, as_of=as_of)


def fingerprint_business(stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
    from src.business_research import get_business_research_service

    return get_business_research_service().input_fingerprint(stock_code, as_of=as_of)


def fingerprint_valuation(stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
    """Price-family inputs: quote, bars, fundamentals, financial basis."""
    from src.tdx_data import get_tdx_service

    try:
        tdx = get_tdx_service()
        code = stock_code.upper()
        quote = dict((tdx.store.get_record("quotes", code) or {}).get("payload") or {})
        fundamentals = tdx.store.get_record("fundamentals", code) or {}
        bars_last = ""
        try:
            rows = tdx.store.get_adjusted_daily_bars(code, limit=1)
            bars_last = str(rows[0].get("trade_date") or "")[:10] if rows else ""
        except Exception:  # noqa: BLE001
            bars_last = ""
        from src.value_price_zones.service import FORMULA_VERSION

        return {
            "input_fingerprint": _digest({
                "v": FORMULA_VERSION, "code": code, "as_of": (as_of or "")[:10],
                "quote_updated": quote.get("updated_at") or fundamentals.get("updated_at"),
                "quote_close": quote.get("price") or quote.get("last") or quote.get("Now"),
                "bars_last": bars_last,
                "fundamentals_updated": fundamentals.get("updated_at"),
            }),
            "quote_updated_at": quote.get("updated_at"),
            "bars_last_date": bars_last,
        }
    except Exception:  # noqa: BLE001
        return None


def fingerprint_risk(stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
    """Risk-rule inputs: financial/business snapshots, thesis, disclosures, price."""
    from src.financial_analysis.service import get_financial_analysis_service
    from src.risk_research import get_risk_research_service

    try:
        code = stock_code.upper()
        financial = get_financial_analysis_service()
        fin_row = financial.store.latest(code, as_of=as_of)
        risk = get_risk_research_service()
        business_row = risk.business_store.latest(code, as_of=as_of) if getattr(risk, "business_store", None) else None
        thesis = risk.thesis_repository.get_current_thesis("CN", code)
        disclosures = risk.disclosure_store.list_materials(code, as_of=as_of) if getattr(risk, "disclosure_store", None) else []
        valuation = None
        try:
            zones = risk.price_zone_service.get_price_zones("CN", code, as_of=as_of) if getattr(risk, "price_zone_service", None) else None
            valuation = dict((zones or {}).get("valuation") or {}).get("status")
        except Exception:  # noqa: BLE001
            valuation = None
        from src.risk_research.service import FORMULA_VERSION as RISK_FORMULA_VERSION

        return {
            "input_fingerprint": _digest({
                "v": RISK_FORMULA_VERSION,
                "fin_snapshot_id": (fin_row or {}).get("id"),
                "fin_source_hash": (fin_row or {}).get("source_hash"),
                "biz_source_hash": (business_row or {}).get("source_hash"),
                "thesis_version": (thesis or {}).get("version"),
                "disclosures": sorted(str(row.get("announcement_date") or "") for row in disclosures or []),
                "valuation_status": valuation,
            }),
            "fin_snapshot_id": (fin_row or {}).get("id"),
            "thesis_version": (thesis or {}).get("version"),
        }
    except Exception:  # noqa: BLE001
        return None


def fingerprint_moat(stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
    from src.moat_evidence.store import MoatEvidenceStore
    from src.moat_research.service import FORMULA_VERSION as MOAT_FORMULA_VERSION

    try:
        code = stock_code.upper()
        rows = MoatEvidenceStore().list("CN", code, as_of=as_of)
        return {
            "input_fingerprint": _digest({
                "v": MOAT_FORMULA_VERSION,
                "evidence": sorted(f"{row.get('fingerprint')}|{row.get('status')}" for row in rows),
            }),
            "evidence_count": len(rows),
        }
    except Exception:  # noqa: BLE001
        return None


def fingerprint_capital_allocation(stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
    from src.capital_allocation_research.service import FORMULA_VERSION as CAP_FORMULA_VERSION
    from src.company_actions.store import CompanyActionEventStore
    from src.tdx_data import get_tdx_service
    from src.tdx_data.financial_history import FinancialHistoryService

    try:
        code = stock_code.upper()
        history = FinancialHistoryService(store=getattr(get_tdx_service(), "store", None)).query(code, as_of=as_of)
        rows = list((history or {}).get("items") or [])
        events = CompanyActionEventStore(initialize=False).list_events("CN", code, as_of=as_of)
        return {
            "input_fingerprint": _digest({
                "v": CAP_FORMULA_VERSION,
                "history": [
                    f"{row.get('report_date')}@{row.get('announcement_date')}:{row.get('raw_version')}"
                    for row in rows
                ],
                "events": sorted(f"{row.get('event_id')}|{row.get('event_date')}" for row in events),
            }),
            "event_count": len(events),
        }
    except Exception:  # noqa: BLE001
        return None
