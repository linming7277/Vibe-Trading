"""价格落点六句正典（2026-09-09 产品约定）。

「未落入」只准表示现价高于全部观察/关注带（偏贵）；
低于低估关注区下沿必须写「低于低估关注区」；无区间/无现价写「资料不足」。
纯函数测试，0 网络、0 写库。
"""

from __future__ import annotations

import pytest

from src.value_price_zones.service import price_position_label

# 600216.SH 2026-09-08 对照阶梯：合理价值 21.03–77.30，默认折扣
# （watch 10% / attractive 20% / deep 30% / buffer 10%）。
LADDER_600216 = [
    {"name": "深度低估区", "low": None, "high": 14.72, "kind": "UNDERVALUED"},
    {"name": "较高安全边际区", "low": 14.72, "high": 16.82, "kind": "UNDERVALUED"},
    {"name": "低估关注区", "low": 16.82, "high": 21.03, "kind": "UNDERVALUED"},
    {"name": "合理区", "low": 21.03, "high": 77.30, "kind": "FAIR"},
    {"name": "偏高区", "low": 77.30, "high": 85.03, "kind": "OVERVALUED"},
    {"name": "明显偏高区", "low": 85.03, "high": None, "kind": "OVERVALUED"},
]


def test_price_below_undervalued_lower_bound() -> None:
    # 600216.SH 12.95 < 低估关注区下沿 16.82 → 「低于低估关注区」，不是「未落入」
    assert price_position_label(12.95, LADDER_600216) == "低于低估关注区"


def test_price_in_undervalued_band() -> None:
    assert price_position_label(18.0, LADDER_600216) == "低估关注区"


def test_price_above_all_finite_upper_edges() -> None:
    bounded = [
        {"name": "低估关注区", "low": 9.0, "high": 10.0, "kind": "UNDERVALUED"},
        {"name": "偏高区", "low": 20.0, "high": 21.0, "kind": "OVERVALUED"},
    ]
    assert price_position_label(22.0, bounded) == "未落入（偏贵，尚未进入观察带）"


def test_no_usable_bands_or_price() -> None:
    assert price_position_label(12.95, []) == "资料不足"
    assert price_position_label(None, LADDER_600216) == "资料不足"
    assert price_position_label("资料不足", LADDER_600216) == "资料不足"
    assert price_position_label(12.95) == "资料不足"


def test_review_and_neutral_bands() -> None:
    assert price_position_label(80.0, LADDER_600216) == "高估复核区"
    assert price_position_label(40.0, LADDER_600216) == "中性"


def test_unknown_band_name_never_invents_a_sentence() -> None:
    assert price_position_label(5.0, [{"name": "神秘带", "low": 1.0, "high": 9.0}]) == "资料不足"


@pytest.mark.parametrize("price", [12.95, 18.0, 40.0, 80.0, 120.0])
def test_only_canonical_sentences_ever_returned(price: str) -> None:
    allowed = {
        "未落入（偏贵，尚未进入观察带）", "高估复核区", "中性",
        "低估关注区", "低于低估关注区", "资料不足",
    }
    assert price_position_label(price, LADDER_600216) in allowed
