"""Excel appendix for persisted Investment Research Daily Brief payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


_HEADERS = [
    "股票代码", "公司", "行业", "估值状态", "现价", "合理价值低", "合理价值中", "合理价值高",
    "相对中位值差距", "历史支撑低", "历史支撑高",
]


def _sheet(workbook: Workbook, title: str, rows: list[dict[str, Any]]) -> None:
    worksheet = workbook.create_sheet(title)
    worksheet.append(_HEADERS)
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in rows:
        support = dict(row.get("historical_support") or {})
        worksheet.append([
            row.get("stock_code"),
            row.get("company_name"),
            row.get("industry_name"),
            row.get("valuation_label"),
            row.get("current_price"),
            row.get("fair_value_low"),
            row.get("fair_value_mid"),
            row.get("fair_value_high"),
            row.get("valuation_gap_percent"),
            support.get("low"),
            support.get("high"),
        ])
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for column in worksheet.iter_cols(min_row=2, min_col=5, max_col=11):
        for cell in column:
            cell.number_format = "0.00"
    widths = [16, 18, 14, 14, 12, 14, 14, 14, 16, 14, 14]
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[chr(64 + index)].width = width


def export_daily_brief_workbook(brief: dict[str, Any], output_path: str | Path) -> Path:
    """Export only persisted Daily Brief rows into a two-sheet workbook."""
    payload = dict(brief.get("brief_payload") or {})
    workbook = Workbook()
    workbook.remove(workbook.active)
    _sheet(workbook, "低估龙头池", list(payload.get("low_value_leader_table") or []))
    _sheet(workbook, "深度低估", list(payload.get("deeply_undervalued_companies") or []))
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path
