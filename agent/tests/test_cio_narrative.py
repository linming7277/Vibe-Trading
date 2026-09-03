"""Boss-facing narrative layer contracts (report quality V2)."""

from __future__ import annotations

import re
from typing import Any

from src.cio_report.narrative import BOSS_SECTIONS, boss_text, render_boss_report, zh

_TRADING = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓|建仓|不碰|试仓")
# Backend tokens that must never appear in the boss narrative.
_BACKEND_TOKENS = re.compile(
    r"\b(GROWTH|STABLE_GROWTH|RECOVERY|CYCLICAL_RECOVERY|DECLINING|FAIR|UNDERVALUED|"
    r"DEEPLY_UNDERVALUED|OVERVALUED|READY|PARTIAL|LIMITED|STRONG|BELOW_AVERAGE|"
    r"NOT_APPLICABLE|NOT_RUN|BEAR|BULL|UNKNOWN|HIGH|MEDIUM|LOW|WATCH|CIO)\b")

_ROWS = [
    {"period_type": "annual", "report_date": "2021-12-31", "revenue": 71.94e8, "net_profit": 15.18e8,
     "gross_margin": 31.90, "net_margin": 21.10, "roe": 32.83, "operating_cash_flow": 9.60e8,
     "accounts_receivable": 17.3e8, "inventory": 19.1e8, "debt_ratio": 48.51, "capex": 22.68e8},
    {"period_type": "annual", "report_date": "2022-12-31", "revenue": 82.82e8, "net_profit": 10.52e8,
     "gross_margin": 30.48, "net_margin": 12.71, "roe": 15.30, "operating_cash_flow": 2.04e8,
     "accounts_receivable": 20.5e8, "inventory": 25.0e8, "debt_ratio": 52.30, "capex": 38.73e8},
    {"period_type": "annual", "report_date": "2023-12-31", "revenue": 93.40e8, "net_profit": -0.36e8,
     "gross_margin": 23.90, "net_margin": -0.38, "roe": -0.47, "operating_cash_flow": 3.17e8,
     "accounts_receivable": 24.0e8, "inventory": 30.0e8, "debt_ratio": 43.87, "capex": 40.0e8},
    {"period_type": "annual", "report_date": "2024-12-31", "revenue": 112.21e8, "net_profit": 2.20e8,
     "gross_margin": 20.00, "net_margin": 1.96, "roe": 1.81, "operating_cash_flow": 4.43e8,
     "accounts_receivable": 28.0e8, "inventory": 34.0e8, "debt_ratio": 44.25, "capex": 16.8e8},
    {"period_type": "annual", "report_date": "2025-12-31", "revenue": 130.52e8, "net_profit": 3.99e8,
     "gross_margin": 20.00, "net_margin": 3.06, "roe": 3.29, "operating_cash_flow": 14.98e8,
     "accounts_receivable": 31.79e8, "inventory": 38.82e8, "debt_ratio": 52.10, "capex": 16.3e8},
]


def _sections() -> list[dict[str, Any]]:
    def sec(section_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"section_type": section_type, "title": section_type, "input_fingerprint": f"fp-{section_type}",
                "freshness_status": "REUSED", "structured_payload": payload, "narrative_md": "", "source_refs": []}

    return [
        sec("company_position", {"stock_name": "士兰微", "level1_name": "电子", "level2_name": "半导体",
                                 "level3_name": "功率半导体", "is_current_l3_leader": False, "main_business": None}),
        sec("financial_path", {"rows": _ROWS}),
        sec("operating_stage", {"stage": "GROWTH"}),
        sec("quality_risk", {"overall_risk": "MEDIUM",
                             "risks": [{"risk_type": "FINANCIAL_DEBT_RATIO", "severity": "MEDIUM",
                                        "text": "资产负债率较上一可比报告期上升约 7.9 个百分点。"},
                                       {"risk_type": "FINANCIAL_INTEREST_DEBT", "severity": "MEDIUM",
                                        "text": "带息债务率较上一可比报告期上升约 8.7 个百分点。"}],
                             "value_trap_risk": "NOT_APPLICABLE",
                             "fact_observations": [{"item": "OCF/归母净利", "value": "3.75", "note": "2025年报"}]}),
        sec("valuation", {"current_price": 34.17, "valuation_status": "FAIR",
                          "fair_value_low": 21.49, "fair_value_mid": 28.18, "fair_value_high": 35.76,
                          "pe": 130.17, "pb": 4.85,
                          "peer_methods": [{"name": "同三级行业 PB 可比", "status": "READY", "peer_count": 15,
                                            "multiple_low": 3.05, "multiple_mid": 4.00, "multiple_high": 5.08}],
                          "plain_summary": "历史估值分位暂缺（序列未物化）"}),
        sec("scenarios", {"status": "LIMITED", "scenarios": {
            "BEAR": {"forecast": [{"year": "2028E", "revenue": 166.34e8, "net_profit": None}]},
            "BASE": {"forecast": [{"year": "2028E", "revenue": 205.95e8, "net_profit": None}]},
            "BULL": {"forecast": [{"year": "2028E", "revenue": 239.46e8, "net_profit": None}]}}}),
        sec("why_research", {"reasons": ["规则风险等级 MEDIUM，无高危项"]}),
        sec("why_caution", {"cautions": ["[MEDIUM] 资产负债率上升约 7.9 个百分点",
                                         "情景引擎受限，净利情景未生成"]}),
        sec("thesis_watchpoints", {"thesis_title": None, "fallback_watchpoints": [
            "经营现金流与净利润的匹配程度（2025年报 OCF 14.98 亿 vs 净利 3.99 亿）",
            "毛利率是否延续修复（2025年报 20.00%）",
            "现价相对合理价值中值 28.18 的偏离是否收敛"]}),
        sec("moat", {"evidence_count": 0, "counter_evidence_count": 0,
                     "dimensions": [{"moat_dimension": "成本优势", "status": "UNKNOWN"}]}),
        sec("capital_allocation", {"dimensions": [{"dimension": "reinvestment", "status": "SUPPORTED",
                                                   "summary": "部分资本开支后年度出现改善迹象"}]}),
        sec("business_structure", {"main_business": None}),
        sec("cio_conclusion", {"verdict": "暂缓优先研究", "focus_tier": None, "valuation_status": "FAIR"}),
    ]


def _render() -> str:
    return render_boss_report(_sections(), stock_code="600460.SH", as_of="2026-08-28")


def test_report_uses_new_boss_section_order_and_titles() -> None:
    md = _render()
    positions = [md.find(f"## {i}. {t}") for i, t in enumerate(BOSS_SECTIONS, 1)]
    assert all(p != -1 for p in positions)
    assert positions == sorted(positions)
    assert "## 6. 当前最核心的经营矛盾" in md and "## 15. 最终研究判断" in md


def test_no_backend_english_tokens_leak_into_boss_narrative() -> None:
    md = _render()
    leaks = _BACKEND_TOKENS.findall(md)
    assert not leaks, leaks
    assert "CIO" not in md


def test_financial_path_narrative_tells_peak_decline_trough_recovery() -> None:
    md = _render()
    assert "高点" in md and "亏损" in md and "恢复" in md
    assert "收入增长但利润未同步恢复" in md
    assert "毛利率" in md and "下行" in md
    # two feishu-friendly tables instead of one 10-column monster
    assert "表1：盈利路径" in md and "表2：现金与资产质量" in md
    assert "应收账款" in md and "资本开支" in md


def test_operating_stage_is_multidimensional() -> None:
    md = _render()
    for dim in ("收入：", "利润：", "毛利率：", "现金流："):
        assert dim in md
    assert "低谷后修复" in md
    assert "综合归纳" in md


def test_core_conflict_paragraph_present_and_grounded() -> None:
    md = _render()
    assert "当前最核心的经营矛盾" in md
    assert "新增收入能否重新转化为足够高的利润和现金回报" in md
    assert "收入持续增长" in md and "盈利能力相比历史高点大幅下降" in md


def test_valuation_answers_the_six_questions() -> None:
    md = _render()
    assert "区间的约" in md and "% 位置" in md  # position in range
    assert "偏离" in md and "%" in md            # distance to mid
    assert "单独用市盈率容易放大" in md            # PE distortion at low-profit cycle
    assert "同三级行业可比公司中位约 4.00" in md    # PB vs peers
    assert "历史估值数据尚未完整物化" in md         # what missing history limits
    assert "最大的前提" in md                      # core premise


def test_risk_logic_summarizes_before_listing() -> None:
    md = _render()
    assert "真正需要关注的是" in md
    assert "重资产扩张与盈利恢复速度之间是否匹配" in md
    assert "已确认风险" in md and "财务观察项" in md and "资料不足项" in md


def test_moat_stays_disciplined_no_free_moat() -> None:
    md = _render()
    assert "不据此认定竞争优势" in md
    assert "规模、排名或知名度本身不构成护城河" in md


def test_scenarios_use_chinese_names_and_limited_note() -> None:
    md = _render()
    assert "谨慎情景" in md and "基准情景" in md and "乐观情景" in md
    assert "当前情景分析受限" in md
    assert "不含任何主观概率" in md


def test_watchpoints_are_prioritized_with_no_thesis_disclaimer() -> None:
    md = _render()
    assert "不代表已经形成正式公司核心逻辑" in md
    assert "最重要验证点" in md and "其次验证点" in md and "长期观察点" in md


def test_final_verdict_has_reasoning_and_up_downgrade_conditions() -> None:
    md = _render()
    assert "暂缓优先研究" in md
    assert "正面变化" in md and "最大风险" in md and "核心矛盾" in md
    assert "升级为重点研究" in md and "下调" in md


def test_no_trading_language() -> None:
    assert not _TRADING.search(_render())


def test_zh_mapping_and_boss_text_helpers() -> None:
    assert zh("GROWTH") == "收入持续增长"
    assert zh("DEEPLY_UNDERVALUED") == "深度低估"
    assert boss_text("处于 BEAR 情景，L3 龙头，状态 UNKNOWN") == "处于 谨慎情景 情景，三级行业 龙头，状态 当前资料不足，暂无法判断"
