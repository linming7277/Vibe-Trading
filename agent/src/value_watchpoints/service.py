"""Read-only Value Line watchpoint projection.  Zero LLM, no persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from src.research_workspace.store import normalize_market, normalize_symbol

from .contracts import (
    FORBIDDEN_WATCHPOINT_KEYS,
    FORMULA_VERSION,
    IMPORTANCE_TIERS,
    public_watchpoint,
)
from .dedupe import merge_watchpoints
from .read_cache import scoped_read_cache
from . import projectors

_TIER_RANK = {name: index for index, name in enumerate(IMPORTANCE_TIERS)}
_CATEGORY_SORT = {
    "THESIS": 0, "RISK": 1, "FINANCIAL": 2, "VALUATION": 3, "BUSINESS": 4, "MOAT": 5, "CAPITAL": 6,
}


class ValueWatchpointProjectionService:
    def __init__(
        self,
        *,
        strategy_loader: Callable[..., dict[str, Any]] | None = None,
        thesis_loader: Callable[..., dict[str, Any] | None] | None = None,
        risk_loader: Callable[..., dict[str, Any]] | None = None,
        financial_loader: Callable[..., dict[str, Any]] | None = None,
        normalized_loader: Callable[..., dict[str, Any]] | None = None,
        cycle_loader: Callable[..., dict[str, Any]] | None = None,
        business_loader: Callable[..., dict[str, Any]] | None = None,
        reliability_loader: Callable[..., dict[str, Any]] | None = None,
        moat_loader: Callable[..., dict[str, Any]] | None = None,
        capital_loader: Callable[..., dict[str, Any]] | None = None,
        deep_loader: Callable[..., dict[str, Any]] | None = None,
        cursor_repository: Any | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.db_path = db_path
        self.cursor_repository = cursor_repository
        self._cursor_repository_resolved = cursor_repository is not None
        self.strategy_loader = strategy_loader
        self.thesis_loader = thesis_loader
        self.risk_loader = risk_loader
        self.financial_loader = financial_loader
        self.normalized_loader = normalized_loader
        self.cycle_loader = cycle_loader
        self.business_loader = business_loader
        self.reliability_loader = reliability_loader
        self.moat_loader = moat_loader
        self.capital_loader = capital_loader
        self.deep_loader = deep_loader

    def _safe(self, loader: Callable[..., Any] | None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if loader is None:
            return {}
        try:
            return dict(loader(*args, **kwargs) or {})
        except Exception:  # noqa: BLE001 - projection must not fail because one module is missing
            return {}

    def _default_loaders(self) -> None:
        if self.strategy_loader is None:
            from src.value_strategy import get_value_strategy_state_service
            state_service = get_value_strategy_state_service()

            def _strategy(market: str, code: str, as_of: str | None = None) -> dict[str, Any]:
                cached = self._cursor_strategy_state(market, code, as_of)
                if cached is not None:
                    return cached
                return state_service.get_strategy_state(market, code, research_as_of=as_of)

            self.strategy_loader = _strategy
        if self.thesis_loader is None:
            from src.company_thesis.store import CompanyThesisRepository
            repo = CompanyThesisRepository(self.db_path)

            def _thesis(market: str, code: str, as_of: str | None = None) -> dict[str, Any] | None:
                if as_of:
                    return repo.thesis_as_of(market, code, as_of)
                return repo.get_current_thesis(market, code)

            self.thesis_loader = _thesis
        if self.risk_loader is None:
            from src.risk_research import get_risk_research_service
            self.risk_loader = get_risk_research_service().get_risk_research
        if self.financial_loader is None:
            from src.financial_analysis.service import get_financial_analysis_service
            financial_service = get_financial_analysis_service()
            self.financial_loader = lambda market, code, as_of=None: financial_service.get_saved_resolved_analysis(
                code, as_of=as_of,
            )
        if self.normalized_loader is None:
            from src.normalized_earnings import get_normalized_earnings_reference_service
            self.normalized_loader = get_normalized_earnings_reference_service().reference
        if self.cycle_loader is None:
            from src.cycle_profit_scenario import get_cycle_profit_scenario_service
            self.cycle_loader = get_cycle_profit_scenario_service().scenario
        if self.business_loader is None:
            from src.business_research import get_business_research_service
            store = get_business_research_service().store

            def _business(market: str, code: str, as_of: str | None = None) -> dict[str, Any]:
                row = store.latest(code, as_of=as_of) or {}
                snap = dict(row.get("snapshot") or {})
                analysis = dict(row.get("analysis") or {})
                return {
                    "id": row.get("id"), "data_as_of": row.get("data_as_of") or snap.get("data_as_of"),
                    "claims": analysis.get("claims") or [], "main_business": snap.get("main_business"),
                }

            self.business_loader = _business
        if self.reliability_loader is None:
            from src.value_price_zones import get_value_price_zone_service
            from src.value_strategy import valuation_reliability
            zones_service = get_value_price_zone_service()

            def _reliability(market: str, code: str, as_of: str | None = None) -> dict[str, Any]:
                zones = zones_service.get_price_zones(market, code, as_of=as_of)
                return valuation_reliability(zones)

            self.reliability_loader = _reliability
        if self.moat_loader is None:
            from src.moat_research import get_moat_research_service
            self.moat_loader = get_moat_research_service().get_research
        if self.capital_loader is None:
            from src.capital_allocation_research import get_capital_allocation_research_service
            self.capital_loader = get_capital_allocation_research_service().get_research
        # ``deep_loader`` is intentionally left unset.  Deep Research coverage
        # never changed the out-of-scope quota (see ``_quota``), and loading it
        # re-ran five live projections per request.

    # ------------------------------------------------------------------
    # Strategy state fast path
    def _cursors(self) -> Any | None:
        if not self._cursor_repository_resolved:
            self._cursor_repository_resolved = True
            try:
                from src.value_strategy.event_store import ValueStrategyEventRepository

                self.cursor_repository = ValueStrategyEventRepository(self.db_path)
            except Exception:  # noqa: BLE001 - fast path is optional
                self.cursor_repository = None
        return self.cursor_repository

    def _cursor_strategy_state(self, market: str, code: str, as_of: str | None) -> dict[str, Any] | None:
        """Reuse the day-end strategy state a cursor already advanced to.

        Only accepted when the cursor sits on the newest research date any
        cursor reached, and was produced by the strategy formula currently in
        force.  Anything else falls back to the authoritative projection.
        """
        repository = self._cursors()
        if repository is None:
            return None
        try:
            from src.value_strategy.service import FORMULA_VERSION as STRATEGY_FORMULA_VERSION

            cursor = repository.get_cursor(market, code)
            if not cursor:
                return None
            cursor_as_of = str(cursor.get("research_as_of") or "")[:10]
            if not cursor_as_of:
                return None
            if as_of and str(as_of)[:10] != cursor_as_of:
                return None
            if cursor_as_of != repository.latest_cursor_research_as_of(market):
                return None
            state = cursor.get("state")
            if not isinstance(state, dict) or not state.get("primary_action"):
                return None
            if str(state.get("formula_version") or "") != STRATEGY_FORMULA_VERSION:
                return None
            return dict(state)
        except Exception:  # noqa: BLE001
            return None

    def get_watchpoints(
        self,
        market: str,
        stock_code: str,
        research_as_of: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        with scoped_read_cache():
            return self._project(market, stock_code, research_as_of, limit)

    def get_watchpoints_batch(
        self,
        market: str,
        stock_codes: Iterable[str],
        research_as_of: str | None = None,
        limit: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Project several companies under one shared read scope.

        Same rules and same output as calling :meth:`get_watchpoints` per
        company; only the shared cross-section reads are done once.
        """
        codes = [str(code) for code in stock_codes]
        results: dict[str, dict[str, Any]] = {}
        with scoped_read_cache():
            for code in codes:
                try:
                    projected = self._project(market, code, research_as_of, limit)
                except Exception as exc:  # noqa: BLE001 - one company must not fail the batch
                    projected = {"stock_code": code, "error": f"{type(exc).__name__}: {exc}"}
                results[projected.get("stock_code") or code] = projected
        return results

    def _project(
        self,
        market: str,
        stock_code: str,
        research_as_of: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        self._default_loaders()
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        as_of = research_as_of
        state = self._safe(self.strategy_loader, normalized_market, symbol, as_of=as_of)
        as_of = research_as_of or str(state.get("research_as_of") or "")[:10] or None
        thesis = None
        try:
            thesis = self.thesis_loader(normalized_market, symbol, as_of=as_of) if self.thesis_loader else None
        except TypeError:
            thesis = self.thesis_loader(normalized_market, symbol) if self.thesis_loader else None
        except Exception:  # noqa: BLE001
            thesis = None
        risk = self._safe(self.risk_loader, normalized_market, symbol, as_of=as_of)
        financial = self._safe(self.financial_loader, normalized_market, symbol, as_of=as_of)
        normalized = self._safe(self.normalized_loader, normalized_market, symbol, as_of=as_of)
        cycle = self._safe(self.cycle_loader, normalized_market, symbol, as_of=as_of)
        business = self._safe(self.business_loader, normalized_market, symbol, as_of=as_of)
        reliability = self._safe(self.reliability_loader, normalized_market, symbol, as_of=as_of)
        if not reliability:
            reliability = dict(((state.get("price_attention") or {}).get("valuation_reliability") or {}))
        moat = self._safe(self.moat_loader, normalized_market, symbol, as_of=as_of)
        capital = self._safe(self.capital_loader, normalized_market, symbol, as_of=as_of)

        candidates: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        for producer, payload in (
            (lambda: projectors.thesis_items(thesis, research_as_of=as_of), None),
            (lambda: projectors.risk_items(risk, research_as_of=as_of), None),
            (lambda: projectors.financial_items(financial, normalized, cycle, research_as_of=as_of), None),
            (lambda: projectors.business_items(business, research_as_of=as_of), None),
            (lambda: projectors.valuation_items(reliability, research_as_of=as_of), None),
            (lambda: projectors.moat_items(moat, research_as_of=as_of), None),
            (lambda: projectors.capital_items(capital, research_as_of=as_of), None),
        ):
            produced, produced_gaps = producer()
            candidates.extend(produced)
            gaps.extend(produced_gaps)

        merged = merge_watchpoints(candidates)
        action = str((state.get("primary_action") or {}).get("status") or "")
        ranked = self._rank(merged, action=action, thesis=thesis, risk=risk)
        if action == "DEFER_RESEARCH":
            ranked = ranked[:1]
        quota = self._quota(state)
        top = self._select_top(ranked, quota)
        if limit is not None:
            top = top[: max(0, int(limit))]
            ranked = ranked[: max(0, int(limit))]

        public_all = [public_watchpoint(item) for item in ranked]
        public_top = [public_watchpoint(item) for item in top]
        for item in public_all + public_top:
            for key in FORBIDDEN_WATCHPOINT_KEYS:
                item.pop(key, None)

        suggested = None
        if any("风险资料不足" in str(gap.get("description") or "") for gap in gaps):
            suggested = "风险资料不足，建议补齐相关披露。"
            if action == "CONTINUE_OBSERVE":
                suggested = "继续观察，同时补齐风险证据。"

        freshness = dict(state.get("freshness") or {})
        freshness.update({
            "financial_as_of": str(financial.get("as_of") or "")[:10] or None,
            "normalized_as_of": str(normalized.get("research_as_of") or "")[:10] or None,
            "business_as_of": str(business.get("data_as_of") or "")[:10] or None,
            "moat_as_of": str(moat.get("research_as_of") or "")[:10] or None,
            "capital_as_of": str(capital.get("research_as_of") or "")[:10] or None,
            "risk_as_of": str(risk.get("as_of") or freshness.get("risk_as_of") or "")[:10] or None,
        })
        return {
            "stock_code": symbol,
            "stock_name": str(state.get("stock_name") or financial.get("stock_name") or symbol),
            "research_as_of": as_of,
            "primary_action": (state.get("primary_action") or {}).get("status"),
            "focus_tier": (state.get("priority") or {}).get("tier"),
            "watchpoints": public_all,
            "data_gaps": gaps,
            "top_watchpoints": public_top,
            "suggested_research_need": suggested,
            "source_freshness": freshness,
            "formula_version": FORMULA_VERSION,
        }

    @staticmethod
    def _quota(state: dict[str, Any]) -> int:
        eligible = str((state.get("eligibility") or {}).get("status") or "") == "IN_VALUE_SCOPE"
        tier = str((state.get("priority") or {}).get("tier") or "")
        # Out-of-scope companies are capped at the company-level Top 3.  Nothing
        # is padded, so a company without research still returns fewer items.
        if not eligible:
            return 3
        return {"A": 3, "B": 2, "C": 1}.get(tier, 1)

    @staticmethod
    def _select_top(ranked: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
        top = ranked[:quota]
        if quota < 2:
            return top
        valuation = next((item for item in ranked if item.get("category") == "VALUATION"), None)
        if valuation is None or any(item.get("category") == "VALUATION" for item in top):
            return top
        replaced = False
        trimmed = []
        for item in reversed(top):
            if not replaced and item.get("category") != "RISK" and item.get("importance_tier") != "CRITICAL":
                replaced = True
                continue
            trimmed.append(item)
        trimmed.reverse()
        if not replaced and trimmed:
            trimmed = trimmed[:-1]
        trimmed.append(valuation)
        return trimmed[:quota]

    @staticmethod
    def _rank(items: list[dict[str, Any]], *, action: str, thesis: dict[str, Any] | None,
              risk: dict[str, Any]) -> list[dict[str, Any]]:
        thesis_status = str((thesis or {}).get("status") or "")
        authority = str((thesis or {}).get("authority_status") or "")
        overall_risk = str((risk or {}).get("overall_risk") or "")

        def bucket(item: dict[str, Any]) -> tuple[int, int, int, str]:
            category = str(item.get("category") or "")
            generic = bool(item.get("generic"))
            action_boost = 8
            if action == "THESIS_REVIEW" and category == "THESIS":
                action_boost = 0
            elif action == "RISK_REVIEW" and category == "RISK":
                action_boost = 0
            elif action == "VALUATION_DATA_REVIEW" and category == "VALUATION":
                action_boost = 0
            elif action == "PRIORITY_RESEARCH" and category in {"THESIS", "RISK", "FINANCIAL"} and not generic:
                action_boost = 1
            elif action == "CONTINUE_OBSERVE" and (
                (category == "RISK") or (category == "THESIS" and not generic) or (
                    category == "FINANCIAL" and item.get("canonical_metric") in {"OCF", "GROSS_MARGIN", "NET_MARGIN"}
                )
            ):
                action_boost = 1
            falsified = 0 if (
                category == "THESIS" and (thesis_status == "FALSIFIED" or authority == "HUMAN_REJECTED")
            ) else 1
            high_risk = 0 if (category == "RISK" and (item.get("importance_tier") == "HIGH" or overall_risk == "HIGH")) else 1
            if generic and category == "THESIS":
                falsified = 2
            group = min(
                falsified,
                high_risk if not (action == "THESIS_REVIEW" and category == "THESIS") else 1,
                0 if action_boost == 0 else 2,
                _CATEGORY_SORT.get(category, 9),
            )
            if category == "THESIS" and not generic and (thesis_status == "FALSIFIED" or authority == "HUMAN_REJECTED"):
                group = 0
            elif category == "RISK" and item.get("importance_tier") == "HIGH":
                group = 1 if group > 0 else group
            elif action_boost == 0:
                group = min(group, 2)
            elif category == "FINANCIAL" and item.get("origin") in {"FINANCIAL_CORE", "NORMALIZED_EARNINGS"} and not generic:
                group = min(group, 3)
            elif category == "VALUATION":
                group = min(group, 4)
            elif category == "BUSINESS":
                group = min(group, 5)
            elif category == "MOAT":
                group = min(group, 6)
            elif category == "CAPITAL":
                group = min(group, 7)
            if generic and category == "THESIS":
                group = max(group, 5)
            return (
                group,
                _TIER_RANK.get(str(item.get("importance_tier") or "LOW"), 9),
                action_boost,
                # Equally important items are ordered by the framework's
                # category priority rather than by key text, so a routine moat
                # re-verification never outranks a core financial question.
                _CATEGORY_SORT.get(category, 9),
                str(item.get("semantic_key") or item.get("title") or ""),
            )

        return sorted(items, key=bucket)


_service: ValueWatchpointProjectionService | None = None


def get_value_watchpoint_projection_service() -> ValueWatchpointProjectionService:
    global _service
    if _service is None:
        _service = ValueWatchpointProjectionService()
    return _service
