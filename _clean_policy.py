import sys, os
sys.path.insert(0, os.path.abspath("agent"))
os.chdir(os.path.abspath("agent"))

from src.strategy_engines.policy_data import PolicyDataService
from src.strategy_engines.value_data_store import ValueDataStore

store = ValueDataStore()
svc = PolicyDataService(store=store)

print("=== final policy_events ===")
for e in store.policies(status=None, limit=50):
    t = (e.get("title") or "").replace("\u200b", "")
    print(f"  [{e['source']}] body={len(e.get('content_text') or ''):5d} {t[:44]:46s} | {e['published_at']} | status={e['status']}")

print("\n=== policy_fit smoke test ===")
for code, name in [("801080","半导体"), ("801750","计算机"), ("801120","电力设备"), ("801010","农林牧渔"), ("801230","汽车"), ("801930","煤炭")]:
    r = svc.policy_fit(code, "2026-08-17")
    print(f"  {name}({code}): status={r['status']} score={r['score']} events={len(r['events'])}")
