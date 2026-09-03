"""Deterministic watchpoint candidates from persisted research only."""

from __future__ import annotations

from typing import Any

from .contracts import GENERIC_THESIS_MARKERS, data_gap, source_ref, watchpoint

_RISK_TITLES = {
    "FINANCIAL_PROFIT_CASH_DIVERGENCE": "利润与经营现金流是否重新匹配",
    "FINANCIAL_RECEIVABLE": "应收账款是否继续扩张",
    "FINANCIAL_INVENTORY": "存货是否继续积压",
    "FINANCIAL_LIQUIDITY": "短期流动性是否继续收紧",
    "FINANCIAL_CASH_COVERAGE": "现金对短期负债的覆盖",
    "FINANCIAL_INTEREST_DEBT": "带息债务是否继续抬升",
    "FINANCIAL_LEVERAGE": "资产负债率是否继续抬升",
    "FINANCIAL_CAPEX": "资本开支与现金流是否匹配",
    "DISCLOSURE": "后续披露能否澄清已提示风险",
    "THESIS_CHALLENGE": "核心逻辑挑战证据是否被回应",
    "VALUE_TRAP": "低估陷阱线索是否恶化",
}

_FINANCIAL_RISK_TYPES = {
    "FINANCIAL_PROFIT_CASH_DIVERGENCE", "FINANCIAL_RECEIVABLE", "FINANCIAL_INVENTORY",
    "FINANCIAL_LIQUIDITY", "FINANCIAL_CASH_COVERAGE", "FINANCIAL_INTEREST_DEBT",
    "FINANCIAL_LEVERAGE", "FINANCIAL_CAPEX",
}

_RISK_METRICS = {
    "FINANCIAL_PROFIT_CASH_DIVERGENCE": "OCF",
    "FINANCIAL_RECEIVABLE": "RECEIVABLE",
    "FINANCIAL_INVENTORY": "INVENTORY",
    "FINANCIAL_INTEREST_DEBT": "INTEREST_BEARING_DEBT",
    "FINANCIAL_LEVERAGE": "DEBT",
    "FINANCIAL_DEBT_RATIO": "DEBT",
    "FINANCIAL_CAPEX": "CAPEX",
    "FINANCIAL_CAPEX_PRESSURE": "CAPEX",
    "FINANCIAL_CASH_COVERAGE": "OCF",
    "FINANCIAL_LIQUIDITY": "LIQUIDITY",
    "FINANCIAL_CASH_FLOW": "OCF",
    "FINANCIAL_MARGIN_DECLINE": "GROSS_MARGIN",
}

_METRIC_TITLES = {
    "GROSS_MARGIN": "毛利率能否维持或修复",
    "NET_MARGIN": "净利率能否回到可核验中枢附近",
    "NET_PROFIT": "利润修复能否持续",
    "OCF": "利润能否转化为经营现金",
    "REVENUE": "收入路径能否延续",
    "DEBT": "资产负债率是否继续抬升",
    "INTEREST_BEARING_DEBT": "带息债务是否继续抬升",
    "RECEIVABLE": "应收账款是否继续快于收入",
    "INVENTORY": "存货是否继续积压",
    "ROE": "ROE 能否止跌",
    "CAPEX": "资本开支与经营现金是否匹配",
}

_RISK_FORMULA_VERSION = "risk-research-v1"
_NE_FORMULA_VERSION = "normalized-earnings-ref-v1"
_CYCLE_FORMULA_VERSION = "cycle-profit-scenario-v1"
_MOAT_FORMULA_VERSION = "moat-research-v1.0.0"
_CAPITAL_FORMULA_VERSION = "capital-allocation-research-v1.0.0"
_RELIABILITY_FORMULA_VERSION = "valuation-reliability-v1"
_DIMENSION_LABELS = {
    "BRAND": "品牌", "SWITCHING_COST": "转换成本", "NETWORK_EFFECT": "网络效应",
    "COST_ADVANTAGE": "成本优势", "EFFICIENT_SCALE": "有效规模", "TECHNOLOGY": "技术",
    "CHANNEL": "渠道", "REGULATORY": "监管许可", "CUSTOMER_RELATIONSHIP": "客户关系",
    "SUPPLY_CHAIN": "供应链",     "DATA_PLATFORM_ECOSYSTEM": "数据与平台",
}
_MATERIAL_YOY = 8.0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in {float("inf"), float("-inf")} else None


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("condition") or value.get("text") or value.get("metric") or "").strip()
    return str(value or "").strip()


def _annual(financial: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in list(financial.get("history") or []) if str(row.get("period_type") or "") == "annual"]


def _is_generic_thesis(text: str) -> bool:
    return any(marker in text for marker in GENERIC_THESIS_MARKERS)


def thesis_items(thesis: dict[str, Any] | None, *, research_as_of: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if not thesis:
        return items, gaps
    authority = str(thesis.get("authority_status") or "")
    status = str(thesis.get("status") or "")
    as_of = str(thesis.get("source_data_as_of") or thesis.get("created_at") or research_as_of or "")[:10] or research_as_of
    refs = [source_ref(
        module="THESIS", formula_version=str(thesis.get("formula_version") or "company-thesis-v1"),
        research_as_of=as_of, thesis_id=thesis.get("thesis_id"), version=thesis.get("version"),
    )]
    cautions: list[str] = []
    if authority == "AI_PROVISIONAL":
        cautions.append("AI初步研究，尚未人工确认")
    elif authority == "LEGACY_UNVERIFIED":
        cautions.append("历史核心逻辑尚未完成当前核验")

    if authority == "HUMAN_REJECTED" or status == "FALSIFIED":
        items.append(watchpoint(
            category="THESIS", title="核心逻辑已被否定，需要重新研究",
            current_state="当前核心逻辑已被人工否定或标记为证伪，不能再当作已确认投资逻辑。",
            positive_condition="需要形成新的、可核验的核心逻辑后再进入持续跟踪。",
            negative_condition="继续沿用已被否定的逻辑作为研究前提。",
            source_module="THESIS", source_refs=refs, research_as_of=as_of,
            importance_tier="CRITICAL", canonical_metric="THESIS_INVALIDATION",
            origin="THESIS", next_review_anchor="MANUAL_REVIEW", cautions=cautions,
            direction="INVALIDATION",
        ))
        return items, gaps

    invalids = [item for item in list(thesis.get("invalid_conditions") or []) if _text(item)]
    supports = [item for item in list(thesis.get("supporting_conditions") or []) if _text(item)]
    metrics = [item for item in list(thesis.get("key_metrics_to_monitor") or []) if _text(item)]
    if not invalids and authority == "LEGACY_UNVERIFIED":
        gaps.append(data_gap(
            category="THESIS", description="历史核心逻辑缺少明确证伪条件，需要人工补充。",
            source_module="THESIS", research_as_of=as_of,
        ))
        return items, gaps
    if not invalids and not supports and not metrics:
        return items, gaps

    generic = bool(invalids) and all(_is_generic_thesis(_text(item)) for item in invalids)
    negative = "；".join(_text(item) for item in invalids[:3]) or "核心逻辑被证伪。"
    positive_bits = [_text(item) for item in supports[:2]]
    positive_bits.extend(_text(item) for item in metrics[:2])
    if not positive_bits:
        positive_bits.append("需要看到已记录的核心逻辑条件没有被新一期数据否定。")
    current = "正式核心逻辑已确认，条件待后续数据核验。" if authority == "HUMAN_CONFIRMED" else (
        "当前为核心逻辑的初步研究上下文，不是已经确认的公司投资逻辑。" if authority == "AI_PROVISIONAL"
        else "历史核心逻辑条件待核验。"
    )
    items.append(watchpoint(
        category="THESIS",
        title="核验核心逻辑是否仍成立" if not generic else "跟踪模板化证伪条件",
        current_state=current,
        positive_condition="；".join(positive_bits),
        negative_condition=negative,
        source_module="THESIS", source_refs=refs, research_as_of=as_of,
        importance_tier="NORMAL" if generic else ("HIGH" if authority == "HUMAN_CONFIRMED" else "NORMAL"),
        canonical_metric="THESIS_INVALIDATION", origin="THESIS",
        next_review_anchor="MANUAL_REVIEW", cautions=cautions, generic=generic,
        direction="INVALIDATION",
    ))
    return items, gaps


def _split_watch_item(watch: str) -> tuple[str, str]:
    text = str(watch or "").strip()
    if not text:
        return "需要看到该项风险指标不再继续恶化。", ""
    negative = text
    positive = "需要看到该项风险指标不再继续恶化。"
    for sep in ("；", ";", "。"):
        if sep in text:
            parts = [part.strip() for part in text.split(sep) if part.strip()]
            if len(parts) >= 2:
                first, second = parts[0], parts[1]
                if any(token in first for token in ("若", "一旦", "恶化", "继续", "跌破", "上升", "下降")):
                    negative, positive = first, second if any(token in second for token in ("若", "改善", "修复", "回升", "覆盖")) else positive
                elif any(token in second for token in ("若", "一旦", "恶化")):
                    positive, negative = first, second
            break
    return positive, negative


def risk_items(risk: dict[str, Any] | None, *, research_as_of: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    payload = dict(risk or {})
    as_of = str(payload.get("as_of") or research_as_of or "")[:10] or research_as_of
    overall = str(payload.get("overall_risk") or "UNKNOWN")
    if overall == "UNKNOWN" or not payload:
        gaps.append(data_gap(
            category="RISK", description="关键风险资料不足，需补充风险证据。",
            source_module="RISK", research_as_of=as_of,
        ))
        return items, gaps
    for row in list(payload.get("risks") or []):
        severity = str(row.get("severity") or "").upper()
        status = str(row.get("status") or "").upper()
        if severity not in {"HIGH", "MEDIUM"} and status not in {"CONFIRMED", "WATCH"}:
            continue
        if status == "UNKNOWN":
            continue
        risk_type = str(row.get("risk_type") or "RISK")
        watch = str(row.get("watch_item") or "")
        positive, negative = _split_watch_item(watch)
        if not negative:
            negative = str(row.get("why_it_matters") or watch or row.get("text") or "")
        financial = risk_type in _FINANCIAL_RISK_TYPES or risk_type.startswith("FINANCIAL_")
        disclosure = "DISCLOSURE" in risk_type or "公告" in str(row.get("text") or "")
        items.append(watchpoint(
            category="RISK",
            title=_RISK_TITLES.get(risk_type, str(row.get("text") or risk_type)[:40] or "风险观察"),
            current_state=str(row.get("text") or ""),
            positive_condition=positive,
            negative_condition=negative or str(row.get("watch_item") or "该项风险继续恶化。"),
            source_module="RISK",
            source_refs=[source_ref(
                module="RISK", formula_version=str(payload.get("formula_version") or _RISK_FORMULA_VERSION),
                research_as_of=as_of, risk_type=risk_type, snapshot_id=payload.get("snapshot_id"),
            )],
            research_as_of=as_of,
            importance_tier="HIGH" if severity == "HIGH" else "NORMAL",
            canonical_metric=_RISK_METRICS.get(risk_type, "RISK_ITEM"),
            origin="RISK_HIGH" if severity == "HIGH" else "RISK_MEDIUM",
            next_review_anchor="NEXT_QUARTER" if financial else ("NEXT_DISCLOSURE" if disclosure else None),
            direction="WORSEN" if severity == "HIGH" else "WATCH",
            cautions=[str(row.get("why_it_matters") or "")] if row.get("why_it_matters") else [],
        ))
    return items, gaps


def _yoy_change(current: dict[str, Any], previous: dict[str, Any], field: str) -> float | None:
    after, before = _number(current.get(field)), _number(previous.get(field))
    if after is None or before is None or before == 0:
        return None
    if field in {"gross_margin", "net_margin", "roe", "debt_ratio", "interest_bearing_debt_ratio"}:
        return after - before
    return (after / before - 1) * 100


def financial_items(
    financial: dict[str, Any],
    normalized: dict[str, Any] | None,
    cycle: dict[str, Any] | None,
    *,
    research_as_of: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if not financial:
        return items, gaps
    annual = _annual(financial)
    as_of = str(financial.get("as_of") or research_as_of or "")[:10] or research_as_of
    feature = dict(financial.get("feature") or {})
    fin_ref = source_ref(module="FINANCIAL", formula_version=str(feature.get("feature_version") or "financial-feature"),
                         research_as_of=as_of, snapshot_id=financial.get("id"))
    selected: set[str] = set()

    def add(metric: str, *, title: str, current: str, positive: str, negative: str, origin: str,
            importance: str = "NORMAL", extra_refs: list[dict[str, Any]] | None = None,
            history_note: str = "") -> None:
        if metric in selected:
            return
        selected.add(metric)
        cautions = [history_note] if history_note else []
        items.append(watchpoint(
            category="FINANCIAL", title=title, current_state=current,
            positive_condition=positive, negative_condition=negative,
            source_module="FINANCIAL", source_refs=[fin_ref, *(extra_refs or [])],
            research_as_of=as_of, importance_tier=importance, canonical_metric=metric,
            origin=origin, next_review_anchor="NEXT_QUARTER", direction="WATCH",
            cautions=cautions,
        ))

    if len(annual) >= 2:
        prev, latest = annual[-2], annual[-1]
        gm_delta = _yoy_change(latest, prev, "gross_margin")
        if gm_delta is not None and gm_delta <= -1.0:
            gm = _number(latest.get("gross_margin"))
            hist = f"最近一期毛利率约 {gm:.1f}%，较上年同期下降 {abs(gm_delta):.1f} 个百分点。" if gm is not None else "最近一期毛利率较上年下降。"
            add("GROSS_MARGIN", title=_METRIC_TITLES["GROSS_MARGIN"], current=hist,
                positive="毛利率不再继续下滑，并出现连续修复。",
                negative="毛利率继续下降。", origin="FINANCIAL_CORE", importance="HIGH",
                history_note="历史依据：相邻两期年报/同口径毛利率。")
        ocf, profit = _number(latest.get("operating_cash_flow")), _number(latest.get("net_profit"))
        if ocf is not None and profit is not None and profit > 0 and ocf < profit * 0.5:
            add("OCF", title=_METRIC_TITLES["OCF"],
                current=f"最近一期经营现金流相对净利润偏弱（OCF/净利润约 {ocf / profit:.2f}）。",
                positive="经营现金流对净利润的覆盖回升，现金转化不再继续走弱。",
                negative="经营现金流继续明显弱于利润。", origin="FINANCIAL_CORE", importance="HIGH")
        debt_delta = _yoy_change(latest, prev, "debt_ratio")
        interest_delta = _yoy_change(latest, prev, "interest_bearing_debt_ratio")
        if (debt_delta is not None and debt_delta >= 2.0) or (interest_delta is not None and interest_delta >= 2.0):
            add("INTEREST_BEARING_DEBT" if interest_delta and interest_delta >= 2 else "DEBT",
                title=_METRIC_TITLES["INTEREST_BEARING_DEBT"] if interest_delta and interest_delta >= 2 else _METRIC_TITLES["DEBT"],
                current="最近一期杠杆或带息债务较上年抬升。",
                positive="带息债务和资产负债率不再继续抬升。",
                negative="杠杆继续上升。", origin="FINANCIAL_CORE", importance="HIGH")
        recv_delta = _yoy_change(latest, prev, "accounts_receivable")
        rev_delta = _yoy_change(latest, prev, "revenue")
        if recv_delta is not None and rev_delta is not None and recv_delta - rev_delta >= _MATERIAL_YOY:
            add("RECEIVABLE", title=_METRIC_TITLES["RECEIVABLE"],
                current="应收账款增速快于收入。",
                positive="应收账款增速回落到收入增速附近。",
                negative="应收继续快于收入扩张。", origin="FINANCIAL_CORE")

    for change in list(feature.get("latest_changes") or []):
        metric = str(change.get("metric") or "")
        mapped = {"revenue": "REVENUE", "net_profit": "NET_PROFIT", "operating_cash_flow": "OCF",
                  "roe": "ROE", "debt_ratio": "DEBT", "gross_margin": "GROSS_MARGIN"}.get(metric)
        pct = _number(change.get("change_percent"))
        if not mapped or pct is None or abs(pct) < _MATERIAL_YOY:
            continue
        if mapped in selected:
            continue
        worse = (mapped in {"OCF", "NET_PROFIT", "REVENUE", "ROE", "GROSS_MARGIN"} and pct < 0) or (mapped == "DEBT" and pct > 0)
        if not worse:
            continue
        add(mapped, title=_METRIC_TITLES.get(mapped, mapped),
            current=f"最近一期{ _METRIC_TITLES.get(mapped, mapped) }同比变化约 {pct:+.1f}%。",
            positive="该项不再继续朝不利方向变化。",
            negative="该项继续朝不利方向变化。", origin="FINANCIAL_CORE")

    ne = dict(normalized or {})
    if ne.get("status") == "READY":
        ne_ref = source_ref(module="NORMALIZED_EARNINGS", formula_version=_NE_FORMULA_VERSION,
                            research_as_of=str(ne.get("research_as_of") or as_of)[:10])
        q = dict(ne.get("latest_quarter_anchor") or {})
        p50 = _number(ne.get("p50_margin"))
        q_nm = _number(q.get("net_margin"))
        if q_nm is not None and p50 is not None and q_nm + 1.0 < p50:
            add("NET_MARGIN", title=_METRIC_TITLES["NET_MARGIN"],
                current=f"最近一期净利率 {q_nm:.1f}% ，低于历史中位约 {p50:.1f}%。",
                positive="净利率不再继续低于历史中位，并靠近已实现中枢。",
                negative="净利率继续低于历史中位水平。", origin="NORMALIZED_EARNINGS",
                extra_refs=[ne_ref], history_note="历史依据：正常化盈利样本的净利率分位。")
        for caution in list(ne.get("quality_cautions") or []):
            text = str(caution)
            if "现金流" in text or "现金" in text:
                add("OCF", title=_METRIC_TITLES["OCF"], current=text,
                    positive="利润转化为现金的质量不再继续走弱。",
                    negative="现金转化继续偏弱。", origin="NORMALIZED_EARNINGS", extra_refs=[ne_ref])
            elif "资本" in text:
                add("CAPEX", title=_METRIC_TITLES["CAPEX"], current=text,
                    positive="资本开支与经营现金流重新匹配。",
                    negative="资本开支继续明显超过经营现金流。", origin="NORMALIZED_EARNINGS", extra_refs=[ne_ref])

    cyc = dict(cycle or {})
    if cyc.get("status") == "READY":
        cyc_ref = source_ref(module="CYCLE_SCENARIO", formula_version=str(cyc.get("formula_version") or _CYCLE_FORMULA_VERSION),
                             research_as_of=str(cyc.get("research_as_of") or as_of)[:10])
        base = dict(cyc.get("base") or cyc.get("scenarios", {}).get("BASE") or {})
        revenue = base.get("revenue") or cyc.get("base_revenue")
        margin = base.get("margin") or cyc.get("base_margin")
        if revenue or margin:
            add("REVENUE", title="收入与利润率能否接近基准情景区间",
                current="周期利润情景已给出经营验证锚，不是目标价格。",
                positive="收入能否接近基准情景区间" + (f"；利润率能否回到正常化中枢附近（约 {float(margin):.1f}%）" if _number(margin) else ""),
                negative="收入或利润率持续偏离基准情景区间。", origin="CYCLE_SCENARIO", extra_refs=[cyc_ref])
    return items, gaps


def business_items(business: dict[str, Any] | None, *, research_as_of: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    payload = dict(business or {})
    as_of = str(payload.get("data_as_of") or research_as_of or "")[:10] or research_as_of
    claims = [dict(item) for item in list(payload.get("claims") or []) if isinstance(item, dict)]
    unknown_topics = {"ASP", "CUSTOMER", "DOWNSTREAM", "CAPACITY"}
    comparable = False
    for claim in claims:
        claim_type = str(claim.get("type") or "").upper()
        topic = str(claim.get("topic") or "").upper()
        text = str(claim.get("text") or claim.get("statement") or "").strip()
        if claim_type == "UNKNOWN":
            if any(token in topic for token in unknown_topics) or any(token in text for token in ("ASP", "客户身份", "下游", "产能利用率")):
                gaps.append(data_gap(category="BUSINESS", description=text or "经营细节资料不足。",
                                     source_module="BUSINESS", research_as_of=as_of))
            continue
        if claim_type != "FACT" or not text:
            continue
        two_period = any(token in text for token in ("同比", "较上", "两期", "上年", "去年", "上一期", "H1", "年报"))
        if two_period:
            comparable = True
            metric = "SEGMENT_MARGIN" if "毛利" in text else "SEGMENT_REVENUE" if "收入" in text or "营收" in text else (
                "CUSTOMER_CONCENTRATION" if "客户" in text else "PRODUCT_VOLUME" if "销量" in text or "产量" in text else "CAPEX"
            )
            items.append(watchpoint(
                category="BUSINESS", title="下一期核对该经营变化是否延续",
                current_state=text, positive_condition="下一期同口径数据延续已观察到的方向，或给出可解释的变化。",
                negative_condition="下一期出现反向变化且缺少经营解释。",
                source_module="BUSINESS",
                source_refs=[source_ref(module="BUSINESS", formula_version="business-research",
                                        research_as_of=as_of, snapshot_id=payload.get("id"))],
                research_as_of=as_of, importance_tier="NORMAL", canonical_metric=metric,
                origin="BUSINESS", next_review_anchor="NEXT_DISCLOSURE" if "年报" in text else "NEXT_QUARTER",
            ))
        elif "分部" in text or "产品" in text:
            items.append(watchpoint(
                category="BUSINESS", title="下一期继续观察该分部收入/毛利率变化",
                current_state=text, positive_condition="下一期给出可比较的分部或产品方向。",
                negative_condition="仍只有单期事实、无法判断方向。",
                source_module="BUSINESS",
                source_refs=[source_ref(module="BUSINESS", formula_version="business-research", research_as_of=as_of)],
                research_as_of=as_of, importance_tier="LOW", canonical_metric="SEGMENT_REVENUE",
                origin="BUSINESS", next_review_anchor="NEXT_QUARTER",
            ))
    if not comparable and not items:
        pass
    return items[:3], gaps


def valuation_items(reliability: dict[str, Any] | None, *, research_as_of: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    payload = dict(reliability or {})
    status = str(payload.get("status") or "")
    as_of = str(payload.get("as_of") or research_as_of or "")[:10] or research_as_of
    if status not in {"WEAK", "INSUFFICIENT"}:
        return items, []
    reasons = [str(item) for item in list(payload.get("reasons") or []) if str(item).strip()]
    current = "；".join(reasons) or "估值可靠性偏弱。"
    items.append(watchpoint(
        category="VALUATION", title="核验合理价值依据",
        current_state=current,
        positive_condition="更多有效同行/方法支持后，估值可靠性提高。",
        negative_condition="继续依赖少量同行或单一方法。",
        source_module="VALUATION",
        source_refs=[source_ref(module="VALUATION_RELIABILITY", formula_version=str(payload.get("formula_version") or _RELIABILITY_FORMULA_VERSION),
                                research_as_of=as_of)],
        research_as_of=as_of,
        importance_tier="HIGH",
        canonical_metric="VALUATION_RELIABILITY", origin="VALUATION",
        next_review_anchor="CONTINUOUS",
        cautions=["这是估值方法与同行样本核验，不是价格涨跌条件。"],
    ))
    return items, []


def moat_items(moat: dict[str, Any] | None, *, research_as_of: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    payload = dict(moat or {})
    as_of = str(payload.get("research_as_of") or research_as_of or "")[:10] or research_as_of
    for dim in list(payload.get("dimensions") or []):
        status = str(dim.get("status") or "UNKNOWN")
        code = str(dim.get("dimension") or dim.get("code") or "MOAT_DIMENSION")
        label = _DIMENSION_LABELS.get(code, code)
        if status == "UNKNOWN":
            gaps.append(data_gap(category="MOAT", description=f"{label}当前证据不足。",
                                 source_module="MOAT", research_as_of=as_of))
            continue
        if status not in {"SUPPORTED", "PARTIAL", "COUNTER_EVIDENCE"} and str(dim.get("evidence_balance") or "") not in {"COUNTER", "CHALLENGED", "MIXED"}:
            continue
        balance = str(dim.get("evidence_balance") or "")
        counters = bool(dim.get("counter_evidence_ids") or dim.get("counter_evidence_count")) or balance in {"COUNTER", "CHALLENGED", "MIXED"}
        # Only a directly challenged advantage competes with core financial and
        # risk questions.  Mixed evidence or an unconfirmed advantage is a
        # normal continuing verification, not the most important thing to check.
        if balance in {"COUNTER", "CHALLENGED"}:
            tier = "HIGH"
        elif counters or status == "PARTIAL":
            tier = "NORMAL"
        else:
            tier = "LOW"
        items.append(watchpoint(
            category="MOAT",
            title=f"继续验证{label}是否仍能在经营数据中体现",
            current_state=str(dim.get("summary") or dim.get("observation") or f"{label}当前为 {status}。"),
            positive_condition="后续经营数据继续体现该竞争优势。",
            negative_condition="出现持续反证，竞争优势被削弱。",
            source_module="MOAT",
            source_refs=[source_ref(module="MOAT", formula_version=str(payload.get("formula_version") or _MOAT_FORMULA_VERSION),
                                    research_as_of=as_of, dimension=code)],
            research_as_of=as_of,
            importance_tier=tier,
            canonical_metric="MOAT_TECHNOLOGY" if code == "TECHNOLOGY" else "MOAT_DIMENSION",
            origin="MOAT", next_review_anchor="NEXT_ANNUAL_REPORT",
        ))
    return items[:4], gaps[:6]


def capital_items(capital: dict[str, Any] | None, *, research_as_of: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    payload = dict(capital or {})
    as_of = str(payload.get("research_as_of") or payload.get("fact_layer_as_of") or research_as_of or "")[:10] or research_as_of
    dimensions = dict(payload.get("dimensions") or {})
    allowed = {"reinvestment", "debt_management", "equity_dilution", "cash_management", "dividend"}
    unknown_default = {"buyback", "m_and_a"}
    for code, value in dimensions.items():
        row = dict(value or {})
        status = str(row.get("status") or "UNKNOWN")
        direction = str(row.get("direction") or "UNKNOWN")
        observation = str(row.get("observation") or "")
        if code in unknown_default or status == "UNKNOWN":
            if code in unknown_default or code in allowed:
                gaps.append(data_gap(category="CAPITAL", description=observation or f"{code}资料不足。",
                                     source_module="CAPITAL", research_as_of=as_of))
            continue
        if code not in allowed:
            continue
        if direction not in {"CAUTION", "POSITIVE"} and status not in {"SUPPORTED", "PARTIAL"}:
            continue
        if direction == "UNKNOWN" and status == "PARTIAL":
            continue
        items.append(watchpoint(
            category="CAPITAL", title=f"继续观察{code}方向",
            current_state=observation, positive_condition="资本配置相关事实不再朝谨慎方向恶化。",
            negative_condition="债务、再投资或现金管理继续朝谨慎方向变化。",
            source_module="CAPITAL",
            source_refs=[source_ref(module="CAPITAL", formula_version=str(payload.get("formula_version") or _CAPITAL_FORMULA_VERSION),
                                    research_as_of=as_of, dimension=code)],
            research_as_of=as_of, importance_tier="LOW", canonical_metric="DEBT" if "debt" in code else "CAPEX",
            origin="CAPITAL", next_review_anchor="NEXT_ANNUAL_REPORT",
        ))
    return items[:3], gaps[:6]
