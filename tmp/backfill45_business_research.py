"""45 只风险 UNKNOWN 修复：重跑业务研究（带引用 claims）+ 重算风险快照（as_of=今天）。

只写 business research 快照与 risk snapshot 的新 source_as_of 行；不碰 Focus/池/口径。
单只失败继续下一只；进度逐行输出。
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter

logging.disable(logging.WARNING)

CODES = ("000156.SZ,000719.SZ,000725.SZ,000887.SZ,000930.SZ,002011.SZ,002226.SZ,002236.SZ,"
         "002249.SZ,002444.SZ,002697.SZ,002705.SZ,002739.SZ,002772.SZ,003012.SZ,300170.SZ,"
         "300296.SZ,300358.SZ,300596.SZ,600060.SH,600167.SH,600210.SH,600258.SH,600299.SH,"
         "600489.SH,600583.SH,600585.SH,600612.SH,600690.SH,600699.SH,600741.SH,600754.SH,"
         "600787.SH,600874.SH,601211.SH,601318.SH,601766.SH,601808.SH,601886.SH,601921.SH,"
         "603345.SH,603373.SH,603515.SH,603889.SH,603899.SH").split(",")
AS_OF = "2026-09-11"


def main() -> int:
    from src.business_research.service import get_business_research_service
    from src.low_value_risk_snapshot.service import LowValuePoolRiskSnapshotService

    biz = get_business_research_service()
    risk = LowValuePoolRiskSnapshotService()
    results: list[dict] = []
    done = biz  # noqa: F841
    for i, code in enumerate(CODES, 1):
        row: dict = {"i": i, "code": code}
        try:
            r = biz.analyze(code, force=True, as_of=AS_OF)
            claims = (r.get("analysis") or {}).get("claims") or []
            row["claims"] = len(claims)
            row["cited"] = sum(
                1 for c in claims
                if isinstance(c, dict) and c.get("text") and c.get("source_keys"))
        except Exception as exc:  # noqa: BLE001
            row["biz_error"] = f"{type(exc).__name__}: {exc}"[:140]
        try:
            out = risk.refresh_company_snapshot(
                market="CN", stock_code=code, source_as_of=AS_OF)
            snap = out.get("snapshot") or {}
            row["overall"] = snap.get("overall_risk")
            row["high"] = snap.get("high_risk_count")
            row["medium"] = snap.get("medium_risk_count")
        except Exception as exc:  # noqa: BLE001
            row["risk_error"] = f"{type(exc).__name__}: {exc}"[:140]
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    tally = Counter(r.get("overall") or ("ERR" if r.get("risk_error") else "?") for r in results)
    print("TALLY:" + json.dumps(dict(tally), ensure_ascii=False), flush=True)
    biz.close()
    risk.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
