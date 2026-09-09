# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

conn = sqlite3.connect(str(Path.home() / ".vibe-trading" / "research.db"))
tabs = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
for t in tabs:
    cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
    joined = " ".join(cols).lower()
    if "main_business" in joined or "fundamental" in t.lower() or "quote_cache" in t.lower() or "security" in t.lower():
        print(t, cols[:25])
