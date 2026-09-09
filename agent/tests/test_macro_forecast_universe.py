"""预测目标标的注册表：分类、冻结、不可变（任务卡1 §3；主规格 §7.2 §7.4）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.macro_forecast.registry import (
    PRIMARY_BENCHMARK, REGISTRY_VERSION, ROLE_EXCLUDED, ROLE_INDUSTRY_TARGET,
    ROLE_PRIMARY_BENCHMARK, ROLE_THEME_TARGET, InstrumentRegistryStore,
    build_registry_rows, classify_board,
)

SECTORS = [
    {"code": "880081.SH", "name": "轮动趋势"},
    {"code": "880201.SH", "name": "黑龙江"},
    {"code": "880506.SH", "name": "5G概念"},
    {"code": "880801.SH", "name": "基金重仓"},
    {"code": "880875.SH", "name": "中小银行"},
    {"code": "880899.SH", "name": "钴金属"},
    {"code": "880910.SH", "name": "特斯拉概念"},
    {"code": "881016.SH", "name": "煤炭开采"},
    {"code": "881306.SH", "name": "半导体"},
    {"code": "881416.SH", "name": "房地产开发"},
]


def test_classify_board_buckets() -> None:
    assert classify_board("880081.SH", "轮动趋势")[0] == "SPECIAL"
    assert classify_board("880201.SH", "黑龙江")[0] == "REGION"
    assert classify_board("880801.SH", "基金重仓")[0] == "STYLE"
    assert classify_board("880875.SH", "中小银行")[0] == "THEME"   # 8808xx 经济主题白名单
    assert classify_board("880899.SH", "钴金属")[0] == "THEME"
    assert classify_board("880506.SH", "5G概念")[0] == "THEME"
    assert classify_board("880910.SH", "特斯拉概念")[0] == "THEME"
    assert classify_board("881016.SH", "煤炭开采")[0] == "INDUSTRY_L2"
    assert classify_board("881416.SH", "房地产开发")[0] == "INDUSTRY_L2"


def test_classify_board_invalid_code_is_unclassified() -> None:
    assert classify_board("junk", "任意")[0] == "UNCLASSIFIED"


def test_registry_rows_roles_and_benchmark() -> None:
    rows = build_registry_rows(SECTORS)
    by_code = {row["code"]: row for row in rows if row["code"]}
    assert by_code[PRIMARY_BENCHMARK]["role"] == ROLE_PRIMARY_BENCHMARK
    assert by_code["881016.SH"]["role"] == ROLE_INDUSTRY_TARGET
    assert by_code["880506.SH"]["role"] == ROLE_THEME_TARGET
    assert by_code["880801.SH"]["role"] == ROLE_EXCLUDED
    assert by_code["880201.SH"]["role"] == ROLE_EXCLUDED
    assert by_code[PRIMARY_BENCHMARK]["history_target_days"] == 500
    assert by_code["881016.SH"]["history_target_days"] == 250
    assert by_code["880506.SH"]["history_target_days"] == 0  # V1 主题只分类不回补


def test_registry_rows_include_reference_and_cross_market_audit() -> None:
    rows = build_registry_rows(SECTORS)
    references = [row for row in rows if row["role"] == "REFERENCE"]
    audits = [row for row in rows if row["role"] == "CROSS_MARKET_AUDIT"]
    assert len(references) == 9
    assert len(audits) == 5  # HSI/DJI/IXIC/SPX/USDCNY，均登记为缺口
    assert all(row["status"] == "GAP" for row in audits)


def test_registry_unique_instrument_ids() -> None:
    rows = build_registry_rows(SECTORS)
    ids = [row["instrument_id"] for row in rows]
    assert len(ids) == len(set(ids))


@pytest.fixture()
def store(tmp_path: Path) -> InstrumentRegistryStore:
    return InstrumentRegistryStore(tmp_path / "test.db")


def test_freeze_is_immutable_same_version(store: InstrumentRegistryStore) -> None:
    first = store.freeze(build_registry_rows(SECTORS), registry_version=REGISTRY_VERSION)
    assert first["status"] == "FROZEN"
    second = store.freeze(build_registry_rows(SECTORS[:3]), registry_version=REGISTRY_VERSION)
    assert second["status"] == "ALREADY_FROZEN"
    assert second["instrument_count"] == first["instrument_count"]
    # 原冻结内容未被改写
    assert len(store.load(registry_version=REGISTRY_VERSION)) == first["instrument_count"]


def test_freeze_new_version_appends_without_touching_old(store: InstrumentRegistryStore) -> None:
    store.freeze(build_registry_rows(SECTORS), registry_version=REGISTRY_VERSION)
    store.freeze(build_registry_rows(SECTORS[:2]), registry_version="forecast-instrument-registry-v1.1.0")
    assert len(store.load(registry_version=REGISTRY_VERSION)) == len(build_registry_rows(SECTORS))
    assert len(store.industry_targets(registry_version=REGISTRY_VERSION)) == 3
    assert store.latest_version() == "forecast-instrument-registry-v1.1.0"


def test_production_shape_snapshot() -> None:
    """生产 587 板块形状的自检（离线运行，不连生产库）。"""
    import sqlite3

    db = Path(r"C:\Users\Administrator\.vibe-trading\tdx_data.db")
    if not db.exists():
        pytest.skip("生产 tdx_data.db 不在本机")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT record_key, name FROM records WHERE dataset='sectors'"
        ).fetchall()
    finally:
        connection.close()
    sectors = [{"code": key, "name": name} for key, name in rows]
    registry = build_registry_rows(sectors)
    counts: dict[str, int] = {}
    for row in registry:
        counts[row["entity_type"]] = counts.get(row["entity_type"], 0) + 1
    assert len(sectors) == 587
    assert counts["INDUSTRY_L2"] == 128
    assert counts["REGION"] == 32
    assert counts["SPECIAL"] == 2
    assert counts["INDEX_BENCHMARK"] == 1
    assert counts["INDEX_REFERENCE"] == 9
    # THEME = 8805/8806/8807/8809（88+98+90+65=341）+ 8808xx 白名单 2 = 343
    assert counts["THEME"] == 343
    assert counts["STYLE"] == 82
