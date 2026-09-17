import os, sqlite3
from pathlib import Path
R=Path(os.environ.get('VIBE_TRADING_HOME', Path.home()/'.vibe-trading'))
rc=sqlite3.connect(f'file:{R}/research.db?mode=ro', uri=True)
emo=rc.execute("SELECT COUNT(*), MAX(as_of) FROM engine_runs WHERE strategy_line='emotion' AND status='completed'").fetchone()
print('emotion', emo)
