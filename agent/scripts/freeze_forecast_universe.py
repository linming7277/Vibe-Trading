"""冻结 V1 预测目标标的注册表（任务卡1 §3）。

从生产 tdx_data.db sectors 数据集读取 587 板块，按数值区间+白名单分类，
连同重点指数与跨市场审计结论一并写入 research.db forecast_instruments
（不可变：同版本重复执行返回 ALREADY_FROZEN）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.registry import (
    REGISTRY_VERSION, InstrumentRegistryStore, build_registry_rows,
)


def load_sectors(tdx_db: Path) -> list[dict]:
    connection = sqlite3.connect(f"file:{tdx_db.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT record_key, name, payload_json FROM records WHERE dataset='sectors' ORDER BY record_key"
        ).fetchall()
    finally:
        connection.close()
    sectors = []
    for record_key, name, payload_json in rows:
        code = str(record_key or "").upper()
        if not code:
            continue
        sectors.append({"code": code, "name": str(name or "")})
    return sectors


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结预测目标标的注册表 V1")
    parser.add_argument("--tdx-db", default=str(get_runtime_root() / "tdx_data.db"))
    parser.add_argument("--research-db", default=str(get_runtime_root() / "research.db"))
    args = parser.parse_args()

    tdx_db = Path(args.tdx_db)
    if not tdx_db.exists():
        print(f"[ERROR] tdx 数据库不存在: {tdx_db}")
        return 2
    sectors = load_sectors(tdx_db)
    print(f"sectors 读取: {len(sectors)} 个板块（快照见 tdx_data.db）")

    rows = build_registry_rows(sectors)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["entity_type"]] = counts.get(row["entity_type"], 0) + 1
    print(f"注册行构造: {len(rows)}；分类计数: {json.dumps(counts, ensure_ascii=False)}")

    store = InstrumentRegistryStore(Path(args.research_db))
    try:
        result = store.freeze(rows, registry_version=REGISTRY_VERSION)
        print(f"冻结结果: {json.dumps(result, ensure_ascii=False)}")
        industry = store.industry_targets(registry_version=REGISTRY_VERSION)
        print(f"行业目标可读取: {len(industry)} 个（代码区间 881xxx）")
        return 0 if result["status"] in {"FROZEN", "ALREADY_FROZEN"} else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
