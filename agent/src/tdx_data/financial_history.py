"""Point-in-time professional financial history from the local TDX package.

The vendor API is deliberately used instead of decoding ``gpcw*.dat`` files.
All monetary fields returned by the tested TQ build are expressed in yuan.
Unknown or inapplicable fields remain ``None``; zero is never used as a
missing-value sentinel.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .client import TdxClient
from .store import TdxDataStore


FINANCIAL_HISTORY_DATASET = "financial_history"
FORMULA_INPUT_VERSION = "tdx-professional-finance-v1.1.0"
DEFAULT_FIELDS = (
    # Balance-sheet fields below are deliberately kept in the PIT history,
    # rather than reading today's fundamental snapshot in later risk rules.
    # This makes receivables, inventory and liquidity checks reproducible at
    # the report announcement date.
    "FN8", "FN11", "FN17", "FN21", "FN40", "FN54", "FN63", "FN69", "FN72", "FN114", "FN183", "FN184", "FN197",
    "FN199", "FN202", "FN210", "FN228", "FN230", "FN232", "FN234",
    "FN238", "FN242", "FN271", "FN281", "FN314", "FN327", "FN329",
)


@dataclass(frozen=True)
class FinancialField:
    code: str
    name: str
    unit: str
    applicable_to: str = "all"
    confirmed: bool = True


# Confirmed against the official TQ field table and sampled from the local
# 2020-2026 professional-finance package on 2026-08-13.
FIELD_MAP: dict[str, FinancialField] = {
    "FN8": FinancialField("FN8", "cash_and_equivalents", "CNY"),
    "FN11": FinancialField("FN11", "accounts_receivable", "CNY"),
    "FN17": FinancialField("FN17", "inventory", "CNY", "non_financial"),
    "FN21": FinancialField("FN21", "current_assets", "CNY"),
    "FN40": FinancialField("FN40", "total_assets", "CNY"),
    "FN54": FinancialField("FN54", "current_liabilities", "CNY"),
    "FN63": FinancialField("FN63", "liabilities", "CNY"),
    "FN69": FinancialField("FN69", "non_current_liabilities", "CNY"),
    "FN72": FinancialField("FN72", "equity", "CNY"),
    "FN114": FinancialField("FN114", "capex", "CNY"),
    "FN183": FinancialField("FN183", "revenue_yoy", "percent"),
    "FN184": FinancialField("FN184", "net_profit_yoy", "percent"),
    "FN197": FinancialField("FN197", "roe", "percent"),
    "FN199": FinancialField("FN199", "net_margin", "percent"),
    "FN202": FinancialField("FN202", "gross_margin", "percent", "non_financial"),
    "FN210": FinancialField("FN210", "debt_ratio", "percent"),
    "FN228": FinancialField("FN228", "cash_conversion", "percent"),
    "FN230": FinancialField("FN230", "revenue", "CNY"),
    "FN232": FinancialField("FN232", "net_profit", "CNY"),
    "FN234": FinancialField("FN234", "operating_cash_flow", "CNY"),
    "FN238": FinancialField("FN238", "total_shares", "share"),
    "FN242": FinancialField("FN242", "shareholders", "person"),
    "FN271": FinancialField("FN271", "parent_equity", "CNY"),
    "FN281": FinancialField("FN281", "weighted_roe", "percent"),
    "FN314": FinancialField("FN314", "financial_announcement_date", "YYMMDD"),
    "FN327": FinancialField("FN327", "interest_bearing_debt_ratio", "percent"),
    "FN329": FinancialField("FN329", "roic", "percent"),
}


def _number(value: Any, *, zero_is_missing: bool = False) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or (zero_is_missing and result == 0):
        return None
    return result


def _date_text(value: Any) -> str | None:
    raw = str(value or "").strip().replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        parsed = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None
    return parsed.isoformat()


def _period_type(report_date: str) -> str:
    suffix = report_date[5:]
    return {"12-31": "annual", "06-30": "semiannual", "03-31": "q1", "09-30": "q3"}.get(suffix, "other")


def package_fingerprint(cw_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    files = []
    digest = hashlib.sha256()
    for path in sorted(cw_dir.glob("gpcw*.dat")):
        if path.stat().st_size <= 20:
            continue
        item = {"name": path.name, "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        files.append(item)
        digest.update(f"{item['name']}:{item['size']}:{item['mtime_ns']}".encode())
    return digest.hexdigest()[:24] if files else "", files


def normalize_financial_row(symbol: str, row: dict[str, Any], raw_version: str) -> dict[str, Any] | None:
    report_date = _date_text(row.get("tag_time"))
    announcement_date = _date_text(row.get("announce_time"))
    if not report_date or not announcement_date:
        return None
    revenue = _number(row.get("FN230"))
    gross_margin = _number(row.get("FN202"), zero_is_missing=True)
    gross_profit = revenue * gross_margin / 100 if revenue is not None and gross_margin is not None else None
    return {
        "symbol": symbol,
        "report_date": report_date,
        "announcement_date": announcement_date,
        "period_type": _period_type(report_date),
        "revenue": revenue,
        "net_profit": _number(row.get("FN232")),
        "operating_cash_flow": _number(row.get("FN234")),
        "equity": _number(row.get("FN72")),
        "parent_equity": _number(row.get("FN271")),
        "assets": _number(row.get("FN40")),
        "liabilities": _number(row.get("FN63")),
        "cash_and_equivalents": _number(row.get("FN8")),
        "accounts_receivable": _number(row.get("FN11")),
        "inventory": _number(row.get("FN17")),
        "current_assets": _number(row.get("FN21")),
        "current_liabilities": _number(row.get("FN54")),
        "non_current_liabilities": _number(row.get("FN69")),
        "interest_bearing_debt_ratio": _number(row.get("FN327")),
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "net_margin": _number(row.get("FN199")),
        "roe": _number(row.get("FN281")) or _number(row.get("FN197")),
        "revenue_yoy": _number(row.get("FN183")),
        "net_profit_yoy": _number(row.get("FN184")),
        "cash_conversion": _number(row.get("FN228")),
        "debt_ratio": _number(row.get("FN210")),
        "capex": _number(row.get("FN114")),
        "shareholders": _number(row.get("FN242")),
        "total_shares": _number(row.get("FN238")),
        # Confirmed from sequential Q1/Q2/Q3/Q4 samples: TDX professional
        # finance exposes these flow fields as single-period, not YTD values.
        "flow_basis": "single_period",
        "source": "TongDaXin professional finance / TQ",
        "data_as_of": announcement_date,
        "raw_version": raw_version,
    }


def _financial_rows(value: Any) -> tuple[list[dict[str, Any]], int]:
    """Normalize the response variants observed across TQ client builds.

    Most symbols return a list of row dictionaries.  Empty/unavailable symbols
    may return ``"--"`` and some builds return either a single row dictionary
    or a dictionary keyed by report date.  Invalid entries are counted and
    skipped so one security cannot abort an all-market refresh.
    """
    if isinstance(value, dict):
        if "tag_time" in value or "announce_time" in value:
            return [value], 0
        rows = [row for row in value.values() if isinstance(row, dict)]
        return rows, len(value) - len(rows)
    if isinstance(value, (list, tuple)):
        rows = [row for row in value if isinstance(row, dict)]
        return rows, len(value) - len(rows)
    return [], int(value not in (None, "", [], {}))


def cagr(values: Iterable[tuple[str, float | None]]) -> dict[str, Any]:
    """Return a guarded 5Y/3Y CAGR result from annual point-in-time values."""
    usable = sorted((str(period), float(value)) for period, value in values if value is not None)
    years = 5 if len(usable) >= 6 else 3 if len(usable) >= 4 else 0
    if not years:
        return {"value": None, "years": None, "status": "insufficient_history"}
    start, end = usable[-(years + 1)][1], usable[-1][1]
    if start <= 0 or end <= 0:
        status = "loss_to_profit" if start <= 0 < end else "non_positive_endpoint"
        return {"value": None, "years": years, "status": status}
    return {"value": ((end / start) ** (1 / years) - 1) * 100, "years": years, "status": "ready"}


class FinancialHistoryService:
    """Collect and query TDX professional financial data with PIT semantics."""

    def __init__(self, store: TdxDataStore | None = None, client: TdxClient | None = None) -> None:
        self.store = store or TdxDataStore()
        self.client = client or TdxClient()

    def package_status(self) -> dict[str, Any]:
        fingerprint, files = package_fingerprint(Path(self.client.home) / "vipdoc" / "cw")
        valid = [item for item in files if item["size"] > 1024]
        return {
            "status": "ready" if valid else "needs_professional_finance",
            "file_count": len(valid),
            "first_period": valid[0]["name"][4:12] if valid else None,
            "last_period": valid[-1]["name"][4:12] if valid else None,
            "raw_version": fingerprint or None,
            "files": valid,
            "field_map": {code: field.__dict__ for code, field in FIELD_MAP.items()},
        }

    def collect(
        self,
        symbols: list[str],
        *,
        start_time: str = "20190101",
        end_time: str = "",
        batch_size: int = 100,
        minimum_symbol_coverage: float = .85,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        package = self.package_status()
        if package["status"] != "ready":
            raise RuntimeError("needs_professional_finance")
        all_records: list[dict[str, Any]] = []
        covered: set[str] = set()
        invalid_rows = 0
        batch_errors: list[str] = []
        total = len(symbols)
        for offset in range(0, total, batch_size):
            batch = symbols[offset:offset + batch_size]
            try:
                result = self.client.call(
                    "get_financial_data", stock_list=batch, field_list=list(DEFAULT_FIELDS),
                    start_time=start_time, end_time=end_time, report_type="announce_time",
                ) or {}
            except Exception as exc:
                result = {}
                batch_errors.append(f"{batch[0]}-{batch[-1]}:{exc}")
            for symbol in batch:
                rows, invalid = _financial_rows(result.get(symbol) if isinstance(result, dict) else None)
                invalid_rows += invalid
                for raw in rows:
                    normalized = normalize_financial_row(symbol, raw, str(package["raw_version"]))
                    if normalized:
                        key = f"{symbol}:{normalized['report_date']}:{normalized['announcement_date']}"
                        all_records.append({"key": key, "category": symbol, "name": symbol, "payload": normalized})
                        covered.add(symbol)
            if progress:
                done = min(offset + len(batch), total)
                progress(done, total, f"专业财务 {done:,}/{total:,}")
        coverage = len(covered) / total if total else 0.0
        if total and coverage < minimum_symbol_coverage:
            raise RuntimeError(
                f"professional_finance_coverage_below_threshold:{len(covered)}/{total}<{minimum_symbol_coverage:.0%}"
            )
        self.store.replace_dataset(FINANCIAL_HISTORY_DATASET, all_records)
        return {
            "status": "ready", "item_count": len(all_records), "symbols": len(covered),
            "total_symbols": total, "coverage": coverage, "raw_version": package["raw_version"],
            "invalid_rows": invalid_rows, "batch_errors": batch_errors[:50],
        }

    def collect_incremental(
        self, symbols: list[str], *, start_time: str = "20190101", end_time: str = "",
        batch_size: int = 20, progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Refresh a bounded research universe without replacing the market cache.

        The full-market collector intentionally performs an atomic replacement.
        A daily research run only owns its selected symbols, so it must upsert
        those PIT records and leave every unrelated cached company untouched.
        """
        package = self.package_status()
        if package["status"] != "ready":
            raise RuntimeError("needs_professional_finance")
        universe = sorted(set(symbol.upper() for symbol in symbols if symbol))
        item_count = 0
        covered: set[str] = set()
        errors: list[str] = []
        invalid_rows = 0
        for offset in range(0, len(universe), batch_size):
            batch = universe[offset:offset + batch_size]
            batch_records: list[dict[str, Any]] = []
            batch_covered: set[str] = set()
            try:
                result = self.client.call(
                    "get_financial_data", stock_list=batch, field_list=list(DEFAULT_FIELDS),
                    start_time=start_time, end_time=end_time, report_type="announce_time",
                ) or {}
            except Exception as exc:
                result = {}
                errors.append(f"{batch[0]}-{batch[-1]}:{exc}")
            finally:
                # The vendor bridge is a local DLL process.  Releasing the
                # handle after a bounded batch avoids carrying a stale session
                # through a long all-pool refresh; the next batch reconnects
                # lazily.  This never affects records already persisted.
                try:
                    self.client.close()
                except Exception:
                    pass
            for symbol in batch:
                rows, invalid = _financial_rows(result.get(symbol) if isinstance(result, dict) else None)
                invalid_rows += invalid
                for raw in rows:
                    normalized = normalize_financial_row(symbol, raw, str(package["raw_version"]))
                    if not normalized:
                        continue
                    batch_records.append({
                        "key": f"{symbol}:{normalized['report_date']}:{normalized['announcement_date']}",
                        "category": symbol, "name": symbol, "payload": normalized,
                    })
                    batch_covered.add(symbol)
            # Persist every completed batch.  The research universe can be
            # large and a local TDX bridge may be interrupted; retaining
            # earlier batches makes the next run safely resumable because
            # records are upserted by symbol/report/announcement key.
            if batch_records:
                try:
                    self.store.upsert_records(FINANCIAL_HISTORY_DATASET, batch_records)
                    item_count += len(batch_records)
                    covered.update(batch_covered)
                except Exception as exc:
                    errors.append(f"{batch[0]}-{batch[-1]}:store:{type(exc).__name__}:{exc}")
            if progress:
                progress(min(offset + len(batch), len(universe)), len(universe), "更新研究池专业财务")
        coverage = len(covered) / len(universe) if universe else 0.0
        return {
            "status": "ready" if coverage == 1 and not errors else "partial",
            "item_count": item_count, "symbols": len(covered), "total_symbols": len(universe),
            "coverage": coverage, "raw_version": package["raw_version"],
            "invalid_rows": invalid_rows, "batch_errors": errors[:50],
        }

    def query(self, symbol: str, *, as_of: str | None = None, period_type: str | None = None) -> dict[str, Any]:
        cutoff = date.fromisoformat(as_of).isoformat() if as_of else None
        rows = self.store.list_records(FINANCIAL_HISTORY_DATASET, category=symbol.upper(), limit=1000)["items"]
        items = [row["payload"] for row in rows]
        if cutoff:
            items = [row for row in items if str(row.get("announcement_date") or "9999-12-31") <= cutoff]
        if period_type:
            items = [row for row in items if row.get("period_type") == period_type]
        items.sort(key=lambda row: (row["report_date"], row["announcement_date"]))
        return {
            "symbol": symbol.upper(), "as_of": cutoff, "period_type": period_type,
            "items": items, "total": len(items), "package": self.package_status(),
        }
