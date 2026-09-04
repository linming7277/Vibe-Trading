#!/usr/bin/env python3
import json, os, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
sys.path.insert(0, str(AGENT))
R = Path(os.environ.get("VIBE_TRADING_HOME", Path.home() / ".vibe-trading"))
os.environ["VIBE_TRADING_HOME"] = str(R)
rc = sqlite3.connect(f"file:{R}/research.db?mode=ro", uri=True)

from src.research_freshness.service import ResearchFreshnessService
from src.capital_allocation_facts.service import get_capital_allocation_fact_service
from src.historical_valuation.service import get_historical_valuation_service
from src.value_watchpoints.service import get_value_watchpoint_projection_service
from src.company_thesis import CompanyThesisService
from src.investment_research_supervisor.daily_brief_store import InvestmentResearchDailyBriefRepository
from src.investment_research_supervisor.hermes_feishu import HermesSupervisorFeishuCredentials, _default_env_path

codes = ["601186.SH", "600522.SH", "688819.SH", "000848.SZ", "002130.SZ", "601827.SH", "002049.SZ", "603055.SH", "002611.SZ", "301267.SZ"]
row = rc.execute("SELECT feature_json FROM company_financial_analysis_snapshots WHERE stock_code=? ORDER BY as_of DESC LIMIT 1", ("600522.SH",)).fetchone()
feat = json.loads(row[0])
hist = feat.get("historical_periods") or []
print("H1_SAMPLE", {"latest_report_period": feat.get("latest_report_period"), "first_hist": hist[0] if hist else None})

store = InvestmentResearchDailyBriefRepository()
print("DAILY_DELIVERY", store.delivery(research_as_of="2026-09-03", channel="hermes_feishu_supervisor", target_id=""))
brief = store.get_brief("2026-09-03")
b = (brief or {}).get("brief") or {}
print("BRIEF_TOP_KEYS", list(b.keys()))
print("BRIEF_HAS_WATCHPOINT", "focus_watchpoints" in b or "watchpoint" in json.dumps(b, ensure_ascii=False).lower())

p = _default_env_path()
print("HERMES_ENV", str(p), p.exists())
try:
    HermesSupervisorFeishuCredentials.load()
    print("HERMES_CREDS", "OK")
except Exception as exc:
    print("HERMES_CREDS", f"ERR:{exc}")

wp = get_value_watchpoint_projection_service()
rf = ResearchFreshnessService()
ths = CompanyThesisService()
print("FOCUS_A_HEADER", "code|name|h1|fclaims|biz|bfresh|risk|rfresh|thesis|moat|cap|hv|wp|cio")
for code in codes:
    name = rc.execute("SELECT company_name FROM company_low_value_leader_pool WHERE stock_code=?", (code,)).fetchone()[0]
    fin = rc.execute("SELECT analysis_status, analysis_payload_json, feature_json FROM company_financial_analysis_snapshots WHERE stock_code=? ORDER BY as_of DESC LIMIT 1", (code,)).fetchone()
    aj = json.loads(fin[1] or "{}") if fin else {}
    fj = json.loads(fin[2] or "{}") if fin and fin[2] else {}
    hist = fj.get("historical_periods") or []
    trends = fj.get("trends") or {}
    latest = str(trends.get("latest_report_period") or trends.get("latest_report_date") or "")
    if not latest and hist:
        latest = str(max((str((h or {}).get("report_date") or "") for h in hist), default=""))
    h1 = "Y" if ("H1" in latest or latest.endswith("0630") or "06-30" in latest or str(trends.get("latest_period_type") or "") == "H1") else "N"
    fclaims = "VALID" if aj.get("claims_status") == "CLAIMS_READY" else ("SUMMARY" if fin and fin[0] == "PARTIAL" else "NONE")
    biz = rc.execute("SELECT analysis_status FROM company_business_research_snapshots WHERE stock_code=? ORDER BY data_as_of DESC LIMIT 1", (code,)).fetchone()
    risk = rc.execute("SELECT overall_risk, financial_status, business_status FROM company_low_value_risk_snapshots WHERE stock_code=? ORDER BY source_as_of DESC LIMIT 1", (code,)).fetchone()
    try:
        bf = rf.business_freshness("CN", code).get("status")
    except Exception:
        bf = "UNK"
    th = ths.get_current_thesis("CN", code)
    tq = "SPECIFIC" if th and len(th.get("core_thesis", "")) >= 80 else "MISSING"
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
    print("|".join([code, name, h1, fclaims, biz[0] if biz else "MISSING", str(bf), risk[0] if risk else "UNK", "STALE" if risk and any(x in ("STALE", "PARTIAL") for x in risk[1:]) else "FRESH", tq, str(moat), str(cap), str(hv), str(wpc), cio[0] if cio else "MISSING"]))
rc.close()
