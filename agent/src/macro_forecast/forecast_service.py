"""联合预测编排（任务卡2）：上下文装配、模式门控、单飞、只读查询。

模式（§44-§46）：
- dry-run：只检查输入包/日历/新鲜度/候选/模型配置；0 模型、0 留档；
- shadow：允许真实模型调用并留档（SHADOW），不计入正式统计；
- official：须日历确认 + 时间窗 + 全部数据 Gate + 人工批准（本阶段不做定时）。

读取路径 get_latest_macro_forecast：0 LLM、0 网络（§41）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.macro_forecast.bars import ForecastBarStore
from src.macro_forecast.bundle import ForecastBundleStore
from src.macro_forecast.contracts import SHANGHAI, next_trading_day
from src.macro_forecast.engine import (
    ForecastEngine, MODEL_HARD_DEADLINE_SECONDS,
)
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.registry import PRIMARY_BENCHMARK, InstrumentRegistryStore
from src.macro_forecast.service import load_industry_rows, load_trading_days

FORECAST_MODEL_ROLE = "risk"  # V1 选定运行配置（runtime-benchmark-v1 §4）：glm-5.3 + 关闭深度思考
# 运行稳定性 V1 实测：GLM 深度思考在 45/60s 内无法完成本任务；关闭后 9.7—12.3s 稳定。
DEFAULT_MODEL_EXTRA_BODY = {"thinking": {"type": "disabled"}}


def _iso(previous_yyyymmdd: str) -> str:
    text = str(previous_yyyymmdd or "").replace("-", "")[:8]
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else ""


def load_macro_context(previous_date: str, *, db_path: Any = None) -> dict[str, Any]:
    """P 日可见宏观上下文：最新快照 + P 日事件 + PIT 口径说明（0 LLM/0 网络）。"""
    path = Path(db_path or (get_runtime_root() / "research.db"))
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        try:
            row = connection.execute(
                "SELECT as_of, status, regime, axes_json FROM macro_snapshots "
                "WHERE as_of<=? AND status IN ('ready','partial') ORDER BY as_of DESC LIMIT 1",
                (_iso(previous_date),),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None  # 库内无快照表（如空测试库/全新环境）→ 如实返回空上下文
        axes: dict[str, Any] = {}
        if row and row["axes_json"]:
            try:
                axes = dict(json.loads(row["axes_json"]))
            except (TypeError, ValueError):
                axes = {}
        events: list[dict[str, Any]] = []
        if row:
            for event in connection.execute(
                "SELECT * FROM macro_events WHERE research_as_of=? ORDER BY created_at LIMIT 5",
                (_iso(previous_date),),
            ).fetchall():
                try:
                    from src.macro_line.events import event_to_chinese

                    text = event_to_chinese(dict(event))
                except Exception:
                    text = f"{event['event_type']} {event['from_value']}->{event['to_value']}"
                events.append({
                    "research_as_of": event["research_as_of"],
                    "event_type": event["event_type"], "axis_key": event["axis_key"],
                    "from_value": event["from_value"], "to_value": event["to_value"],
                    "text": text,
                })
        return {
            "snapshot_as_of": row["as_of"] if row else None,
            "regime": row["regime"] if row else None,
            "axes": axes,
            "events": events,
            "pit_caveat": "宏观快照按 P 日可见口径读取；序列为日期级发布保守边界（FORWARD_OBSERVED）。",
        }
    finally:
        connection.close()


def resolve_model_config(*, role: str = FORECAST_MODEL_ROLE, db_path: Any = None) -> dict[str, Any] | None:
    """复用 agent_model_configs 角色配置；未启用或凭据不全 → None（引擎按契约降级）。"""
    from src.research_tasks.store import ResearchTaskStore

    store = ResearchTaskStore(str(Path(db_path or (get_runtime_root() / "research.db"))))
    try:
        config = store.get_runtime_config(role)
    except KeyError:
        return None
    finally:
        store.close()
    if not (config.get("enabled") and config.get("model")):
        return None
    return {
        "provider": config.get("provider"), "model": config.get("model"),
        "base_url": config.get("base_url"), "api_key": config.get("api_key"),
        "role": role,
    }


def _load_bundle_for_target(target_date: str, *, research_db: Any) -> dict[str, Any] | None:
    store = ForecastBundleStore(research_db)
    try:
        latest = store.latest_for_target(target_date)
        return (latest or {}).get("payload")
    finally:
        store.close()


def prepare_forecast_inputs(
    *,
    target_date: str,
    research_db: Any = None,
    bundle: dict[str, Any] | None = None,
    candidate_limit: int | None = None,
) -> dict[str, Any]:
    """装配预测全部确定性输入：bundle + K线 + 行业注册 + 宏观上下文 + 引擎 prep。"""
    research_db = research_db or (get_runtime_root() / "research.db")
    bundle = bundle or _load_bundle_for_target(target_date, research_db=research_db)
    if bundle is None:
        return {"status": "NO_BUNDLE",
                "message": f"目标 {target_date} 无已冻结输入包；先运行 build_forecast_input_bundle.py"}
    engine = ForecastEngine(ForecastStore(research_db))
    registry = InstrumentRegistryStore(research_db)
    bar_store = ForecastBarStore(research_db)
    try:
        industry_rows = load_industry_rows(registry)
        previous_date = str((bundle.get("target") or {}).get("previous_date") or "")
        from src.macro_forecast.registry import REFERENCE_INDEXES as _REF_INDEXES

        codes = ([PRIMARY_BENCHMARK] + [code for code, _ in _REF_INDEXES]
                 + [row["code"] for row in industry_rows])
        bars_map = bar_store.read_bars(codes, end_date=previous_date or "99999999", count=320)
        macro_context = load_macro_context(previous_date, db_path=research_db)
        bundle_id = f"{(bundle.get('target') or {}).get('target_date')}@{str(bundle.get('fingerprint'))[-12:]}"
        # prompt v2.1：从输入包 cross_market_context 提取官方中间价摘要（唯一 USE_NOW 项；
        # 旧版输入包无该字段 → None → payload 与 v2 语义一致，向后兼容）。
        from src.macro_forecast.prompt import build_cny_mid_summary

        cross_market_summary = build_cny_mid_summary(bundle.get("cross_market_context") or {})
        prep = engine.prepare(
            bundle=bundle, bars_map=bars_map, industry_rows=industry_rows,
            macro_context=macro_context, bundle_id=bundle_id, candidate_limit=candidate_limit,
            cross_market_summary=cross_market_summary,
        )
        return {"status": "PREPARED", "engine": engine, "prep": prep, "bundle": bundle,
                "macro_context": macro_context, "industry_rows": industry_rows}
    finally:
        registry.close()
        bar_store.close()


def run_macro_forecast(
    *,
    target_date: str | None = None,
    mode: str = "dry-run",
    model_role: str = FORECAST_MODEL_ROLE,
    research_db: Any = None,
    trading_days: list[str] | None = None,
    bundle: dict[str, Any] | None = None,
    model_extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """dry-run 只检查；shadow 真调用留档；official 须全部 Gate + 人工批准。"""
    research_db = research_db or (get_runtime_root() / "research.db")
    if target_date is None:
        days = trading_days if trading_days is not None else load_trading_days()
        target_date = next_trading_day(datetime.now().strftime("%Y%m%d"), days) or ""
        if not target_date:
            return {"status": "CALENDAR_UNAVAILABLE", "message": "日历未覆盖当前日之后的交易日"}
    target_date = str(target_date).replace("-", "")

    prepared = prepare_forecast_inputs(target_date=target_date, research_db=research_db, bundle=bundle)
    if prepared["status"] != "PREPARED":
        return prepared
    prep, bundle = prepared["prep"], prepared["bundle"]

    if mode == "dry-run":
        model_config = resolve_model_config(role=model_role, db_path=research_db)
        return {
            "status": "DRY_RUN",
            "target": target_date,
            "bundle": {"bundle_id": prep["bundle_id"], "fingerprint": prep["fingerprint"]},
            "market_ready": prep["gates"]["benchmark_ready"],
            "macro_direction_ok": prep["gates"]["macro_direction_ok"],
            "industry_eligible_count": prep["industry_eligible_count"],
            "industry_total": prep["industry_total"],
            "candidate_strong": len(prep["candidates"]["strong"]),
            "candidate_weak": len(prep["candidates"]["weak"]),
            "data_gaps": bundle.get("gaps"),
            "calendar_status": (bundle.get("target") or {}).get("calendar_status"),
            "deterministic_abstain": prep["deterministic_abstain"],
            "deterministic_abstain_reason": prep["deterministic_abstain_reason"],
            "would_call_llm": prep["would_call_llm"],
            "max_requests": 2 if prep["would_call_llm"] else 0,
            "model_config_ready": model_config is not None,
            "prompt_size": prep["prompt_size"],
        }

    if mode not in {"shadow", "draft", "official"}:
        raise ValueError(f"未知模式：{mode}")
    run_mode = mode.upper() if mode != "shadow" else "SHADOW"
    if run_mode == "OFFICIAL":
        calendar_status = (bundle.get("target") or {}).get("calendar_status")
        if calendar_status != "CALENDAR_CONFIRMED":
            return {"status": "BLOCKED_CALENDAR_UNVERIFIED",
                    "message": "目标日未获交易日历确认，仅允许 DRAFT/SHADOW（§二/§三十八）"}
        publish_deadline = (bundle.get("target") or {}).get("publish_deadline")
        if publish_deadline:
            now = datetime.now(SHANGHAI)
            deadline = datetime.fromisoformat(str(publish_deadline))
            if now > deadline:
                return {"status": "BLOCKED_TIME_WINDOW_PASSED",
                        "message": f"已过发布截止 {publish_deadline}，正式锁定不再补写（§4.3）"}

    model_config = resolve_model_config(role=model_role, db_path=research_db)
    effective_extra = model_extra_body if model_extra_body is not None else DEFAULT_MODEL_EXTRA_BODY
    if model_config is not None and effective_extra:
        model_config = {**model_config, "extra_body": effective_extra}
    engine: ForecastEngine = prepared["engine"]
    owner = f"run-{uuid.uuid4().hex[:8]}"
    lease_key = f"forecast:{run_mode}:{target_date}:{prep['fingerprint']}"
    lease = engine.store.acquire_lease(
        lease_key, owner=owner, ttl_seconds=MODEL_HARD_DEADLINE_SECONDS + 30)
    if not lease.get("acquired"):
        return {"status": "SINGLE_FLIGHT_BUSY", "message": "同目标正在运行（单飞保护）"}
    try:
        return engine.run(
            prep=prep, bundle=bundle, run_mode=run_mode, model_config=model_config,
            macro_summary=prepared["macro_context"],
        )
    finally:
        engine.store.release_lease(lease_key, owner=owner)
        engine.store.close()


def get_latest_macro_forecast(*, research_db: Any = None,
                              exclude_experiment_arm: bool = False) -> dict[str, Any] | None:
    """只读最新预测留档：0 LLM、0 网络（§41）。"""
    store = ForecastStore(research_db or (get_runtime_root() / "research.db"))
    try:
        row = store.latest(exclude_experiment_arm=exclude_experiment_arm)
        if not row:
            return None
        result = dict(row)
        try:
            result["structured_payload"] = json.loads(result.pop("structured_payload_json") or "{}")
        except (TypeError, ValueError):
            result["structured_payload"] = {}
        return result
    finally:
        store.close()
