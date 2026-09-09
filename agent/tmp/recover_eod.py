import sys, json
sys.path.insert(0, ".")
from src.value_workspace.automation import get_value_research_scheduler

result = get_value_research_scheduler().recover_latest_completed()
print(json.dumps(result, ensure_ascii=False, default=str))
