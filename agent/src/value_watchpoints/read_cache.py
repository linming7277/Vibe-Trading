"""Read-side caching for the watchpoint projection.

Two independent mechanisms, both read-only and both rule-preserving.

``scoped_read_cache``
    Memoizes deterministic reads for the lifetime of one projection scope
    (one request, or one batch).  It removes duplicate loads of the same
    research object inside a single scope and is released afterwards, so it
    can never serve a stale result to a later request.

Fingerprint memos
    The Level-3 pool and leader quality profile otherwise rebuild market
    cross-sections on every call.  Each is a pure function of its arguments
    plus the underlying caches, so results are memoized under a key that
    includes a cheap fingerprint of those caches.  Any source refresh changes
    the fingerprint and drops every entry.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, defaultdict
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from typing import Any, Callable, Iterator

FINGERPRINT_MODULES = ("financial_history", "stock_list", "daily_history")


class FingerprintMemo:
    """Small LRU keyed by call arguments plus a source fingerprint."""

    def __init__(self, name: str, *, slots: int = 24) -> None:
        self.name = name
        self.slots = slots
        self._lock = threading.Lock()
        self._fingerprint: str | None = None
        self._entries: OrderedDict[Any, Any] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def clear(self) -> None:
        with self._lock:
            self._fingerprint = None
            self._entries.clear()

    def load(
        self, loader: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any], fingerprint: str,
    ) -> Any:
        key = (args, tuple(sorted(kwargs.items())))
        with self._lock:
            if self._fingerprint != fingerprint:
                self._fingerprint = fingerprint
                self._entries.clear()
            elif key in self._entries:
                self._entries.move_to_end(key)
                self.hits += 1
                return self._entries[key]
        value = loader(*args, **kwargs)
        with self._lock:
            if self._fingerprint == fingerprint:
                self._entries[key] = value
                self._entries.move_to_end(key)
                while len(self._entries) > self.slots:
                    self._entries.popitem(last=False)
            self.misses += 1
        return value


leader_pool_memo = FingerprintMemo("leader_pool", slots=4)
leader_profile_memo = FingerprintMemo("leader_quality_profile", slots=256)

_MEMOS = (leader_pool_memo, leader_profile_memo)


def clear_memos() -> None:
    for memo in _MEMOS:
        memo.clear()


def memo_stats() -> dict[str, dict[str, int]]:
    return {memo.name: {"hits": memo.hits, "misses": memo.misses} for memo in _MEMOS}


# ----------------------------------------------------------------------
def _leader_service() -> Any | None:
    try:
        from src.leader_quality_profile import get_leader_quality_profile_service

        return get_leader_quality_profile_service()
    except Exception:  # noqa: BLE001 - caching is optional
        return None


def _strategy_service() -> Any | None:
    try:
        from src.value_strategy import get_value_strategy_state_service

        return get_value_strategy_state_service()
    except Exception:  # noqa: BLE001
        return None


def _targeted_financial_loader(leader: Any, symbol: str, requested_as_of: str | None) -> Callable[[str], Any]:
    """Build the exact leader-profile finance input for one peer group only.

    ``ValueLineService._load_financials`` decodes every A-share history record
    although a leader profile consumes only the target company's Level-3
    peers.  This applies the same PIT/latest-revision rules to that bounded
    symbol set using the existing category index.
    """
    run = leader.leader_store.latest_run(requested_as_of)
    rows = leader.leader_store.all_rows(run["id"]) if run else []
    target = next((row for row in rows if str(row.get("stock_code") or "").upper() == symbol.upper()), None)
    industry_code = str((target or {}).get("level3_code") or "")
    symbols = {
        str(row.get("stock_code") or "").upper()
        for row in rows
        if str(row.get("level3_code") or "") == industry_code
        and str(row.get("eligibility_status") or "") == "eligible"
        and row.get("leader_rank") is not None
    }
    symbols.add(symbol.upper())
    cache = getattr(getattr(leader, "_value_line", None), "cache", None)

    def load(as_of: str) -> dict[str, list[dict[str, Any]]]:
        if cache is None:
            return leader.financial_loader(as_of)
        latest_by_report: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for peer in symbols:
            items = cache.list_records("financial_history", category=peer, limit=10_000)["items"]
            for item in items:
                row = item["payload"]
                if str(row.get("announcement_date") or "9999-12-31") > as_of:
                    continue
                row_symbol = str(row.get("symbol") or item.get("category") or "")
                report_date = str(row.get("report_date") or "")
                previous = latest_by_report[row_symbol].get(report_date)
                if not previous or str(row.get("announcement_date") or "") > str(previous.get("announcement_date") or ""):
                    latest_by_report[row_symbol][report_date] = row
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row_symbol, reports in latest_by_report.items():
            result[row_symbol] = list(reports.values())
        for values in result.values():
            values.sort(key=lambda row: (row["report_date"], row["announcement_date"]))
        return result

    return load


def _optimized_leader_profile(leader: Any, market: str, symbol: str, as_of: str | None) -> dict[str, Any]:
    """Run the existing profile rules with a bounded, read-only finance loader."""
    from src.leader_quality_profile.service import LeaderQualityProfileService

    service = LeaderQualityProfileService(
        leader_store=leader.leader_store,
        financial_loader=_targeted_financial_loader(leader, symbol, as_of),
    )
    return service.get_profile(market, symbol, as_of)


def _saved_leader_pool_reader(state: Any, as_of: str | None) -> dict[str, Any] | None:
    """Read materialized pool members without rebuilding all industry scores."""
    risk = getattr(state, "risk_service", None)
    original = getattr(risk, "_read_leader_pool", None)
    if original is None:
        return None
    from src.level3_leaders import get_level3_leader_service

    service = get_level3_leader_service()
    candidates = service.store.list_pools(limit=200)
    if as_of:
        candidates = [pool for pool in candidates if str(pool.get("as_of") or "")[:10] <= as_of]
    if not candidates:
        return None
    # RiskResearchService consumes only stock_code and lifecycle_status from
    # members.  Both are already persisted; _enrich_pool does not alter them.
    return service.store.get_pool(str(candidates[0]["id"]), include_inactive=True)


def source_fingerprint() -> str | None:
    """Cheap change token for the caches every cross-section read depends on.

    Uses the TongDaXin module bookkeeping rows and the newest Level-3 leader
    run, both O(1) lookups.  Returns ``None`` when the token cannot be
    established, which disables the fingerprint memos entirely.
    """
    parts: list[str] = []
    leader = _leader_service()
    cache = getattr(getattr(leader, "_value_line", None), "cache", None)
    if cache is None:
        return None
    try:
        states = {str(item.get("module")): item for item in cache.module_states()}
    except Exception:  # noqa: BLE001
        return None
    for module in FINGERPRINT_MODULES:
        row = states.get(module) or {}
        parts.append(f"{module}={row.get('item_count')}/{row.get('updated_at')}/{row.get('last_success_at')}")
    store = getattr(leader, "leader_store", None)
    if store is None:
        return None
    try:
        run = store.latest_run(None) or {}
    except Exception:  # noqa: BLE001
        return None
    parts.append(f"l3_run={run.get('id')}/{run.get('as_of')}/{run.get('created_at')}")
    return "|".join(parts)


def _install(owner: Any, name: str, replacement: Any) -> Callable[[], None] | None:
    """Shadow one read method, returning an exact restore callback."""
    own = name in vars(owner) if hasattr(owner, "__dict__") else False
    original = vars(owner).get(name) if own else None
    try:
        setattr(owner, name, replacement)
    except (AttributeError, TypeError):
        return None

    def restore() -> None:
        if own:
            setattr(owner, name, original)
        else:
            vars(owner).pop(name, None)

    return restore


@contextmanager
def _memoized(owner: Any, name: str, memo: FingerprintMemo, fingerprint: str | None) -> Iterator[None]:
    original = getattr(owner, name, None) if owner is not None else None
    if original is None or fingerprint is None:
        yield
        return

    def call(*args: Any, **kwargs: Any) -> Any:
        return memo.load(original, args, kwargs, fingerprint)

    restore = _install(owner, name, call)
    if restore is None:
        yield
        return
    try:
        yield
    finally:
        restore()


def _scope_targets() -> list[tuple[Any, str]]:
    """Deterministic reads a single scope may otherwise repeat."""
    pairs: list[tuple[Any, str]] = []

    def add(owner: Any, *names: str) -> None:
        if owner is not None:
            pairs.extend((owner, name) for name in names)

    state = _strategy_service()
    if state is not None:
        add(getattr(state, "pool_repository", None), "active")
        add(getattr(state, "focus_service", None), "get_focus_selection")
        add(getattr(state, "price_zone_service", None), "get_price_zones")
        add(getattr(state, "entry_service", None), "get_entry_research")
        add(getattr(state, "exit_service", None), "get_exit_research")
        add(getattr(state, "risk_service", None), "get_risk_research")
        add(getattr(state, "thesis_repository", None), "get_current_thesis", "thesis_as_of")

    leader = _leader_service()
    add(getattr(leader, "leader_store", None), "latest_run", "all_rows", "industry_rows")

    try:
        from src.moat_research import get_moat_research_service

        moat = get_moat_research_service()
        add(getattr(moat, "business_store", None), "latest")
        add(getattr(moat, "business_profiles", None), "profile")
        add(getattr(moat, "evidence_store", None), "list")
    except Exception:  # noqa: BLE001
        pass

    try:
        from src.financial_analysis.service import get_financial_analysis_service

        financial = get_financial_analysis_service()
        add(financial, "get_saved_resolved_analysis")
        add(getattr(financial, "store", None), "latest", "recent")
    except Exception:  # noqa: BLE001
        pass

    return pairs


@contextmanager
def scoped_read_cache(*, maxsize: int = 1024) -> Iterator[None]:
    """Install the scope memo and the fingerprint memos for one projection scope."""
    fingerprint = source_fingerprint()
    leader = _leader_service()
    state = _strategy_service()
    patched: list[Callable[[], None]] = []
    with ExitStack() as stack:
        if leader is not None and fingerprint is not None:
            def profile(market: str, symbol: str, as_of: str | None = None) -> dict[str, Any]:
                return leader_profile_memo.load(
                    lambda *args: _optimized_leader_profile(leader, *args),
                    (market, symbol, as_of),
                    {},
                    fingerprint,
                )

            restore = _install(leader, "get_profile", profile)
            if restore is not None:
                stack.callback(restore)
        risk = getattr(state, "risk_service", None)
        if risk is not None and fingerprint is not None:
            def pool_reader(as_of: str | None) -> dict[str, Any] | None:
                return leader_pool_memo.load(
                    lambda value: _saved_leader_pool_reader(state, value),
                    (as_of,),
                    {},
                    fingerprint,
                )

            restore = _install(risk, "leader_pool_reader", pool_reader)
            if restore is not None:
                stack.callback(restore)
        try:
            for owner, name in _scope_targets():
                original = getattr(owner, name, None)
                # An enclosing scope already installed a memo here; keep the
                # wider one so a batch shares its cross-section reads.
                if original is None or getattr(original, "cache_info", None) is not None:
                    continue
                restore = _install(owner, name, lru_cache(maxsize=maxsize)(original))
                if restore is not None:
                    patched.append(restore)
            yield
        finally:
            for restore in reversed(patched):
                restore()
