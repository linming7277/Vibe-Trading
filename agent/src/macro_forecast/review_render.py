"""复盘渲染器 V1：确定性生成老板版次日预测复盘（§30/§64，0 LLM）。

只读取结构化 outcome 与原 forecast 的结构负载（预测时依据原样附带，§32）；
不写"为什么预测错了"（§31 避免 hindsight bias）。
"""

from __future__ import annotations

import json
from typing import Any

REVIEW_RENDERER_VERSION = "macro-forecast-review-renderer-v1"

MARKET_CN = {"STRONGER": "偏强", "RANGE_BOUND": "震荡", "WEAKER": "偏弱", "ABSTAIN": "暂不判断"}
OUTCOME_CN = {
    "HIT": "命中", "MISS": "未命中", "ABSTAINED": "暂不判断",
    "NOT_EVALUABLE": "数据不足无法评价", "PENDING": "尚未到复盘时间",
}


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:+.2f}%"


def _pp(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:+.2f}pp"


def render_review(outcome: dict[str, Any], forecast_row: dict[str, Any]) -> str:
    """由 outcome（结构化）+ 原预测行（结构负载）确定性渲染。"""
    payload = json.loads(forecast_row.get("structured_payload_json") or "{}")
    model_output = payload.get("model_output") or {}
    market = model_output.get("market") or {}
    target = outcome.get("target_trade_date") or "—"

    lines: list[str] = []
    lines.append("# 次日预测复盘")
    lines.append("")
    lines.append(f"日期：{target}")
    lines.append("")

    evaluation = outcome.get("market_evaluation")
    predicted = outcome.get("predicted_market_direction")
    lines.append("## 1. 大盘预测")
    lines.append("")
    lines.append(f"预测：{MARKET_CN.get(predicted, predicted or '未生成')}")
    if evaluation == "PENDING":
        lines.append("实际：尚未到复盘时间（T 日正式收盘未入库，不用盘中数据）")
        lines.append("结果：尚未到复盘时间")
    elif evaluation == "NOT_EVALUABLE":
        lines.append(f"实际：数据不足无法评价（{outcome.get('not_evaluable_reason') or '行情或预测状态不满足评价门'}）")
        lines.append("结果：数据不足无法评价")
    else:
        lines.append(f"实际：{outcome.get('actual_market_class')}")
        lines.append(f"实际涨跌：{_pct(outcome.get('actual_return'))}（{outcome.get('previous_trade_date')} 收盘 "
                     f"{outcome.get('previous_close')} → {target} 收盘 {outcome.get('actual_close')}）")
        lines.append(f"结果：{OUTCOME_CN.get(evaluation, evaluation)}")
        lines.append("")
        lines.append("当时主要依据（原样引用，不重新生成）：")
        lines.append(f"- 依据：{_evidence_text(payload, market.get('evidence_keys'))}")
        lines.append(f"- 反向因素：{_evidence_text(payload, market.get('counter_evidence_keys'))}")
    lines.append("")

    for title, side in (("## 2. 相对看强行业", "RELATIVE_STRONG"), ("## 3. 相对看弱行业", "RELATIVE_WEAK")):
        lines.append(title)
        lines.append("")
        rows = [item for item in outcome.get("industry_results") or [] if item.get("side") == side]
        if not rows:
            lines.append("（无该侧预测）")
            lines.append("")
            continue
        lines.append("| 行业 | 实际涨跌 | 相对沪深300 | 结果 |")
        lines.append("| --- | --- | --- | --- |")
        for item in rows:
            result = OUTCOME_CN.get(item.get("evaluation"), item.get("evaluation"))
            lines.append(f"| {item.get('display_name')} | {_pct(item.get('r_industry'))} | "
                         f"{_pp(item.get('rr'))} | {result} |")
        lines.append("")
        note_rows = [item for item in rows if item.get("absolute_note")]
        for item in note_rows:
            lines.append(f"（说明：{item.get('display_name')} {item['absolute_note']}。）")
        if note_rows:
            lines.append("")

    lines.append("## 4. 本日总结")
    lines.append("")
    market_line = OUTCOME_CN.get(evaluation, evaluation or "—")
    industry_rate = outcome.get("industry_hit_rate")
    lines.append(f"大盘：{market_line}")
    if industry_rate is None:
        lines.append("行业：无可评价预测")
    else:
        lines.append(f"行业：{outcome.get('strong_hits', 0) + outcome.get('weak_hits', 0)} / "
                     f"{(outcome.get('strong_evaluable_count') if outcome.get('strong_evaluable_count') is not None else outcome.get('strong_count', 0)) + (outcome.get('weak_evaluable_count') if outcome.get('weak_evaluable_count') is not None else outcome.get('weak_count', 0))} 命中")
        lines.append(f"强行业：{outcome.get('strong_hits', 0)}/{outcome.get('strong_count', 0)}；"
                     f"弱行业：{outcome.get('weak_hits', 0)}/{outcome.get('weak_count', 0)}")
    lines.append("")

    lines.append("## 5. 数据说明")
    lines.append("")
    lines.append(f"- 固定评价口径：{outcome.get('outcome_formula_version')}（大盘 ±0.50% 区间，边界属震荡；行业只看相对沪深300）")
    lines.append(f"- 基准：{outcome.get('benchmark_symbol')} 沪深300 价格指数正式收盘")
    lines.append(f"- 失效事件观察：{outcome.get('invalidation_observed')}（V1 不因事件自动取消成绩）")
    lines.append("- 行业自身涨跌不改变判定：预测相对看强的行业自身下跌但跌幅小于沪深300 仍计命中")
    lines.append("- 本复盘不解释对错原因，只对照预测时依据与真实结果")
    return "\n".join(lines)


def _evidence_text(payload: dict[str, Any], keys: Any) -> str:
    items = [str(item) for item in (keys or []) if str(item).strip()]
    if not items:
        return "—"
    info = payload.get("input") or {}
    alias = info.get("alias_map") or {}
    catalog = info.get("evidence_catalog") or {}
    values = info.get("evidence_values") or {}
    rendered = []
    for key in items:
        full = alias.get(key, key)
        description = catalog.get(full)
        value = values.get(key)
        rendered.append(f"{description}[{value}]" if description and value is not None
                        else (description or key))
    return "、".join(rendered)
