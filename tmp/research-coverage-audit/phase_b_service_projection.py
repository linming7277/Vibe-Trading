# -*- coding: utf-8 -*-
"""Phase B: service-level read-only projections over DB snapshot copies.

Guarantees:
- Production DBs are never opened for writing: we first copy them with the
  sqlite backup API from mode=ro connections into a temp runtime root, then
  point VIBE_TRADING_HOME at that temp root before importing any src module.
- LLM calls: 0 (all services used are deterministic projections).
- Network calls: 0 (all inputs are local SQLite stores).

Usage: python phase_b_service_projection.py [--smoke]
Output: tmp/research-coverage-audit/phase_b.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_ROOT = HERE.parents[1] / "agent"
REAL_ROOT = Path.home() / ".vibe-trading"
SNAP_ROOT = HERE / f"runtime-snapshot-{time.strftime('%Y%m%d-%H%M%S')}"


def snapshot_dbs() -> None:
    """Consistent copies of research.db / tdx_data.db into SNAP_ROOT."""
    SNAP_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("research.db", "tdx_data.db"):
        src = REAL_ROOT / name
        dst = SNAP_ROOT / name
        source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(str(dst))
        with target:
            source.backup(target)
        source.close()
        target.close()
    agent_json = REAL_ROOT / "agent.json"
    if agent_json.exists():
        shutil.copy2(agent_json, SNAP_ROOT / "agent.json")
    # best-effort cleanup of older snapshot dirs (Windows may still hold one)
    for old in HERE.glob("runtime-snapshot-*"):
        if old == SNAP_ROOT:
            continue
        try:
            shutil.rmtree(old)
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    snapshot_dbs()
    os.environ["VIBE_TRADING_HOME"] = str(SNAP_ROOT)
    sys.path.insert(0, str(AGENT_ROOT))

    from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
    from src.value_watchpoints import get_value_watchpoint_projection_service

    pool = LowValueLeaderPoolRepository()
    codes = [str(item["stock_code"]) for item in pool.active("CN")]
    pool.close()
    appendix_codes = [c for c in ("600460.SH", "002371.SZ") if c not in set(codes)]
    if args.smoke:
        # focus A first 3 + one appendix for wiring check
        smoke_main = codes[:3]
        codes = smoke_main
        appendix_codes = appendix_codes[:1]
    all_codes = codes + appendix_codes
    print(f"companies={len(codes)} appendix={appendix_codes}")

    timings = {}

    # ---- frozen batch watchpoint path --------------------------------------
    t0 = time.perf_counter()
    wp_service = get_value_watchpoint_projection_service()
    watchpoints = wp_service.get_watchpoints_batch("CN", all_codes)
    timings["watchpoint_batch_s"] = round(time.perf_counter() - t0, 2)

    from src.deep_research.coverage import get_deep_research_coverage_service
    from src.moat_research import get_moat_research_service
    from src.capital_allocation_research import get_capital_allocation_research_service
    from src.normalized_earnings import get_normalized_earnings_reference_service
    from src.cycle_profit_scenario import get_cycle_profit_scenario_service
    from src.research_freshness import get_research_freshness_service
    from src.value_watchpoints.read_cache import scoped_read_cache

    # DeepResearchCoverageService re-instantiates several schema-ensuring
    # stores on every coverage() call (~5s of pure constructor overhead each).
    # Cache those constructors in this harness: same classes, same reads, and
    # the snapshot copies make even accidental writes impossible.
    _ctor_cache: dict[type, object] = {}

    def _cached_ctor(cls):
        def factory(*_args, **_kwargs):
            if cls not in _ctor_cache:
                _ctor_cache[cls] = cls(*_args, **_kwargs)
            return _ctor_cache[cls]
        return factory

    import src.disclosure_materials.store as _dm_store
    import src.level3_leaders.business_profiles as _bp_mod
    import src.moat_evidence.store as _me_store
    import src.company_thesis.store as _ct_store
    import src.company_thesis.draft_store as _ct_draft
    _dm_store.DisclosureMaterialStore = _cached_ctor(_dm_store.DisclosureMaterialStore)
    _bp_mod.CompanyBusinessProfileService = _cached_ctor(_bp_mod.CompanyBusinessProfileService)
    _me_store.MoatEvidenceStore = _cached_ctor(_me_store.MoatEvidenceStore)
    _ct_store.CompanyThesisRepository = _cached_ctor(_ct_store.CompanyThesisRepository)
    _ct_draft.CompanyThesisDraftRepository = _cached_ctor(_ct_draft.CompanyThesisDraftRepository)

    deep_svc = get_deep_research_coverage_service()
    moat_svc = get_moat_research_service()
    cap_svc = get_capital_allocation_research_service()
    norm_svc = get_normalized_earnings_reference_service()
    cycle_svc = get_cycle_profit_scenario_service()
    fresh_svc = get_research_freshness_service()

    def safe(fn, *fargs, **fkwargs):
        try:
            return fn(*fargs, **fkwargs)
        except Exception as exc:  # noqa: BLE001
            return {"__error__": f"{type(exc).__name__}: {exc}"}

    per_company = {}
    phase_start = time.perf_counter()
    scope = scoped_read_cache()
    scope.__enter__()
    for idx, code in enumerate(all_codes):
        entry: dict = {"in_pool": code not in set(appendix_codes)}

        wp = watchpoints.get(code) or {}
        entry["watchpoint"] = {
            "error": wp.get("error"),
            "top_count": len(wp.get("top_watchpoints") or []),
            "watchpoint_count": len(wp.get("watchpoints") or []),
            "data_gap_count": len(wp.get("data_gaps") or []),
            "primary_action": wp.get("primary_action"),
            "focus_tier": wp.get("focus_tier"),
            "suggested_research_need": wp.get("suggested_research_need"),
            "source_freshness": wp.get("source_freshness") or {},
            "categories": sorted({str(w.get("category")) for w in (wp.get("watchpoints") or [])}),
        }

        cov = safe(deep_svc.coverage, "CN", code)
        entry["deep_coverage"] = cov

        moat = safe(moat_svc.get_research, "CN", code)
        dims = moat.get("dimensions") or []
        entry["moat"] = {
            "status": moat.get("status"),
            "dimension_count": len(dims),
            "supported": sum(1 for d in dims if d.get("status") == "SUPPORTED"),
            "partial": sum(1 for d in dims if d.get("status") == "PARTIAL"),
            "unknown": sum(1 for d in dims if d.get("status") == "UNKNOWN"),
            "challenged": sum(1 for d in dims if d.get("evidence_balance") == "CHALLENGED"),
            "applicable": sum(1 for d in dims if d.get("applicability") == "APPLICABLE"),
            "challenges_count": len(moat.get("moat_challenges") or []),
            "data_gaps": len(moat.get("moat_data_gaps") or []),
            "error": moat.get("__error__"),
        }

        cap = safe(cap_svc.get_research, "CN", code)
        cap_dims = cap.get("dimensions") or {}
        core = ("reinvestment", "debt_management", "cash_management", "equity_dilution")
        entry["capital"] = {
            "status": cap.get("status"),
            "core_known": sum(1 for k in core if (cap_dims.get(k) or {}).get("status") not in (None, "UNKNOWN")),
            "dividend_status": (cap_dims.get("dividend") or {}).get("status"),
            "buyback_status": (cap_dims.get("buyback") or {}).get("status"),
            "mna_status": (cap_dims.get("m_and_a") or {}).get("status"),
            "data_gaps": [g.get("item") if isinstance(g, dict) else str(g) for g in (cap.get("data_gaps") or [])],
            "error": cap.get("__error__"),
        }

        norm = safe(norm_svc.reference, "CN", code)
        entry["normalized"] = {
            "status": norm.get("status"),
            "applicability": norm.get("applicability"),
            "insufficient_reason": norm.get("insufficient_reason"),
            "error": norm.get("__error__"),
        }

        cyc = safe(cycle_svc.scenario, "CN", code)
        entry["cycle"] = {
            "status": cyc.get("status"),
            "applicability": cyc.get("applicability"),
            "not_applicable_reason": cyc.get("not_applicable_reason"),
            "error": cyc.get("__error__"),
        }

        fresh = safe(fresh_svc.classify, "CN", code)
        entry["freshness"] = fresh

        per_company[code] = entry
        if (idx + 1) % 50 == 0:
            print(f"  {idx + 1}/{len(all_codes)} elapsed={time.perf_counter() - phase_start:.1f}s")

    scope.__exit__(None, None, None)
    timings["per_company_services_s"] = round(time.perf_counter() - phase_start, 2)

    # ---- live CIO section freshness for companies that have reports ---------
    from src.cio_report import get_cio_report_service

    cio_svc = get_cio_report_service()
    cio_live = {}
    for code in all_codes:
        live = safe(cio_svc.classify_report_sections, "CN", code)
        if live and not live.get("__error__"):
            cio_live[code] = live
        elif live and live.get("__error__") and "NOT_FOUND" not in str(live.get("__error__")):
            cio_live[code] = {"error": live.get("__error__")}

    result = {
        "timings": timings,
        "company_count": len(per_company),
        "cio_live_freshness": cio_live,
        "per_company": per_company,
    }
    out = HERE / ("phase_b_smoke.json" if args.smoke else "phase_b.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(timings, ensure_ascii=False))
    print(f"phase B done -> {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
