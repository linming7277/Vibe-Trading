"""Partitioned point-in-time Parquet warehouse and provider fallback adapter."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import duckdb

from backtest.loaders import registry as loader_registry
from backtest.loaders.base import NoAvailableSourceError
from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoricalFeatureStore:
    """Write immutable market/date partitions with explicit availability time."""

    REQUIRED_PIT_COLUMNS = ("data_as_of", "available_at", "source")

    def __init__(self, root: Path | None = None, catalog_db: Path | None = None) -> None:
        self.root = Path(root or (get_runtime_root() / "history"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_db = Path(catalog_db or (get_runtime_root() / "research.db"))
        initializer = ResearchWorkspaceStore(self.catalog_db, seed=False)
        initializer.close()

    def write_partition(
        self,
        *,
        market: str,
        dataset: str,
        data_as_of: str,
        frame: pd.DataFrame,
        provider: str,
        available_at: str | None = None,
        coverage: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not 0 <= coverage <= 1:
            raise ValueError("coverage must be between 0 and 1")
        materialized = frame.copy()
        available = available_at or _now()
        if "data_as_of" not in materialized:
            materialized["data_as_of"] = data_as_of
        else:
            materialized["data_as_of"] = materialized["data_as_of"].fillna(data_as_of)
        if "available_at" not in materialized:
            materialized["available_at"] = available
        else:
            materialized["available_at"] = materialized["available_at"].fillna(available)
        # The catalog records the aggregate provider chain.  Preserve an
        # existing row-level source so mixed TDX/AKShare partitions remain
        # auditable instead of relabeling every row as the aggregate chain.
        if "source" not in materialized:
            materialized["source"] = provider
        else:
            materialized["source"] = materialized["source"].fillna(provider)
        partition = self.root / market.lower() / dataset / f"date={data_as_of}"
        partition.mkdir(parents=True, exist_ok=True)
        target = partition / "part-000.parquet"
        temporary = partition / "part-000.parquet.tmp"
        materialized = materialized.reset_index(drop=True)
        connection = duckdb.connect()
        try:
            connection.register("partition_frame", materialized)
            escaped = str(temporary).replace("'", "''")
            connection.execute(f"COPY partition_frame TO '{escaped}' (FORMAT PARQUET)")
        finally:
            connection.close()
        temporary.replace(target)
        record = {
            "id": f"data_{uuid.uuid4().hex[:16]}", "market": market, "dataset": dataset,
            "partition_path": str(target), "provider": provider, "data_as_of": data_as_of,
            "available_at": available, "row_count": len(materialized), "coverage": coverage,
            "status": "ready" if coverage >= .8 else "partial", "metadata": metadata or {}, "created_at": _now(),
        }
        with sqlite3.connect(str(self.catalog_db)) as conn:
            conn.execute(
                """INSERT INTO data_catalog
                   (id,market,dataset,partition_path,provider,data_as_of,available_at,row_count,coverage,status,metadata_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market,dataset,partition_path) DO UPDATE SET
                     provider=excluded.provider,data_as_of=excluded.data_as_of,
                     available_at=excluded.available_at,row_count=excluded.row_count,
                     coverage=excluded.coverage,status=excluded.status,
                     metadata_json=excluded.metadata_json,created_at=excluded.created_at""",
                (
                    record["id"], market, dataset, record["partition_path"], provider, data_as_of,
                    available, len(materialized), coverage, record["status"],
                    json.dumps(record["metadata"], ensure_ascii=False, sort_keys=True), record["created_at"],
                ),
            )
        return record

    def read_as_of(self, *, market: str, dataset: str, as_of: str, available_before: str | None = None) -> pd.DataFrame:
        with sqlite3.connect(str(self.catalog_db)) as conn:
            conn.row_factory = sqlite3.Row
            clauses = ["market=?", "dataset=?", "data_as_of<=?"]
            args: list[Any] = [market, dataset, as_of]
            if available_before:
                clauses.append("available_at<=?")
                args.append(available_before)
            rows = conn.execute(
                f"SELECT * FROM data_catalog WHERE {' AND '.join(clauses)} ORDER BY data_as_of,partition_path",  # noqa: S608
                args,
            ).fetchall()
        frames = []
        for row in rows:
            if Path(row["partition_path"]).exists():
                connection = duckdb.connect()
                try:
                    frames.append(connection.execute("SELECT * FROM read_parquet(?)", [row["partition_path"]]).fetch_df())
                finally:
                    connection.close()
        return pd.concat(frames).sort_index() if frames else pd.DataFrame()

    def catalog(self, *, market: str | None = None, dataset: str | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if market:
            clauses.append("market=?")
            args.append(market)
        if dataset:
            clauses.append("dataset=?")
            args.append(dataset)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with sqlite3.connect(str(self.catalog_db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"SELECT * FROM data_catalog {where} ORDER BY data_as_of DESC", args).fetchall()  # noqa: S608
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            result.append(item)
        return result


class MultiSourceHistoryAdapter:
    """Use the existing loader registry while recording the provider actually used."""

    MARKET_TYPES = {"CN": "a_share", "HK": "hk_equity"}
    # Historical research has different priorities from low-latency quotes:
    # PIT/accounting depth wins for A shares, and the read-only Futu feed wins
    # for Hong Kong when configured. Public sources remain explicit fallbacks.
    PROVIDER_CHAINS = {
        "CN": ("tushare", "akshare", "baostock", "mootdx", "tencent", "eastmoney", "local"),
        "HK": ("futu", "yahoo", "tencent", "eastmoney", "akshare", "yfinance", "tushare", "longbridge", "local"),
    }

    def __init__(self, store: HistoricalFeatureStore | None = None) -> None:
        self.store = store or HistoricalFeatureStore()

    def _resolve(self, market: str) -> Any:
        if market not in self.MARKET_TYPES:
            raise ValueError("market must be CN or HK")
        loader_registry._ensure_registered()
        tried: list[str] = []
        for name in self.PROVIDER_CHAINS[market]:
            loader_cls = loader_registry.LOADER_REGISTRY.get(name)
            if loader_cls is None:
                continue
            tried.append(name)
            try:
                loader = loader_cls()
                if loader.is_available():
                    return loader
            except Exception:
                continue
        raise NoAvailableSourceError(f"No available historical provider for {market}; tried: {tried}")

    def provider_status(self, market: str) -> dict[str, Any]:
        try:
            loader = self._resolve(market)
            return {"market": market, "status": "ready", "provider": loader.name, "fallback_chain": list(self.PROVIDER_CHAINS[market])}
        except Exception as exc:
            return {"market": market, "status": "unavailable", "provider": None, "fallback_chain": list(self.PROVIDER_CHAINS.get(market, ())), "error": str(exc)}

    def backfill_ohlcv(self, *, market: str, codes: list[str], start_date: str = "2015-01-01", end_date: str) -> list[dict[str, Any]]:
        if market not in self.MARKET_TYPES:
            raise ValueError("market must be CN or HK")
        loader = self._resolve(market)
        frames = loader.fetch(codes, start_date, end_date, interval="1D")
        records = []
        for code, frame in frames.items():
            if frame is None or frame.empty:
                continue
            normalized = frame.copy()
            normalized["symbol"] = code
            for as_of, daily in normalized.groupby(normalized.index.strftime("%Y-%m-%d")):
                records.append(self.store.write_partition(
                    market=market, dataset="ohlcv", data_as_of=str(as_of), frame=daily,
                    provider=loader.name, coverage=1.0, metadata={"symbol": code, "adjustment": "source_normalized"},
                ))
        return records


class OfficialCsvImportAdapter:
    """Import operator-supplied official/PIT datasets without losing timing metadata."""

    def __init__(self, store: HistoricalFeatureStore | None = None) -> None:
        self.store = store or HistoricalFeatureStore()

    def import_file(
        self,
        *,
        path: Path,
        market: str,
        dataset: str,
        provider: str,
        data_as_of_column: str = "data_as_of",
        available_at_column: str = "available_at",
    ) -> list[dict[str, Any]]:
        frame = pd.read_csv(path)
        if data_as_of_column not in frame.columns or available_at_column not in frame.columns:
            raise ValueError("official CSV requires data_as_of and available_at columns")
        if frame.empty:
            return []
        records: list[dict[str, Any]] = []
        for (data_as_of, available_at), partition in frame.groupby(
            [data_as_of_column, available_at_column], dropna=False, sort=True,
        ):
            if pd.isna(data_as_of) or pd.isna(available_at):
                raise ValueError("data_as_of and available_at cannot be missing")
            payload = partition.drop(columns=[data_as_of_column, available_at_column])
            records.append(self.store.write_partition(
                market=market,
                dataset=dataset,
                data_as_of=str(data_as_of),
                available_at=str(available_at),
                frame=payload,
                provider=provider,
                coverage=1.0,
                metadata={"import_file": path.name, "pit": True},
            ))
        return records
