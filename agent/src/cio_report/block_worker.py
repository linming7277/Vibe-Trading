"""CIO 分块后台 worker（V1：PRICE + FINANCIAL 两种触发，确定性写，零 LLM）。

事件队列 cio_block_jobs（research.db）；universe = 最新 l3_leader_pool 成员，
池外事件丢弃；同一 (code, block_id, fingerprint) 已 QUEUED/SUCCESS 不重复入队。
worker 单线程逐条消费，单只失败继续；写回只替换对应节、原地更新
（保留 previous_report_id 链）。默认不启动，env HZ_CIO_BLOCK_WORKER=on 才拉起。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

WORKER_ENV = "HZ_CIO_BLOCK_WORKER"
DEFAULT_INTERVAL_S = 30.0

# 块 → CIO 报告 section_type（FINANCIAL 只更新数字表、叙述标 STALE）
BLOCK_SECTIONS: dict[str, tuple[str, ...]] = {
    "PRICE": ("valuation",),
    "FINANCIAL": ("financial_path", "latest_quarter"),
}


def pool_universe() -> set[str]:
    """最新 l3_leader_pool 在席成员（NEW/ACTIVE/REENTERED）。"""
    import sqlite3

    conn = sqlite3.connect(os.path.join(
        os.environ.get("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading"), "research.db"))
    try:
        pool = conn.execute(
            "SELECT id FROM l3_leader_pool_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if not pool:
            return set()
        member_rows = conn.execute(
            "SELECT stock_code FROM l3_leader_pool_members WHERE pool_id=? "
            "AND lifecycle_status IN ('NEW','ACTIVE','REENTERED')", (pool[0],)).fetchall()
        return {str(r[0]).upper() for r in member_rows}
    finally:
        conn.close()


def price_fingerprints(codes: list[str]) -> dict[str, str]:
    """每只的廉价价格指纹 = 最新一根日线收盘/日期（一次聚合查询）。"""
    if not codes:
        return {}
    conn = sqlite3.connect(os.path.join(
        os.environ.get("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading"), "tdx_data.db"))
    conn.row_factory = sqlite3.Row
    try:
        latest = conn.execute("SELECT MAX(trade_date) FROM adjusted_daily_bars").fetchone()[0]
        if not latest:
            return {}
        rows = conn.execute(
            "SELECT stock_code, close FROM adjusted_daily_bars WHERE trade_date=?", (latest,)).fetchall()
    finally:
        conn.close()
    wanted = {c.upper() for c in codes}
    return {r["stock_code"]: f"{latest}:{r['close']}" for r in rows if r["stock_code"] in wanted}


def financial_fingerprints(codes: list[str]) -> dict[str, str]:
    """每只的财务指纹 = 财务快照的历史截止期（= 最新报告期口径）。"""
    import sqlite3

    conn = sqlite3.connect(os.path.join(
        os.environ.get("VIBE_TRADING_HOME", r"C:\Users\Administrator\.vibe-trading"), "research.db"))
    conn.row_factory = sqlite3.Row
    try:
        # 必须先 fetchall 再关连接：cursor 惰性加载，close 后迭代会抛
        # sqlite3.ProgrammingError（2026-09-15 修复）。
        rows = conn.execute(
            "SELECT stock_code, historical_cutoff, updated_at FROM company_financial_analysis_snapshots"
        ).fetchall()
    finally:
        conn.close()
    wanted = {c.upper() for c in codes}
    fingerprints: dict[str, str] = {}
    for row in rows:
        code = str(row["stock_code"] or "").upper()
        if code in wanted:
            fingerprints[code] = f"{row['historical_cutoff']}@{row['updated_at']}"
    return fingerprints


def _qualified_close_date() -> str | None:
    """TDX 最新合格收盘日；拿不到 → None（本轮 0 入队、0 写库）。"""
    try:
        from src.tdx_data import get_tdx_service

        _ready, _reason, snapshot = get_tdx_service().latest_qualified_close_snapshot()
        market_date = str((snapshot or {}).get("market_date") or "")[:10]
        return market_date or None
    except Exception:  # noqa: BLE001 - 缺数据就停，绝不发明日期
        logger.error("cio block enqueue skipped: TDX qualified close snapshot unavailable")
        return None


def enqueue_eod_block_jobs(as_of: str | None = None, *, store=None) -> dict[str, Any]:
    """EOD 触发器（fail-soft 由调用方兜底）：新报告期入 FINANCIAL。

    PRICE 的 EOD 主路径是 ``refresh_cio_price_blocks`` 直接刷（确定性、
    指纹幂等），不在此重复入队——避免同一只票被写两次报告。PRICE 入队
    仅保留给非 EOD 场景（如盘后补数）显式调用 ``enqueue_price_job``。
    """
    from src.cio_report.store import CioReportStore

    latest_close = _qualified_close_date()
    if latest_close is None:
        return {"queued": 0, "discarded": 0, "universe": 0, "status": "TDX_NOT_READY"}
    as_of = str(as_of or latest_close)[:10]

    store = store or CioReportStore()
    universe = pool_universe()
    if not universe:
        return {"queued": 0, "discarded": 0, "universe": 0}
    queued = 0
    fin_map = financial_fingerprints(sorted(universe))
    for code in sorted(universe):
        fin_fp = fin_map.get(code)
        if fin_fp:
            last = store.latest_block_fingerprint(code, "FINANCIAL")
            last_period = str(last or "").split("@")[0]
            new_period = str(fin_fp).split("@")[0]
            # 仅当报告期（历史截止期）比已处理指纹更新才入队
            if new_period > last_period:
                queued += store.enqueue_block_job(code, "FINANCIAL", "EOD_FINANCIAL", as_of, fin_fp) == "QUEUED"
    return {"queued": queued, "universe": len(universe), "discarded": 0}


def enqueue_price_job(code: str, fingerprint: str, *, as_of: str, trigger: str = "BACKFILL_PRICE",
                      store=None) -> str:
    """非 EOD 场景（盘后补数）对单只入 PRICE 任务；重复指纹返回 DUPLICATE。"""
    from src.cio_report.store import CioReportStore

    store = store or CioReportStore()
    if code.upper() not in pool_universe():
        return "OUT_OF_POOL"
    return store.enqueue_block_job(code.upper(), "PRICE", trigger, as_of, fingerprint)


def refresh_financial_blocks(as_of: str | None = None, *, store=None) -> dict[str, Any]:
    """EOD 直接刷龙头池 FINANCIAL 块（同步 enqueue+立即消费，不需要 worker 线程）。

    期次未变 → 指纹相同全部复用、零写入；报告期更新 → 确定性重建
    financial_path/latest_quarter 节、叙述标 STALE，零 LLM。
    """
    from src.cio_report.store import CioReportStore

    latest_close = _qualified_close_date()
    if latest_close is None:
        logger.error("cio financial block refresh skipped: TDX qualified close not ready")
        return {"status": "TDX_NOT_READY", "queued": 0, "processed": 0, "universe": 0}
    as_of = str(as_of or latest_close)[:10]
    store = store or CioReportStore()
    worker = CioBlockWorker(store=store)
    universe = pool_universe()
    if not universe:
        return {"status": "COMPLETED", "queued": 0, "processed": 0, "universe": 0}
    fin_map = financial_fingerprints(sorted(universe))
    queued = 0
    for code in sorted(universe):
        fp = fin_map.get(code)
        if fp and store.latest_block_fingerprint(code, "FINANCIAL") != fp:
            queued += store.enqueue_block_job(code, "FINANCIAL", "EOD_FINANCIAL", as_of, fp) == "QUEUED"
    processed = worker.process_queued()
    return {"status": "COMPLETED", "queued": queued, "processed": processed,
            "as_of": as_of, "universe": len(universe)}


class CioBlockWorker:
    """单线程后台消费 QUEUED 分块任务；单只失败继续。"""

    def __init__(self, *, interval_s: float = DEFAULT_INTERVAL_S, store=None) -> None:
        from src.cio_report.store import CioReportStore

        self.interval_s = float(interval_s)
        self.store = store or CioReportStore()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="cio-block-worker")
        self._thread.start()
        logger.info("CioBlockWorker started (interval=%ss)", self.interval_s)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.process_queued()
            except Exception:  # noqa: BLE001 - worker 永不退出
                logger.exception("cio block worker cycle failed")

    def process_queued(self, limit: int = 20) -> int:
        jobs = self.store.queued_block_jobs(limit)
        for job in jobs:
            code, block_id = str(job["code"]), str(job["block_id"])
            if code not in pool_universe():
                self.store.finish_block_job(int(job["job_id"]), "DISCARDED")
                continue
            try:
                self.refresh_block(code, block_id, as_of=str(job["as_of"]))
                self.store.finish_block_job(int(job["job_id"]), "SUCCESS")
            except Exception as exc:  # noqa: BLE001 - 单只失败继续
                logger.warning("cio block job failed code=%s block=%s: %s", code, block_id, exc)
                self.store.finish_block_job(int(job["job_id"]), "FAILED")
        return len(jobs)

    def refresh_block(self, code: str, block_id: str, *, as_of: str) -> dict[str, Any]:
        """确定性刷新对应节。PRICE 统一走 service.refresh_cio_block（含块指纹
        契约与幂等自愈，与 EOD 的 refresh_cio_price_blocks 同一实现——指纹
        相同即 REUSED，绝不双写）；FINANCIAL 只更新数字节、叙述标 STALE，
        无确定性摘要能力时按 STALE 降级，禁止新开 LLM。"""
        if block_id == "PRICE":
            from src.cio_report.service import get_cio_report_service

            result = get_cio_report_service().refresh_cio_block("CN", code, "PRICE", as_of=as_of)
            status = str(result.get("status") or "")
            if status == "REUSED":
                return {"status": "REUSED", "code": code, "block": block_id}
            if status == "REFRESHED":
                return {"status": "UPDATED", "code": code, "block": block_id,
                        "sections": list(BLOCK_SECTIONS[block_id])}
            return {"status": f"SKIPPED_{status}", "code": code, "block": block_id}

        from src.cio_report.builder import CioSectionBuilder

        builder = CioSectionBuilder("CN", code, as_of)
        builders = {
            "FINANCIAL": [builder.build_financial_path, builder.build_latest_quarter],
        }
        if block_id not in builders:
            raise ValueError(f"unknown block {block_id}")
        report = self.store.latest_report("CN", code, as_of=as_of)
        if not report:
            return {"status": "SKIPPED_NO_REPORT", "code": code, "block": block_id}
        for build in builders[block_id]:
            section = build()
            section["freshness_status"] = "STALE"
            self.store.update_report_section(int(report["id"]), section, keep_narrative=True)
        return {"status": "UPDATED", "code": code, "block": block_id,
                "sections": list(BLOCK_SECTIONS[block_id])}


_worker: CioBlockWorker | None = None


def start_cio_block_worker(*, interval_s: float = DEFAULT_INTERVAL_S) -> dict[str, Any]:
    """backend 启动挂点：env HZ_CIO_BLOCK_WORKER=on 才拉起，默认 off。"""
    global _worker
    if os.environ.get(WORKER_ENV, "off").strip().lower() != "on":
        return {"started": False, "reason": f"{WORKER_ENV}!=on"}
    if _worker is None:
        _worker = CioBlockWorker(interval_s=interval_s)
    _worker.start()
    return {"started": True, "interval_s": _worker.interval_s}
