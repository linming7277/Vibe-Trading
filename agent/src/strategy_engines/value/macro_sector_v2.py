"""Versioned macro-to-TDX-industry exposure matrix.

The 128 TDX second-level industries are the fine-grained track layer used by
Value Line V2.  Membership is explicit so a newly introduced industry cannot
silently inherit a broad keyword rule.  Exposure changes require a new matrix
version and therefore produce a new provenance key.
"""

from __future__ import annotations

from typing import Any

FORMULA_VERSION = "value-macro-sector-v2.1.0"

AXIS_LABELS = {
    "growth": "增长",
    "inflation": "通胀",
    "liquidity": "流动性",
    "credit": "信用",
    "financial_conditions": "金融条件",
}

GROUP_LABELS = {
    "financial": "金融",
    "real_estate": "地产与建筑",
    "resources": "资源与原材料",
    "industrial": "高端制造与工业",
    "consumer_discretionary": "可选消费",
    "consumer_staples": "必选消费与农业",
    "technology": "科技成长",
    "media_services": "传媒与服务",
    "healthcare": "医药医疗",
    "utilities": "公用与环保",
    "transport": "交通运输",
    "diversified": "综合与专业服务",
}

GROUP_EXPOSURES: dict[str, dict[str, float]] = {
    "financial": {"growth": .10, "liquidity": .25, "credit": .35, "financial_conditions": .30},
    "real_estate": {"growth": .20, "liquidity": .30, "credit": .40, "financial_conditions": .10},
    "resources": {"growth": .35, "inflation": .35, "credit": .15, "financial_conditions": .15},
    "industrial": {"growth": .45, "liquidity": .15, "credit": .20, "financial_conditions": .20},
    "consumer_discretionary": {"growth": .50, "inflation": -.20, "credit": .15, "financial_conditions": .15},
    "consumer_staples": {"growth": .25, "inflation": -.35, "liquidity": .10, "financial_conditions": .30},
    "technology": {"growth": .30, "liquidity": .25, "financial_conditions": .45},
    "media_services": {"growth": .35, "inflation": -.10, "liquidity": .20, "financial_conditions": .35},
    "healthcare": {"growth": .15, "inflation": -.20, "liquidity": .15, "financial_conditions": .50},
    "utilities": {"growth": .10, "inflation": -.20, "liquidity": .20, "credit": .20, "financial_conditions": .30},
    "transport": {"growth": .45, "inflation": -.15, "credit": .15, "financial_conditions": .25},
    "diversified": {"growth": .35, "inflation": -.10, "liquidity": .20, "credit": .15, "financial_conditions": .20},
}

INDUSTRY_GROUPS: dict[str, tuple[str, ...]] = {
    "financial": ("证券", "全国性银行", "地方性银行", "保险", "多元金融"),
    "real_estate": (
        "房地产开发", "房产服务", "商业物业经营", "房屋建设", "基础建设", "专业工程",
        "工程咨询服务", "装修装饰", "装饰建材", "水泥", "玻璃玻纤",
    ),
    "resources": (
        "贵金属", "工业金属", "稀有金属", "能源金属", "金属新材料", "煤炭开采", "焦炭加工",
        "油气开采", "油服工程", "石油化工", "冶钢原料", "普钢", "特钢", "非金属材料",
        "化学原料", "化学制品", "农用化工", "化纤", "塑料", "橡胶", "造纸", "包装印刷", "林业",
    ),
    "industrial": (
        "专用设备", "通用设备", "自动化设备", "工程机械", "轨交设备", "电机制造", "电网设备",
        "其他发电设备", "风电设备", "光伏设备", "电池", "环保设备", "环境监测", "航空装备", "航海装备",
        "航天装备", "地面兵装", "军工电子", "商用车", "汽车零部件", "摩托车及其他", "通信工程",
    ),
    "consumer_discretionary": (
        "乘用车", "汽车服务", "白色家电", "黑色家电", "厨卫电器", "小家电", "家电零部件",
        "家居用品", "文娱用品", "一般零售", "专业连锁", "电子商务", "贸易", "旅游", "酒店餐饮",
        "体育", "教育培训", "饰品", "服装家纺", "纺织制造", "日用化工",
    ),
    "consumer_staples": (
        "食品加工", "调味品", "休闲食品", "饮料乳品", "酿酒", "农产品加工", "饲料", "养殖业",
        "种植业", "渔业",
    ),
    "technology": (
        "半导体", "元器件", "消费电子", "其他电子", "电子化学品", "光学光电", "IT设备",
        "软件服务", "云服务", "产业互联网", "通信设备", "电信服务",
    ),
    "media_services": ("数字媒体", "游戏", "影视院线", "广播电视", "出版业", "广告营销"),
    "healthcare": ("中药", "化学制药", "生物制品", "医疗器械", "医疗服务", "医药商业", "医疗美容", "动物保健"),
    "utilities": ("电力", "水务", "燃气", "环境治理"),
    "transport": ("物流", "公路铁路", "航运港口", "航空机场"),
    "diversified": ("综合类", "专业服务"),
}

INDUSTRY_OVERRIDES: dict[str, dict[str, float]] = {
    "证券": {"liquidity": .30, "credit": .20, "financial_conditions": .50},
    "全国性银行": {"growth": .15, "liquidity": .15, "credit": .55, "financial_conditions": .15},
    "地方性银行": {"growth": .20, "liquidity": .15, "credit": .55, "financial_conditions": .10},
    "保险": {"growth": .20, "liquidity": -.15, "credit": .20, "financial_conditions": .45},
    "贵金属": {"inflation": .35, "liquidity": .25, "financial_conditions": -.40},
    "煤炭开采": {"growth": .30, "inflation": .45, "credit": .15, "financial_conditions": .10},
    "油气开采": {"growth": .30, "inflation": .45, "credit": .10, "financial_conditions": .15},
    "房地产开发": {"growth": .20, "liquidity": .30, "credit": .45, "financial_conditions": .05},
    "半导体": {"growth": .30, "liquidity": .25, "financial_conditions": .45},
    "酿酒": {"growth": .30, "inflation": -.30, "liquidity": .10, "financial_conditions": .30},
    "电力": {"growth": .10, "inflation": -.25, "liquidity": .20, "credit": .15, "financial_conditions": .30},
}

INDUSTRY_TO_GROUP: dict[str, str] = {
    industry: group for group, industries in INDUSTRY_GROUPS.items() for industry in industries
}
_INDUSTRY_COUNT = sum(len(industries) for industries in INDUSTRY_GROUPS.values())
if len(INDUSTRY_TO_GROUP) != _INDUSTRY_COUNT:
    raise RuntimeError("duplicate_tdx_industry_in_macro_matrix")
if len(INDUSTRY_TO_GROUP) != 128:
    raise RuntimeError(f"macro_matrix_expected_128_industries_got_{len(INDUSTRY_TO_GROUP)}")

DEFAULT_EXPOSURE = {"growth": .35, "inflation": -.10, "liquidity": .20, "credit": .15, "financial_conditions": .20}


def profile_for(industry_name: str) -> dict[str, Any]:
    group = INDUSTRY_TO_GROUP.get(industry_name)
    exposure = INDUSTRY_OVERRIDES.get(industry_name) or GROUP_EXPOSURES.get(group or "") or DEFAULT_EXPOSURE
    return {
        "industry_name": industry_name,
        "group": group or "unmapped",
        "group_name": GROUP_LABELS.get(group or "", "未映射"),
        "exposure": dict(exposure),
        "explicit": group is not None,
        "matrix_version": FORMULA_VERSION,
    }


def describe(industry_name: str, macro_axes: dict[str, float | None]) -> dict[str, Any]:
    profile = profile_for(industry_name)
    drivers: list[dict[str, Any]] = []
    for axis, sensitivity in profile["exposure"].items():
        value = macro_axes.get(axis)
        if value is None:
            continue
        adjusted = 100 - float(value) if sensitivity < 0 else float(value)
        drivers.append({
            "axis": axis,
            "axis_name": AXIS_LABELS.get(axis, axis),
            "axis_score": round(float(value), 4),
            "sensitivity": sensitivity,
            "adjusted_score": round(adjusted, 4),
            "weighted_value": adjusted * abs(sensitivity),
        })
    total = sum(abs(float(item["sensitivity"])) for item in drivers)
    score = round(sum(float(item["weighted_value"]) for item in drivers) / total, 4) if total else None
    for item in drivers:
        item["contribution"] = round(float(item.pop("weighted_value")) / total, 4) if total else None
    drivers.sort(key=lambda item: (-abs(float(item["sensitivity"])), str(item["axis"])))
    stance = "beneficiary" if score is not None and score >= 60 else "headwind" if score is not None and score <= 40 else "neutral"
    return {**profile, "score": score, "stance": stance, "drivers": drivers}


def calculate(industry_name: str, macro_axes: dict[str, float | None]) -> float | None:
    return describe(industry_name, macro_axes)["score"]
