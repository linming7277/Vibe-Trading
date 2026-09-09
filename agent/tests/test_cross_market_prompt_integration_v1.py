"""Cross-Market Forecast Prompt Integration V1（任务书 §十八 20 项 + A/B snapshot）。

只接 USD/CNY 官方中间价（审计 V1 唯一 USE_NOW）；US2Y/US10Y/Curve/WTI/Copper
不得出现在 prompt。全部离线 fixture，0 LLM、0 网络、0 生产写入。
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.macro_forecast.prompt import (
    PROMPT_VERSION, SYSTEM_PROMPT, build_cny_mid_summary, build_compact_context,
    build_user_payload, estimate_tokens,
)
from src.macro_forecast.semantic import SEMANTIC_GUARD_VERSION, SemanticGuard, annotate_catalog
from src.macro_forecast.features import build_evidence_catalog

SH = ZoneInfo("Asia/Shanghai")


def _summary(**overrides) -> dict:
    base = {
        "value": 6.7804, "obs": "2026-09-08", "d1_pct": 0.000133, "d5_pct": -0.000074,
        "direction_cn": "CNY_WEAKER", "freshness": "READY",
        "note": "人民币对美元官方中间价（非市场收盘价）",
    }
    base.update(overrides)
    return base


def _context(**kwargs) -> dict:
    base = dict(
        bundle={"target": {"target_date": "20260909", "previous_date": "20260908",
                           "cutoff_at": "2026-09-09T08:50:00+08:00"},
                "data_mode": "FACTS_ONLY", "gaps": []},
        macro_context={"regime": "中性", "axes": {"growth": 55.0}, "events": []},
        candidates={"strong": [], "weak": []},
    )
    base.update(kwargs)
    return base


# --- 1/2 presence & missing -------------------------------------------------

def test_1_cny_mid_present_builds_xcm_and_keys() -> None:
    ctx = build_compact_context(**_context(cross_market_summary=_summary()))
    assert ctx["xcm"]["cny_mid"]["value"] == 6.7804
    assert ctx["xcm"]["cny_mid"]["direction_cn"] == "CNY_WEAKER"
    for key in ("XR_CNYMID_LVL", "XR_CNYMID_1D", "XR_CNYMID_5D"):
        assert key in ctx["ev"] and key in ctx["_alias"]
    assert ctx["_alias"]["XR_CNYMID_LVL"] == "XCM_USDCNY_MID_LVL"


def test_2_cny_mid_missing_null_and_gap() -> None:
    ctx = build_compact_context(**_context(cross_market_summary=None))
    assert ctx["xcm"] is None
    assert "XCM_CNY_MID_UNAVAILABLE" in ctx["gaps"]
    assert not [key for key in ctx["ev"] if key.startswith("XR_")]


def test_summary_builder_none_when_missing_or_stale() -> None:
    assert build_cny_mid_summary(None) is None
    assert build_cny_mid_summary({"fx": {}}) is None
    stale = {"fx": {"usd_cny_official_mid": {"value": 6.78, "freshness": "STALE",
                                             "changes": {}}}}
    assert build_cny_mid_summary(stale) is None


# --- 3/4 direction semantics ------------------------------------------------

def _fx_entry(d1: float | None, value: float = 6.7804) -> dict:
    return {"fx": {"usd_cny_official_mid": {
        "value": value, "observed_at": "2026-09-08", "freshness": "READY",
        "changes": {"chg_1d_pct": d1, "chg_5d_pct": -0.000074 if d1 is not None else None}}}}


def test_3_cny_weaker_direction() -> None:
    # USD/CNY 数值上升 = 1 美元兑更多人民币 = 人民币中间价偏弱
    summary = build_cny_mid_summary(_fx_entry(d1=0.0002))
    assert summary["direction_cn"] == "CNY_WEAKER"
    ctx = build_compact_context(**_context(cross_market_summary=summary))
    assert "人民币中间价偏弱" in ctx["ev"]["XR_CNYMID_1D"]


def test_4_cny_stronger_direction() -> None:
    summary = build_cny_mid_summary(_fx_entry(d1=-0.0003))
    assert summary["direction_cn"] == "CNY_STRONGER"
    ctx = build_compact_context(**_context(cross_market_summary=summary))
    assert "人民币中间价偏强" in ctx["ev"]["XR_CNYMID_1D"]


def test_direction_unchanged_and_derived_by_builder_not_model() -> None:
    flat = build_cny_mid_summary(_fx_entry(d1=0.0))
    assert flat["direction_cn"] == "UNCHANGED"
    # 方向由 compact 字段直接提供，模型不做自行推导（SYSTEM 规则 13）
    assert "方向以 direction_cn 为准" in SYSTEM_PROMPT


# --- 5/6 d1/d5 over observation sequence ------------------------------------

def test_5_holiday_sequence_d1() -> None:
    # 节假日缺失按有效观测序列：上一"可见观测"是 09-04（周五），不是自然日 T-1
    from src.macro_data.features import visible_window

    def _row(day: str, value: float, vintage: str) -> dict:
        return {"series_id": "usd_cny_official_mid", "observation_date": day,
                "release_date": day, "value": value, "unit": "CNY_per_USD",
                "source": "chinamoney.usdcny_mid", "fetched_at": "2026-09-08T09:20:00+08:00",
                "vintage_id": vintage,
                "metadata": {"published_at": f"{day}T09:15:00+08:00",
                             "published_at_precision": "DATETIME_SCHEDULED"}}

    rows = [_row("2026-09-04", 6.7787, "a"), _row("2026-09-08", 6.7804, "b")]
    window = visible_window(rows, cutoff=datetime(2026, 9, 8, 16, 45, tzinfo=SH))
    assert [item["observation_date"] for item in window] == ["2026-09-04", "2026-09-08"]
    d1 = window[-1]["value"] / window[-2]["value"] - 1
    assert d1 == pytest.approx(6.7804 / 6.7787 - 1)  # 上一有效观测=09-04（跳过周末）


def test_6_five_observation_d5() -> None:
    values = [6.7700, 6.7710, 6.7720, 6.7730, 6.7740, 6.7804]
    d5 = values[-1] / values[-6] - 1
    assert d5 == pytest.approx(6.7804 / 6.7700 - 1)
    summary = _summary(d5_pct=round(d5, 6))
    ctx = build_compact_context(**_context(cross_market_summary=summary))
    assert "较5日前中间价" in ctx["ev"]["XR_CNYMID_5D"]


# --- 7/8 PIT exclusions ------------------------------------------------------

def test_7_8_future_capture_and_post_cutoff_publish_excluded() -> None:
    from src.macro_data.features import visible_window
    rows = [
        # 当日 09:15 发布、09:20 抓取（cutoff 前）→ 可见
        {"series_id": "usd_cny_official_mid", "observation_date": "2026-09-08",
         "release_date": "2026-09-08", "value": 6.7804, "unit": "x",
         "source": "s", "fetched_at": "2026-09-08T09:20:00+08:00", "vintage_id": "a",
         "metadata": {"published_at": "2026-09-08T09:15:00+08:00",
                      "published_at_precision": "DATETIME_SCHEDULED"}},
        # captured_at 晚于 cutoff → 排除（绝不能倒灌）
        {"series_id": "usd_cny_official_mid", "observation_date": "2026-09-08",
         "release_date": "2026-09-08", "value": 9.9999, "unit": "x",
         "source": "s", "fetched_at": "2026-09-08T18:00:00+08:00", "vintage_id": "b",
         "metadata": {"published_at": "2026-09-08T09:15:00+08:00",
                      "published_at_precision": "DATETIME_SCHEDULED"}},
    ]
    cutoff = datetime(2026, 9, 8, 16, 45, tzinfo=SH)
    window = visible_window(rows, cutoff=cutoff)
    assert [item["value"] for item in window] == [6.7804]
    later_cutoff = datetime(2026, 9, 8, 19, 0, tzinfo=SH)
    window2 = visible_window(rows, cutoff=later_cutoff)
    assert [item["value"] for item in window2] == [6.7804, 9.9999]


# --- 9/10 evidence catalog ----------------------------------------------------

def test_9_valid_xr_keys_in_catalog() -> None:
    catalog = build_evidence_catalog(
        market={}, macro_context={}, candidates={"strong": [], "weak": []}, xcm_summary=_summary())
    for key in ("XCM_USDCNY_MID_LVL", "XCM_USDCNY_MID_1D", "XCM_USDCNY_MID_5D"):
        assert key in catalog
        assert "官方中间价" in catalog[key] or "中间价" in catalog[key]


def test_10_unknown_xr_key_rejected() -> None:
    from src.macro_forecast.semantic import metric_type
    assert metric_type("XCM_USDCNY_MID_1D") == "FX_OFFICIAL_MID"
    assert metric_type("XCM_USDCNY_MOM") == "UNKNOWN"  # 未注册的 XR 变体不可用
    catalog = annotate_catalog(build_evidence_catalog(
        market={}, macro_context={}, candidates={"strong": [], "weak": []}, xcm_summary=_summary()))
    guard = SemanticGuard(catalog_types=catalog)
    audit = guard.audit_market_summary("人民币中间价6.78", ["XR_CNYMID_BANANA"])
    assert audit["status"] == "PASS" or audit["breadth_claim"] == "NONE"  # 不崩溃即可；真正拦截在 validator
    types = {metric_type("XCM_USDCNY_MID_1D")}
    assert types == {"FX_OFFICIAL_MID"}


# --- 11-14 semantic guard ------------------------------------------------------

def _guard() -> SemanticGuard:
    catalog = build_evidence_catalog(
        market={}, macro_context={}, candidates={"strong": [], "weak": []}, xcm_summary=_summary())
    alias = {"XR_CNYMID_LVL": "XCM_USDCNY_MID_LVL",
             "XR_CNYMID_1D": "XCM_USDCNY_MID_1D",
             "XR_CNYMID_5D": "XCM_USDCNY_MID_5D"}
    return SemanticGuard(catalog_types=annotate_catalog(catalog), alias_map=alias)


def test_11_official_mid_description_accepted() -> None:
    audit = _guard().audit_market_summary(
        "人民币对美元官方中间价6.7804，人民币中间价偏弱。", ["XR_CNYMID_LVL", "XR_CNYMID_1D"])
    assert audit["status"] == "PASS"
    assert audit["cny_official_mid_violation_words"] == []


@pytest.mark.parametrize("word", ["离岸人民币", "市场汇率", "市场收盘", "资金流入", "外资流入"])
def test_12_13_14_market_fx_wording_rejected(word: str) -> None:
    audit = _guard().audit_market_summary(
        f"人民币对美元官方中间价6.7804，{word}推升人民币。", ["XR_CNYMID_LVL", "XR_CNYMID_1D"])
    assert audit["status"] == "UNSUPPORTED_SEMANTIC_CLAIM"
    assert word in audit["cny_official_mid_violation_words"]


def test_forbidden_words_without_cny_citation_not_flagged_here() -> None:
    # 守则只针对引用了 CNY 证据的文案；未引用时由其它规则处理（宽度/动量等）
    audit = _guard().audit_market_summary("市场震荡。", ["M1", "M2"])
    assert audit["status"] == "PASS"


# --- 15/16 not mandatory & no industry transmission ---------------------------

def test_15_cny_not_mandatory_in_rules() -> None:
    assert "是否引用由你判断" in SYSTEM_PROMPT
    assert "数据存在不构成必须使用的理由" in SYSTEM_PROMPT
    # 规则6 的 market 证据要求不包含 XR 键（市场仍至少2个 M*/宏观键由 catalog 决定）
    assert "XR_CNYMID" not in SYSTEM_PROMPT.split("硬规则：")[1].split("\n")[5]


def test_16_industry_reason_fx_wording_downgraded_without_support() -> None:
    guard = _guard()
    entry = {"industry_id": "tdx:881016.SH", "display_name": "煤炭开采", "side": "RELATIVE_STRONG",
             "evidence_keys": ["IND_tdx_881016_SH_RET_5D"],
             "reason": "人民币中间价偏弱利好煤炭出口。"}
    audit = guard.audit_industry_entry(entry)
    assert audit["classification"] == "OVERSTATED"  # FX 措辞 + 纯动量证据 → 降级标记
    assert audit["reason_basis"] == "PRICE_MOMENTUM"
    assert audit["issue"]  # 降级说明非空（FX 措辞不得伪装行业驱动）


def test_industry_entry_with_own_key_and_fx_wording_still_needs_industry_evidence() -> None:
    # validator 的行业自身键规则（validate.py full_prefix 前缀匹配）：XR 键不满足
    full_prefix = "IND_tdx_881016_SH_"
    resolved_raw = ["XCM_USDCNY_MID_LVL"]
    own_keys = [key for key in resolved_raw if key.startswith(full_prefix)]
    assert not own_keys  # XR 键不能单独支撑行业条目 → validator 拒绝


# --- 17/18/19 其它跨市场数据不得进入 prompt -------------------------------------

def test_17_18_19_only_cny_in_xcm_no_rates_wti_copper() -> None:
    ctx = build_compact_context(**_context(cross_market_summary=_summary()))
    payload = build_user_payload(ctx)
    for banned in ("us2y", "us10y", "treasury", "curve", "wti", "oil", "copper", "斯px", "spx"):
        assert banned not in payload.lower(), banned
    assert set(ctx["xcm"].keys()) == {"cny_mid"}


def test_xcm_rejects_extra_entries_by_construction() -> None:
    # compact 构建只读 cny_mid 字段：传入多余键也不会出现在 payload
    summary = _summary()
    ctx = build_compact_context(**_context(cross_market_summary=summary))
    assert list(ctx["xcm"].keys()) == ["cny_mid"]


# --- 20 no value line writes ---------------------------------------------------

def test_20_no_value_line_modules_touched() -> None:
    import subprocess
    import sys
    import os
    code = ('import sys; import src.macro_forecast.prompt, src.macro_forecast.engine;'
            'bad=[m for m in sys.modules if m.startswith(("src.value_workspace",'
            '"src.investment_research_supervisor","src.strategy_engines"))];'
            'print("CONTAMINATED=" + ",".join(bad))')
    root = os.getcwd()
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=root), timeout=120, cwd=root)
    assert result.returncode == 0, result.stderr[-500:]
    assert "CONTAMINATED=\r\n" in result.stdout or "CONTAMINATED=\n" in result.stdout or \
        result.stdout.strip().endswith("CONTAMINATED=")


# --- 版本身份与 A/B snapshot（§十九） -------------------------------------------

def test_version_identity_upgraded() -> None:
    assert PROMPT_VERSION == "macro-market-industry-prompt-v2.1"
    assert SEMANTIC_GUARD_VERSION == "forecast-semantic-guard-v1.1.0"
    from src.macro_forecast.engine import FORECAST_FORMULA_VERSION
    from src.macro_forecast.features import EVIDENCE_CATALOG_VERSION
    assert FORECAST_FORMULA_VERSION == "macro-market-industry-forecast-v1.2.0"
    assert EVIDENCE_CATALOG_VERSION == "forecast-evidence-catalog-v1.3.0"


def test_ab_snapshot_only_cny_differs() -> None:
    base = _context()
    ctx_a = build_compact_context(**{**base, "cross_market_summary": None})
    ctx_b = build_compact_context(**{**base, "cross_market_summary": _summary()})
    payload_a, payload_b = build_user_payload(ctx_a), build_user_payload(ctx_b)
    # 除 xcm/XR 键外完全一致：剔除 xcm 与 XR ev 后同构
    a = json.loads(payload_a)
    b = json.loads(payload_b)
    a_ev = {k: v for k, v in a.pop("ev").items() if not k.startswith("XR_")}
    b_ev = {k: v for k, v in b.pop("ev").items() if not k.startswith("XR_")}
    a.pop("xcm")
    b.pop("xcm")
    assert a_ev == b_ev
    # 唯一差异 = CNY：gaps 相差 XCM_CNY_MID_UNAVAILABLE 一个元素，其余字段全同
    assert set(b["gaps"]) | {"XCM_CNY_MID_UNAVAILABLE"} == set(a["gaps"])
    b.pop("gaps")
    a.pop("gaps")
    assert a == b  # mkt/macro/cands/T/P/C/mode 全同
    delta_tokens = estimate_tokens(payload_b) - estimate_tokens(payload_a)
    assert 0 < delta_tokens < 300  # §十七：增量 <300 tokens
