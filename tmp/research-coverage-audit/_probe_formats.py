# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect("file:C:/Users/Administrator/.vibe-trading/research.db?mode=ro", uri=True)
cur = conn.cursor()
print("disc codes:", [r[0] for r in cur.execute("SELECT DISTINCT stock_code FROM company_disclosure_documents LIMIT 6")])
print("pool codes:", [r[0] for r in cur.execute("SELECT stock_code FROM company_low_value_leader_pool WHERE pool_status='ACTIVE' LIMIT 6")])
n = cur.execute(
    "SELECT COUNT(DISTINCT d.stock_code) FROM company_disclosure_documents d "
    "JOIN company_low_value_leader_pool p "
    "ON d.stock_code = REPLACE(REPLACE(p.stock_code,'.SH',''),'.SZ','') "
    "WHERE p.pool_status='ACTIVE'"
).fetchone()[0]
print("disc companies matching pool (bare join):", n)

rows = cur.execute(
    "SELECT f.stock_code, f.as_of, "
    "json_extract(f.feature_json,'$.data_quality.latest_announcement_date') la, "
    "json_extract(f.feature_json,'$.data_quality.latest_report_date') lr "
    "FROM company_financial_analysis_snapshots f "
    "JOIN (SELECT stock_code, MAX(as_of) mas, MAX(created_at) mct FROM company_financial_analysis_snapshots GROUP BY stock_code) t "
    "ON f.stock_code=t.stock_code AND f.as_of=t.mas AND f.created_at=t.mct "
    "WHERE f.stock_code IN (SELECT stock_code FROM company_low_value_leader_pool WHERE pool_status='ACTIVE') "
    "ORDER BY f.stock_code LIMIT 8"
).fetchall()
for r in rows:
    print(r)
print("fin snapshot as_of months:")
for r in cur.execute(
    "SELECT substr(f.as_of,1,7) m, COUNT(*) FROM company_financial_analysis_snapshots f "
    "JOIN (SELECT stock_code, MAX(as_of) mas, MAX(created_at) mct FROM company_financial_analysis_snapshots GROUP BY stock_code) t "
    "ON f.stock_code=t.stock_code AND f.as_of=t.mas AND f.created_at=t.mct "
    "WHERE f.stock_code IN (SELECT stock_code FROM company_low_value_leader_pool WHERE pool_status='ACTIVE') "
    "GROUP BY m ORDER BY m"
):
    print(" ", r)
