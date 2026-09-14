"""CIO 分块后台 worker：入队/去重/池过滤/确定性写回/默认关闭。

不打 LLM、不发飞书；价格区与财务节用 monkeypatch 假数据，只验证
队列机制与「只替换对应节、其余节不动」的写回语义。
"""

from __future__ import annotations

import src.cio_report.block_worker as block_worker_module
from src.cio_report.block_worker import (
    CioBlockWorker,
    enqueue_eod_block_jobs,
    start_cio_block_worker,
)
from src.cio_report.store import CioReportStore
from src.strategy_engines import macro_data as macro_data_module
from src.strategy_engines.value_data_store import ValueDataStore

AS_OF = "2026-09-10"


def _store(tmp_path) -> CioReportStore:
    return CioReportStore(tmp_path / "research.db")


def _seed_report(store: CioReportStore, code: str) -> int:
    """最小可写报告：两节（valuation=PRICE 块、financial_path=FINANCIAL 块）。"""
    from src.cio_report.builder import SECTION_TITLES

    def sec(section_type: str, body: str) -> dict:
        return {
            "section_type": section_type, "title": SECTION_TITLES[section_type],
            "input_fingerprint": f"{section_type}-old", "freshness_status": "REFRESHED",
            "structured_payload": {"note": body}, "narrative_md": f"{section_type} 旧叙述 {body}",
            "source_refs": [],
        }

    saved = store.save_report(
        market="CN", stock_code=code, research_as_of=AS_OF,
        overall_freshness="READY", input_fingerprint="old-fp",
        module_hashes={}, sections=[sec("valuation", "价格旧"), sec("financial_path", "财务旧")],
        narrative_report_md="旧叙述", synthesis_source="TEMPLATE",
        formula_version="test", prompt_version="test", model_version="",
        previous_report_id=None,
    )
    return int(saved["id"])


def _install_pool(monkeypatch, codes: list[str]) -> None:
    monkeypatch.setattr(block_worker_module, "pool_universe", lambda: {c.upper() for c in codes})


def _install_price_fp(monkeypatch, mapping: dict[str, str]) -> None:
    monkeypatch.setattr(block_worker_module, "price_fingerprints", lambda codes: mapping)


def _install_fin_fp(monkeypatch, mapping: dict[str, str]) -> None:
    monkeypatch.setattr(block_worker_module, "financial_fingerprints", lambda codes: mapping)


def test_worker_price_consumed_via_refresh_cio_block(tmp_path, monkeypatch) -> None:
    """PRICE 任务由 worker 消费 → service.refresh_cio_block（与 EOD 同一实现，
    指纹幂等防双写）；valuation 更新、financial_path 节保持。"""
    from src.cio_report.blocks import block_fingerprint
    from src.cio_report.builder import CioSectionBuilder
    from src.cio_report.service import CioReportService

    store = _store(tmp_path)
    report_id = _seed_report(store, "003012.SZ")
    _install_pool(monkeypatch, ["003012.SZ"])
    monkeypatch.setattr(block_worker_module, "_qualified_close_date", lambda: "2026-09-10")

    svc = CioReportService(store=store)
    monkeypatch.setattr(
        "src.cio_report.service.get_cio_report_service", lambda: svc)

    zones = {"as_of": "2026-09-10", "price_as_of": "2026-09-10", "current_price": 25.8,
             "position_label": "中性", "plain_summary": "夹具",
             "valuation": {"status": "NEUTRAL", "fair_value_low": 20.0, "fair_value_mid": 25.0,
                           "fair_value_high": 30.0, "methods": []},
             "confluence_zones": [{"low": 24.0, "high": 26.0}]}
    monkeypatch.setattr(CioSectionBuilder, "_zones", lambda self: zones)
    monkeypatch.setattr(CioSectionBuilder, "_financial", lambda self: {})

    fp = block_fingerprint("003012.SZ", {
        "code": "003012.SZ", "close_as_of": "2026-09-10", "close": 25.8,
        "zone_low": 24.0, "zone_high": 26.0, "position_label": "中性"})
    from src.cio_report.block_worker import enqueue_price_job
    assert enqueue_price_job("003012.SZ", fp, as_of=AS_OF, store=store) == "QUEUED"

    worker = CioBlockWorker(store=store)
    assert worker.process_queued() == 1

    latest = store.latest_report("CN", "003012.SZ", as_of=None)
    valuation = next(s for s in latest["sections"] if s["section_type"] == "valuation")
    financial = next(s for s in latest["sections"] if s["section_type"] == "financial_path")
    payload = valuation["structured_payload"]
    assert payload["current_price"] == 25.8
    assert payload["position_label"] == "中性"
    assert payload["block_fingerprints"]["PRICE"] == fp
    # 其它节不动
    assert financial["narrative_md"] == "financial_path 旧叙述 财务旧"
    assert latest["previous_report_id"] == report_id


def test_price_job_duplicate_not_enqueued(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _install_pool(monkeypatch, ["003012.SZ"])
    monkeypatch.setattr(block_worker_module, "_qualified_close_date", lambda: "2026-09-10")
    from src.cio_report.block_worker import enqueue_price_job

    assert enqueue_price_job("003012.SZ", "fp-1", as_of=AS_OF, store=store) == "QUEUED"
    assert enqueue_price_job("003012.SZ", "fp-1", as_of=AS_OF, store=store) == "DUPLICATE"
    assert store.queued_block_jobs()[0]["fingerprint"] == "fp-1"


def test_tdx_not_ready_zero_enqueue_zero_write(tmp_path, monkeypatch) -> None:
    """a. TDX 未就绪 → 本轮 0 入队、0 写库。"""
    store = _store(tmp_path)
    _install_pool(monkeypatch, ["003012.SZ"])
    _install_fin_fp(monkeypatch, {"003012.SZ": "2026-06-30@2026-08-20T00:00:00"})
    monkeypatch.setattr(block_worker_module, "_qualified_close_date", lambda: None)

    result = enqueue_eod_block_jobs(AS_OF, store=store)
    assert result["status"] == "TDX_NOT_READY"
    assert result["queued"] == 0
    assert store.queued_block_jobs() == []
    assert store._conn.execute(
        "SELECT COUNT(*) FROM cio_block_jobs").fetchone()[0] == 0


def test_out_of_pool_discarded(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _install_pool(monkeypatch, ["600216.SH"])  # 池里只有它
    _install_fin_fp(monkeypatch, {"600216.SH": "2026-06-30@x", "999999.SZ": "2026-06-30@x"})
    result = enqueue_eod_block_jobs(AS_OF, store=store)
    assert result["queued"] == 1  # 只有池内 600216 入队
    jobs = store.queued_block_jobs()
    assert all(j["code"] == "600216.SH" for j in jobs)


def test_financial_new_period_enqueues_old_period_not(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _seed_report(store, "003012.SZ")
    _install_pool(monkeypatch, ["003012.SZ"])
    _install_price_fp(monkeypatch, {})
    _install_fin_fp(monkeypatch, {"003012.SZ": "2026-06-30@2026-08-20T00:00:00"})
    result = enqueue_eod_block_jobs(AS_OF, store=store)
    assert result["queued"] == 1
    jobs = store.queued_block_jobs()
    assert [j["block_id"] for j in jobs] == ["FINANCIAL"]
    # 同期次再触发 → 不入队
    assert enqueue_eod_block_jobs(AS_OF, store=store)["queued"] == 0
    # 旧期次（比指纹里的 period 旧）→ 不入队
    _install_fin_fp(monkeypatch, {"003012.SZ": "2026-03-31@2026-04-20T00:00:00"})
    assert enqueue_eod_block_jobs(AS_OF, store=store)["queued"] == 0


def test_default_env_does_not_start_worker(monkeypatch) -> None:
    import src.cio_report.block_worker as bw

    monkeypatch.delenv(bw.WORKER_ENV, raising=False)
    monkeypatch.setattr(bw, "_worker", None)
    result = bw.start_cio_block_worker()
    assert result["started"] is False
    assert bw._worker is None  # 未创建线程


def test_worker_module_never_imports_chatllm() -> None:
    import inspect

    from src.cio_report import block_worker as bw

    source = inspect.getsource(bw)
    for banned in ("ChatLLM", "_synthesize", "analyze(", "ProviderModelRuntime",
                   "run_macro_forecast", "build_report"):
        assert banned not in source, banned
    imported = {name for name in dir(bw) if not name.startswith("__")}
    assert not any("llm" in name.lower() for name in imported)


from src.cio_report.builder import CioSectionBuilder as CioSectionBuilderShim  # noqa: E402
