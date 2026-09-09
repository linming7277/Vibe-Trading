"""V28 Canary：用最新 completed EOD 数据构建 v28 简报（不发送飞书）。

使用 2026-09-07 真实数据（今日收盘 K 线已采集、实时日历可确认）：
- 今日市场复盘（确定性）
- 当日预测复盘（target=09-07 的 outcome；T=09-08 预测尚未到期，如实标注）
- 下一交易日前瞻（读最新留档预测，不调模型）
- 宏观环境复用 + 价值线各段（若生产池/焦点数据就绪则全量，否则该段如实降级）
输出完整 narrative + card preview + payload summary；Production 发送 = 无。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root


def main() -> int:
    parser = argparse.ArgumentParser(description="v28 canary 构建（不发送）")
    parser.add_argument("--as-of", default="2026-09-07")
    parser.add_argument("--out", default="../docs/daily-brief/v28-canary")
    args = parser.parse_args()

    from src.investment_research_supervisor.daily_brief_service import (
        FORMULA_VERSION, InvestmentResearchDailyBriefService,
    )
    from src.investment_research_supervisor.daily_brief_notification_service import build_daily_brief_card as build_card

    print(f"formula_version: {FORMULA_VERSION}")
    service = InvestmentResearchDailyBriefService()
    try:
        result = service.build(research_as_of=args.as_of)
        print(f"build status: {result.status} reused={result.reused}")
        if result.status != "READY":
            error = getattr(result, "brief", {}).get("last_error") or ""
            print(f"build failed（生产池/焦点数据未就绪属预期）：{error}")
        brief = result.brief or {}
        payload = dict(brief.get("brief_payload") or {})
        payload.update({k: brief.get(k) for k in
                        ("market_review", "forecast_review", "next_outlook") if brief.get(k) is not None})
        # 即使 build 因价值线数据未就绪 FAILED，也可用降级 payload 组装三大新段的 canary 视图
        if not payload.get("market_review"):
            from src.macro_forecast.market_review import build_market_review
            from src.macro_forecast.forecast_service import get_latest_macro_forecast

            review = build_market_review(as_of=args.as_of.replace("-", ""), previous_date="20260904")
            review["available"] = True
            latest = get_latest_macro_forecast()
            payload["market_review"] = review
            payload["forecast_review"] = {"available": False,
                                           "reason": "上一交易日未形成有效预测，本日无预测成绩可复盘。"}
            structured = (latest or {}).get("structured_payload") or {}
            output = structured.get("model_output") or {}
            entries = (structured.get("validation") or {}).get("industry_entries") or []
            payload["next_outlook"] = {
                "available": bool(latest), "forecast_id": (latest or {}).get("id"),
                "status": (latest or {}).get("status"),
                "target_trade_date": (structured.get("input") or {}).get("target_trade_date"),
                "calendar_unverified": (structured.get("input") or {}).get("calendar_status") == "CALENDAR_UNVERIFIED_NEXT_SESSION",
                "abstained": bool(output.get("abstain")), "direction": (output.get("market") or {}).get("direction"),
                "summary": (output.get("market") or {}).get("summary"),
                "strong_industries": [{"name": e.get("display_name")} for e in entries if e.get("side") == "RELATIVE_STRONG"][:3],
                "weak_industries": [{"name": e.get("display_name")} for e in entries if e.get("side") == "RELATIVE_WEAK"][:3],
                "invalidation": [str(i) for i in ((output.get("market") or {}).get("invalidation_conditions") or [])][:1],
                "data_gaps": list((structured.get("input") or {}).get("gaps") or [])[:3],
            }
        card = build_card({"brief_payload": payload,
                           "macro_environment": payload.get("macro_environment") or {"available": False}})
        card_chars = len(json.dumps(card, ensure_ascii=False, default=str))
        narrative_chars = len(str(payload.get("text") or ""))
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "narrative.txt").write_text(str(payload.get("text") or ""), encoding="utf-8")
        (out_dir / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        summary = {
            "formula_version": FORMULA_VERSION, "as_of": args.as_of,
            "build_status": result.status,
            "market_review": payload.get("market_review", {}).get("available"),
            "market_class": (payload.get("market_review") or {}).get("benchmark", {}).get("market_class"),
            "forecast_review": payload.get("forecast_review", {}).get("available"),
            "next_outlook": {k: payload.get("next_outlook", {}).get(k) for k in
                             ("available", "direction", "target_trade_date", "calendar_unverified")},
            "narrative_chars": narrative_chars,
            "card_chars": card_chars,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                               encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        service.close() if hasattr(service, "close") else None


if __name__ == "__main__":
    raise SystemExit(main())
