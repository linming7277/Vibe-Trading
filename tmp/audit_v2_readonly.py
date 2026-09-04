#!/usr/bin/env python3
"""Read-only HZStock V2 audit data collector. No writes, no network, no LLM."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
sys.path.insert(0, str(AGENT))

RUNTIME = Path(os.environ.get("VIBE_TRADING_HOME", Path.home() / ".vibe-trading"))
RESEARCH_DB = RUNTIME / "research.db"
TDX_DB = RUNTIME / "tdx_data.db"

out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "runtime": str(RUNTIME)}


def q(conn: sqlite3.Connection, sql: str, params=()):
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def q1(conn, sql, params=()):
    rows = q(conn, sql, params)
    return rows[0] if rows else {}


def table_exists(conn, name: str) -> bool:
    return bool(q1(conn, "SELECT 1 AS x FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def db_size(path: Path) -> float | None:
    return round(path.stat().st_size / (1024**2), 2) if path.exists() else None


# --- DB baseline ---
out["databases"] = {
    "research_db": {"path": str(RESEARCH_DB), "size_mb": db_size(RESEARCH_DB)},
    "tdx_data_db": {"path": str(TDX_DB), "size_mb": db_size(TDX_DB)},
}

if not RESEARCH_DB.exists():
    print(json.dumps({"error": "research.db missing", **out}, ensure_ascii=False, indent=2))
    sys.exit(1)

rc = sqlite3.connect(f"file:{RESEARCH_DB}?mode=ro", uri=True)
rc.row_factory = sqlite3.Row

# schema version
schema = q1(rc, "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1") if table_exists(rc, "schema_migrations") else {}
out["schema_version"] = schema.get("version")

# --- TDX market close / EOD ---
if TDX_DB.exists():
    tc = sqlite3.connect(f"file:{TDX_DB}?mode=ro", uri=True)
    tc.row_factory = sqlite3.Row
    close_rows = q(tc, """
        SELECT market_date, qualification, run_status, quotes_status, quotes_item_count, updated_at
        FROM v_market_close_qualifications
        WHERE market='CN'
        ORDER BY market_date DESC LIMIT 10
    """) if table_exists(tc, "v_market_close_qualifications") else []
    out["market_close"] = close_rows
    qualified = [r for r in close_rows if r.get("qualification") == "QUALIFIED"]
    out["latest_qualified_market_close"] = qualified[0]["market_date"] if qualified else (close_rows[0]["market_date"] if close_rows else None)
    tc.close()
else:
    out["market_close"] = []
    out["latest_qualified_market_close"] = None

# Low value refresh / source_as_of
lv_refresh = q(rc, """
    SELECT source_as_of, status, created_at, updated_at, active_count, removed_count
    FROM low_value_leader_pool_refreshes
    ORDER BY source_as_of DESC LIMIT 5
""") if table_exists(rc, "low_value_leader_pool_refreshes") else []
out["low_value_refresh_history"] = lv_refresh
latest_lv = lv_refresh[0] if lv_refresh else {}
out["latest_low_value_source_as_of"] = latest_lv.get("source_as_of")

# L3 runs
l3_runs = q(rc, "SELECT id, as_of, status, created_at, completed_at FROM value_level3_leader_runs ORDER BY as_of DESC, completed_at DESC LIMIT 5") if table_exists(rc, "value_level3_leader_runs") else []
out["l3_runs"] = l3_runs
latest_l3 = l3_runs[0] if l3_runs else {}
out["latest_l3_run"] = latest_l3
if latest_l3.get("id"):
    stats_row = q1(rc, "SELECT statistics_json FROM value_level3_leader_runs WHERE id=?", (latest_l3["id"],))
    try:
        out["latest_l3_statistics"] = json.loads(stats_row.get("statistics_json") or "{}")
    except json.JSONDecodeError:
        out["latest_l3_statistics"] = {}

# L3 pool members current (latest completed pool run)
if table_exists(rc, "l3_leader_pool_runs"):
    latest_pool = q1(rc, "SELECT id, as_of, status, completed_at FROM l3_leader_pool_runs WHERE status='COMPLETED' ORDER BY as_of DESC, completed_at DESC LIMIT 1")
    out["latest_l3_pool_run"] = latest_pool
    if latest_pool.get("id") and table_exists(rc, "l3_leader_pool_members"):
        members = q(rc, """
            SELECT leader_rank, COUNT(*) AS cnt
            FROM l3_leader_pool_members
            WHERE pool_id=? AND lifecycle_status IN ('NEW','ACTIVE','REENTERED')
            GROUP BY leader_rank ORDER BY leader_rank
        """, (latest_pool["id"],))
        out["l3_current_top_counts"] = members

# L1/L2/L3 catalog counts from tdx if available
if TDX_DB.exists():
    tc = sqlite3.connect(f"file:{TDX_DB}?mode=ro", uri=True)
    tc.row_factory = sqlite3.Row
    for ds, label in [
        ("research_terminal_industry_members", "l1_l2_l3_catalog"),
        ("research_industry_hierarchy", "industry_hierarchy"),
    ]:
        if table_exists(tc, "records"):
            cnt = q1(tc, "SELECT COUNT(*) AS c FROM records WHERE dataset=?", (ds,))
            out[f"tdx_{label}_records"] = cnt.get("c")
    tc.close()

# Low value pool active
lv_active = q(rc, """
    SELECT valuation_status, leader_rank, COUNT(*) AS cnt
    FROM company_low_value_leader_pool
    WHERE pool_status='ACTIVE'
    GROUP BY valuation_status, leader_rank
""") if table_exists(rc, "company_low_value_leader_pool") else []
out["low_value_active_breakdown"] = lv_active
lv_totals = q(rc, """
    SELECT pool_status, COUNT(*) AS cnt FROM company_low_value_leader_pool GROUP BY pool_status
""") if table_exists(rc, "company_low_value_leader_pool") else []
out["low_value_status_totals"] = lv_totals

# Strategy cursors & events
cursors = q(rc, "SELECT COUNT(*) AS cnt FROM value_strategy_state_cursors") if table_exists(rc, "value_strategy_state_cursors") else [{"cnt": 0}]
out["strategy_cursor_count"] = cursors[0]["cnt"]
latest_cursor = q1(rc, "SELECT research_as_of, updated_at FROM value_strategy_state_cursors ORDER BY research_as_of DESC LIMIT 1") if table_exists(rc, "value_strategy_state_cursors") else {}
out["latest_strategy_cursor"] = latest_cursor

events_by_type = q(rc, """
    SELECT event_type, COUNT(*) AS cnt FROM value_strategy_state_events GROUP BY event_type ORDER BY cnt DESC
""") if table_exists(rc, "value_strategy_state_events") else []
out["strategy_events_by_type"] = events_by_type
out["strategy_event_total"] = sum(r["cnt"] for r in events_by_type)

event_status = q(rc, """
    SELECT status, COUNT(*) AS cnt FROM value_strategy_state_events GROUP BY status
""") if table_exists(rc, "value_strategy_state_events") else []
out["event_open_ack_closed"] = event_status
delivery_status = q(rc, """
    SELECT delivery_status, delivery_mode, COUNT(*) AS cnt FROM value_strategy_event_deliveries GROUP BY delivery_status, delivery_mode
""") if table_exists(rc, "value_strategy_event_deliveries") else []
out["event_delivery_status"] = delivery_status
latest_event = q1(rc, "SELECT event_type, research_as_of, created_at FROM value_strategy_state_events ORDER BY created_at DESC LIMIT 1") if table_exists(rc, "value_strategy_state_events") else {}
out["latest_strategy_event"] = latest_event

# Daily brief
brief = q1(rc, "SELECT research_as_of, created_at, formula_version FROM investment_research_daily_briefs ORDER BY research_as_of DESC LIMIT 1") if table_exists(rc, "investment_research_daily_briefs") else {}
out["latest_daily_brief"] = brief

# Focus via service
try:
    os.environ.setdefault("VIBE_TRADING_HOME", str(RUNTIME))
    from src.focus_selection.service import get_focus_selection_service
    from src.value_strategy.service import get_value_strategy_state_service

    focus = get_focus_selection_service().get_focus_selection()
    out["focus_counts"] = {k: len(focus.get(k) or []) for k in ("A", "B", "C")}
    out["focus_research_as_of"] = focus.get("as_of") or focus.get("research_as_of")

    focus_a_detail = []
    svc = get_value_strategy_state_service()
    for item in focus.get("A") or []:
        code = item.get("stock_code")
        try:
            st = svc.get_strategy_state("CN", code)
            focus_a_detail.append({
                "stock_code": code,
                "stock_name": item.get("stock_name") or item.get("company_name"),
                "primary_action": st.get("primary_action"),
                "risk_level": (st.get("risk") or {}).get("level"),
                "priority_tier": st.get("priority_tier"),
            })
        except Exception as exc:
            focus_a_detail.append({"stock_code": code, "error": str(exc)})
    out["focus_a_detail"] = focus_a_detail
except Exception as exc:
    out["focus_service_error"] = str(exc)

# PIT readiness
try:
    from src.pit_replay.readiness import PITReplayReadinessService
    pit = PITReplayReadinessService()
    trust = pit.trust_start_date()
    ready_dates = pit.list_ready_dates(limit=30)
    ready_full = [d for d in ready_dates if d.get("status") == "READY"]
    out["pit_trust_start_date"] = trust
    out["pit_ready_date_count"] = len(ready_full)
    out["pit_ready_dates_sample"] = ready_dates[:10]
    # comparable intervals: consecutive ready dates
    ready_days = sorted([d["research_as_of"] for d in ready_full])
    intervals = []
    for i in range(1, len(ready_days)):
        intervals.append({"from": ready_days[i-1], "to": ready_days[i]})
    out["pit_comparable_intervals"] = len(intervals)
    out["pit_gate_check"] = {
        "qualified_eod_gte_5": len(ready_full) >= 5,
        "comparable_intervals_gte_3": len(intervals) >= 3,
        "max_complete_companies": max((d.get("complete_companies") or 0) for d in ready_full) if ready_full else 0,
        "complete_companies_gte_100": max((d.get("complete_companies") or 0) for d in ready_full) >= 100 if ready_full else False,
    }
    pit.close()
except Exception as exc:
    out["pit_error"] = str(exc)

# Valuation bundles / immutable snapshots
if table_exists(rc, "valuation_method_snapshots"):
    vms = q(rc, "SELECT research_as_of, COUNT(*) AS cnt FROM valuation_method_snapshots WHERE market='CN' GROUP BY research_as_of ORDER BY research_as_of DESC LIMIT 5")
    out["valuation_bundle_coverage"] = vms
if table_exists(rc, "company_low_value_leader_pool_snapshots"):
    snaps = q(rc, "SELECT source_as_of, COUNT(*) AS cnt FROM company_low_value_leader_pool_snapshots GROUP BY source_as_of ORDER BY source_as_of DESC LIMIT 5")
    out["immutable_pool_snapshots"] = snaps

# Financial analysis snapshots - H1/Q1 stats for low value pool
lv_codes = [r["stock_code"] for r in q(rc, "SELECT stock_code FROM company_low_value_leader_pool WHERE pool_status='ACTIVE'")] if table_exists(rc, "company_low_value_leader_pool") else []
focus_a_codes = [x["stock_code"] for x in out.get("focus_a_detail", []) if x.get("stock_code")]

def financial_stats(codes: list[str]) -> dict:
    if not codes or not table_exists(rc, "company_financial_analysis_snapshots"):
        return {}
    placeholders = ",".join("?" * len(codes))
    rows = q(rc, f"""
        SELECT stock_code, analysis_status, as_of, feature_json, analysis_payload_json
        FROM company_financial_analysis_snapshots
        WHERE stock_code IN ({placeholders})
        ORDER BY as_of DESC, created_at DESC
    """, codes)
    seen = {}
    for row in rows:
        c = row["stock_code"]
        if c in seen:
            continue
        seen[c] = row
    h1, q1, older, missing = 0, 0, 0, 0
    claim_bucket = Counter()
    raw_claims = accepted = rejected = 0
    reject_reasons = Counter()
    for code in codes:
        row = seen.get(code)
        if not row:
            missing += 1
            claim_bucket["NOT_RUN"] += 1
            continue
        feature = {}
        analysis = {}
        try:
            feature = json.loads(row.get("feature_json") or "{}")
        except json.JSONDecodeError:
            pass
        try:
            analysis = json.loads(row.get("analysis_payload_json") or "{}")
        except json.JSONDecodeError:
            pass
        latest = str(feature.get("latest_report_period") or feature.get("latest_period") or "")
        if not latest:
            hist = feature.get("historical_periods") or feature.get("history") or []
            if hist and isinstance(hist, list):
                latest = str((hist[0] or {}).get("report_period") or (hist[0] or {}).get("report_date") or "")
        if "H1" in latest or latest.endswith("0630") or latest.endswith("-06-30"):
            h1 += 1
        elif "Q1" in latest or latest.endswith("0331") or latest.endswith("-03-31"):
            q1 += 1
        elif latest:
            older += 1
        status = row.get("analysis_status") or "NOT_RUN"
        claims_status = analysis.get("claims_status") or "UNKNOWN"
        quality = analysis.get("quality_status") or "UNKNOWN"
        if status == "FAILED":
            claim_bucket["FAILED"] += 1
        elif claims_status == "CLAIMS_READY" and quality == "STRUCTURED":
            claim_bucket["VALID_CLAIMS"] += 1
        elif status == "PARTIAL" or claims_status == "SUMMARY_ONLY" or quality == "SUMMARY_ONLY":
            claim_bucket["SUMMARY_ONLY"] += 1
        elif status == "COMPLETED":
            claim_bucket["VALID_CLAIMS"] += 1
        else:
            claim_bucket["NOT_RUN"] += 1
        for claim in analysis.get("claims") or []:
            raw_claims += 1
            if claim.get("accepted") is True:
                accepted += 1
            elif claim.get("rejected") is True or claim.get("reject_reason"):
                rejected += 1
                reject_reasons[str(claim.get("reject_reason") or claim.get("validation_error_code") or "OTHER")] += 1
        for claim in analysis.get("rejected_claims") or []:
            rejected += 1
            reject_reasons[str(claim.get("reject_reason") or claim.get("validation_error_code") or "OTHER")] += 1
    return {
        "total": len(codes), "h1_ready": h1, "q1_only": q1, "older": older, "missing": missing,
        "claim_buckets": dict(claim_bucket),
        "raw_claims": raw_claims, "accepted_claims": accepted, "rejected_claims": rejected,
        "reject_reasons": dict(reject_reasons),
    }

out["financial_pool"] = financial_stats(lv_codes)
out["financial_focus_a"] = financial_stats(focus_a_codes)

# Business research
if table_exists(rc, "company_business_research_snapshots"):
    biz_rows = q(rc, """
        SELECT stock_code, analysis_status, data_as_of, analysis_json, snapshot_json, created_at
        FROM company_business_research_snapshots
        ORDER BY data_as_of DESC, created_at DESC
    """)
    seen_b = {}
    for row in biz_rows:
        if row["stock_code"] not in seen_b:
            seen_b[row["stock_code"]] = row

    def biz_stats(codes):
        st, fr, claims_cnt = Counter(), Counter(), Counter()
        for code in codes:
            row = seen_b.get(code)
            if not row:
                st["MISSING"] += 1
                fr["UNKNOWN"] += 1
                claims_cnt["0"] += 1
                continue
            status = row.get("analysis_status") or "UNKNOWN"
            if status in {"COMPLETED", "PARTIAL", "FAILED"}:
                st[status] += 1
            else:
                st["MISSING"] += 1
            analysis = {}
            try:
                analysis = json.loads(row.get("analysis_json") or "{}")
            except json.JSONDecodeError:
                pass
            freshness = analysis.get("freshness_status") or analysis.get("freshness") or "UNKNOWN"
            fr[freshness] += 1
            n = len([c for c in (analysis.get("claims") or []) if isinstance(c, dict)])
            if n == 0:
                claims_cnt["0"] += 1
            elif n <= 3:
                claims_cnt["1-3"] += 1
            else:
                claims_cnt["4+"] += 1
        return {"status": dict(st), "freshness": dict(fr), "claims_buckets": dict(claims_cnt)}

    out["business_pool"] = biz_stats(lv_codes)
    out["business_focus_a"] = biz_stats(focus_a_codes)

# Business driver evidence
if table_exists(rc, "company_business_driver_evidence"):
    drivers = q(rc, "SELECT dimension, COUNT(*) AS cnt FROM company_business_driver_evidence GROUP BY dimension")
    out["business_driver_types"] = drivers
    pool_set = set(lv_codes)
    if pool_set:
        ph = ",".join("?" * len(pool_set))
        in_pool = q1(rc, f"SELECT COUNT(DISTINCT stock_code) AS c FROM company_business_driver_evidence WHERE stock_code IN ({ph})", list(pool_set))
        out["business_driver_pool_companies"] = in_pool.get("c", 0)
    else:
        out["business_driver_pool_companies"] = 0

# Risk snapshots
if table_exists(rc, "company_low_value_risk_snapshots"):
    risk_rows = q(rc, """
        SELECT stock_code, overall_risk, value_trap_risk, financial_status, business_status, thesis_status, source_as_of
        FROM company_low_value_risk_snapshots ORDER BY source_as_of DESC
    """)
    seen_r = {}
    for row in risk_rows:
        if row["stock_code"] not in seen_r:
            seen_r[row["stock_code"]] = row

    def risk_stats(codes):
        lvl, fr = Counter(), Counter()
        for code in codes:
            row = seen_r.get(code)
            if not row:
                lvl["UNKNOWN"] += 1
                fr["UNKNOWN"] += 1
                continue
            lvl[str(row.get("overall_risk") or "UNKNOWN")] += 1
            statuses = [row.get("financial_status"), row.get("business_status"), row.get("thesis_status")]
            if any(s in {"STALE", "PARTIAL"} for s in statuses):
                fr["STALE"] += 1
            elif all(s in {"READY", "COMPLETED", "AVAILABLE"} for s in statuses if s):
                fr["FRESH"] += 1
            else:
                fr["PARTIAL"] += 1
        return {"levels": dict(lvl), "freshness": dict(fr)}

    out["risk_pool"] = risk_stats(lv_codes)
    out["risk_focus_a"] = risk_stats(focus_a_codes)

# Value trap from strategy state for focus
try:
    traps = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    for item in out.get("focus_a_detail", []):
        code = item.get("stock_code")
        if not code:
            continue
        st = get_value_strategy_state_service().get_strategy_state("CN", code)
        trap = (st.get("value_trap") or {}).get("level") or "UNKNOWN"
        traps[trap] = traps.get(trap, 0) + 1
    out["focus_a_value_trap"] = traps
except Exception:
    pass

# Thesis stats via repository (schema-safe)
try:
    from src.company_thesis import CompanyThesisService
    thesis_svc = CompanyThesisService()
    generic_markers = ("行业龙头", "长期价值", "竞争优势", "估值修复")

    def thesis_quality(thesis: dict | None) -> str:
        if not thesis:
            return "MISSING"
        text = str(thesis.get("core_thesis") or "")
        if len(text) < 80:
            return "MISSING"
        hits = sum(1 for m in generic_markers if m in text)
        return "GENERIC_TEMPLATE" if hits >= 3 and "具体" not in text else "COMPANY_SPECIFIC"

    thesis_stats = Counter()
    field_stats = Counter()
    focus_a_thesis = []
    for code in lv_codes:
        thesis = thesis_svc.get_current_thesis("CN", code)
        auth = (thesis or {}).get("authority_status") or "MISSING"
        thesis_stats[auth] += 1
        if thesis:
            if thesis.get("supporting_conditions"):
                field_stats["supporting_nonempty"] += 1
            if thesis.get("invalid_conditions"):
                field_stats["invalid_nonempty"] += 1
            if thesis.get("key_metrics_to_monitor"):
                field_stats["key_metrics_nonempty"] += 1
        if code in focus_a_codes:
            focus_a_thesis.append({
                "stock_code": code,
                "authority": auth,
                "thesis_status": (thesis or {}).get("status"),
                "quality": thesis_quality(thesis),
            })
    out["thesis_authority_pool"] = dict(thesis_stats)
    out["thesis_field_stats"] = dict(field_stats)
    out["focus_a_thesis"] = focus_a_thesis
    out["focus_a_thesis_quality"] = dict(Counter(t["quality"] for t in focus_a_thesis))
    thesis_svc.close()
except Exception as exc:
    out["thesis_error"] = str(exc)

# Moat
if table_exists(rc, "company_moat_evidence"):
    moat = q(rc, "SELECT stock_code, status FROM company_moat_evidence WHERE status='ACTIVE' OR status IS NOT NULL")
    moat_codes = {r["stock_code"] for r in moat if r.get("status") == "ACTIVE"}
    def moat_stats(codes):
        c = Counter()
        for code in codes:
            c["READY" if code in moat_codes else "MISSING"] += 1
        return dict(c)
    out["moat_pool"] = moat_stats(lv_codes)
    out["moat_focus_ab"] = moat_stats(focus_a_codes + [x["stock_code"] for x in out.get("focus_a_detail", [])])

# CIO reports
if table_exists(rc, "company_cio_research_reports"):
    cio = q(rc, "SELECT stock_code, synthesis_source, overall_freshness, status, research_as_of FROM company_cio_research_reports ORDER BY research_as_of DESC, updated_at DESC")
    cio_seen = {}
    for row in cio:
        if row["stock_code"] not in cio_seen:
            cio_seen[row["stock_code"]] = row
    pool_with = sum(1 for c in lv_codes if c in cio_seen)
    fa_with = sum(1 for c in focus_a_codes if c in cio_seen)
    out["cio_pool_reports"] = pool_with
    out["cio_focus_a_reports"] = fa_with
    out["cio_synthesis_breakdown"] = dict(Counter(r.get("synthesis_source") for r in cio_seen.values()))

# TDX finance freshness (read-only local)
try:
    from src.tdx_data.financial_history import FinancialHistoryService
    fhs = FinancialHistoryService()
    # reference from disclosure max announcement
    ref = None
    if table_exists(rc, "company_disclosure_documents"):
        ref_row = q1(rc, "SELECT MAX(announcement_date) AS d FROM company_disclosure_documents")
        ref = ref_row.get("d")
    out["tdx_finance_freshness"] = fhs.check_finance_source_freshness(reference_latest_announcement_date=ref)
    fhs.close()
except Exception as exc:
    out["tdx_finance_freshness_error"] = str(exc)

# Watchpoint perf sample (small, no benchmark storm)
try:
    from src.value_watchpoints.service import get_value_watchpoint_projection_service
    wp = get_value_watchpoint_projection_service()
    times = []
    for code in (focus_a_codes[:3] or ["600460.SH"]):
        t0 = time.perf_counter()
        wp.get_watchpoints("CN", code)
        times.append((code, round((time.perf_counter() - t0) * 1000, 1)))
    out["watchpoint_sample_ms"] = times
    if len(focus_a_codes) >= 3:
        t0 = time.perf_counter()
        for code in focus_a_codes[:5]:
            wp.get_watchpoints("CN", code)
        out["watchpoint_focus5_batch_ms"] = round((time.perf_counter() - t0) * 1000, 1)
except Exception as exc:
    out["watchpoint_sample_error"] = str(exc)

# Sentiment / macro from research.db tables
for tbl, key in [("engine_runs", "sentiment_runs"), ("macro_snapshots", "macro_snapshots"), ("macro_briefs", "macro_briefs")]:
    if table_exists(rc, tbl):
        out[key] = q(rc, f"SELECT * FROM {tbl} ORDER BY rowid DESC LIMIT 3")

# Research as of synthesis
out["current_research_as_of"] = max(
    filter(None, [
        out.get("latest_low_value_source_as_of"),
        out.get("focus_research_as_of"),
        latest_cursor.get("research_as_of"),
        brief.get("research_as_of"),
        latest_l3.get("as_of"),
    ]),
    default=None,
)

rc.close()
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
