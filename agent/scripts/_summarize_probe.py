import json
from pathlib import Path

base = Path(r"D:\AI\hzstock\docs\value-strategy")
data = json.loads((base / "_watchpoint_six_company_probe.json").read_text(encoding="utf-8"))
lines = []
for code, value in data.items():
    lines.append(f"== {code} {value['stock_name']} as_of={value['research_as_of']} action={value['primary_action']} tier={value['focus_tier']}")
    for index, item in enumerate(value["top"], 1):
        lines.append(f"  TOP{index} [{item['category']}/{item['importance']}] {item['title']} src={item['sources']}")
    lines.append("  ALL: " + " | ".join(value["all_titles"]))
    lines.append("  GAPS: " + " | ".join(value["data_gaps"]))
(base / "_six.txt").write_text("\n".join(lines), encoding="utf-8")
