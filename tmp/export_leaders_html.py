# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pandas as pd

src = Path(r"d:/AI/hzstock/tmp/当前行业龙头_V3_Top2_2026-09-07_20260908_1039.xlsx")
out = Path(r"d:/AI/hzstock/tmp/当前行业龙头_V3_Top2_2026-09-07.html")
csv_dir = Path(r"d:/AI/hzstock/tmp/当前行业龙头_V3_csv")
csv_dir.mkdir(exist_ok=True)

sheets = pd.read_excel(src, sheet_name=None)
parts = [
    "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
    "<title>当前行业龙头 V3 Top2</title>",
    "<style>",
    "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;margin:24px;background:#f7f7f5;color:#1a1a1a}",
    "h1{font-size:22px;margin:0 0 8px}.meta{color:#666;margin-bottom:20px}",
    "nav a{margin-right:12px;color:#0b57d0;text-decoration:none}",
    "section{background:#fff;border:1px solid #e5e5e2;border-radius:10px;padding:16px;margin:18px 0}",
    "h2{font-size:16px;margin:0 0 12px}",
    "table{border-collapse:collapse;width:100%;font-size:13px}",
    "th,td{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;white-space:nowrap}",
    "th{position:sticky;top:0;background:#f0f0ec;z-index:1}",
    ".wrap{max-height:70vh;overflow:auto}",
    "</style></head><body>",
    "<h1>当前行业龙头（V3 · 每行业前2）</h1>",
    "<div class='meta'>与 Excel 同内容 · as_of 2026-09-07 · 用浏览器打开即可，无需安装 Office</div>",
    "<nav>",
]
for name in sheets:
    parts.append(f"<a href='#{name}'>{name}</a>")
parts.append("</nav>")

for name, df in sheets.items():
    df.to_csv(csv_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    parts.append(f"<section id='{name}'><h2>{name}（{len(df)} 行）</h2><div class='wrap'>")
    parts.append(df.to_html(index=False, border=0, na_rep=""))
    parts.append("</div></section>")

parts.append("</body></html>")
out.write_text("".join(parts), encoding="utf-8")
print(out)
print(csv_dir)
