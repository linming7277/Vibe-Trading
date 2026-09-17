"""LLM 简要分析：宏观总览页「最近 N 天要闻」的三五句摘要。

项目的受控 LLM 点位之一（AGENTS.md 铁律：确定性优先，LLM 只在受控点位）。
设计三原则：

* **缓存优先、页面不阻塞**：分析按内容指纹缓存在 research.db
  （``morning_news_analysis`` 表），6 小时内直接复用；过期后由页面请求触发
  后台线程重新生成——首个请求只拿到 "generating"，刷新后可见。
* **fail-closed**：模型输出先过交易用语审查，不合格即丢弃、不落库，
  页面只显示新闻本体；分析不可用时整块隐藏，绝不降级编造。
* **明确标注**：前端固定渲染「AI 生成 · 仅供参考」标识。

模型配置复用 ``research_lead`` 角色（与 CIO 综合同源，研究员设置页可调）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.paths import get_runtime_root

logger = logging.getLogger(__name__)

SHANGHAI_TZ = timezone(timedelta(hours=8))
ANALYSIS_MAX_AGE_HOURS = 6
_BANNED_RE = re.compile(r"买入|卖出|加仓|减仓|仓位|止盈|止损|目标价|建议(买入|卖出|关注)|开仓|平仓")
_INFLIGHT: set[str] = set()
_INFLIGHT_LOCK = threading.Lock()

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS morning_news_analysis (
    fingerprint TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    content_md TEXT,
    model TEXT,
    days INTEGER,
    error TEXT,
    generated_at TEXT
)
"""


def _conn(research_db_path=None) -> sqlite3.Connection:
    if research_db_path is None:
        research_db_path = get_runtime_root() / "research.db"
    conn = sqlite3.connect(str(research_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_TABLE_SQL)
    return conn


def analysis_fingerprint(news: dict) -> str:
    """按生效新闻集计算指纹：任一标题/日期变化都会改变指纹。"""
    digest = hashlib.sha256()
    for group in news.get("days") or []:
        digest.update(group.get("date", "").encode())
        for side in ("domestic", "overseas"):
            for item in group.get(side) or []:
                digest.update(f"{item.get('title')}|{item.get('url')}".encode())
    return digest.hexdigest()[:24]


def cached_analysis(fingerprint: str, *, research_db_path=None, now=None) -> dict[str, Any] | None:
    now_dt = now or datetime.now(SHANGHAI_TZ)
    cutoff = (now_dt - timedelta(hours=ANALYSIS_MAX_AGE_HOURS)).isoformat(timespec="seconds")
    conn = _conn(research_db_path)
    try:
        row = conn.execute(
            "SELECT content_md, model, generated_at FROM morning_news_analysis "
            "WHERE fingerprint=? AND status='ready' AND generated_at >= ?",
            (fingerprint, cutoff),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"status": "ready", "content_md": str(row["content_md"] or ""),
            "model": str(row["model"] or ""), "generated_at": str(row["generated_at"] or "")}


def _build_prompt_and_payload(news: dict, days: int) -> tuple[str, dict[str, Any]]:
    domestic: list[str] = []
    overseas: list[str] = []
    for group in news.get("days") or []:
        day = str(group.get("date") or "")
        for item in group.get("domestic") or []:
            domestic.append(f"[{day}] {item.get('title')}")
        for item in group.get("overseas") or []:
            overseas.append(f"[{day}] {item.get('title')}")
    instruction = (
        "你是恒值投资的宏观研究员。下面是最近"
        f"{days}天的公开快讯标题（已按国内/海外分组，[日期] 为所属日）。"
        "任务：写给老板看的简要分析，帮他快速把握这几天值得注意的宏观与市场动向。"
        "要求："
        "1) 输出 3-6 条要点，每条以「- 」开头单独一行，总计 150-300 字，先国内后海外；"
        "2) 只概括标题里真实出现的信息与数字，不得新增任何事实、数字或因果推断；"
        "3) 禁止交易表述（买入/卖出/加仓/减仓/仓位/止盈/止损/目标价/建议等），禁止方向预测与操作指引；"
        "4) 全部中文，不用英文缩写（标题原文的专有名词除外）；"
        '5) 输出必须是且仅是一个 JSON 对象：{"text":"<要点内容>"}'
    )
    payload = {"国内": domestic, "海外": overseas}
    return instruction, payload


def generate_news_analysis(news: dict, *, days: int = 3, research_db_path=None, now=None) -> dict[str, Any]:
    """同步生成一次分析并落库；供后台线程与测试直接调用。"""
    now_dt = now or datetime.now(SHANGHAI_TZ)
    fingerprint = analysis_fingerprint(news)
    from src.research_tasks.service import ProviderModelRuntime
    from src.research_tasks.store import ResearchTaskStore

    config = ResearchTaskStore().get_runtime_config("research_lead")
    instruction, payload = _build_prompt_and_payload(news, days)
    runtime = ProviderModelRuntime()
    kwargs: dict[str, Any] = {
        "role": "research_lead", "phase": "MACRO_NEWS_ANALYSIS",
        "model": str(config["model"]), "instruction": instruction, "payload": payload,
        "timeout_seconds": 120, "max_tokens": 800,
    }
    model_name = str(config["model"])
    try:
        if config.get("base_url"):
            output = runtime.invoke_with_connection(
                **kwargs, base_url=str(config["base_url"]), api_key=str(config.get("api_key") or ""))
        else:
            output = runtime.invoke(**kwargs, provider=str(config.get("provider") or "openai"))
        text = str(dict(output).get("text") or "").strip()
    except Exception as exc:  # noqa: BLE001 - 分析失败不影响新闻本体
        logger.warning("news analysis generation failed: %s", exc)
        status, content, error = "failed", "", f"{type(exc).__name__}: {exc}"
    else:
        if not text or _BANNED_RE.search(text):
            status, content, error = "discarded", "", "banned_language" if text else "empty"
        else:
            status, content, error = "ready", text, ""

    conn = _conn(research_db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO morning_news_analysis(fingerprint,status,content_md,model,days,error,generated_at)"
                " VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status, content_md=excluded.content_md,"
                " model=excluded.model, days=excluded.days, error=excluded.error, generated_at=excluded.generated_at",
                (fingerprint, status, content, model_name, int(days), error,
                 now_dt.isoformat(timespec="seconds")),
            )
    finally:
        conn.close()
    return {"status": status, "content_md": content, "generated_at": now_dt.isoformat(timespec="seconds")}


def get_or_schedule_analysis(news: dict, *, days: int = 3, research_db_path=None, now=None) -> dict[str, Any]:
    """路由入口：命中缓存直接返回；过期/缺失时后台生成并返回 generating。"""
    fingerprint = analysis_fingerprint(news)
    cached = cached_analysis(fingerprint, research_db_path=research_db_path, now=now)
    if cached:
        return cached

    key = f"{fingerprint}:{days}"
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return {"status": "generating"}
        _INFLIGHT.add(key)

    def _run() -> None:
        try:
            generate_news_analysis(news, days=days, research_db_path=research_db_path)
        except Exception:  # noqa: BLE001 - 后台线程永不上抛
            logger.exception("news analysis background generation failed")
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(key)

    threading.Thread(target=_run, daemon=True, name="macro-news-analysis").start()
    return {"status": "generating"}
