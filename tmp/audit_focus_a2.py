#!/usr/bin/env python3
import json, os, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
R = Path(os.environ.get("VIBE_TRADING_HOME", Path.home() / ".vibe-trading"))
os.environ["VIBE_TRADING_HOME"] = str(R)
rc = sqlite3.connect(f"file:{R}/research.db?mode=ro", uri=True)

from src.research_freshness.service import ResearchFreshnessService
from src.capital_allocation_facts.service import get_capital_allocation_fact_service
from src.historical_valuation.service import get_historical_valuation_service
from src.value_watchpoints.service import get_value_watchpoint_projection_service
from src.company_thesis import CompanyThesisService
from src.investment_research_supervisor.hermes_feishu import HermesSupervisorFeishuCredentials, _default_env_path

codes = ["601186.SH", "600522.SH", "688819.SH", "000848.SZ", "002130.SZ", "601827.SH", "002049.SZ", "603055.SH", "002611.SZ", "301267.SZ"]
wp = get_value_watchpoint_projection_service()
rf = ResearchFreshnessService()
ths = CompanyThesisService()

def latest_period(fj: dict) -> tuple[str, str]:
    hist = fj.get("historical_periods") or []
    if not hist:
        return "", ""
    mx = max(hist, key=lambda h: str(h.get("report_date") or ""))
    return str(mx.get("report_date") or ""), str(mx.get("period_type") or "")

def boss_status(row: dict) -> str:
    if row["fclaims"] != "VALID" or row["biz"] != "COMPLETED":
        return "BLOCKED_BY_DATA"
    if row["rfresh"] == "STALE" or row["bfresh"] in {"STALE", "UNKNOWN", "UNK"}:
        return "NEEDS_RESEARCH"
    if row["cio"] == "MISSING" or row["moat"] == "0":
        return "READY_WITH_CAUTIONS"
    return "BOSS_READY"

def next_gap(row: dict) -> str:
    if row["rfresh"] == "STALE":
        return "WAIT_RISK_REFRESH"
    if row["cio"] == "MISSING":
        return "CIO_MISSING_AFTER_THESIS"
    if row["moat"] == "0":
        return "MOAT_MISSING"
    if row["bfresh"] in {"STALE", "UNKNOWN", "UNK"}:
        return "BUSINESS_STALE"
    return "NONE"

rows = []
for code in codes:
    name = rc.execute("SELECT company_name FROM company_low_value_leader_pool WHERE stock_code=?", (code,)).fetchone()[0]
    fin = rc.execute("SELECT analysis_status, analysis_payload_json, feature_json FROM company_financial_analysis_snapshots WHERE stock_code=? ORDER BY as_of DESC LIMIT 1", (code,)).fetchone()
    aj = json.loads(fin[1] or "{}") if fin else {}
    fj = json.loads(fin[2] or "{}") if fin and fin[2] else {}
    rd, pt = latest_period(fj)
    h1 = "Y" if pt == "semiannual" and rd.endswith("06-30") else "N"
    fclaims = "VALID" if aj.get("claims_status") == "CLAIMS_READY" else "SUMMARY"
    biz = rc.execute("SELECT analysis_status FROM company_business_research_snapshots WHERE stock_code=? ORDER BY data_as_of DESC LIMIT 1", (code,)).fetchone()
    risk = rc.execute("SELECT overall_risk, financial_status, business_status FROM company_low_value_risk_snapshots WHERE stock_code=? ORDER BY source_as_of DESC LIMIT 1", (code,)).fetchone()
    try:
        bf = rf.business_freshness("CN", code).get("status")
    except Exception:
        bf = "UNK"
    th = ths.get_current_thesis("CN", code)
    tq = "COMPANY_SPECIFIC" if th and len(th.get("core_thesis", "")) >= 80 else "MISSING"
    moat = rc.execute("SELECT COUNT(*) FROM company_moat_evidence WHERE stock_code=? AND status='ACTIVE'", (code,)).fetchone()[0]
    try:
        cap = get_capital_allocation_fact_service().get_facts("CN", code).get("status")
    except Exception:
        cap = "UNK"
    try:
        hv = get_historical_valuation_service().get_snapshot("CN", code).get("historical_valuation_status")
    except Exception:
        hv = "UNK"
    wpc = len(wp.get_watchpoints("CN", code).get("watchpoints") or [])
    cio = rc.execute("SELECT status FROM company_cio_research_reports WHERE stock_code=? ORDER BY research_as_of DESC LIMIT 1", (code,)).fetchone()
    row = {
        "code": code, "name": name, "h1": h1, "period": rd, "fclaims": fclaims,
        "biz": biz[0] if biz else "MISSING", "bfresh": str(bf),
        "risk": risk[0] if risk else "UNK",
        "rfresh": "STALE" if risk and any(x in ("STALE", "PARTIAL") for x in risk[1:]) else "FRESH",
        "thesis": tq, "moat": str(moat), "cap": str(cap), "hv": str(hv), "wp": str(wpc),
        "cio": cio[0] if cio else "MISSING",
    }
    row["boss"] = boss_status(row)
    row["gap"] = next_gap(row)
    rows.append(row)

brief = rc.execute("SELECT brief_payload_json FROM investment_research_daily_briefs WHERE research_as_of='2026-09-03'").fetchone()
payload = json.loads(brief[0] or "{}") if brief else {}
print("BRIEF_HAS_WATCHPOINT", "focus_watchpoints" in payload or "watchpoint" in json.dumps(payload, ensure_ascii=False).lower())
print("HERMES_ENV", _default_env_path(), _default_env_path().exists())
try:
    HermesSupervisorFeishuCredentials.load()
    print("HERMES_CREDS", "OK")
except Exception as exc:
    print("HERMES_CREDS", exc)
print(json.dumps(rows, ensure_ascii=False, indent=2))
rc.close()
