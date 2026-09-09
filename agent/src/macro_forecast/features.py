"""确定性特征投影与候选预筛（预测引擎 V1 §10 §11 §18）。

模型上下文只能来自已冻结的输入包 + 本地已留档的快照/事件/K线，全部确定性
计算（0 LLM / 0 网络）。行业证据行（IndustryFeatureRow）与候选预筛规则
（industry-candidate-selection-v1.0.0）都是版本化的确定性产物：同输入必同输出。
"""

from __future__ import annotations

from typing import Any

CANDIDATE_RULES_VERSION = "industry-candidate-selection-v1.1.0"
FEATURE_PROJECTION_VERSION = "industry-feature-projection-v1.0.0"
# v1.3.0: 新增 XCM_USDCNY_MID_*（人民币官方中间价；prompt v2.1）
EVIDENCE_CATALOG_VERSION = "forecast-evidence-catalog-v1.3.0"

# §八：行业进入 strong/weak 候选最少历史（本轮回补目标口径 250 日）。
MIN_INDUSTRY_HISTORY_BARS = 250
# §三：预筛上限（20/12/10/8 基准后 V1 取 12；调用方可覆盖用于基准测试）。
MAX_CANDIDATES_PER_SIDE = 12
# §十三：同上级行业组（macro_sector_v2 INDUSTRY_TO_GROUP 既有映射）候选上限。
MAX_PER_GROUP_PER_SIDE = 4

BENCHMARK = "000300.SH"

ELIGIBLE = "ELIGIBLE"
EXCLUDED_INSUFFICIENT_HISTORY = "EXCLUDED_INSUFFICIENT_HISTORY"
EXCLUDED_NO_P_BAR = "EXCLUDED_NO_P_BAR"


def _day(value: object) -> str:
    return str(value or "").replace("-", "")[:8]


def _returns_from_closes(closes: list[float], windows: tuple[int, ...]) -> dict[int, float | None]:
    result: dict[int, float | None] = {}
    for window in windows:
        if len(closes) > window and closes[-(window + 1)] > 0:
            result[window] = round(closes[-1] / closes[-(window + 1)] - 1, 12)
        else:
            result[window] = None
    return result


def _closes_up_to(bars: list[dict[str, Any]], previous_date: str) -> list[float]:
    p = _day(previous_date)
    ordered = sorted(
        (row for row in bars
         if row.get("close") is not None and float(row["close"]) > 0
         and str(row.get("trade_date") or "").replace("-", "")[:8] <= p),
        key=lambda row: str(row.get("trade_date") or ""),
    )
    return [float(row["close"]) for row in ordered]


def _vol_20d(closes: list[float]) -> float | None:
    if len(closes) < 21:
        return None
    returns = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))
               if closes[i - 1] > 0]
    if len(returns) < 20:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return round(variance ** 0.5, 12)


def _trend_consistency(relative: dict[int, float | None]) -> int | None:
    """相对收益 1/5/20 日同号为 +1/-1；分裂为 0；缺值为 None。"""
    values = [relative.get(window) for window in (1, 5, 20)]
    if any(value is None for value in values):
        return None
    signs = {1 if value > 0 else (-1 if value < 0 else 0) for value in values}  # type: ignore[operator]
    if signs == {1}:
        return 1
    if signs == {-1}:
        return -1
    return 0


def build_industry_features(
    *,
    bars_map: dict[str, list[dict[str, Any]]],
    industry_rows: list[dict[str, Any]],
    previous_date: str,
    macro_axes: dict[str, float | None] | None = None,
) -> list[dict[str, Any]]:
    """为全部 128 行业构造特征行；不足 250 日 → EXCLUDED_INSUFFICIENT_HISTORY。"""
    benchmark_closes = _closes_up_to(bars_map.get(BENCHMARK) or [], previous_date)
    benchmark_returns = _returns_from_closes(benchmark_closes, (1, 5, 20))
    # 参与排名的行业：P 日有 K 线且历史达标。
    rows: list[dict[str, Any]] = []
    for entry in industry_rows:
        code = str(entry.get("code") or "")
        name = str(entry.get("name_zh") or "")
        closes = _closes_up_to(bars_map.get(code) or [], previous_date)
        # P 日 bar 校验：恰有 P 日有效收盘
        has_p_bar = any(
            _day(row.get("trade_date")) == _day(previous_date) and row.get("close")
            for row in (bars_map.get(code) or [])
        )
        ret = _returns_from_closes(closes, (1, 5, 20))
        relative = {
            window: (round(ret[window] - benchmark_returns[window], 12)
                     if ret[window] is not None and benchmark_returns[window] is not None else None)
            for window in (1, 5, 20)
        }
        if len(closes) < MIN_INDUSTRY_HISTORY_BARS:
            history_status, bars_count = EXCLUDED_INSUFFICIENT_HISTORY, len(closes)
        elif not has_p_bar:
            history_status, bars_count = EXCLUDED_NO_P_BAR, len(closes)
        else:
            history_status, bars_count = ELIGIBLE, len(closes)
        row = {
            "industry_id": f"tdx:{code}", "code": code, "name": name,
            "bars_count": bars_count, "history_status": history_status,
            "ret_1d": ret[1], "ret_5d": ret[5], "ret_20d": ret[20],
            "relative_1d": relative[1], "relative_5d": relative[5], "relative_20d": relative[20],
            "vol_20d": _vol_20d(closes),
            "trend_consistency": _trend_consistency(relative),
            "rank_1d": None, "rank_5d": None, "rank_20d": None,
            "macro_stance": None, "macro_explanation": None,
        }
        if macro_axes is not None and name:
            try:
                from src.strategy_engines.value.macro_sector_v2 import describe

                info = describe(name, dict(macro_axes))
                row["macro_stance"] = info.get("stance")
                row["macro_explanation"] = (
                    f"宏观暴露矩阵（{info.get('matrix_version')}，专家假设）："
                    f"组={info.get('group_name')}，stance={info.get('stance')}"
                )
            except Exception:
                row["macro_stance"] = None
        rows.append(row)
    # 相对收益排名（仅 ELIGIBLE 行参与；1 = 最强）。缺值行业该窗口不排名。
    for window, key in ((1, "rank_1d"), (5, "rank_5d"), (20, "rank_20d")):
        pool = [row for row in rows if row["history_status"] == ELIGIBLE and row[f"relative_{window}d"] is not None]
        pool.sort(key=lambda row: (-row[f"relative_{window}d"], row["industry_id"]))
        for rank, row in enumerate(pool, 1):
            row[key] = rank
    return rows


def select_candidates(
    features: list[dict[str, Any]], *, limit: int = MAX_CANDIDATES_PER_SIDE,
    max_per_group: int = MAX_PER_GROUP_PER_SIDE,
) -> dict[str, list[dict[str, Any]]]:
    """确定性预筛：相对强弱 + 趋势一致性 + 数据完整性（规则版本化，不发明总分）。

    规则（v1.1.0，在 v1.0.0 基础上收紧）：
    - 只取 history_status=ELIGIBLE 且 relative_1d/5d/20d 与 trend_consistency 齐全；
    - strong 候选按 (relative_5d 降序, trend_consistency 降序, relative_1d 降序,
      industry_id 升序) 取前 N；weak 候选按反向序取前 N；
    - §十三 diversity cap：同上级行业组（既有 INDUSTRY_TO_GROUP 映射）每侧最多
      max_per_group 个；无映射的行业不受组内上限约束（不构成同群证据）；
    - 候选不是预测，最终由模型联合判断。
    """
    from src.strategy_engines.value.macro_sector_v2 import INDUSTRY_TO_GROUP

    eligible = [
        row for row in features
        if row["history_status"] == ELIGIBLE
        and all(row[key] is not None for key in ("relative_1d", "relative_5d", "relative_20d"))
        and row["trend_consistency"] is not None
    ]
    strong_order = sorted(
        eligible,
        key=lambda row: (-row["relative_5d"], -row["trend_consistency"], -row["relative_1d"], row["industry_id"]),
    )
    weak_order = sorted(
        eligible,
        key=lambda row: (row["relative_5d"], row["trend_consistency"], row["relative_1d"], row["industry_id"]),
    )

    def take(order: list[dict[str, Any]], exclude: set[str] | None = None) -> list[dict[str, Any]]:
        picked: list[dict[str, Any]] = []
        group_counts: dict[str, int] = {}
        for row in order:
            if len(picked) >= limit:
                break
            if exclude and row["industry_id"] in exclude:
                continue  # 已入对侧池的行业不再重复占位（生产两侧本不相交）
            group = INDUSTRY_TO_GROUP.get(str(row.get("name") or ""))
            if group is not None:
                # §十三 diversity cap 只对可证实的同组行业生效；
                # 无映射行业不构成"同群"证据，不受组内上限约束。
                if group_counts.get(group, 0) >= max_per_group:
                    continue
                group_counts[group] = group_counts.get(group, 0) + 1
            picked.append(row)
        return picked

    strong = take(strong_order)
    weak = take(weak_order, exclude={row["industry_id"] for row in strong})
    if not weak and weak_order:
        # 可选池太小（eligible < 2*limit）时两侧必然重叠：保底取弱侧排序，
        # 语义与旧行为一致（强/弱不交叉是足够大池时的性质，不是小池约束）。
        weak = take(weak_order)
    return {
        "candidate_rules_version": CANDIDATE_RULES_VERSION,
        "strong": strong,
        "weak": weak,
        "eligible_count": len(eligible),
    }


def build_evidence_catalog(
    *,
    market: dict[str, Any],
    macro_context: dict[str, Any],
    candidates: dict[str, Any],
    benchmark_ret_20d: float | None = None,
    reference_5d: dict[str, float | None] | None = None,
    breadth_20d: float | None = None,
    risk_appetite: float | None = None,
    xcm_summary: dict[str, Any] | None = None,
) -> dict[str, str]:
    """合法 evidence key 的完整目录（§18：模型不能自由写 source）。

    v1.2：接入真实 MARKET_BREADTH / RISK_APPETITE / INDUSTRY_TREND 键，
    使宽度类措辞有据可引（语义守则 §四：不得用背景指数冒充宽度）。
    v1.1：目录不进 prompt（模型只见短键别名映射）；本目录用于服务端
    别名解析后的校验与回查。REF 键使用去后缀代码（如 999999）。
    """
    catalog: dict[str, str] = {}
    benchmark = market.get("benchmark") or {}
    catalog["MKT_BENCH_RET_1D"] = "沪深300 1日收益（P-1→P 收盘）"
    catalog["MKT_BENCH_RET_5D"] = "沪深300 5日收益"
    if benchmark_ret_20d is not None:
        catalog["MKT_BENCH_RET_20D"] = "沪深300 20日收益"
    catalog["MKT_BENCH_VOL_20D"] = "沪深300 20日日收益波动（未年化）"
    if benchmark.get("amount_ratio_vs_20d_mean") is not None:
        catalog["MKT_BENCH_AMOUNT_RATIO_20D"] = "沪深300 成交额相对20日均值"
    if breadth_20d is not None:
        catalog["MKT_BREADTH_20D"] = "A股20日市场宽度（站上均线占比）"
    if risk_appetite is not None:
        catalog["MKT_RISK_APPETITE"] = "中证全指风险偏好指标"
    for code, value in sorted((reference_5d or {}).items()):
        if value is not None:
            catalog[f"MKT_REF_{code}_RET_5D"] = f"{code} 5日收益（背景指数）"
    for code, features in sorted((market.get("reference") or {}).items()):
        if features.get("status") == "READY" and features.get("ret_5d") is not None:
            stripped = str(code).replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
            catalog[f"MKT_REF_{stripped}_RET_5D"] = f"{code} 5日收益（背景指数）"
    catalog["MACRO_REGIME_LATEST"] = "宏观状态（五轴引擎，最近 P 日可见快照）"
    for axis in sorted((macro_context.get("axes") or {})):
        catalog[f"MACRO_AXIS_{axis.upper()}"] = f"宏观轴：{axis}"
    catalog["MACRO_COVERAGE_GROUPS"] = "宏观方向输入组覆盖"
    for event in macro_context.get("events") or []:
        key = f"MACRO_EVENT_{_day(event.get('research_as_of'))}_{str(event.get('event_type')).replace('MACRO_', '')}"
        if event.get("axis_key"):
            key += f"_{str(event['axis_key']).upper()}"
        catalog[key] = f"宏观事件 {event.get('event_type')}"
    for row in (*candidates["strong"], *candidates["weak"]):
        iid = row["industry_id"].replace("tdx:", "").replace(".", "_")
        # §50 输入压缩 + 语义守则 v1.2：每行业登记 REL/RET/TC/STANCE 键；
        # 完整特征值已随候选行进入 prompt，键目录不再展开全部字段。
        for field, label in (
            ("ret_5d", "5日收益"), ("relative_5d", "5日相对沪深300"),
            ("vol_20d", "20日波动"), ("tc", "趋势一致性（1/0/-1）"),
        ):
            value = row.get("trend_consistency") if field == "tc" else row.get(field)
            if value is not None:
                catalog[f"IND_{iid}_{field.upper()}"] = f"{row['name']} {label}"
        if row.get("macro_stance"):
            catalog[f"IND_{iid}_MACRO_STANCE"] = f"{row['name']} 宏观暴露立场（专家假设）"
    if xcm_summary is not None:
        # v1.3.0：人民币官方中间价（审计 V1 唯一 USE_NOW 项；非市场收盘价/非 CNH）
        catalog["XCM_USDCNY_MID_LVL"] = "人民币对美元官方中间价水平（当日09:15官方公布，非市场收盘价）"
        if xcm_summary.get("d1_pct") is not None:
            catalog["XCM_USDCNY_MID_1D"] = "官方中间价较前一中间价变动（USD/CNY数值上升=人民币中间价偏弱）"
        if xcm_summary.get("d5_pct") is not None:
            catalog["XCM_USDCNY_MID_5D"] = "官方中间价较5日前中间价变动"
    return catalog
