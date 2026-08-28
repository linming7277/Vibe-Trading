"""Render the executive value-observation table for Feishu cards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


_FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")
_COLUMNS = (
    ("公司 / 代码", 280),
    ("现价", 130),
    ("历史支撑", 220),
    ("合理价值范围", 430),
    ("中位值差距", 220),
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if _FONT_PATH.exists():
        return ImageFont.truetype(str(_FONT_PATH), size=size, index=1 if bold else 0)
    return ImageFont.load_default()


def _text(value: Any) -> str:
    return "—" if value is None else str(value)


def render_value_observation_table(brief: dict[str, Any], output_path: Path) -> Path:
    payload = dict(brief.get("brief_payload") or {})
    rows = list(payload.get("executive_watchlist") or [])
    width = sum(width for _, width in _COLUMNS)
    title_height, header_height, row_height, footer_height = 72, 54, 70, 24
    height = title_height + header_height + max(1, len(rows)) * row_height + footer_height
    image = Image.new("RGB", (width, height), "#F6F8FB")
    draw = ImageDraw.Draw(image)
    title_font, header_font = _font(26, bold=True), _font(19, bold=True)
    body_font, code_font = _font(18), _font(15)

    research_as_of = _text(payload.get("research_as_of") or brief.get("research_as_of"))
    draw.text((28, 20), "重点研究观察", fill="#102A43", font=title_font)
    date_box = draw.textbbox((0, 0), research_as_of, font=body_font)
    draw.text((width - 28 - (date_box[2] - date_box[0]), 26), research_as_of, fill="#486581", font=body_font)

    y = title_height
    draw.rounded_rectangle((0, y, width, y + header_height), radius=10, fill="#153B63")
    x = 0
    for label, column_width in _COLUMNS:
        draw.text((x + 16, y + 15), label, fill="white", font=header_font)
        x += column_width
    y += header_height

    for index, item in enumerate(rows or [{}]):
        fill = "#FFFFFF" if index % 2 == 0 else "#F1F5F9"
        draw.rectangle((0, y, width, y + row_height), fill=fill)
        x = 0
        support = dict(item.get("historical_support") or {})
        support_text = (
            f"{_text(support.get('low'))}–{_text(support.get('high'))}"
            if support.get("low") is not None and support.get("high") is not None
            else "—"
        )
        gap = item.get("valuation_gap_percent")
        gap_text = f"{gap:.2f}%" if isinstance(gap, (int, float)) else "—"
        values = (
            (_text(item.get("company_name")), _text(item.get("stock_code"))),
            _text(item.get("current_price")),
            support_text,
            f"{_text(item.get('fair_value_low'))}–{_text(item.get('fair_value_high'))}",
            gap_text,
        )
        for position, ((_, column_width), value) in enumerate(zip(_COLUMNS, values)):
            draw.line((x, y, x, y + row_height), fill="#D9E2EC", width=1)
            if position == 0:
                name, code = value
                draw.text((x + 16, y + 13), name, fill="#102A43", font=body_font)
                draw.text((x + 16, y + 41), code, fill="#627D98", font=code_font)
            else:
                box = draw.textbbox((0, 0), value, font=body_font)
                text_width = box[2] - box[0]
                draw.text(
                    (x + (column_width - text_width) / 2, y + 24),
                    value,
                    fill="#102A43" if position != 4 else "#C05621",
                    font=body_font,
                )
            x += column_width
        draw.line((width - 1, y, width - 1, y + row_height), fill="#D9E2EC", width=1)
        draw.line((0, y + row_height, width, y + row_height), fill="#D9E2EC", width=1)
        y += row_height

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path
