# -*- coding: utf-8 -*-
"""Phase A: strict read-only table-level audit over production DBs.

Value Line Company Research Coverage & Data Gap Priority Audit V1.
- Opens both DBs with sqlite3 URI mode=ro: production writes are impossible.
- No service calls, no LLM, no network.
Output: tmp/research-coverage-audit/phase_a.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

RESEARCH_DB = Path.home() / ".vibe-trading" / "research.db"
TDX_DB = Path.home() / ".vibe-trading" / "tdx_data.db"
OUT = Path(__file__).resolve().parent / "phase_a.json"

POOL_AS_OF = None  # resolved from data


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def loads(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except Exception:
        return fallback


def main() -> None:
    started = time.perf_counter()
    research = ro(RESEARCH_DB)
    tdx = ro(TDX_DB)
    cur = research.cursor()

    # ---- anchor universe -------------------------------------------------
    pool_rows = cur.execute(
        "SELECT * FROM company_low_value_leader_pool WHERE market='CN' AND pool_status='ACTIVE'"
    ).fetchall()
    pool = {r["stock_code"]: dict(r) for r in pool_rows}
    as_of_counter = Counter(str(r["source_as_of"])[:10] for r in pool_rows)
    pool_as_of = as_of_counter.most_common(1)[0][0]

    cursors = {}
    for r in cur.execute("SELECT * FROM value_strategy_state_cursors").fetchall():
        cursors[r["stock_code"]] = dict(r)

    companies = []
    for code, item in pool.items():
        c = cursors.get(code, {})
        companies.append({
            "stock_code": code,
            "bare_code": code.split(".")[0],
            "company_name": item.get("company_name"),
            "industry_code": item.get("industry_code"),
            "industry_name": item.get("industry_name"),
            "valuation_status": item.get("valuation_status"),
            "historical_valuation_status": item.get("historical_valuation_status"),
            "pool_source_as_of": str(item.get("source_as_of"))[:10],
            "leader_score": item.get("leader_score"),
            "focus": c.get("current_priority"),
            "primary_action": c.get("current_primary_action"),
            "cursor_risk": c.get("current_risk"),
            "cursor_value_trap": c.get("current_value_trap"),
            "cursor_thesis_status": c.get("current_thesis_status"),
            "cursor_thesis_authority": c.get("current_thesis_authority"),
            "cursor_valuation_reliability": c.get("current_valuation_reliability"),
            "cursor_research_as_of": str(c.get("research_as_of") or "")[:10],
        })
    print(f"pool active={len(pool)} as_of={pool_as_of} cursors={len(cursors)}")

    # ---- financial analysis snapshots (latest per code) -------------------
    fin_latest: dict[str, dict] = {}
    fin_rows = cur.execute(
        """SELECT stock_code, id, as_of, created_at, feature_status, forecast_status,
                  analysis_status, historical_cutoff, financial_feature_version,
                  feature_json, analysis_payload_json, data_gaps_json, source_hash,
                  agent_provider, agent_model, agent_error
           FROM company_financial_analysis_snapshots
           ORDER BY stock_code, as_of DESC, created_at DESC, rowid DESC"""
    ).fetchall()
    fin_all_status: dict[str, list] = defaultdict(list)
    for r in fin_rows:
        code = r["stock_code"]
        fin_all_status[code].append((r["as_of"], r["analysis_status"]))
        if code not in fin_latest:
            row = dict(r)
            feature = loads(row.pop("feature_json"), {}) or {}
            analysis = loads(row.pop("analysis_payload_json"), None)
            row["feature"] = feature
            row["analysis"] = analysis
            row["data_gaps"] = loads(row.pop("data_gaps_json"), [])
            dq = (feature or {}).get("data_quality") or {}
            row["dq"] = dq
            fin_latest[code] = row

    # ---- business research snapshots (latest per code) --------------------
    biz_latest: dict[str, dict] = {}
    biz_rows = cur.execute(
        """SELECT stock_code, id, data_as_of, created_at, analysis_status,
                  snapshot_json, analysis_json, agent_error
           FROM company_business_research_snapshots
           ORDER BY stock_code, data_as_of DESC, created_at DESC, rowid DESC"""
    ).fetchall()
    for r in biz_rows:
        code = r["stock_code"]
        if code not in biz_latest:
            row = dict(r)
            row["snapshot"] = loads(row.pop("snapshot_json"), {}) or {}
            row["analysis"] = loads(row.pop("analysis_json"), None)
            biz_latest[code] = row

    # ---- business missing root causes: scan ALL business snapshots' errors
    biz_root = Counter()
    for r in biz_rows:
        st = r["analysis_status"]
        err = str(r["agent_error"] or "")
        if st == "NOT_RUN":
            biz_root["NOT_RUN"] += 1
        elif st == "FAILED":
            biz_root["LLM_FAILED"] += 1
        elif st == "PARTIAL":
            biz_root["PARTIAL_CLAIMS"] += 1
    for code in pool:
        if code not in biz_latest:
            biz_root["NO_SNAPSHOT"] += 1

    # ---- disclosure documents ---------------------------------------------
    disc = {}
    for r in cur.execute(
        """SELECT stock_code,
                  COUNT(*) AS doc_count,
                  SUM(CASE WHEN report_kind='ANNUAL' THEN 1 ELSE 0 END) AS annual,
                  SUM(CASE WHEN report_kind='SEMIANNUAL' THEN 1 ELSE 0 END) AS semiannual,
                  SUM(CASE WHEN report_kind='Q1' THEN 1 ELSE 0 END) AS q1,
                  SUM(CASE WHEN report_kind='Q3' THEN 1 ELSE 0 END) AS q3,
                  MAX(announcement_date) AS latest_announcement,
                  SUM(CASE WHEN extraction_status='READY' THEN 1 ELSE 0 END) AS extracted,
                  SUM(CASE WHEN extraction_status='FAILED' THEN 1 ELSE 0 END) AS parse_failed,
                  SUM(CASE WHEN text_sha256 IS NOT NULL AND text_sha256!='' THEN 1 ELSE 0 END) AS has_text
           FROM company_disclosure_documents GROUP BY stock_code"""
    ).fetchall():
        # disclosure table keys by bare code ('000034'); pool keys by '000034.SZ'
        disc[str(r["stock_code"]).split(".")[0]] = dict(r)

    # ---- risk snapshots: latest per code ----------------------------------
    risk_latest: dict[str, dict] = {}
    for r in cur.execute(
        """SELECT * FROM company_low_value_risk_snapshots
           ORDER BY stock_code, source_as_of DESC, updated_at DESC"""
    ).fetchall():
        code = r["stock_code"]
        if code not in risk_latest:
            risk_latest[code] = dict(r)

    # ---- risk research preparation (latest per code) -----------------------
    prep_latest: dict[str, dict] = {}
    for r in cur.execute(
        """SELECT market, stock_code, research_as_of, financial_status,
                  business_profile_status, business_research_status, disclosure_status,
                  thesis_status, overall_status, missing_capabilities_json, last_error,
                  draft_status, validation_status, provisional_thesis_status, updated_at
           FROM company_risk_research_preparation
           ORDER BY stock_code, updated_at DESC"""
    ).fetchall():
        code = r["stock_code"]
        if code not in prep_latest:
            row = dict(r)
            row["missing_capabilities"] = loads(row.pop("missing_capabilities_json"), [])
            prep_latest[code] = row

    # ---- theses current ----------------------------------------------------
    thesis_latest: dict[str, dict] = {}
    for r in cur.execute(
        """SELECT * FROM company_theses WHERE is_current=1
           ORDER BY stock_code, version DESC"""
    ).fetchall():
        code = r["stock_code"]
        if code not in thesis_latest:
            row = dict(r)
            row["invalid_conditions"] = loads(row.pop("invalid_conditions_json"), [])
            row["supporting_conditions"] = loads(row.pop("supporting_conditions_json"), [])
            row["key_metrics_to_monitor"] = loads(row.pop("key_metrics_to_monitor_json"), [])
            thesis_latest[code] = row

    thesis_evidence = defaultdict(int)
    for r in cur.execute(
        "SELECT thesis_id, COUNT(*) n FROM company_thesis_evidence WHERE is_active=1 GROUP BY thesis_id"
    ).fetchall():
        thesis_evidence[r["thesis_id"]] = r["n"]

    # ---- moat evidence counts ----------------------------------------------
    moat_counts: dict[str, dict] = {}
    for r in cur.execute(
        """SELECT stock_code, COUNT(*) n, COUNT(DISTINCT moat_dimension) dims
           FROM company_moat_evidence WHERE status='ACTIVE' GROUP BY stock_code"""
    ).fetchall():
        moat_counts[r["stock_code"]] = dict(r)
    moat_types = Counter()
    for r in cur.execute(
        "SELECT evidence_type, COUNT(*) n FROM company_moat_evidence WHERE status='ACTIVE' GROUP BY evidence_type"
    ).fetchall():
        moat_types[r["evidence_type"]] = r["n"]

    # ---- business driver evidence ------------------------------------------
    driver_counts: dict[str, dict] = defaultdict(lambda: {"n": 0, "dims": Counter()})
    for r in cur.execute(
        "SELECT stock_code, dimension, COUNT(*) n FROM company_business_driver_evidence WHERE status='ACTIVE' GROUP BY stock_code, dimension"
    ).fetchall():
        d = driver_counts[r["stock_code"]]
        d["n"] += r["n"]
        d["dims"][r["dimension"]] = r["n"]

    # ---- CIO reports ---------------------------------------------------------
    cio_latest: dict[str, dict] = {}
    for r in cur.execute(
        """SELECT id, stock_code, research_as_of, status, overall_freshness,
                  synthesis_source, formula_version, model_version, created_at, updated_at
           FROM company_cio_research_reports
           ORDER BY stock_code, research_as_of DESC, created_at DESC"""
    ).fetchall():
        code = r["stock_code"]
        if code not in cio_latest:
            cio_latest[code] = dict(r)
    cio_sections = defaultdict(list)
    for r in cur.execute(
        "SELECT report_id, section_type, freshness_status, structured_payload_json, narrative_md FROM company_cio_report_sections"
    ).fetchall():
        payload = loads(r["structured_payload_json"], {}) or {}
        cio_sections[r["report_id"]].append({
            "section_type": r["section_type"],
            "freshness_status": r["freshness_status"],
            "payload_status": payload.get("status"),
            "has_narrative": bool(r["narrative_md"]),
        })

    # ---- historical valuation coverage (tdx) --------------------------------
    hist_val = {}
    for r in tdx.execute("SELECT * FROM historical_valuation_coverage").fetchall():
        hist_val[r["stock_code"]] = dict(r)
    try:
        qrow = tdx.execute(
            """SELECT market_date, qualification FROM v_market_close_qualifications
               WHERE market='CN' ORDER BY market_date DESC LIMIT 10"""
        ).fetchall()
        qualified_dates = [dict(x) for x in qrow]
    except Exception as exc:  # noqa: BLE001
        qualified_dates = [{"error": str(exc)}]

    # ---- assemble per-company raw facts --------------------------------------
    per_company = {}
    for item in companies:
        code = item["stock_code"]
        fin = fin_latest.get(code)
        fin_dq = (fin or {}).get("dq") or {}
        claims = ((fin or {}).get("analysis") or {}).get("claims") or []
        claims_with_sources = [c for c in claims if (c.get("source_keys") or c.get("evidence_keys"))]
        biz = biz_latest.get(code)
        biz_snap = (biz or {}).get("snapshot") or {}
        biz_dq = biz_snap.get("data_quality") or {}
        biz_analysis = (biz or {}).get("analysis") or {}
        biz_claims = biz_analysis.get("claims") or []
        risk = risk_latest.get(code)
        prep = prep_latest.get(code)
        thesis = thesis_latest.get(code)
        d = disc.get(item["bare_code"]) or {}
        moat = moat_counts.get(code) or {"n": 0, "dims": 0}
        drv = driver_counts.get(code) or {"n": 0, "dims": {}}
        cio = cio_latest.get(code)
        hv = hist_val.get(code) or {}

        per_company[code] = {
            **item,
            "financial": {
                "has_snapshot": fin is not None,
                "as_of": str((fin or {}).get("as_of") or "")[:10],
                "feature_status": (fin or {}).get("feature_status"),
                "forecast_status": (fin or {}).get("forecast_status"),
                "analysis_status": (fin or {}).get("analysis_status"),
                "annual_period_count": fin_dq.get("annual_period_count"),
                "latest_report_date": fin_dq.get("latest_report_date"),
                "latest_announcement_date": fin_dq.get("latest_announcement_date"),
                "coverage_note": fin_dq.get("coverage"),
                "missing_fields": fin_dq.get("missing_fields") or [],
                "data_gaps": (fin or {}).get("data_gaps") or [],
                "history_len": len((fin or {}).get("history") or []) if (fin or {}).get("history") else None,
                "claims_total": len(claims),
                "claims_with_sources": len(claims_with_sources),
                "analysis_metadata": (((fin or {}).get("analysis") or {}).get("analysis_metadata") or {}),
                "agent_error": (fin or {}).get("agent_error"),
            },
            "business": {
                "has_snapshot": biz is not None,
                "data_as_of": str((biz or {}).get("data_as_of") or "")[:10],
                "analysis_status": (biz or {}).get("analysis_status"),
                "dq_status": biz_dq.get("status"),
                "main_business": biz_snap.get("main_business"),
                "products_count": len(biz_snap.get("products") or []),
                "business_changes_count": len(biz_snap.get("business_changes") or []),
                "claims_total": len(biz_claims),
                "claims_with_sources": sum(1 for c in biz_claims if c.get("source_keys")),
                "field_statuses": biz_dq.get("field_statuses") or {},
                "missing_fields": biz_dq.get("missing_fields") or [],
                "agent_error": (biz or {}).get("agent_error"),
            },
            "disclosure": {
                "doc_count": d.get("doc_count") or 0,
                "annual": d.get("annual") or 0,
                "semiannual": d.get("semiannual") or 0,
                "q1": d.get("q1") or 0,
                "q3": d.get("q3") or 0,
                "latest_announcement": d.get("latest_announcement"),
                "extracted": d.get("extracted") or 0,
                "parse_failed": d.get("parse_failed") or 0,
                "has_text": d.get("has_text") or 0,
            },
            "risk": {
                "has_snapshot": risk is not None,
                "source_as_of": str((risk or {}).get("source_as_of") or "")[:10],
                "overall_risk": (risk or {}).get("overall_risk"),
                "value_trap_risk": (risk or {}).get("value_trap_risk"),
                "material_risk_count": (risk or {}).get("material_risk_count"),
                "high_risk_count": (risk or {}).get("high_risk_count"),
                "financial_status": (risk or {}).get("financial_status"),
                "business_status": (risk or {}).get("business_status"),
                "thesis_status": (risk or {}).get("thesis_status"),
                "error": (risk or {}).get("error"),
                "formula_version": (risk or {}).get("formula_version"),
            },
            "preparation": {
                "has_row": prep is not None,
                "research_as_of": str((prep or {}).get("research_as_of") or "")[:10],
                "overall_status": (prep or {}).get("overall_status"),
                "financial_status": (prep or {}).get("financial_status"),
                "business_profile_status": (prep or {}).get("business_profile_status"),
                "business_research_status": (prep or {}).get("business_research_status"),
                "disclosure_status": (prep or {}).get("disclosure_status"),
                "thesis_status": (prep or {}).get("thesis_status"),
                "missing_capabilities": (prep or {}).get("missing_capabilities") or [],
                "last_error": (prep or {}).get("last_error"),
                "draft_status": (prep or {}).get("draft_status"),
                "validation_status": (prep or {}).get("validation_status"),
                "provisional_thesis_status": (prep or {}).get("provisional_thesis_status"),
            },
            "thesis": None if thesis is None else {
                "thesis_id": thesis.get("thesis_id"),
                "authority_status": thesis.get("authority_status"),
                "status": thesis.get("status"),
                "confidence": thesis.get("confidence"),
                "version": thesis.get("version"),
                "core_thesis_len": len(str(thesis.get("core_thesis") or "")),
                "title": thesis.get("title"),
                "invalid_count": len(thesis.get("invalid_conditions") or []),
                "supporting_count": len(thesis.get("supporting_conditions") or []),
                "metrics_count": len(thesis.get("key_metrics_to_monitor") or []),
                "source_data_as_of": str(thesis.get("source_data_as_of") or "")[:10],
                "updated_at": str(thesis.get("updated_at") or "")[:10],
                "created_by": thesis.get("created_by"),
                "evidence_count": thesis_evidence.get(thesis.get("thesis_id"), 0),
                "invalid_conditions": thesis.get("invalid_conditions") or [],
                "core_thesis_head": str(thesis.get("core_thesis") or "")[:160],
            },
            "moat_evidence": {"count": moat.get("n") or 0, "dimensions": moat.get("dims") or 0},
            "business_driver": {"count": drv.get("n") or 0, "dims": dict(drv.get("dims") or {})},
            "cio": None if cio is None else {
                "report_id": cio.get("id"),
                "research_as_of": str(cio.get("research_as_of") or "")[:10],
                "status": cio.get("status"),
                "overall_freshness": cio.get("overall_freshness"),
                "synthesis_source": cio.get("synthesis_source"),
                "created_at": str(cio.get("created_at") or "")[:10],
                "sections": cio_sections.get(cio.get("id")) or [],
            },
            "historical_valuation": {
                "has_row": bool(hv),
                "coverage_status": hv.get("coverage_status"),
                "first_date": hv.get("first_date"),
                "last_date": hv.get("last_date"),
                "pe_count": hv.get("pe_count"),
                "pb_count": hv.get("pb_count"),
                "dividend_yield_count": hv.get("dividend_yield_count"),
                "last_error": hv.get("last_error"),
            },
        }

    # ---- appendix: pool-external special companies table facts -------------
    appendix_codes = ["600460.SH", "002371.SZ"]
    appendix = {}
    for code in appendix_codes:
        fin = fin_latest.get(code)
        biz = biz_latest.get(code)
        risk = risk_latest.get(code)
        thesis = thesis_latest.get(code)
        cio = cio_latest.get(code)
        appendix[code] = {
            "in_pool": code in pool,
            "financial": None if fin is None else {
                "as_of": str(fin.get("as_of"))[:10], "feature_status": fin.get("feature_status"),
                "analysis_status": fin.get("analysis_status"),
                "dq": fin.get("dq"),
            },
            "business": None if biz is None else {
                "data_as_of": str(biz.get("data_as_of"))[:10], "analysis_status": biz.get("analysis_status"),
                "dq_status": (biz.get("snapshot") or {}).get("data_quality", {}).get("status"),
                "main_business": (biz.get("snapshot") or {}).get("main_business"),
            },
            "risk": None if risk is None else {
                "source_as_of": str(risk.get("source_as_of"))[:10],
                "overall_risk": risk.get("overall_risk"),
                "value_trap_risk": risk.get("value_trap_risk"),
            },
            "thesis": None if thesis is None else {
                "authority_status": thesis.get("authority_status"),
                "status": thesis.get("status"),
                "invalid_count": len(thesis.get("invalid_conditions") or []),
                "evidence_count": thesis_evidence.get(thesis.get("thesis_id"), 0),
            },
            "moat_evidence": moat_counts.get(code) or {"n": 0, "dims": 0},
            "business_driver": dict(driver_counts.get(code) or {"n": 0, "dims": {}}),
            "cio": None if cio is None else {
                "research_as_of": str(cio.get("research_as_of"))[:10],
                "overall_freshness": cio.get("overall_freshness"),
                "synthesis_source": cio.get("synthesis_source"),
                "sections": cio_sections.get(cio.get("id")) or [],
            },
            "historical_valuation": hist_val.get(code) or {},
            "disclosure": disc.get(code.split(".")[0]) or {},
        }

    result = {
        "pool_as_of": pool_as_of,
        "active_count": len(pool),
        "focus_counts": Counter(c["focus"] or "UNKNOWN" for c in companies),
        "primary_action_counts": Counter(c["primary_action"] or "UNKNOWN" for c in companies),
        "cursor_risk_counts": Counter(c["cursor_risk"] or "UNKNOWN" for c in companies),
        "cursor_trap_counts": Counter(c["cursor_value_trap"] or "UNKNOWN" for c in companies),
        "moat_evidence_types": dict(moat_types),
        "business_root_causes_all": dict(biz_root),
        "qualified_close_dates": qualified_dates,
        "financial_companies": len(fin_latest),
        "business_companies": len(biz_latest),
        "disclosure_companies": len(disc),
        "thesis_current_companies": len(thesis_latest),
        "per_company": per_company,
        "appendix_pool_external": appendix,
    }

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"phase A done in {time.perf_counter() - started:.1f}s -> {OUT}")
    print("focus:", dict(result["focus_counts"]))
    print("primary_action:", dict(result["primary_action_counts"]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
