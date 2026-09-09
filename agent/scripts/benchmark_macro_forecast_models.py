"""压缩版 prompt 的模型 A/B 基准（运行稳定性 V1 §十五-§十七）。

预算：每模型 1 次 logical（≤2 实际请求，45s）；明确 transport timeout 时可选
升级 60s 再测 1 次 logical。总计与最终 SHADOW 合并 ≤4 logical。
不持久化预测（benchmark 只测成功率/时延/结构化合规率），留档走正式 SHADOW。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.engine import _parse_json_object, ForecastEngine
from src.macro_forecast.forecast_service import (
    prepare_forecast_inputs, resolve_model_config,
)
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.validate import ForecastValidator

LOGICAL_BUDGET = 3  # 基准阶段最多 3 logical；最终 SHADOW 1 logical（合计 4）


def run_one(engine: ForecastEngine, prep, *, model_config, timeout: int, logical_used: list[int]) -> dict:
    logical_used[0] += 1
    started = time.monotonic()
    call = engine.call_model(
        prep["payload_text"], model_config={**model_config, "request_timeout_seconds": timeout},
    )
    wall_ms = int((time.monotonic() - started) * 1000)
    result = {
        "model": model_config["model"], "role": model_config.get("role"),
        "timeout": timeout, "logical_calls": 1,
        "actual_requests": call.get("requests") or 0,
        "retries": call.get("retries") or 0,
        "latency_ms": call.get("latency_ms") or wall_ms,
        "prompt_tokens_estimate": prep["prompt_size"]["input_token_estimate"],
        "ok": bool(call.get("ok")),
    }
    if not call.get("ok"):
        result.update({"result": "MODEL_FAILED", "error_class": call.get("error_class"), "output_chars": 0})
        return result
    raw = str(call.get("raw_text") or "")
    result["output_chars"] = len(raw)
    try:
        output = _parse_json_object(raw)
    except Exception as exc:  # noqa: BLE001
        result.update({"result": "INVALID_OUTPUT", "validation": {"error": f"JSON: {exc}"}})
        return result
    validator = ForecastValidator(
        evidence_catalog=prep["evidence_catalog"],
        candidate_ids={row["industry_id"] for row in (*prep["candidates"]["strong"], *prep["candidates"]["weak"])},
        industry_names={row["industry_id"]: row["name"] for row in prep["candidates"]["strong"] + prep["candidates"]["weak"]},
        market_features=(prep["context"].get("mkt") or {}),
        alias_map=prep.get("alias_map") or {},
        candidate_alias=prep.get("candidate_alias") or {},
    )
    report = validator.validate(output)
    result.update({
        "result": "VALIDATION_PASS" if report["valid"] else "INVALID_OUTPUT",
        "direction": (output.get("market") or {}).get("direction"),
        "abstain": bool(output.get("abstain")),
        "validation_issues": report["issues"][:5],
    })
    if report["valid"]:
        entries = report["industry_entries"]
        result["strong"] = [entry["display_name"] for entry in entries if entry["side"] == "RELATIVE_STRONG"]
        result["weak"] = [entry["display_name"] for entry in entries if entry["side"] == "RELATIVE_WEAK"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="压缩 prompt 模型基准")
    parser.add_argument("--target", default="20260908")
    parser.add_argument("--roles", default="macro_policy,risk", help="逗号分隔的 agent_model_configs 角色")
    parser.add_argument("--escalate-60", action="store_true", help="45s 双超时后允许 60s 升级（多耗 1 logical）")
    parser.add_argument("--disable-thinking", action="store_true", help="GLM 关闭深度思考（extra_body）")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    research_db = get_runtime_root() / "research.db"
    prepared = prepare_forecast_inputs(target_date=args.target, research_db=research_db)
    if prepared["status"] != "PREPARED":
        print(json.dumps(prepared, ensure_ascii=False))
        return 2
    prep = prepared["prep"]
    print(f"prompt: chars={prep['prompt_size']['total_chars']} tokens~={prep['prompt_size']['input_token_estimate']} "
          f"hash={prep['prompt_hash']} strong={len(prep['candidates']['strong'])} weak={len(prep['candidates']['weak'])}")

    store = ForecastStore(research_db)
    engine = prepared["engine"]
    logical_used = [0]
    results: list[dict] = []
    try:
        for role in [item.strip() for item in args.roles.split(",") if item.strip()]:
            config = resolve_model_config(role=role, db_path=research_db)
            if config is None:
                results.append({"model": f"role:{role}", "result": "CONFIG_MISSING"})
                continue
            if args.disable_thinking:
                config = {**config, "extra_body": {"thinking": {"type": "disabled"}}}
            outcome = run_one(engine, prep, model_config=config, timeout=45, logical_used=logical_used)
            results.append(outcome)
            print(json.dumps(outcome, ensure_ascii=False))
            if (not outcome["ok"]) and outcome.get("error_class") in {"APITimeoutError", "TimeoutError", "HARD_DEADLINE"} \
                    and args.escalate_60 and logical_used[0] < LOGICAL_BUDGET:
                escalated = run_one(engine, prep, model_config=config, timeout=60, logical_used=logical_used)  # noqa: same config
                results.append(escalated)
                print(json.dumps(escalated, ensure_ascii=False))
            if logical_used[0] >= LOGICAL_BUDGET:
                break
    finally:
        store.close()

    summary = {
        "target": args.target, "prompt_hash": prep["prompt_hash"],
        "logical_calls_used": logical_used[0], "results": results,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
