"""联合预测引擎：确定性准备 + 单次模型调用 + 校验 + 留档（预测引擎 V1）。

预算契约（§25-§29）：
- 每 target 最多 1 次 logical forecast invocation；
- 单请求 timeout 45s（ChatLLM timeout_seconds，底层 max_retries=0 禁隐式重试）；
- 仅瞬时错误（Timeout/Connection/429/5xx/Overloaded）允许 1 次重试；
- 模型阶段硬上限 95s，超时 → MODEL_FAILED 直接停止；
- 内容校验失败 0 次重试（INVALID_OUTPUT）；
- 熔断：连续 2 次同类瞬断 → 10 分钟冷却（config 可调）。

降级（§29）：模型失败仍可确定性生成宏观事实摘要，但大盘/行业预测明确"未生成"，
状态 MODEL_FAILED；禁止模板自动"震荡"。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from src.macro_forecast.features import (
    CANDIDATE_RULES_VERSION, EVIDENCE_CATALOG_VERSION, FEATURE_PROJECTION_VERSION,
    build_evidence_catalog, build_industry_features, select_candidates,
)
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.prompt import (
    PROMPT_VERSION, SYSTEM_PROMPT, build_compact_context, build_user_payload,
    estimate_prompt_size, prompt_hash,
)
from src.macro_forecast.render import RENDERER_VERSION, render_narrative
from src.macro_forecast.validate import ForecastValidator

logger = logging.getLogger(__name__)

# v1.2.0: prompt 输入新增 xcm.cny_mid（USD/CNY 官方中间价，prompt v2.1）
FORECAST_FORMULA_VERSION = "macro-market-industry-forecast-v1.2.0"

# §25-§28 + 压缩稳定性任务 §十四：默认 45s，可按 canary 流程升到 60s；
# 单请求上限 60s，模型阶段硬上限 125s。
REQUEST_TIMEOUT_SECONDS = 45
MAX_REQUEST_TIMEOUT_SECONDS = 60
MAX_EXTRA_REQUESTS = 1  # 瞬时错误后的额外真实请求次数
MODEL_HARD_DEADLINE_SECONDS = 125
CIRCUIT_CONSECUTIVE = 2
CIRCUIT_COOLDOWN_SECONDS = 600

TRANSIENT_MARKERS = (
    "timeout", "timed out", "connection", "connect", "429", "rate limit",
    "502", "503", "504", "service unavailable", "overloaded", "temporarily",
)

STATUS_DRAFT = "DRAFT"
STATUS_SHADOW = "SHADOW"
STATUS_OFFICIAL = "OFFICIAL"
STATUS_ABSTAINED = "ABSTAINED"
STATUS_MODEL_FAILED = "MODEL_FAILED"
STATUS_INVALID_OUTPUT = "INVALID_OUTPUT"

DIRECTION_CN = {"STRONGER": "偏强", "RANGE_BOUND": "震荡", "WEAKER": "偏弱", "ABSTAIN": "暂不判断"}
DATA_MODE_CN = {
    "FULL": "数据完整",
    "DOMESTIC_LIMITED": "当前主要基于境内数据",
    "FACTS_ONLY": "仅事实简报（核心输入不足）",
}


def _is_transient(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


class ForecastEngine:
    """一次 run = 确定性准备 + （受控）一次模型调用 + 校验 + 留档。"""

    def __init__(self, store: ForecastStore, *, model_factory: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.model_factory = model_factory
        self.clock = clock

    # ------------------------------------------------------------------
    # 确定性准备（dry-run 也走这一段；<2s 目标）
    # ------------------------------------------------------------------

    def prepare(self, *, bundle: dict[str, Any], bars_map: dict[str, list[dict[str, Any]]],
                industry_rows: list[dict[str, Any]], macro_context: dict[str, Any],
                bundle_id: str, candidate_limit: int | None = None,
                cross_market_summary: dict[str, Any] | None = None) -> dict[str, Any]:
        target = bundle.get("target") or {}
        previous_date = str(target.get("previous_date") or "")
        features = build_industry_features(
            bars_map=bars_map, industry_rows=industry_rows,
            previous_date=previous_date, macro_axes=macro_context.get("axes"),
        )
        candidates = (select_candidates(features, limit=candidate_limit) if candidate_limit
                      else select_candidates(features))
        # §八：基准 20 日收益从 K 线确定性计算；宽度/风险偏好取包内可见事实。
        benchmark_closes = [row for row in bars_map.get("000300.SH") or []
                            if str(row.get("trade_date") or "").replace("-", "")[:8] <= previous_date.replace("-", "")
                            and row.get("close")]
        benchmark_ret_20d = None
        if len(benchmark_closes) >= 21:
            closes = [float(row["close"]) for row in benchmark_closes[-21:]]
            if closes[0] > 0:
                benchmark_ret_20d = round(closes[-1] / closes[0] - 1, 12)
        facts_by_series = {fact.get("series_id"): fact for fact in bundle.get("macro_facts") or []}
        # §八：背景指数 5 日收益从 K 线确定性计算（输入包 reference 不含背景指数时）。
        from src.macro_forecast.registry import REFERENCE_INDEXES as _REF_INDEXES

        reference_5d: dict[str, float | None] = {}
        for code, _ in _REF_INDEXES:
            rows = [row for row in bars_map.get(code) or []
                    if str(row.get("trade_date") or "").replace("-", "")[:8] <= previous_date.replace("-", "")
                    and row.get("close")]
            closes = [float(row["close"]) for row in rows]
            reference_5d[code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")] = (
                round(closes[-1] / closes[-6] - 1, 12) if len(closes) >= 6 and closes[-6] > 0 else None
            )
        market_extras = {
            "benchmark_ret_20d": benchmark_ret_20d,
            "breadth_20d": (facts_by_series.get("a_share_breadth_20d") or {}).get("value"),
            "risk_appetite": (facts_by_series.get("csi_all_share_risk_appetite") or {}).get("value"),
            "reference_5d": reference_5d,
        }
        evidence_catalog = build_evidence_catalog(
            market=bundle.get("market") or {}, macro_context=macro_context,
            candidates=candidates, benchmark_ret_20d=benchmark_ret_20d,
            reference_5d=reference_5d,
            breadth_20d=market_extras.get("breadth_20d"),
            risk_appetite=market_extras.get("risk_appetite"),
            xcm_summary=cross_market_summary,
        )
        context = build_compact_context(
            bundle={**bundle, "_bundle_id": bundle_id},
            macro_context=macro_context, candidates=candidates, market_extras=market_extras,
            cross_market_summary=cross_market_summary,
        )
        payload_text = build_user_payload(context)
        size = estimate_prompt_size(SYSTEM_PROMPT, payload_text)
        # 短键别名（§六）：模型只引用别名；服务端保留别名→完整键与候选短 id 映射。
        candidate_alias = {}
        for prefix, rows in (("S", candidates["strong"]), ("W", candidates["weak"])):
            for index, row in enumerate(rows, 1):
                candidate_alias[f"{prefix}{index}"] = {
                    "industry_id": row["industry_id"], "name": row["name"], "code": row["code"],
                }
        benchmark = (bundle.get("market") or {}).get("benchmark") or {}
        gates = {
            "benchmark_ready": benchmark.get("status") == "READY",
            "macro_direction_ok": (bundle.get("macro_coverage") or {}).get("direction_input_ok") is True,
            "calendar_confirmed": target.get("calendar_status") == "CALENDAR_CONFIRMED",
            "candidates_ready": bool(candidates["strong"] or candidates["weak"]),
        }
        eligible = sum(1 for row in features if row["history_status"] == "ELIGIBLE")
        return {
            "bundle_id": bundle_id, "fingerprint": bundle.get("fingerprint"),
            "target_trade_date": target.get("target_date"),
            "features": features, "candidates": candidates,
            "evidence_catalog": evidence_catalog, "context": context,
            "alias_map": context.get("_alias") or {},
            "candidate_alias": candidate_alias,
            "payload_text": payload_text, "prompt_hash": prompt_hash(payload_text),
            "prompt_size": size,
            "gates": gates,
            "industry_eligible_count": eligible,
            "industry_total": len(features),
            "would_call_llm": gates["benchmark_ready"] and gates["macro_direction_ok"],
            "deterministic_abstain": not (gates["benchmark_ready"] and gates["macro_direction_ok"]),
            "deterministic_abstain_reason": (
                None if gates["benchmark_ready"] and gates["macro_direction_ok"]
                else self._gate_reason(gates, bundle)
            ),
        }

    @staticmethod
    def _gate_reason(gates: dict[str, bool], bundle: dict[str, Any]) -> str:
        reasons = []
        if not gates["benchmark_ready"]:
            reasons.append("基准行情未就绪（P 日收盘缺失或历史不足）——数据缺口")
        if not gates["macro_direction_ok"]:
            reasons.append("宏观方向输入组覆盖不足（增长/价格/信用流动性不足两组）——数据缺口")
        return "；".join(reasons) or "输入异常"

    # ------------------------------------------------------------------
    # 模型调用（受控预算）
    # ------------------------------------------------------------------

    def call_model(self, payload_text: str, *, model_config: dict[str, Any],
                   deadline_elapsed: float = 0.0) -> dict[str, Any]:
        """单 logical 调用；仅瞬时错误重试 1 次；返回原始输出与计数。"""
        from src.providers.chat import ChatLLM

        circuit = self.store.circuit_open(consecutive=CIRCUIT_CONSECUTIVE,
                                          cooldown_seconds=CIRCUIT_COOLDOWN_SECONDS)
        if circuit.get("open"):
            return {"ok": False, "error_class": "CIRCUIT_OPEN",
                    "detail": f"熔断中（{circuit.get('failure_class')}）", "requests": 0, "retries": 0}
        # §十四：默认 45s；canary 流程允许显式升到 60s（单请求上限）。
        timeout = REQUEST_TIMEOUT_SECONDS
        override = model_config.get("request_timeout_seconds")
        if override is not None:
            timeout = max(1, min(int(override), MAX_REQUEST_TIMEOUT_SECONDS))
        # 可选运行时开关（model_config.extra_body）：如 GLM 系关闭深度思考
        # {"thinking": {"type": "disabled"}}，用于盘前时效稳定（运行稳定性 V1）。
        extra_body = model_config.get("extra_body") or None
        client = (
            self.model_factory(
                model_name=model_config["model"], provider_name=model_config.get("provider") or "openai",
                base_url=model_config.get("base_url"), api_key=model_config.get("api_key") or "",
                timeout_seconds=timeout, max_retries=0,
            )
            if self.model_factory is not None
            else ChatLLM(
                model_name=model_config["model"], provider_name=model_config.get("provider") or "openai",
                base_url=model_config.get("base_url"), api_key=model_config.get("api_key") or "",
                timeout_seconds=timeout, max_retries=0, extra_body=extra_body,
            )
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload_text},
        ]
        # JSON 输出模式与财务分析管线同口径：约束输出结构、避免长篇思维链
        # 拖垮盘前时效（运行稳定性 V1）。
        response_format = {"type": "json_object"}
        requests = 0
        retries = 0
        started = self.clock()
        while True:
            request_started = self.clock()
            requests += 1
            try:
                response = client.chat(messages, response_format=response_format)
                latency_ms = int((self.clock() - request_started) * 1000)
                self.store.record_provider_success()
                usage = getattr(response, "usage_metadata", None)
                return {
                    "ok": True, "raw_text": (response.content or ""),
                    "requests": requests, "retries": retries, "latency_ms": latency_ms,
                    "usage": dict(usage) if isinstance(usage, dict) else None,
                }
            except Exception as exc:  # noqa: BLE001 - 分类后按契约处置
                latency_ms = int((self.clock() - request_started) * 1000)
                transient = _is_transient(exc)
                error_class = type(exc).__name__
                if transient:
                    self.store.record_provider_failure(error_class, f"{error_class}: {exc}")
                if not transient:
                    return {"ok": False, "error_class": error_class,
                            "detail": f"{error_class}: {exc}", "requests": requests,
                            "retries": retries, "latency_ms": latency_ms}
                if retries >= MAX_EXTRA_REQUESTS:
                    return {"ok": False, "error_class": error_class,
                            "detail": f"{error_class}: {exc}（瞬时重试用尽）",
                            "requests": requests, "retries": retries, "latency_ms": latency_ms}
                retries += 1
                if (self.clock() - started) + deadline_elapsed > MODEL_HARD_DEADLINE_SECONDS - timeout:
                    return {"ok": False, "error_class": "HARD_DEADLINE",
                            "detail": "模型阶段总时限将超 95s，停止重试",
                            "requests": requests, "retries": retries, "latency_ms": latency_ms}

    # ------------------------------------------------------------------
    # 完整 run（SHADOW / DRAFT / OFFICIAL）
    # ------------------------------------------------------------------

    def run(self, *, prep: dict[str, Any], bundle: dict[str, Any], run_mode: str,
            model_config: dict[str, Any] | None = None,
            macro_summary: dict[str, Any] | None = None) -> dict[str, Any]:
        assert run_mode in ("DRAFT", "SHADOW", "OFFICIAL")
        target_date = str(prep["target_trade_date"])
        # §37：同输入同版本的既有有效结论 → REUSED；失败留档不阻塞后续探测
        # （节奏由熔断冷却约束，失败历史仍不可变保留）。
        existing = self.store.existing_run(
            target_date=target_date, fingerprint=str(prep["fingerprint"]),
            formula_version=FORECAST_FORMULA_VERSION, run_mode=run_mode,
        )
        if existing and existing["status"] in ("DRAFT", "SHADOW", "OFFICIAL", "ABSTAINED"):
            return {"status": "REUSED", "id": existing["id"], "row": existing,
                    "logical_calls": 0, "actual_requests": 0, "retry_count": 0}
        if run_mode == "OFFICIAL":
            official = self.store.official_for_target(target_date)
            if official:
                return {"status": "REUSED", "id": official["id"], "row": official,
                        "logical_calls": 0, "actual_requests": 0, "retry_count": 0}

        base_record: dict[str, Any] = {
            "target_trade_date": target_date, "run_mode": run_mode,
            "input_bundle_id": prep["bundle_id"], "input_fingerprint": str(prep["fingerprint"]),
            "forecast_formula_version": FORECAST_FORMULA_VERSION, "prompt_version": PROMPT_VERSION,
            "renderer_version": RENDERER_VERSION, "candidate_rules_version": CANDIDATE_RULES_VERSION,
        }

        # 确定性弃权（§22/§23：数据缺口 grounding 由 gate 理由保证）
        if prep["deterministic_abstain"]:
            payload = self._structured_payload(
                prep=prep, bundle=bundle, model_output=None, report=None,
                macro_summary=macro_summary, abstain_reason=prep["deterministic_abstain_reason"],
            )
            narrative = render_narrative(payload, bundle)
            record = {**base_record, "status": STATUS_ABSTAINED, "market_direction": None,
                      "structured_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                      "narrative_md": narrative, "logical_calls": 0, "actual_requests": 0,
                      "retry_count": 0, "prompt_chars": len(prep["payload_text"]),
                      "model_provider": (model_config or {}).get("provider"),
                      "model_name": (model_config or {}).get("model")}
            saved = self.store.save(record)
            return {**saved, "status": STATUS_ABSTAINED, "logical_calls": 0,
                    "actual_requests": 0, "retry_count": 0, "payload": payload}

        if model_config is None:
            return {"status": "CONFIG_MISSING", "message": "模型配置不可用（forecast 角色未启用）",
                    "logical_calls": 0, "actual_requests": 0, "retry_count": 0}

        call = self.call_model(prep["payload_text"], model_config=model_config)
        counters = {
            "logical_calls": 1,
            "actual_requests": int(call.get("requests") or 0),
            "retry_count": int(call.get("retries") or 0),
        }
        if not call.get("ok"):
            payload = self._structured_payload(
                prep=prep, bundle=bundle, model_output=None, report=None,
                macro_summary=macro_summary, model_error=call.get("detail") or call.get("error_class"),
            )
            narrative = render_narrative(payload, bundle)
            record = {**base_record, "status": STATUS_MODEL_FAILED, "market_direction": None,
                      "structured_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                      "narrative_md": narrative, "model_latency_ms": call.get("latency_ms"),
                      "prompt_chars": len(prep["payload_text"]), "output_chars": 0,
                      "model_provider": model_config.get("provider"),
                      "model_name": model_config.get("model"), **counters}
            saved = self.store.save(record)
            return {**saved, "status": STATUS_MODEL_FAILED, **counters, "payload": payload,
                    "error_class": call.get("error_class")}

        raw_text = str(call.get("raw_text") or "")
        try:
            model_output = _parse_json_object(raw_text)
        except (ValueError, json.JSONDecodeError) as exc:
            payload = self._structured_payload(
                prep=prep, bundle=bundle, model_output=None, report=None,
                macro_summary=macro_summary, model_error=f"JSON 解析失败：{exc}",
            )
            narrative = render_narrative(payload, bundle)
            record = {**base_record, "status": STATUS_INVALID_OUTPUT, "market_direction": None,
                      "structured_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                      "narrative_md": narrative, "model_latency_ms": call.get("latency_ms"),
                      "prompt_chars": len(prep["payload_text"]), "output_chars": len(raw_text),
                      "model_provider": model_config.get("provider"),
                      "model_name": model_config.get("model"), **counters}
            saved = self.store.save(record)
            return {**saved, "status": STATUS_INVALID_OUTPUT, **counters, "payload": payload}

        validator = ForecastValidator(
            evidence_catalog=prep["evidence_catalog"],
            candidate_ids={row["industry_id"] for row in (*prep["candidates"]["strong"], *prep["candidates"]["weak"])},
            industry_names={row["industry_id"]: row["name"] for row in prep["candidates"]["strong"] + prep["candidates"]["weak"]},
            market_features=(bundle.get("market") or {}).get("benchmark") or {},
            alias_map=prep.get("alias_map") or {},
            candidate_alias=prep.get("candidate_alias") or {},
        )
        report = validator.validate(model_output)
        # 语义守则 V1：结构校验后追加确定性语义审计（0 LLM）。
        from src.macro_forecast.semantic import SemanticGuard, annotate_catalog

        catalog_types = annotate_catalog(prep["evidence_catalog"])
        semantic_report = SemanticGuard(
            catalog_types=catalog_types, alias_map=prep.get("alias_map") or {},
        ).run(model_output, report.get("industry_entries") or [])
        for entry, audit in zip(report.get("industry_entries") or [], semantic_report["industries"]):
            entry["reason_basis"] = audit["reason_basis"]
        abstained = bool(model_output.get("abstain"))
        if not report["valid"]:
            payload = self._structured_payload(
                prep=prep, bundle=bundle, model_output=model_output, report=report,
                macro_summary=macro_summary,
            )
            narrative = render_narrative(payload, bundle)
            record = {**base_record, "status": STATUS_INVALID_OUTPUT,
                      "market_direction": (model_output.get("market") or {}).get("direction"),
                      "structured_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                      "narrative_md": narrative, "model_latency_ms": call.get("latency_ms"),
                      "prompt_chars": len(prep["payload_text"]), "output_chars": len(raw_text),
                      "model_provider": model_config.get("provider"),
                      "model_name": model_config.get("model"), **counters}
            saved = self.store.save(record)
            return {**saved, "status": STATUS_INVALID_OUTPUT, **counters, "payload": payload,
                    "validation": report}

        direction = "ABSTAIN" if abstained else (model_output.get("market") or {}).get("direction")
        final_status = STATUS_ABSTAINED if abstained else (
            STATUS_OFFICIAL if run_mode == "OFFICIAL" else run_mode)
        payload = self._structured_payload(
            prep=prep, bundle=bundle, model_output=model_output, report=report,
            macro_summary=macro_summary, semantic_report=semantic_report,
        )
        narrative = render_narrative(payload, bundle)
        record = {**base_record, "status": final_status, "market_direction": direction,
                  "structured_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                  "narrative_md": narrative, "model_latency_ms": call.get("latency_ms"),
                  "prompt_chars": len(prep["payload_text"]), "output_chars": len(raw_text),
                  "model_provider": model_config.get("provider"),
                  "model_name": model_config.get("model"),
                  "published_at": _now() if final_status == STATUS_OFFICIAL else None, **counters}
        saved = self.store.save(record)
        return {**saved, "status": final_status, **counters, "payload": payload,
                "validation": report, "direction": direction}

    def _structured_payload(self, *, prep: dict[str, Any], bundle: dict[str, Any],
                            model_output: dict[str, Any] | None, report: dict[str, Any] | None,
                            macro_summary: dict[str, Any] | None,
                            abstain_reason: str | None = None,
                            model_error: str | None = None,
                            semantic_report: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "versions": {
                "forecast_formula_version": FORECAST_FORMULA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "renderer_version": RENDERER_VERSION,
                "candidate_rules_version": CANDIDATE_RULES_VERSION,
                "feature_projection_version": FEATURE_PROJECTION_VERSION,
                "evidence_catalog_version": EVIDENCE_CATALOG_VERSION,
            },
            "input": {
                "bundle_id": prep["bundle_id"], "fingerprint": prep["fingerprint"],
                "target_trade_date": prep["target_trade_date"],
                "data_mode": bundle.get("data_mode"),
                "gaps": bundle.get("gaps"),
                "industry_eligible_count": prep["industry_eligible_count"],
                "industry_total": prep["industry_total"],
                "prompt_hash": prep.get("prompt_hash"),
                "prompt_size": prep.get("prompt_size"),
                "alias_map": prep.get("alias_map") or {},
                "candidate_alias": prep.get("candidate_alias") or {},
                "evidence_values": (prep.get("context") or {}).get("ev") or {},
                "evidence_catalog": prep.get("evidence_catalog") or {},
            },
            "market_context": (bundle.get("market") or {}).get("benchmark"),
            "macro_context": macro_summary,
            "model_output": model_output,
            "validation": report,
            "semantic": semantic_report,
            "abstain_reason": abstain_reason,
            "model_error": model_error,
            "theme_forecast": None,
        }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
