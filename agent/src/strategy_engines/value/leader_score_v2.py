"""Immutable Value Leader V2 formula."""

from __future__ import annotations

from ..common.scoring import weighted_score

FORMULA_VERSION = "value-leader-v2.0.0"
WEIGHTS = {
    "industry_position": .25,
    "profitability": .20,
    "growth_stability": .15,
    "cash_flow": .15,
    "valuation": .15,
    "governance_risk": .10,
}

DIMENSION_LABELS = {
    "industry_position": "规模与经营地位",
    "profitability": "盈利能力",
    "growth_stability": "成长稳定性",
    "cash_flow": "现金流质量",
    "valuation": "同行相对估值",
    "governance_risk": "财务稳健与波动",
}

DIMENSION_METRIC_WEIGHTS = {
    "industry_position": {"market_cap": .40, "revenue": .30, "net_profit": .30},
    "profitability": {"roe": .40, "gross_margin": .30, "net_margin": .30},
    "growth_stability": {
        "revenue_cagr": .30, "profit_cagr": .30,
        "growth_consistency": .20, "growth_low_volatility": .20,
    },
    "cash_flow": {
        "cash_conversion": .30, "ocf_margin": .30,
        "positive_ocf_years": .20, "ocf_trend": .20,
    },
    "valuation": {"pe": .40, "pb": .30, "dividend_yield": .30},
    "governance_risk": {"debt_safety": .40, "shareholder_stability": .30, "low_beta": .30},
}

METRIC_DEFINITIONS = {
    "market_cap": {"label": "总市值", "unit": "亿元", "higher_is_better": True, "description": "公司当前总市值"},
    "revenue": {"label": "营业收入", "unit": "元", "higher_is_better": True, "description": "最新完整年度营业收入"},
    "net_profit": {"label": "净利润", "unit": "元", "higher_is_better": True, "description": "最新完整年度净利润"},
    "roe": {"label": "ROE", "unit": "%", "higher_is_better": True, "description": "最新完整年度净资产收益率"},
    "gross_margin": {"label": "毛利率", "unit": "%", "higher_is_better": True, "description": "最新完整年度毛利率"},
    "net_margin": {"label": "净利率", "unit": "%", "higher_is_better": True, "description": "最新完整年度净利率"},
    "revenue_cagr": {"label": "营收CAGR", "unit": "%", "higher_is_better": True, "description": "优先5年、至少3年的营收复合增速"},
    "profit_cagr": {"label": "利润CAGR", "unit": "%", "higher_is_better": True, "description": "优先5年、至少3年的利润复合增速"},
    "growth_consistency": {"label": "增长一致性", "unit": "%", "higher_is_better": True, "description": "营收和利润保持正增长的年度比例"},
    "growth_low_volatility": {"label": "增长低波动", "unit": "相对值", "higher_is_better": True, "description": "增长波动越小，数值越靠近0"},
    "cash_conversion": {"label": "现金转换率", "unit": "%", "higher_is_better": True, "description": "经营现金流/净利润；利润很小时可能异常放大"},
    "ocf_margin": {"label": "经营现金流率", "unit": "%", "higher_is_better": True, "description": "经营现金流/营业收入"},
    "positive_ocf_years": {"label": "正现金流年份", "unit": "%", "higher_is_better": True, "description": "经营现金流为正的年度比例"},
    "ocf_trend": {"label": "现金流趋势", "unit": "%", "higher_is_better": True, "description": "经营现金流近3年复合变化"},
    "pe": {"label": "PE(TTM)", "unit": "倍", "higher_is_better": False, "description": "只比较正PE，越低相对越有吸引力"},
    "pb": {"label": "PB", "unit": "倍", "higher_is_better": False, "description": "市净率，越低相对越有吸引力"},
    "dividend_yield": {"label": "股息率", "unit": "%", "higher_is_better": True, "description": "当前股息率"},
    "debt_safety": {"label": "低负债", "unit": "反向值", "higher_is_better": True, "description": "资产负债率取反，负债率越低越好"},
    "shareholder_stability": {"label": "股东人数稳定", "unit": "反向%", "higher_is_better": True, "description": "最近两期股东人数变化绝对值取反"},
    "low_beta": {"label": "低市场敏感度", "unit": "反向值", "higher_is_better": True, "description": "Beta绝对值取反，越接近0越稳定"},
}


def formula_contract() -> dict:
    """Return the single frontend/backend explanation contract."""
    return {
        "version": FORMULA_VERSION,
        "comparison_scope": "仅在同一通达信三级/末级行业内部比较，不可跨行业比较",
        "leader_limit": 2,
        "minimum_overall_coverage": .80,
        "minimum_dimension_coverage": .50,
        "normalization": "行业内2.5%/97.5%缩尾后计算0-100分位；并列值取平均分位",
        "dimensions": [
            {
                "key": key, "label": DIMENSION_LABELS[key], "weight": WEIGHTS[key],
                "metrics": [
                    {"key": metric, "weight": weight, **METRIC_DEFINITIONS[metric]}
                    for metric, weight in DIMENSION_METRIC_WEIGHTS[key].items()
                ],
            }
            for key in WEIGHTS
        ],
    }


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS, minimum_coverage=.80)
