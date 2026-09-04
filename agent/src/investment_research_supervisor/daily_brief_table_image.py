"""Render the executive value-observation table for Feishu cards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


_FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")
_COLUMNS = (
    ("公司 / 代码", 270),
    ("现价", 120),
    ("历史支撑", 210),
    ("合理价值范围", 400),
    ("差距", 170),
)
# 简约配色：浅底、无竖线、细斑马纹、单一强调色。
_BG = "#FFFFFF"
_ZEBRA = "#F7F9FC"
_HEADER_BG = "#2B4A6F"
_TITLE = "#22303F"
_BODY = "#33475B"
_MUTED = "#8A9BA8"
_ACCENT = "#B45309"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if _FONT_PATH.exists():
        return ImageFont.truetype(str(_FONT_PATH), size=size, index=1 if bold else 0)
    return ImageFont.load_default()


def _text(value: Any) -> str:
    return "—" if value is None else str(value)


def _fmt_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _fmt_gap(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:+.0f}%"


def render_value_observation_table(brief: dict[str, Any], output_path: Path) -> Path:
    payload = dict(brief.get("brief_payload") or {})
    rows = list(payload.get("executive_watchlist") or [])
    width = sum(width for _, width in _COLUMNS)
    margin, title_height, header_height, row_height = 26, 64, 48, 56
    height = title_height + header_height + max(1, len(rows)) * row_height + 20
    image = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(image)
    title_font, header_font = _font(24, bold=True), _font(17, bold=True)
    body_font, code_font = _font(17), _font(13)

    research_as_of = _text(payload.get("research_as_of") or brief.get("research_as_of"))
    draw.text((margin, 18), "重点研究", fill=_TITLE, font=title_font)
    date_box = draw.textbbox((0, 0), research_as_of, font=body_font)
    draw.text(
        (width - margin - (date_box[2] - date_box[0]), 26),
        research_as_of, fill=_MUTED, font=body_font,
    )

    y = title_height
    draw.rounded_rectangle((margin, y, width - margin, y + header_height), radius=8, fill=_HEADER_BG)
    x = margin
    for label, column_width in _COLUMNS:
        if label in {"现价", "差距"}:
            box = draw.textbbox((0, 0), label, font=header_font)
            draw.text((x + (column_width - (box[2] - box[0])) / 2, y + 12), label, fill="white", font=header_font)
        else:
            draw.text((x + 16, y + 12), label, fill="white", font=header_font)
        x += column_width
    y += header_height + 6

    for index, item in enumerate(rows or [{}]):
        if index % 2 == 1:
            draw.rounded_rectangle(
                (margin, y, width - margin, y + row_height), radius=6, fill=_ZEBRA,
            )
        x = margin
        support = dict(item.get("historical_support") or {})
        support_text = (
            f"{_fmt_number(support.get('low'))}–{_fmt_number(support.get('high'))}"
            if support.get("low") is not None and support.get("high") is not None
            else "—"
        )
        values = (
            (_text(item.get("company_name")), _text(item.get("stock_code"))),
            _text(item.get("current_price")),
            support_text,
            f"{_fmt_number(item.get('fair_value_low'))}–{_fmt_number(item.get('fair_value_high'))}",
            _fmt_gap(item.get("valuation_gap_percent")),
        )
        for position, ((_, column_width), value) in enumerate(zip(_COLUMNS, values)):
            if position == 0:
                name, code = value
                draw.text((x + 16, y + 8), name, fill=_TITLE, font=body_font)
                draw.text((x + 16, y + 33), code, fill=_MUTED, font=code_font)
            else:
                box = draw.textbbox((0, 0), value, font=body_font)
                text_width = box[2] - box[0]
                color = _ACCENT if position == 4 else _BODY
                draw.text(
                    (x + (column_width - text_width) / 2, y + 18),
                    value,
                    fill=color,
                    font=body_font,
                )
            x += column_width
        y += row_height

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path
