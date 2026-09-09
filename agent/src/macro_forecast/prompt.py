"""联合预测 Prompt v2（紧凑版）：输入压缩 + 短键别名 + 硬规则指令。

压缩契约（宏观预测输入压缩与运行稳定性 V1 §三-§十一）：
- 候选行业行只保留 id/name/ret5/rel1/rel5/rel20/tc/ms/hs；
- 证据目录只给 短键→紧凑值；完整含义由服务层 alias 映射保留与回查；
- 宏观只给 regime/五轴/最近变化/覆盖/缺口；市场只给基准核心字段；
- 指令压成 12 条硬规则，详细校验交给 validate.py；
- 输入不含历史 K 线、不含宏观原始序列、不含任何海外推测数据。
"""

from __future__ import annotations

import hashlib
from typing import Any

PROMPT_VERSION = "macro-market-industry-prompt-v2.1"  # v2.1: xcm.cny_mid 官方中间价接入（审计 V1：USE_NOW 唯一项）

MARKET_DIRECTIONS = ("STRONGER", "RANGE_BOUND", "WEAKER", "ABSTAIN")
NO_COUNTER_KEY = "NO_MATERIAL_COUNTER_EVIDENCE_IN_BUNDLE"

SYSTEM_PROMPT = """任务：基于输入JSON，对下一交易日T做一次联合研究判断（非交易建议），输出一个JSON对象。
硬规则：
1.只能使用输入JSON中的数据；不得使用你记忆中的新闻、行情或常识补数据。
2.仅允许使用输入JSON中明确提供的数据；不得使用你记忆中的新闻、行情或常识补数据。跨市场输入仅限 xcm 字段明确提供的人民币对美元官方中间价；输入未提供的海外指数、美债利率、商品价格及其它汇率信息一律不得自行补充或推测。
3.market.direction 只能是 STRONGER|RANGE_BOUND|WEAKER|ABSTAIN，指沪深300指数T日收盘相对P日收盘的表现状态；不预测点位。
4.行业只能引用 cands 中给出的 id（S*/W*）；relative_strong 与 relative_weak 各最多3个，允许0个；同一 id 不得同时出现在两侧。
5.strong/weak 是相对沪深300的次日相对强弱；行业自身可能下跌，跌幅小于沪深300仍属相对强。
6.所有 evidence_keys 必须引用 ev 中的短键：market 至少2个；每个行业至少1个该行业自身键（如 S1_REL5）；理由提及宏观必须同时引用 G*/A*/E* 键。
7.market 必须给 counter_evidence_keys 至少1个（ev 中的键）；输入内确无相反信号时才可用 NO_MATERIAL_COUNTER_EVIDENCE_IN_BUNDLE。
8.invalidation_conditions 至少1条可执行条件（具体事件/数据变化），禁止"市场存在不确定性"类空话。
9.禁止输出概率、置信度、点位、价格预期、仓位、交易指令。
10.证据不足必须 abstain=true 并在 abstain_reason 说明具体缺口/信号冲突/日历问题；核心数据齐备时不得仅以"有风险"弃权。
11.输出JSON必须且只需以下结构（不得增删字段）：
{"market":{"direction":"","summary":"一句话中文","evidence_keys":[],"counter_evidence_keys":[],"invalidation_conditions":[]},
"industries":{"relative_strong":[{"id":"","reason":"中文","evidence_keys":[]}],"relative_weak":[]},
"overall_cautions":["中文"],"abstain":false,"abstain_reason":null}
12.行业对象使用 "id" 字段引用候选 id；summary/reason 用简洁中文。
13.xcm.cny_mid 为人民币对美元官方中间价（当日09:15官方公布，非市场收盘价、非离岸CNH）。方向以 direction_cn 为准（USD/CNY数值上升=人民币中间价偏弱）。summary 中提及人民币或汇率时必须引用对应 XR_CNYMID_* 证据键并采用 direction_cn 的方向文案；禁止"离岸人民币""市场汇率""市场收盘""资金流入/外资流入"表述；禁止由中间价推导具体行业涨跌。是否引用由你判断，数据存在不构成必须使用的理由。"""


def _pct(value: Any) -> str:
    if value is None:
        return "null"
    return f"{float(value) * 100:.2f}%"


def _pp(value: Any) -> str:
    if value is None:
        return "null"
    return f"{float(value) * 100:+.2f}pp"


def _axis_tier(value: Any) -> str:
    if value is None:
        return "缺"
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "缺"
    if score >= 60:
        return "暖"
    if score <= 40:
        return "冷"
    return "中"


def _day(value: object) -> str:
    text = str(value or "").replace("-", "")[:8]
    return text if len(text) == 8 else str(value or "")


# ---------------------------------------------------------------------------
# 紧凑上下文 + 短键别名
# ---------------------------------------------------------------------------

def build_cny_mid_summary(cross_market_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 bundle cross_market_context 提取 USD/CNY 官方中间价的 compact 摘要。

    审计 V1 结论：六项跨市场数据中唯一 USE_NOW。方向语义在这里定死
    （USD/CNY 数值上升 = 人民币中间价偏弱），模型不做自行推导。
    不可用（缺失/非 READY）→ None，调用方置 xcm.cny_mid=null 并加 gap。
    """
    fx = ((cross_market_context or {}).get("fx") or {}).get("usd_cny_official_mid") or {}
    value = fx.get("value")
    if value is None or fx.get("freshness") != "READY":
        return None
    changes = fx.get("changes") or {}
    d1 = changes.get("chg_1d_pct")
    direction = None
    if d1 is not None:
        direction = "CNY_WEAKER" if d1 > 0 else ("CNY_STRONGER" if d1 < 0 else "UNCHANGED")
    return {
        "value": round(float(value), 4),
        "obs": fx.get("observed_at"),
        "d1_pct": round(float(d1), 6) if d1 is not None else None,
        "d5_pct": round(float(d5), 6) if (d5 := changes.get("chg_5d_pct")) is not None else None,
        "direction_cn": direction,
        "freshness": fx.get("freshness"),
        "note": "人民币对美元官方中间价（非市场收盘价）",
    }


_GAP_XCM_CNY = "XCM_CNY_MID_UNAVAILABLE"


def build_compact_context(
    *,
    bundle: dict[str, Any],
    macro_context: dict[str, Any],
    candidates: dict[str, Any],
    market_extras: dict[str, Any] | None = None,
    cross_market_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 ≤8k token 目标的模型输入；同时返回 alias 映射由调用方保留。"""
    from src.macro_forecast.registry import REFERENCE_INDEXES

    target = bundle.get("target") or {}
    benchmark = (bundle.get("market") or {}).get("benchmark") or {}
    extras = market_extras or {}
    # 输入包 market.reference 可能包含行业条目（Phase1 包按全量代码填充）；
    # 紧凑上下文只保留真正的背景指数（§八）。
    reference_codes = {code for code, _ in REFERENCE_INDEXES}

    # --- 市场段（§八） -------------------------------------------------
    # ref5 优先取引擎从 K 线确定性计算的背景指数 5 日收益（extras.reference_5d）；
    # 输入包 market.reference 若含背景指数亦可回退（Phase1 包按全量代码填充）。
    _ref5_src = (bundle.get("market") or {}).get("reference") or {}
    mkt: dict[str, Any] = {
        "ret_1d": benchmark.get("ret_1d"),
        "ret_5d": benchmark.get("ret_5d"),
        "ret_20d": extras.get("benchmark_ret_20d"),
        "vol_20d": benchmark.get("vol_20d"),
        "amt20": benchmark.get("amount_ratio_vs_20d_mean"),
        "breadth": extras.get("breadth_20d"),
        "risk_app": extras.get("risk_appetite"),
        "ref5": (
            {str(code): value for code, value in sorted((extras.get("reference_5d") or {}).items())
             if value is not None}
            or {
                code.replace(".SH", "").replace(".SZ", "").replace(".BJ", ""): features.get("ret_5d")
                for code, features in sorted(_ref5_src.items())
                if code in reference_codes and features.get("status") == "READY"
            }
        ),
    }

    # --- 宏观段（§七） --------------------------------------------------
    axes = macro_context.get("axes") or {}
    changes = [
        f"{_day(event.get('research_as_of'))} {str(event.get('axis_key') or event.get('event_type')).replace('MACRO_', '')} {event.get('from_value')}->{event.get('to_value')}"
        for event in (macro_context.get("events") or [])[:5]
    ]
    macro: dict[str, Any] = {
        "regime": macro_context.get("regime"),
        "axes": {key: axes.get(key) for key in sorted(axes)},
        "changes": changes,
        "coverage": (bundle.get("macro_coverage") or {}).get("direction_groups_covered"),
        "pit": "axes/facts按P日可见保守口径",
    }

    # --- 候选段（§三§四） -------------------------------------------------
    def slim_row(row: dict[str, Any], short_id: str) -> dict[str, Any]:
        return {
            "id": short_id, "n": row.get("name"),
            "ret5": row.get("ret_5d"),
            "rel1": row.get("relative_1d"), "rel5": row.get("relative_5d"), "rel20": row.get("relative_20d"),
            "tc": row.get("trend_consistency"), "ms": row.get("macro_stance"), "hs": row.get("history_status"),
        }

    strong_rows, weak_rows = [], []
    for index, row in enumerate(candidates["strong"], 1):
        strong_rows.append(slim_row(row, f"S{index}"))
    for index, row in enumerate(candidates["weak"], 1):
        weak_rows.append(slim_row(row, f"W{index}"))

    # --- 证据段（§五§六）：短键 -> 紧凑值；alias -> 完整键 --------------------
    ev: dict[str, str] = {}
    alias: dict[str, str] = {}
    ev["M1"], alias["M1"] = _pct(benchmark.get("ret_1d")), "MKT_BENCH_RET_1D"
    ev["M2"], alias["M2"] = _pct(benchmark.get("ret_5d")), "MKT_BENCH_RET_5D"
    if mkt["ret_20d"] is not None:
        ev["M3"], alias["M3"] = _pct(mkt["ret_20d"]), "MKT_BENCH_RET_20D"
    ev["M4"], alias["M4"] = f"{benchmark.get('vol_20d') if benchmark.get('vol_20d') is None else round(float(benchmark['vol_20d']), 5)}", "MKT_BENCH_VOL_20D"
    if mkt["amt20"] is not None:
        ev["M5"], alias["M5"] = f"x{mkt['amt20']:.2f}", "MKT_BENCH_AMOUNT_RATIO_20D"
    # 语义守则 v1.2：真实宽度/风险偏好可引键（不得用背景指数冒充宽度）。
    if mkt["breadth"] is not None:
        ev["MB"], alias["MB"] = f"{float(mkt['breadth']):.1f}", "MKT_BREADTH_20D"
    if mkt["risk_app"] is not None:
        ev["MR"], alias["MR"] = f"{float(mkt['risk_app']):.1f}", "MKT_RISK_APPETITE"
    for index, (code, value) in enumerate(mkt["ref5"].items(), 6):
        key = f"M{index}"
        ev[key], alias[key] = _pct(value), f"MKT_REF_{code}_RET_5D"
    ev["G1"], alias["G1"] = str(macro.get("regime")), "MACRO_REGIME_LATEST"
    for index, (axis, value) in enumerate(sorted(axes.items()), 1):
        key = f"A{index}"
        ev[key], alias[key] = f"{axis} {value} {_axis_tier(value)}", f"MACRO_AXIS_{axis.upper()}"
    for index, event in enumerate((macro_context.get("events") or [])[:5], 1):
        key = f"E{index}"
        suffix = f"_{str(event.get('axis_key')).upper()}" if event.get("axis_key") else ""
        alias[key] = f"MACRO_EVENT_{_day(event.get('research_as_of'))}_{str(event.get('event_type')).replace('MACRO_', '')}{suffix}"
        ev[key] = changes[index - 1]
    ev["CV"], alias["CV"] = "/".join(macro.get("coverage") or []), "MACRO_COVERAGE_GROUPS"
    for row, short_id in zip(candidates["strong"], (f"S{i}" for i in range(1, len(candidates["strong"]) + 1))):
        full = row["industry_id"].replace("tdx:", "").replace(".", "_")
        if row.get("relative_5d") is not None:
            ev[f"{short_id}_REL5"], alias[f"{short_id}_REL5"] = _pp(row["relative_5d"]), f"IND_{full}_RELATIVE_5D"
        if row.get("ret_5d") is not None:
            ev[f"{short_id}_RET5"], alias[f"{short_id}_RET5"] = _pct(row["ret_5d"]), f"IND_{full}_RET_5D"
        if row.get("macro_stance"):
            ev[f"{short_id}_MS"], alias[f"{short_id}_MS"] = str(row["macro_stance"]), f"IND_{full}_MACRO_STANCE"
    for row, short_id in zip(candidates["weak"], (f"W{i}" for i in range(1, len(candidates["weak"]) + 1))):
        full = row["industry_id"].replace("tdx:", "").replace(".", "_")
        if row.get("relative_5d") is not None:
            ev[f"{short_id}_REL5"], alias[f"{short_id}_REL5"] = _pp(row["relative_5d"]), f"IND_{full}_RELATIVE_5D"
        if row.get("ret_5d") is not None:
            ev[f"{short_id}_RET5"], alias[f"{short_id}_RET5"] = _pct(row["ret_5d"]), f"IND_{full}_RET_5D"
        if row.get("macro_stance"):
            ev[f"{short_id}_MS"], alias[f"{short_id}_MS"] = str(row["macro_stance"]), f"IND_{full}_MACRO_STANCE"

    gaps = list(bundle.get("gaps") or [])
    if mkt["breadth"] is None:
        gaps = gaps + ["BREADTH_UNAVAILABLE"]
    if mkt["risk_app"] is None:
        gaps = gaps + ["RISK_APPETITE_UNAVAILABLE"]

    # --- 跨市场段（v2.1：仅 USD/CNY 官方中间价，审计 V1 USE_NOW 唯一项） ---
    xcm: dict[str, Any] | None = None
    if cross_market_summary is not None:
        cny = cross_market_summary
        xcm = {"cny_mid": cny}
        obs = _day(cny.get("obs"))
        direction_text = {
            "CNY_STRONGER": "人民币中间价偏强",
            "CNY_WEAKER": "人民币中间价偏弱",
            "UNCHANGED": "人民币中间价基本持平",
        }.get(cny.get("direction_cn"))
        ev["XR_CNYMID_LVL"], alias["XR_CNYMID_LVL"] = (
            f"官方中间价 {cny.get('value')} obs={obs}", "XCM_USDCNY_MID_LVL")
        if cny.get("d1_pct") is not None:
            ev["XR_CNYMID_1D"], alias["XR_CNYMID_1D"] = (
                f"较前中间价 {_pct(cny['d1_pct'])}（{direction_text}）obs={obs}",
                "XCM_USDCNY_MID_1D")
        if cny.get("d5_pct") is not None:
            ev["XR_CNYMID_5D"], alias["XR_CNYMID_5D"] = (
                f"较5日前中间价 {_pct(cny['d5_pct'])} obs={obs}", "XCM_USDCNY_MID_5D")
    else:
        gaps = gaps + [_GAP_XCM_CNY]

    return {
        "T": target.get("target_date"), "P": target.get("previous_date"), "C": target.get("cutoff_at"),
        "mode": bundle.get("data_mode"),
        "mkt": mkt,
        "macro": macro,
        "xcm": xcm,  # v2.1：仅 cny_mid；None 时整体为 null（payload 语义=不可用）
        "gaps": sorted(set(gaps)),
        "cands": {"s": strong_rows, "w": weak_rows},
        "ev": ev,
        "_alias": alias,  # 服务层保留；序列化时剔除
    }


def strip_private(context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in context.items() if not key.startswith("_")}


def build_user_payload(context: dict[str, Any]) -> str:
    import json

    return json.dumps(strip_private(context), ensure_ascii=False, separators=(",", ":"))


def prompt_hash(payload_text: str) -> str:
    return "p_" + hashlib.sha256(payload_text.encode("utf-8")).hexdigest()[:16]


def estimate_tokens(text: str) -> int:
    """混合估算：CJK ~1.6 字符/token，ASCII ~4 字符/token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 1.6 + other / 4)


def estimate_prompt_size(system_text: str, user_payload: str) -> dict[str, int]:
    return {
        "system_chars": len(system_text),
        "payload_chars": len(user_payload),
        "total_chars": len(system_text) + len(user_payload),
        "input_token_estimate": estimate_tokens(system_text) + estimate_tokens(user_payload),
    }
