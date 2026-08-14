"""Built-in research and deterministic strategy schedules."""

from __future__ import annotations

import time

from src.scheduled_research.executor import next_due
from src.scheduled_research.models import JobStatus, ScheduledResearchJob

WORKSPACE_SCHEDULES = (
    {
        "id": "hengzhi-macro-morning",
        "schedule": "30 7 * * *",
        "prompt": (
            "生成A股与港股每日宏观研究简报。所有事实必须带来源与data_as_of；"
            "只解释宏观环境，不得自行填写策略引擎基础分。"
        ),
        "config": {"workspace_refresh": "macro", "markets": ["CN", "HK"]},
    },
    {
        "id": "hengzhi-cn-hk-close",
        "schedule": "30 16 * * 1-5",
        "prompt": (
            "收盘后分别调用run_strategy_engine，运行CN/HK的value和emotion两条确定性策略线。"
            "不得自行填写行业分、候选分或情绪分。完成后只汇报run_id、数据日期、覆盖率和失败原因。"
        ),
        "config": {"strategy_engine_refresh": "all", "markets": ["CN", "HK"]},
    },
    {
        "id": "hengzhi-value-entry-monitor",
        "schedule": "45 16 * * 1-5",
        "prompt": "运行 A 股价值投资入场监控；只生成提醒和复核任务，不连接实盘下单。",
        "config": {"value_entry_monitor": True, "market": "CN"},
    },
)


def ensure_workspace_schedules(store) -> int:
    """Create missing built-in jobs without overwriting user edits."""
    now_ms = int(time.time() * 1000)
    created = 0
    for spec in WORKSPACE_SCHEDULES:
        if store.get(spec["id"]) is not None:
            continue
        store.upsert(
            ScheduledResearchJob(
                id=spec["id"], prompt=spec["prompt"], schedule=spec["schedule"],
                next_run_at=next_due(spec["schedule"], now_ms, "Asia/Shanghai"),
                status=JobStatus.PENDING, created_at=now_ms, config=dict(spec["config"]),
                timezone="Asia/Shanghai",
            )
        )
        created += 1
    return created
