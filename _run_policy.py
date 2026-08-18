import sys, os
sys.path.insert(0, os.path.abspath("agent"))
os.chdir(os.path.abspath("agent"))

from src.strategy_engines.value_data_store import ValueDataStore

store = ValueDataStore()
print("=== All events with url & source (to classify noise vs real) ===")
for e in store.policies(status=None, limit=100):
    title = (e.get("title") or "").replace("\u200b", "")
    url = e.get("normalized_url") or ""
    print(f"[{e['source']}] body={len(e.get('content_text') or ''):5d} {title[:34]:36s} | {url[:80]}")
