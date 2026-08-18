import os, sqlite3, glob
p = glob.glob(os.path.expanduser("~/.vibe-trading/tdx_data.db"))[0]
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row

print("=== module_state (value modules only) ===")
for r in c.execute("select * from module_state"):
    m = dict(r)
    if m["module"] in ("financial_history","market_history","macro","policy","scores"):
        print(m["module"], "|", m["status"], "|", (m.get("message") or "")[:60], "| err:", (m.get("error") or "")[:80])
        print("    last_success_at:", m.get("last_success_at"), "| updated:", m.get("updated_at"))

print("=== financial_history coverage (distinct symbols) ===")
print(c.execute("select count(distinct category) from records where dataset='financial_history'").fetchone()[0])
print("=== value_leader_scores_v2 by as_of ===")
for r in c.execute("select category, count(*) n from records where dataset='value_leader_scores_v2' group by category"):
    print(dict(r))
print("=== value_sector_scores_v2 by category ===")
for r in c.execute("select category, count(*) n from records where dataset='value_sector_scores_v2' group by category"):
    print(dict(r))
