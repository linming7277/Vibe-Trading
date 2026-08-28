"""ResearchFreshnessService — module-level freshness classification (plan §7).

``classify(market, stock_code, as_of)`` returns one entry per research module:
status ∈ {FRESH, STALE, PARTIALLY_STALE, UNKNOWN, INVALID, NOT_PERSISTED},
plus the stale reason and current input fingerprint.  The service performs no
refreshes and never infers staleness from calendar dates alone — only from
real input-fingerprint differences (plan §7.2).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.research_freshness import fingerprints
from src.research_freshness.manifests import ResearchManifestStore

# module key -> (title, classification strategy)
_MODULES: tuple[tuple[str, str], ...] = (
    ("financial", "财务研究（特征/预测/叙述）"),
    ("business", "经营研究"),
    ("valuation", "估值（价格区间/历史分位/入场/退出）"),
    ("risk", "风险研究（规则引擎）"),
    ("moat", "护城河（证据/研究）"),
    ("capital_allocation", "资本配置"),
    ("thesis", "公司核心逻辑（Thesis/Evidence/Review）"),
    ("risk_snapshot", "风险快照（低估池）"),
    ("low_value_pool", "低估龙头池成员资格"),
    ("daily_brief", "每日投研简报"),
)


class ResearchFreshnessService:
    def __init__(self, manifest_store: ResearchManifestStore | None = None) -> None:
        self.manifests = manifest_store or ResearchManifestStore()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_as_of(as_of: str | None) -> str:
        return str(as_of or date.today().isoformat())[:10]

    @staticmethod
    def _entry(
        module: str, title: str, status: str, *,
        reason: str = "", fingerprint: str | None = None, persisted_as_of: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "module": module, "title": title, "status": status, "stale_reason": reason,
            "input_fingerprint": fingerprint, "persisted_as_of": persisted_as_of,
            **(extra or {}),
        }

    # ------------------------------------------------------------------
    # per-module classifiers
    # ------------------------------------------------------------------
    def _classify_financial(self, stock_code: str, as_of: str) -> dict[str, Any]:
        from src.financial_analysis.service import get_financial_analysis_service

        persisted = get_financial_analysis_service().store.latest(stock_code, as_of=as_of)
        current = fingerprints.fingerprint_financial(stock_code, as_of=as_of)
        title = dict(_MODULES)["financial"]
        if persisted is None:
            return self._entry("financial", title, "NOT_PERSISTED", reason="尚无财务快照",
                               fingerprint=(current or {}).get("source_hash"))
        persisted_hash = str(persisted.get("source_hash") or "")
        current_hash = str((current or {}).get("source_hash") or "")
        analysis_status = str(persisted.get("analysis_status") or "")
        if current_hash and persisted_hash and current_hash != persisted_hash:
            return self._entry(
                "financial", title, "STALE",
                reason="输入指纹已变化（新财报/行情/版本），确定性层待 prepare 换代",
                fingerprint=current_hash, persisted_as_of=str(persisted.get("as_of") or "")[:10],
            )
        # Deterministic layer FRESH; narrative layer is a sub-capability.
        if analysis_status == "COMPLETED":
            sub = "FRESH"
        elif analysis_status in {"NOT_RUN", "CONFIGURATION_REQUIRED"}:
            sub = "NOT_RUN"
        else:
            sub = "UNKNOWN"
        return self._entry(
            "financial", title, "FRESH" if sub == "FRESH" else "PARTIALLY_STALE",
            reason="" if sub == "FRESH" else f"确定性层可复用；叙述层 {sub}（analysis_status={analysis_status}）",
            fingerprint=current_hash or persisted_hash,
            persisted_as_of=str(persisted.get("as_of") or "")[:10],
            extra={"narrative_status": sub},
        )

    def _classify_business(self, stock_code: str, as_of: str) -> dict[str, Any]:
        from src.business_research import get_business_research_service

        persisted = get_business_research_service().store.latest(stock_code, as_of=as_of)
        current = fingerprints.fingerprint_business(stock_code, as_of=as_of)
        title = dict(_MODULES)["business"]
        if persisted is None:
            return self._entry("business", title, "NOT_PERSISTED", reason="尚无经营研究快照",
                               fingerprint=(current or {}).get("source_hash"))
        current_hash = str((current or {}).get("source_hash") or "")
        persisted_hash = str(persisted.get("source_hash") or "")
        if current_hash and current_hash != persisted_hash:
            return self._entry("business", title, "STALE", reason="画像/披露输入已变化",
                               fingerprint=current_hash,
                               persisted_as_of=str(persisted.get("data_as_of") or "")[:10])
        return self._entry("business", title, "FRESH", fingerprint=persisted_hash,
                           persisted_as_of=str(persisted.get("data_as_of") or "")[:10])

    @staticmethod
    def _classify_deterministic(module: str, title: str, fp: dict[str, Any] | None) -> dict[str, Any]:
        """Live deterministic projections are recomputed on read — always usable."""
        if fp is None:
            return {"module": module, "title": title, "status": "UNKNOWN",
                    "stale_reason": "输入指纹暂不可计算", "input_fingerprint": None,
                    "persisted_as_of": None}
        return {"module": module, "title": title, "status": "FRESH",
                "stale_reason": "确定性现算层，读取时重算，无过期概念",
                "input_fingerprint": fp.get("input_fingerprint"), "persisted_as_of": None,
                "projection": "live"}

    def _classify_thesis(self, stock_code: str, as_of: str) -> dict[str, Any]:
        from src.company_thesis.review_store import CompanyThesisReviewRepository
        from src.company_thesis.store import CompanyThesisRepository

        title = dict(_MODULES)["thesis"]
        try:
            repo = CompanyThesisRepository()
            thesis = repo.get_current_thesis("CN", stock_code)
            if thesis is None:
                return self._entry("thesis", title, "NOT_PERSISTED", reason="尚未建立核心逻辑")
            reviews = CompanyThesisReviewRepository().list_reviews_for_company("CN", stock_code)
            latest_review = next((r for r in reviews if str(r.get("thesis_id") or "") == str(thesis.get("thesis_id"))), None)
            stale = bool((latest_review or {}).get("is_stale"))
            return self._entry(
                "thesis", title, "FRESH" if not stale else "INVALID",
                reason="版本化 append-only；Review 与当前证据集不一致，需人工复核" if stale else "",
                persisted_as_of=str(thesis.get("source_data_as_of") or "")[:10],
                extra={
                    "thesis_version": thesis.get("version"),
                    "authority_status": thesis.get("authority_status"),
                    "review_is_stale": stale,
                },
            )
        except Exception:  # noqa: BLE001
            return self._entry("thesis", title, "UNKNOWN", reason="读取失败")

    def _classify_manifest_backed(self, module: str, title: str, stock_code: str,
                                  as_of: str, current_fp: str | None) -> dict[str, Any]:
        """Date-keyed writers without inline hashes → compare via research_manifests."""
        manifest = self.manifests.latest(research_type=module, market="CN", stock_code=stock_code, as_of=as_of)
        if manifest is None:
            return self._entry(module, title, "UNKNOWN",
                               reason="该写入器尚未记录输入指纹（旧数据或未刷新）", fingerprint=current_fp)
        recorded = str(manifest.get("input_fingerprint") or "")
        if current_fp and recorded and current_fp != recorded:
            return self._entry(module, title, "STALE", reason="输入指纹已变化，待下次刷新",
                               fingerprint=current_fp,
                               persisted_as_of=str(manifest.get("research_as_of") or ""))
        return self._entry(module, title, "FRESH", fingerprint=recorded or current_fp,
                           persisted_as_of=str(manifest.get("research_as_of") or ""))

    def _classify_risk_snapshot(self, stock_code: str, as_of: str) -> dict[str, Any]:
        current = fingerprints.fingerprint_risk(stock_code, as_of=as_of)
        return self._classify_manifest_backed(
            "risk_snapshot", dict(_MODULES)["risk_snapshot"], stock_code, as_of,
            (current or {}).get("input_fingerprint"),
        )

    def _classify_low_value_pool(self, stock_code: str, as_of: str) -> dict[str, Any]:
        from src.low_value_leader_pool.store import LowValueLeaderPoolRepository

        title = dict(_MODULES)["low_value_pool"]
        try:
            row = LowValueLeaderPoolRepository().active_map("CN").get(stock_code.upper())
            if row is None or str(row.get("pool_status") or "") != "ACTIVE":
                return self._entry("low_value_pool", title, "NOT_PERSISTED", reason="不在当前低估池")
            return self._entry(
                "low_value_pool", title, "FRESH",
                reason=f"ACTIVE，池基准日 {str(row.get('source_as_of') or '')[:10]}（EOD 按日幂等刷新）",
                persisted_as_of=str(row.get("source_as_of") or "")[:10],
                extra={"valuation_status": row.get("valuation_status"), "entry_level": row.get("entry_level")},
            )
        except Exception:  # noqa: BLE001
            return self._entry("low_value_pool", title, "UNKNOWN", reason="读取失败")

    def _classify_daily_brief(self, as_of: str) -> dict[str, Any]:
        from src.investment_research_supervisor.daily_brief_store import InvestmentResearchDailyBriefRepository

        title = dict(_MODULES)["daily_brief"]
        try:
            brief = InvestmentResearchDailyBriefRepository().get_completed(as_of)
            if brief is None:
                return self._entry("daily_brief", title, "NOT_PERSISTED", reason="当日简报尚未生成")
            return self._entry("daily_brief", title, "FRESH", persisted_as_of=str(brief.get("research_as_of") or "")[:10])
        except Exception:  # noqa: BLE001
            return self._entry("daily_brief", title, "UNKNOWN", reason="读取失败")

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def classify(self, market: str, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        code = stock_code.upper()
        resolved = self._resolve_as_of(as_of)
        modules: list[dict[str, Any]] = [
            self._classify_financial(code, resolved),
            self._classify_business(code, resolved),
            self._classify_deterministic(
                "valuation", dict(_MODULES)["valuation"], fingerprints.fingerprint_valuation(code, resolved)),
            self._classify_deterministic(
                "risk", dict(_MODULES)["risk"], fingerprints.fingerprint_risk(code, resolved)),
            self._classify_deterministic(
                "moat", dict(_MODULES)["moat"], fingerprints.fingerprint_moat(code, resolved)),
            self._classify_deterministic(
                "capital_allocation", dict(_MODULES)["capital_allocation"],
                fingerprints.fingerprint_capital_allocation(code, resolved)),
            self._classify_thesis(code, resolved),
            self._classify_risk_snapshot(code, resolved),
            self._classify_low_value_pool(code, resolved),
            self._classify_daily_brief(resolved),
        ]
        statuses = [m["status"] for m in modules]
        if all(s == "FRESH" for s in statuses):
            overall = "FRESH"
        elif any(s in {"STALE", "INVALID"} for s in statuses):
            overall = "STALE"
        elif any(s == "PARTIALLY_STALE" for s in statuses):
            overall = "PARTIALLY_STALE"
        elif any(s == "UNKNOWN" for s in statuses):
            overall = "UNKNOWN"
        else:
            overall = "UNKNOWN"
        return {
            "market": market, "stock_code": code, "research_as_of": resolved,
            "overall_freshness": overall,
            "modules": modules,
            "summary": {
                status: sum(1 for s in statuses if s == status)
                for status in ("FRESH", "PARTIALLY_STALE", "STALE", "UNKNOWN", "INVALID", "NOT_PERSISTED")
            },
        }


_service: ResearchFreshnessService | None = None


def get_research_freshness_service() -> ResearchFreshnessService:
    global _service
    if _service is None:
        _service = ResearchFreshnessService()
    return _service
