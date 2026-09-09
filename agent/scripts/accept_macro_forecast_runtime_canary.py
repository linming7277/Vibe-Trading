"""运行验收 V1：动态目标 → 采集 → 重建 bundle → dry-run → SHADOW → 审计 → 读路径。

只验收运行栈能否稳定产生合法预测；不改预测逻辑。
输出 JSON 报告并按 §二十四 给出 RUNTIME_READY 判定。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.bars import ForecastBarStore, collect_history
from src.macro_forecast.forecast_service import (
    get_latest_macro_forecast, load_trading_days, prepare_forecast_inputs,
    resolve_model_config, run_macro_forecast,
)
from src.macro_forecast.registry import REGISTRY_VERSION, InstrumentRegistryStore

SH = ZoneInfo("Asia/Shanghai")

_OVERSEAS_TOKENS = ("美股", "纳斯达克", "道琼斯", "标普", "隔夜", "昨夜", "外盘", "美联储", "港股", "恒生")
_DETERMINISM_TOKENS = ("必然", "必将", "一定会", "肯定会", "注定", "毫无疑问")
_POSITIVE_TOKENS = ("走强", "占优", "领先", "跑赢", "强于", "上涨", "韧性强")
_NEGATIVE_TOKENS = ("走弱", "落后", "跑输", "弱于", "下跌", "承压", "疲弱")


def _table_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0] for name in names}
    finally:
        conn.close()


def audit_content_support(payload: dict[str, Any], prep: dict[str, Any]) -> dict[str, Any]:
    """§十五：确定性内容支撑审计。UNSUPPORTED 任一 → FAIL。"""
    output = payload.get("model_output") or {}
    data_mode = (payload.get("input") or {}).get("data_mode")
    market = output.get("market") or {}
    alias = (payload.get("input") or {}).get("alias_map") or {}
    values = (payload.get("input") or {}).get("evidence_values") or {}
    findings: list[dict[str, str]] = []

    def classify(label: str, text: str, *, hypothetical_overseas_ok: bool = False) -> None:
        unsupported = overstated = False
        if data_mode != "FULL" and any(tok in text for tok in _OVERSEAS_TOKENS):
            framed_hypothetical = any(mark in text for mark in
                                      ("无法验证", "恢复", "若", "如果", "一旦", "缺失", "OVERSEAS"))
            if hypothetical_overseas_ok and framed_hypothetical:
                pass  # 失效条件中的前瞻性外部冲击假设，且明示当前不可验证 → 合法
            else:
                findings.append({"item": label, "class": "UNSUPPORTED", "issue": "缺海外数据却提及海外行情"})
                return
        if any(tok in text for tok in _DETERMINISM_TOKENS):
            overstated = True
        findings.append({
            "item": label,
            "class": "OVERSTATED" if overstated else "SUPPORTED",
            "issue": "确定性表述（历史表现写成确定未来）" if overstated else "",
        })

    classify("market.summary", str(market.get("summary") or ""))
    for index, item in enumerate(market.get("invalidation_conditions") or []):
        # 失效条件天然是前瞻假设：明示不可验证/条件式的海外冲击提法合法
        classify(f"market.invalidation[{index}]", str(item), hypothetical_overseas_ok=True)

    entries = (payload.get("validation") or {}).get("industry_entries") or []
    for entry in entries:
        label = f"industry[{entry.get('display_name')}]"
        reason = str(entry.get("reason") or "")
        # 方向一致性：理由方向词 vs 自身 RELATIVE_5D 证据值符号
        rel5 = None
        for short, full in alias.items():
            if full.endswith("_RELATIVE_5D") and short in (entry.get("evidence_keys") or []):
                raw = values.get(short)
                if raw not in (None, "null"):
                    rel5 = float(str(raw).replace("%", "").replace("pp", "").replace("+", ""))
        if rel5 is not None:
            says_positive = any(tok in reason for tok in _POSITIVE_TOKENS)
            says_negative = any(tok in reason for tok in _NEGATIVE_TOKENS)
            if says_positive and rel5 < 0:
                findings.append({"item": label, "class": "UNSUPPORTED",
                                 "issue": f"理由称强但 RELATIVE_5D={rel5} 为负"})
                continue
            if says_negative and rel5 > 0:
                findings.append({"item": label, "class": "UNSUPPORTED",
                                 "issue": f"理由称弱但 RELATIVE_5D={rel5} 为正"})
                continue
        # 数值一致性：理由中的 pp/% 数与证据值差 > 1.0pp → UNSUPPORTED
        for match in re.finditer(r"([+-]?\d+(?:\.\d+)?)\s*(?:pp|个百分点)", reason):
            claimed = float(match.group(1))
            if rel5 is not None and abs(abs(claimed) - abs(rel5) * 100) > 1.0:
                findings.append({"item": label, "class": "UNSUPPORTED",
                                 "issue": f"理由数值 {claimed}pp 与证据 {round(rel5 * 100, 2)}pp 不符"})
                break
        else:
            if data_mode != "FULL" and any(tok in reason for tok in _OVERSEAS_TOKENS):
                findings.append({"item": label, "class": "UNSUPPORTED", "issue": "理由提及海外行情"})
            elif any(tok in reason for tok in _DETERMINISM_TOKENS):
                findings.append({"item": label, "class": "OVERSTATED", "issue": "确定性表述"})
            else:
                findings.append({"item": label, "class": "SUPPORTED", "issue": ""})
    unsupported = [item for item in findings if item["class"] == "UNSUPPORTED"]
    return {"findings": findings, "unsupported_count": len(unsupported),
            "quality": "FAIL" if unsupported else "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description="运行验收 V1")
    parser.add_argument("--target", required=True)
    parser.add_argument("--collect-bars", action="store_true", help="先采集最新收盘 K 线（0 LLM/0 外网）")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--second-tier-timeout", type=int, default=60)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    research_db = get_runtime_root() / "research.db"
    report: dict[str, Any] = {"started_at": datetime.now(SH).isoformat()}

    # §22 前置快照：价值线/宏观不变量
    before_counts = _table_counts(research_db)

    # §3/4：动态日历 + （可选）采集最新收盘 + 重建 bundle（0 LLM）
    trading_days = load_trading_days()
    today = datetime.now(SH)
    report["calendar"] = {
        "today": today.strftime("%Y-%m-%d %H:%M"),
        "calendar_max": trading_days[-1] if trading_days else None,
        "target": args.target,
        "in_calendar": args.target in trading_days,
    }
    if args.collect_bars:
        registry = InstrumentRegistryStore(research_db)
        bar_store = ForecastBarStore(research_db)
        tdx = None
        try:
            rows_registry = registry.load(registry_version=REGISTRY_VERSION)
            targets = [row for row in rows_registry if row.get("history_target_days")]
            from src.tdx_data.client import TdxClient

            tdx = TdxClient()
            end = today.strftime("%Y%m%d")
            rows = collect_history(tdx, [row["code"] for row in targets], end_date=end,
                                   count=520, progress=lambda *a: None)
            written = bar_store.write_bars(rows)
            report["bar_collection"] = written
        finally:
            if tdx is not None:
                tdx.close()
            registry.close()
            bar_store.close()

    from src.macro_forecast.bundle import ForecastBundleStore
    from src.macro_forecast.service import prepare_input_bundle

    bundle_result = prepare_input_bundle(
        target_date=args.target, trading_days=trading_days, save=True,
    )
    report["bundle_build"] = {k: v for k, v in bundle_result.items() if k != "payload"}
    if bundle_result.get("status") != "BUILT":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 2

    # §5 dry-run（0 LLM）
    dry_started = time.monotonic()
    prepared = prepare_forecast_inputs(target_date=args.target, research_db=research_db)
    prep = prepared["prep"]
    bundle = prepared["bundle"]
    deterministic_prepare_ms = int((time.monotonic() - dry_started) * 1000)
    model_config = resolve_model_config(db_path=research_db)
    report["dry_run"] = {
        "target_trade_date": prep["target_trade_date"],
        "input_bundle_id": prep["bundle_id"],
        "input_fingerprint": prep["fingerprint"],
        "calendar_status": (bundle.get("target") or {}).get("calendar_status"),
        "market_freshness": (bundle.get("market") or {}).get("benchmark", {}).get("status"),
        "bars_as_of": bundle.get("bars_as_of"),
        "macro_freshness": bundle.get("macro_coverage"),
        "industry_freshness": {"eligible": prep["industry_eligible_count"], "total": prep["industry_total"]},
        "data_mode": bundle.get("data_mode"),
        "eligible_industry_count": prep["industry_eligible_count"],
        "strong_candidates": len(prep["candidates"]["strong"]),
        "weak_candidates": len(prep["candidates"]["weak"]),
        "prompt_chars": prep["prompt_size"]["total_chars"],
        "estimated_tokens": prep["prompt_size"]["input_token_estimate"],
        "prompt_hash": prep["prompt_hash"],
        "would_call_model": "YES" if prep["would_call_llm"] else "NO",
        "model_config": ({k: v for k, v in model_config.items() if k != "api_key"} if model_config else None),
        "deterministic_prepare_ms": deterministic_prepare_ms,
    }
    if not prep["would_call_llm"]:
        report["dry_run"]["blocked_reason"] = prep["deterministic_abstain_reason"]
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 3

    # §7-11：SHADOW（45s 档；失败且为运输层超时 → 60s 档）
    shadow_started = time.monotonic()
    result = run_macro_forecast(target_date=args.target, mode="shadow")
    total_ms = int((time.monotonic() - shadow_started) * 1000)
    row = result.get("row") or {}
    report["shadow"] = {
        "status": result.get("status"),
        "id": result.get("id"),
        "market_direction": row.get("market_direction"),
        "logical_calls": result.get("logical_calls", row.get("logical_calls")),
        "actual_requests": result.get("actual_requests", row.get("actual_requests")),
        "retries": result.get("retry_count", row.get("retry_count")),
        "model_latency_ms": row.get("model_latency_ms"),
        "total_ms": total_ms,
        "prompt_chars": row.get("prompt_chars"),
        "output_chars": row.get("output_chars"),
    }
    payload = result.get("payload")
    if payload is None and row.get("structured_payload_json"):
        payload = json.loads(row["structured_payload_json"])
        result = {**result, "payload": payload}

    # 第二档：仅当第一档 MODEL_FAILED 且错误为超时类
    if report["shadow"]["status"] == "MODEL_FAILED" and result.get("error_class") in {
            "APITimeoutError", "TimeoutError", "HARD_DEADLINE"}:
        second_started = time.monotonic()
        # 引擎按 model_config.request_timeout_seconds 控制单请求超时；
        # 通过 benchmark 通道执行第二档（不落新预测，测可运行性）
        from src.macro_forecast.engine import _parse_json_object
        from src.macro_forecast.forecast_store import ForecastStore
        from src.macro_forecast.validate import ForecastValidator

        store = ForecastStore(research_db)
        engine = prepared["engine"]
        try:
            call = engine.call_model(
                prep["payload_text"],
                model_config={**(model_config or {}), "request_timeout_seconds": args.second_tier_timeout},
            )
        finally:
            store.close()
        report["second_tier"] = {
            "timeout": args.second_tier_timeout,
            "ok": bool(call.get("ok")),
            "requests": call.get("requests"), "latency_ms": call.get("latency_ms"),
            "total_ms": int((time.monotonic() - second_started) * 1000),
        }
        if call.get("ok"):
            report["second_tier"]["output_chars"] = len(str(call.get("raw_text") or ""))
            report["second_tier"]["usage"] = call.get("usage")
            validator = ForecastValidator(
                evidence_catalog=prep["evidence_catalog"],
                candidate_ids={r["industry_id"] for r in (*prep["candidates"]["strong"], *prep["candidates"]["weak"])},
                industry_names={r["industry_id"]: r["name"] for r in prep["candidates"]["strong"] + prep["candidates"]["weak"]},
                market_features=(bundle.get("market") or {}).get("benchmark") or {},
                alias_map=prep.get("alias_map") or {}, candidate_alias=prep.get("candidate_alias") or {},
            )
            try:
                output = _parse_json_object(str(call.get("raw_text") or ""))
                report["second_tier"]["validation"] = validator.validate(output)
            except Exception as exc:  # noqa: BLE001
                report["second_tier"]["validation"] = {"valid": False, "error": str(exc)}

    # §15 内容支撑审计 + §19 读路径 + §22 后置快照
    if payload is not None:
        audit_started = time.monotonic()
        report["content_audit"] = audit_content_support(payload, prep)
        report["content_audit_ms"] = int((time.monotonic() - audit_started) * 1000)

    read_started = time.monotonic()
    latest1 = get_latest_macro_forecast(research_db=research_db)
    latest2 = get_latest_macro_forecast(research_db=research_db)
    report["read_path"] = {
        "ms": int((time.monotonic() - read_started) * 1000),
        "stable": bool(latest1 and latest2 and latest1["id"] == latest2["id"]),
        "latest_id": (latest1 or {}).get("id"), "latest_status": (latest1 or {}).get("status"),
    }
    after_counts = _table_counts(research_db)
    delta = {name: after_counts[name] - before_counts.get(name, 0)
             for name in after_counts if after_counts[name] != before_counts.get(name, 0)}
    report["table_deltas"] = delta
    report["value_line_impact"] = "NONE" if all(
        name.startswith(("forecast_", "sqlite_", "macro_market_forecasts")) for name in delta) else "ISSUE"

    # §24 判定
    tokens = prep["prompt_size"]["input_token_estimate"]
    valid_output = payload is not None and (payload.get("validation") or {}).get("valid") is True
    latency_ok = (report["shadow"].get("model_latency_ms") or 10**9) <= 60000
    quality_ok = (report.get("content_audit") or {}).get("quality") == "PASS"
    report["runtime_gate"] = "RUNTIME_READY" if all([
        tokens <= 8000, valid_output, quality_ok, latency_ok,
        report["value_line_impact"] == "NONE",
    ]) else "RUNTIME_NOT_READY"

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                  encoding="utf-8")
        print(f"report saved: {args.out}")
    print(json.dumps({k: v for k, v in report.items() if k not in {"dry_run"}},
                     ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report["dry_run"], ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
