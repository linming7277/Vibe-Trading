# -*- coding: utf-8 -*-
"""Render latest READY daily brief to HTML. Does not send Feishu."""
from __future__ import annotations

import json
import sqlite3
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(r"d:/AI/hzstock/agent")))

from src.investment_research_supervisor.daily_brief_notification_service import (
    _forecast_review_block,
    _macro_environment_block,
    _market_review_block,
    _next_outlook_block,
    _summary_metrics,
    _value_observation_table,
    build_daily_brief_card,
)

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
row = conn.execute(
    """SELECT research_as_of, status, formula_version, brief_payload_json, updated_at
       FROM investment_research_daily_briefs
       WHERE status='READY'
       ORDER BY research_as_of DESC LIMIT 1"""
).fetchone()
conn.close()
if not row:
    raise SystemExit("no READY brief")

as_of = row["research_as_of"]
payload = json.loads(row["brief_payload_json"] or "{}")
brief = {
    "research_as_of": as_of,
    "brief_payload": payload,
    "macro_environment": payload.get("macro_environment") or {},
    "price_condition_digest": payload.get("price_condition_digest") or {},
}
card = build_daily_brief_card(brief, include_bitable_link=False)
watch_blocks = _value_observation_table(brief)

md_sections = []
for fn in (_summary_metrics,):
    md_sections.append(fn(payload))
for block in (
    _market_review_block(brief),
    _forecast_review_block(brief),
    _next_outlook_block(brief),
    watch_blocks,
):
    for el in block:
        if el.get("tag") == "markdown" and el.get("content"):
            md_sections.append(el["content"])


def md_to_html(text: str) -> str:
    lines = []
    for raw in text.split("\n"):
        line = escape(raw)
        line = line.replace("**", "<b>", 1)
        while "**" in line:
            line = line.replace("**", "</b>", 1).replace("**", "<b>", 1)
        lines.append(line)
    return "<br>".join(lines)


rows_html = []
for item in payload.get("executive_watchlist") or []:
    support = dict(item.get("historical_support") or {})
    rows_html.append(
        "<tr>"
        f"<td>{escape(str(item.get('company_name') or ''))}<br><span class='code'>{escape(str(item.get('stock_code') or ''))}</span></td>"
        f"<td>{escape(str(item.get('industry_name') or ''))}</td>"
        f"<td class='num'>{item.get('current_price') if item.get('current_price') is not None else '—'}</td>"
        f"<td class='num'>{item.get('fair_value_low') if item.get('fair_value_low') is not None else '—'}</td>"
        f"<td class='num'>{item.get('fair_value_mid') if item.get('fair_value_mid') is not None else '—'}</td>"
        f"<td class='num'>{item.get('fair_value_high') if item.get('fair_value_high') is not None else '—'}</td>"
        f"<td class='num'>{('+' if (item.get('valuation_gap_percent') or 0) >= 0 else '')}{item.get('valuation_gap_percent')}%</td>"
        f"<td class='num'>{support.get('low') if support.get('low') is not None else '—'}–{support.get('high') if support.get('high') is not None else '—'}</td>"
        "</tr>"
    )

card_html = []
for el in card["elements"]:
    if el.get("tag") == "markdown":
        card_html.append(f"<div class='card-line'>{md_to_html(el.get('content') or '')}</div>")
    elif el.get("tag") == "hr":
        card_html.append("<hr>")
    elif el.get("tag") == "note":
        note = (el.get("elements") or [{}])[0].get("content") or ""
        card_html.append(f"<div class='note'>{escape(note)}</div>")

out = Path(r"d:/AI/hzstock/tmp") / f"日报预览_{as_of}.html"
out.write_text(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>投研日报预览 {as_of}</title>
<style>
body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;margin:24px;background:#f6f7f4;color:#1a1a1a}}
h1{{margin:0 0 6px}} .meta{{color:#666;margin-bottom:18px}}
.wrap{{background:#fff;border:1px solid #e6e6e2;border-radius:10px;padding:16px;margin:16px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}}
th{{background:#244062;color:#fff}}
.num{{text-align:right;white-space:nowrap}}
.code{{color:#888;font-size:11px}}
.card-line{{padding:6px 0;line-height:1.55}}
.note{{color:#7a4e00;background:#fff8e7;padding:8px 10px;border-radius:6px;margin:8px 0}}
hr{{border:none;border-top:1px solid #eee}}
</style></head><body>
<h1>投研日报预览（{as_of}）</h1>
<div class="meta">未发飞书。数据来自已落库 READY 简报 · 公式 {escape(row['formula_version'])} · 更新 {escape(str(row['updated_at']))} · 重点研究口径 {escape(str(payload.get('executive_watchlist_basis') or ''))}</div>
<div class="wrap"><h2>重点研究（价格 / 估值 / 支撑）</h2>
<table><thead><tr>
<th>公司</th><th>行业</th><th>现价</th><th>合理价值低</th><th>中</th><th>高</th><th>距中枢</th><th>支撑位</th>
</tr></thead><tbody>{''.join(rows_html) or '<tr><td colspan="8">暂无重点研究</td></tr>'}</tbody></table>
</div>
<div class="wrap"><h2>卡片正文（将发飞书的样子）</h2>
{''.join(card_html)}
</div>
</body></html>
""", encoding="utf-8")
print(json.dumps({
    "html": str(out),
    "as_of": as_of,
    "watchlist_count": len(payload.get("executive_watchlist") or []),
    "sent": False,
}, ensure_ascii=False))
