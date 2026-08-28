"""Read-only factual quality profiles for saved L3 leader snapshots.

The service intentionally does *not* recalculate Leader Score, create a moat
score, call an LLM, or write to research/Thesis/Risk tables.  It translates
already persisted L3 rows and point-in-time professional-finance history into
transparent peer facts and explicitly reports what cannot be inferred.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import median, pstdev
from typing import Any, Callable

from src.level3_leaders.store import Level3LeaderStore
from src.research_workspace.store import normalize_market, normalize_symbol
from src.strategy_engines.common.normalization import cross_sectional_percentiles
from src.strategy_engines.value_line import ValueLineService


FORMULA_VERSION = "leader-quality-profile-v1.0.0"
MIN_RELIABLE_PEERS = 5
MIN_COMPARABLE_PEERS = 3

METRICS: tuple[dict[str, Any], ...] = (
    {"key": "revenue", "label": "营收规模", "dimension": "SCALE", "unit": "元", "higher": True, "source": "leader"},
    {"key": "net_profit", "label": "净利润规模", "dimension": "SCALE", "unit": "元", "higher": True, "source": "leader"},
    {"key": "roe", "label": "ROE", "dimension": "PROFITABILITY", "unit": "%", "higher": True, "source": "leader"},
    {"key": "gross_margin", "label": "毛利率", "dimension": "PROFITABILITY", "unit": "%", "higher": True, "source": "leader"},
    {"key": "net_margin", "label": "净利率", "dimension": "PROFITABILITY", "unit": "%", "higher": True, "source": "leader"},
    {"key": "revenue_cagr", "label": "营收增长", "dimension": "GROWTH", "unit": "%", "higher": True, "source": "leader"},
    {"key": "profit_cagr", "label": "利润增长", "dimension": "GROWTH", "unit": "%", "higher": True, "source": "leader"},
    {"key": "cash_conversion", "label": "现金转换", "dimension": "CASH_QUALITY", "unit": "%", "higher": True, "source": "leader"},
    {"key": "ocf_margin", "label": "经营现金流率", "dimension": "CASH_QUALITY", "unit": "%", "higher": True, "source": "leader"},
    {"key": "debt_ratio", "label": "资产负债率", "dimension": "FINANCIAL_STRENGTH", "unit": "%", "higher": False, "source": "leader"},
    {"key": "capex_to_revenue", "label": "资本开支/营收", "dimension": "CAPITAL_INTENSITY", "unit": "%", "higher": None, "source": "finance"},
)

_CATEGORY_LABELS = {
    "SCALE": "规模", "PROFITABILITY": "盈利", "GROWTH": "成长",
    "CASH_QUALITY": "现金质量", "FINANCIAL_STRENGTH": "财务稳健",
    "CAPITAL_INTENSITY": "资本投入强度",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _status(percentile: float | None, valid_peer_count: int) -> str:
    if percentile is None or valid_peer_count < MIN_COMPARABLE_PEERS:
        return "UNKNOWN"
    if percentile >= 75:
        return "STRONG"
    if percentile >= 60:
        return "ABOVE_AVERAGE"
    if percentile >= 40:
        return "NORMAL"
    return "BELOW_AVERAGE"


def _sample_quality(valid_peer_count: int, total_peer_count: int) -> str:
    if valid_peer_count < MIN_COMPARABLE_PEERS:
        return "INSUFFICIENT_PEER_SAMPLE"
    if valid_peer_count < MIN_RELIABLE_PEERS:
        return "SMALL_PEER_SAMPLE"
    if valid_peer_count < total_peer_count:
        return "PARTIAL"
    return "READY"


def _annual_rows(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    annual = [
        row for row in rows
        if str(row.get("symbol") or "").upper() == symbol
        and row.get("period_type") == "annual"
    ]
    annual.sort(key=lambda row: (str(row.get("report_date") or ""), str(row.get("announcement_date") or "")))
    return annual


class LeaderQualityProfileService:
    """Build a deterministic profile from persisted facts only."""

    def __init__(
        self,
        *,
        leader_store: Level3LeaderStore | None = None,
        financial_loader: Callable[[str], dict[str, list[dict[str, Any]]]] | None = None,
    ) -> None:
        self.leader_store = leader_store or Level3LeaderStore()
        self._owns_store = leader_store is None
        self._value_line: ValueLineService | None = None
        if financial_loader is None:
            self._value_line = ValueLineService()
            financial_loader = self._value_line._load_financials
        self.financial_loader = financial_loader

    def close(self) -> None:
        if self._value_line is not None:
            self._value_line.close()
        if self._owns_store:
            self.leader_store.close()

    @staticmethod
    def _row_value(row: dict[str, Any], key: str, finance: dict[str, list[dict[str, Any]]]) -> float | None:
        raw = row.get("raw_features") if isinstance(row.get("raw_features"), dict) else {}
        if key == "debt_ratio":
            raw_value = _number(raw.get("debt_safety"))
            return -raw_value if raw_value is not None else None
        if key == "capex_to_revenue":
            annual = _annual_rows(finance.get(str(row.get("stock_code") or "").upper(), []), str(row.get("stock_code") or "").upper())
            latest = annual[-1] if annual else {}
            capex, revenue = _number(latest.get("capex")), _number(latest.get("revenue"))
            return capex / revenue * 100 if capex is not None and revenue not in {None, 0} else None
        return _number(raw.get(key))

    @staticmethod
    def _financial_trace(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
        annual = _annual_rows(rows, symbol)
        latest = annual[-1] if annual else None
        return {
            "source": "TongDaXin professional finance",
            "symbol": symbol,
            "report_date": latest.get("report_date") if latest else None,
            "announcement_date": latest.get("announcement_date") if latest else None,
            "record_key": (
                f"financial_history:{symbol}:{latest.get('report_date')}:{latest.get('announcement_date')}"
                if latest else None
            ),
            "annual_record_count": len(annual),
        }

    @staticmethod
    def _metric_percentiles(rows: list[dict[str, Any]], finance: dict[str, list[dict[str, Any]]], key: str, higher: bool | None) -> dict[str, float | None]:
        if higher is None:
            return {str(row.get("stock_code")): None for row in rows}
        values = [{key: LeaderQualityProfileService._row_value(row, key, finance)} for row in rows]
        normalized = cross_sectional_percentiles(values, {key: higher})
        return {str(row.get("stock_code")): _number(normalized[index].get(key)) for index, row in enumerate(rows)}

    @staticmethod
    def _metric_item(
        metric: dict[str, Any], company: dict[str, Any], peers: list[dict[str, Any]], finance: dict[str, list[dict[str, Any]]],
        trace: dict[str, Any], run: dict[str, Any],
    ) -> dict[str, Any]:
        key, symbol = str(metric["key"]), str(company["stock_code"])
        company_value = LeaderQualityProfileService._row_value(company, key, finance)
        values = [(str(row.get("stock_code")), LeaderQualityProfileService._row_value(row, key, finance)) for row in peers]
        finite = [value for _, value in values if value is not None]
        valid_peer_count = len(finite)
        total_peer_count = len(peers)
        percentile_map = LeaderQualityProfileService._metric_percentiles(peers, finance, key, metric["higher"])
        # Capital intensity has no universal "better" direction.  Its relative
        # amount is shown, but never promoted into a quality strength/weakness.
        percentile = percentile_map.get(symbol)
        relation = "NOT_SCORED"
        if metric["higher"] is not None and percentile is not None:
            relation = _status(percentile, valid_peer_count)
        return {
            "dimension": metric["dimension"],
            "dimension_label": _CATEGORY_LABELS[metric["dimension"]],
            "metric": key,
            "label": metric["label"],
            "unit": metric["unit"],
            "company_value": company_value,
            "peer_median": median(finite) if finite else None,
            "peer_percentile": percentile,
            "valid_peer_count": valid_peer_count,
            "total_peer_count": total_peer_count,
            "status": relation,
            "comparison_direction": (
                "higher_is_better" if metric["higher"] is True else "lower_is_better" if metric["higher"] is False
                else "higher_means_more_investment_not_better"
            ),
            "data_quality": _sample_quality(valid_peer_count, total_peer_count),
            "reporting_period": trace["report_date"],
            "announcement_cutoff": trace["announcement_date"],
            "source_refs": [
                {"source": "value_level3_leaders", "run_id": run["id"], "as_of": run["as_of"], "stock_code": symbol},
                trace,
            ],
        }

    @staticmethod
    def _category_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[str(item["dimension"])].append(item)
        result = []
        for key, members in grouped.items():
            meaningful = [float(item["peer_percentile"]) for item in members if item.get("peer_percentile") is not None]
            valid = min((int(item["valid_peer_count"]) for item in members if item.get("peer_percentile") is not None), default=0)
            status = _status(median(meaningful) if meaningful else None, valid)
            if key == "CAPITAL_INTENSITY":
                status = "UNKNOWN"
            result.append({
                "dimension": key, "label": _CATEGORY_LABELS[key], "status": status,
                "metrics": [item["metric"] for item in members],
                "valid_metric_count": len(meaningful), "total_metric_count": len(members),
            })
        return result

    @staticmethod
    def _profitability_quality(
        company: dict[str, Any], peer_items: list[dict[str, Any]], finance: dict[str, list[dict[str, Any]]], trace: dict[str, Any],
    ) -> dict[str, Any]:
        symbol = str(company["stock_code"])
        annual = _annual_rows(finance.get(symbol, []), symbol)
        history = {
            key: [{"report_date": row.get("report_date"), "value": _number(row.get(key))} for row in annual if _number(row.get(key)) is not None]
            for key in ("roe", "gross_margin", "net_margin", "operating_cash_flow", "net_profit", "revenue")
        }
        cash_conversion = [
            row["value"] / profit["value"] * 100
            for row, profit in zip(history["operating_cash_flow"], history["net_profit"])
            if row["report_date"] == profit["report_date"] and profit["value"] not in {0, None}
        ]
        metrics = {item["metric"]: item for item in peer_items}
        profitability = [metrics[key] for key in ("roe", "gross_margin", "net_margin") if key in metrics]
        cash = [metrics[key] for key in ("cash_conversion", "ocf_margin") if key in metrics]
        positive_ocf_years = sum(1 for row in annual if (_number(row.get("operating_cash_flow")) or 0) > 0)
        return {
            "status": _status(
                median([float(item["peer_percentile"]) for item in profitability if item.get("peer_percentile") is not None]) if profitability else None,
                min((int(item["valid_peer_count"]) for item in profitability if item.get("peer_percentile") is not None), default=0),
            ),
            "cash_quality_status": _status(
                median([float(item["peer_percentile"]) for item in cash if item.get("peer_percentile") is not None]) if cash else None,
                min((int(item["valid_peer_count"]) for item in cash if item.get("peer_percentile") is not None), default=0),
            ),
            "history": history,
            "cash_conversion_history": cash_conversion,
            "positive_ocf_years": positive_ocf_years,
            "annual_observation_count": len(annual),
            "reporting_period": trace["report_date"],
            "announcement_cutoff": trace["announcement_date"],
            "disclaimer": "使用 ROE、利润率与经营现金流的会计口径事实；当前没有可运行 ROIC，不能据此称为资本回报优秀。",
        }

    @staticmethod
    def _pricing_power_proxy(company: dict[str, Any], peers: list[dict[str, Any]], finance: dict[str, list[dict[str, Any]]], peer_items: list[dict[str, Any]]) -> dict[str, Any]:
        symbol = str(company["stock_code"])
        annual = _annual_rows(finance.get(symbol, []), symbol)
        gross_history = [_number(row.get("gross_margin")) for row in annual if _number(row.get("gross_margin")) is not None]
        net_history = [_number(row.get("net_margin")) for row in annual if _number(row.get("net_margin")) is not None]
        item_by_metric = {str(item["metric"]): item for item in peer_items}
        gross = item_by_metric.get("gross_margin")
        net = item_by_metric.get("net_margin")
        stable = len(gross_history) >= 3 and pstdev(gross_history) <= 0.75 * max(1.0, abs(median(gross_history)))
        percentiles = [
            float(item["peer_percentile"]) for item in (gross, net)
            if item and item.get("peer_percentile") is not None
        ]
        peer_count = min((int(item["valid_peer_count"]) for item in (gross, net) if item and item.get("peer_percentile") is not None), default=0)
        relative = median(percentiles) if percentiles else None
        if relative is None or peer_count < MIN_COMPARABLE_PEERS or len(gross_history) < 3:
            status = "UNKNOWN"
        elif relative >= 75 and stable:
            status = "STRONG_PROXY"
        elif relative >= 50:
            status = "MODERATE_PROXY"
        else:
            status = "WEAK_PROXY"
        return {
            "status": status,
            "gross_margin_history": gross_history,
            "net_margin_history": net_history,
            "gross_margin_stable": stable if len(gross_history) >= 3 else None,
            "peer_margin_percentile": relative,
            "valid_peer_count": peer_count,
            "comparison_scope": "当前 L3 行业内真实可评分同行分布",
            "disclaimer": "这是利润率表现代理，不等于确认公司拥有定价权。",
        }

    def _stability(self, symbol: str, position: dict[str, Any], as_of: str) -> dict[str, Any]:
        runs = self.leader_store.completed_runs(through_as_of=as_of)
        level3_code = str(position.get("level3_code") or "")
        path: list[dict[str, Any]] = []
        prior_present = False
        entered = left = reentered = 0
        for run in runs:
            row = next((candidate for candidate in self.leader_store.industry_rows(run["id"], level3_code)
                        if str(candidate.get("stock_code")) == symbol), None)
            rank = row.get("leader_rank") if row and row.get("eligibility_status") == "eligible" else None
            present = rank is not None
            if present and not prior_present:
                if any(item.get("leader_rank") is not None for item in path):
                    reentered += 1
                else:
                    entered += 1
            if prior_present and not present:
                left += 1
            prior_present = present
            path.append({"run_id": run["id"], "as_of": run["as_of"], "leader_rank": rank,
                         "leader_score": row.get("leader_score") if row else None,
                         "coverage": row.get("coverage") if row else None})
        top1 = sum(item.get("leader_rank") == 1 for item in path)
        top2 = sum((item.get("leader_rank") or 999) <= 2 for item in path)
        observed = sum(item.get("leader_rank") is not None for item in path)
        if len(path) < 3 or observed < 3:
            status = "INSUFFICIENT_HISTORY"
        elif observed == len(path) and top2 == len(path):
            status = "SHORT_WINDOW_STABLE"
        elif top2 / len(path) >= .6:
            status = "SHORT_WINDOW_MIXED"
        else:
            status = "SHORT_WINDOW_VOLATILE"
        return {
            "status": status,
            "observation_start": path[0]["as_of"] if path else None,
            "observation_end": path[-1]["as_of"] if path else None,
            "observation_window": f"{path[0]['as_of']} 至 {path[-1]['as_of']}" if path else None,
            "run_count": len(path), "observed_rank_count": observed,
            "top1_count": top1, "top2_count": top2, "rank_path": path,
            "score_path": [{"as_of": item["as_of"], "leader_score": item["leader_score"]} for item in path],
            "entered_count": entered, "left_count": left, "reentered_count": reentered,
            "disclaimer": "仅基于当前已保存的短期 L3 运行窗口，不代表长期龙头稳定性。",
        }

    @staticmethod
    def _moat_gaps(level3_name: str) -> list[str]:
        gaps = ["无市场份额数据", "无品牌强度数据", "无客户留存数据", "无专利质量数据", "无渠道覆盖数据", "无单位成本数据"]
        if any(token in level3_name for token in ("餐饮", "酒店", "零售")):
            gaps.extend(["无门店同店数据", "无单店经营数据", "无会员复购数据"])
        elif any(token in level3_name for token in ("半导体", "设备", "芯片")):
            gaps.extend(["无客户认证数据", "无设备性能/良率数据", "无安装基数数据"])
        else:
            gaps.extend(["无产能利用率数据", "无产品价格与销量拆分"])
        return gaps

    def get_profile(self, market: str, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        if normalized_market != "CN":
            raise ValueError("Leader Quality Profile V1 当前仅支持 A 股（CN）")
        symbol = normalize_symbol(normalized_market, stock_code)
        requested_as_of = str(as_of)[:10] if as_of else None
        if requested_as_of:
            date.fromisoformat(requested_as_of)
        run = self.leader_store.latest_run(requested_as_of)
        if run is None:
            return {
                "company": {"market": normalized_market, "stock_code": symbol, "stock_name": symbol},
                "research_as_of": requested_as_of, "leader_position": {"status": "RUN_NOT_AVAILABLE"},
                "leader_stability": {"status": "INSUFFICIENT_HISTORY", "rank_path": []},
                "peer_advantages": [], "profitability_quality": {"status": "UNKNOWN"},
                "pricing_power_proxy": {"status": "UNKNOWN"}, "strengths": [], "weaknesses": [],
                "moat_data_gaps": self._moat_gaps(""),
                "data_quality": {"status": "RUN_NOT_AVAILABLE", "missing_fields": ["L3 run"]},
                "formula_version": FORMULA_VERSION,
            }
        all_rows = self.leader_store.all_rows(run["id"])
        matches = [row for row in all_rows if str(row.get("stock_code")).upper() == symbol]
        if not matches:
            return {
                "company": {"market": normalized_market, "stock_code": symbol, "stock_name": symbol},
                "research_as_of": run["as_of"], "leader_position": {"status": "NOT_IN_CURRENT_L3_RUN", "run_id": run["id"]},
                "leader_stability": {"status": "INSUFFICIENT_HISTORY", "rank_path": []},
                "peer_advantages": [], "profitability_quality": {"status": "UNKNOWN"},
                "pricing_power_proxy": {"status": "UNKNOWN"}, "strengths": [], "weaknesses": [],
                "moat_data_gaps": self._moat_gaps(""),
                "data_quality": {"status": "NOT_IN_CURRENT_L3_RUN", "missing_fields": ["L3 industry membership"]},
                "formula_version": FORMULA_VERSION,
            }
        matches.sort(key=lambda row: (row.get("leader_rank") is None, row.get("leader_rank") or 999, str(row.get("level3_code"))))
        company = matches[0]
        peers = [row for row in self.leader_store.industry_rows(run["id"], str(company["level3_code"])) if row.get("eligibility_status") == "eligible" and row.get("leader_rank") is not None]
        finance = self.financial_loader(run["as_of"])
        trace = self._financial_trace(finance.get(symbol, []), symbol)
        peer_items = [self._metric_item(metric, company, peers, finance, trace, run) for metric in METRICS]
        categories = self._category_items(peer_items)
        next_peer = next((row for row in peers if row.get("leader_rank") == (company.get("leader_rank") or 0) + 1), None)
        gap = None
        if next_peer and _number(company.get("leader_score")) is not None and _number(next_peer.get("leader_score")) is not None:
            gap = round(float(company["leader_score"]) - float(next_peer["leader_score"]), 4)
        position = {
            "status": "READY" if company.get("leader_rank") is not None else "INELIGIBLE",
            "level1": {"code": company["level1_code"], "name": company["level1_name"]},
            "level2": {"code": company["level2_code"], "name": company["level2_name"]},
            "level3": {"code": company["level3_code"], "name": company["level3_name"]},
            "industry_code": company["level3_code"], "rank": company.get("leader_rank"),
            "leader_score": company.get("leader_score"), "valid_peer_count": len(peers),
            "total_peer_count": len(self.leader_store.industry_rows(run["id"], str(company["level3_code"]))),
            "score_coverage": company.get("coverage"), "score_components": company.get("component_scores") or {},
            "formula_version": company.get("leader_formula_version"), "run_id": run["id"], "as_of": run["as_of"],
            "gap_to_next": gap,
            "next_company": None if not next_peer else {"stock_code": next_peer["stock_code"], "stock_name": next_peer["stock_name"], "rank": next_peer["leader_rank"], "leader_score": next_peer["leader_score"]},
            "plain_explanation": f"当前在{company['level3_name']} L3 行业的可评分公司中排名第{company.get('leader_rank') or '—'}。这不是市场份额或现实行业第一。",
        }
        strengths = [item for item in categories if item["status"] in {"STRONG", "ABOVE_AVERAGE"}]
        weaknesses = [item for item in categories if item["status"] == "BELOW_AVERAGE"]
        peer_count = len(peers)
        return {
            "company": {"market": normalized_market, "stock_code": symbol, "stock_name": company.get("stock_name") or symbol},
            "research_as_of": run["as_of"], "leader_position": position,
            "leader_stability": self._stability(symbol, company, run["as_of"]),
            "peer_advantages": peer_items, "peer_advantage_categories": categories,
            "profitability_quality": self._profitability_quality(company, peer_items, finance, trace),
            "pricing_power_proxy": self._pricing_power_proxy(company, peers, finance, peer_items),
            "strengths": strengths, "weaknesses": weaknesses,
            "moat_data_gaps": self._moat_gaps(str(company["level3_name"])),
            "data_quality": {
                "status": "READY" if peer_count >= MIN_RELIABLE_PEERS else "PARTIAL",
                "peer_sample": _sample_quality(peer_count, len(self.leader_store.industry_rows(run["id"], str(company["level3_code"])))),
                "small_peer_sample": peer_count < MIN_RELIABLE_PEERS,
                "pit_financial_cutoff": trace["announcement_date"],
                "catalog_as_of": run.get("catalog_as_of"),
                "missing_fields": [item["metric"] for item in peer_items if item["company_value"] is None],
                "disclaimer": "财务按公告日截止；行业成员与部分行情/估值来自保存的 L3 运行快照。画像不确认任何护城河。",
            },
            "source_traceability": {"l3_run_id": run["id"], "l3_run_as_of": run["as_of"], "financial": trace},
            "formula_version": FORMULA_VERSION,
        }


_service: LeaderQualityProfileService | None = None


def get_leader_quality_profile_service() -> LeaderQualityProfileService:
    global _service
    if _service is None:
        _service = LeaderQualityProfileService()
    return _service
