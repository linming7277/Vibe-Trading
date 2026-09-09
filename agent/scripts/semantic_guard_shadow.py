"""对既有 SHADOW 留档做语义重验证与修正渲染（0 LLM / 0 模型重跑）。

原始预测记录不可覆盖：结果写入 forecast_semantic_validations（衍生件），
修正视图仅存在于该衍生留档与文档产物。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.forecast_service import prepare_forecast_inputs
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.render import render_narrative
from src.macro_forecast.semantic import (
    SEMANTIC_GUARD_VERSION, SemanticGuard, annotate_catalog, revised_output_from,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="SHADOW 语义重验证（0 LLM）")
    parser.add_argument("--forecast-id", required=True)
    parser.add_argument("--out-narrative", default=None)
    args = parser.parse_args()

    research_db = get_runtime_root() / "research.db"
    conn = sqlite3.connect(f"file:{research_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, target_trade_date, input_fingerprint, status, structured_payload_json FROM macro_market_forecasts WHERE id=?",
        (args.forecast_id,),
    ).fetchone()
    if not row:
        print(f"[ERROR] 预测留档不存在: {args.forecast_id}")
        return 2
    payload = json.loads(row["structured_payload_json"])
    conn.close()

    prepared = prepare_forecast_inputs(target_date=str(row["target_trade_date"]).replace("-", ""),
                                       research_db=research_db)
    if prepared["status"] != "PREPARED":
        print(json.dumps(prepared, ensure_ascii=False))
        return 2
    prep = prepared["prep"]
    if prep["prompt_hash"] != (payload.get("input") or {}).get("prompt_hash"):
        # 同 bundle 重算指纹一致才可复用服务端映射；不一致则以存储的 alias 目录为准
        stored_alias = (payload.get("input") or {}).get("alias_map") or {}
        catalog_types = annotate_catalog((payload.get("input") or {}).get("evidence_catalog") or {})
        alias_map = stored_alias
    else:
        catalog_types = annotate_catalog(prep["evidence_catalog"])
        alias_map = prep["alias_map"]

    guard = SemanticGuard(catalog_types=catalog_types, alias_map=alias_map)
    entries = (payload.get("validation") or {}).get("industry_entries") or []
    report = guard.run(payload.get("model_output") or {}, entries)

    # 修正视图 + 渲染（原始 model_output 不动）
    revised = revised_output_from(report, payload.get("model_output") or {})
    enriched = {**payload,
                "model_output": revised,
                "semantic": report,
                "validation": {**(payload.get("validation") or {}),
                               "industry_entries": [
                                   {**entry, "reason_basis": audit["reason_basis"]}
                                   for entry, audit in zip(entries, report["industries"])
                               ]}}
    enriched["input"] = {**(payload.get("input") or {}),
                         "alias_map": alias_map,
                         "evidence_values": (prep.get("context") or {}).get("ev") or {},
                         "evidence_catalog": prep.get("evidence_catalog") or (payload.get("input") or {}).get("evidence_catalog") or {}}
    bundle = prepared["bundle"]
    narrative = render_narrative(enriched, bundle)

    store = ForecastStore(research_db)
    try:
        saved = store.save_semantic_validation(
            args.forecast_id, guard_version=SEMANTIC_GUARD_VERSION,
            report=report, revised_narrative_md=narrative,
        )
    finally:
        store.close()

    if args.out_narrative:
        Path(args.out_narrative).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_narrative).write_text(narrative, encoding="utf-8")

    summary = {
        "forecast_id": args.forecast_id,
        "original_status": row["status"],
        "guard_version": SEMANTIC_GUARD_VERSION,
        "semantic_status": report["status"],
        "market_summary": {k: report["market_summary"][k] for k in
                           ("breadth_claim", "breadth_claim_removed_clauses",
                            "background_index_substitution", "status")},
        "invalidation": {k: report["invalidation"][k] for k in
                         ("outcome_phrases", "kept", "removed", "used_fallback", "status")},
        "basis_counts": report["basis_counts"],
        "strong": [item for item in report["industries"] if item["side"] == "RELATIVE_STRONG"],
        "weak": [item for item in report["industries"] if item["side"] == "RELATIVE_WEAK"],
        "save": saved,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
