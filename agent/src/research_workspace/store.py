"""SQLite persistence and deterministic portfolio math for the research workspace."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.config.paths import get_runtime_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


MARKET_META = {
    "CN": {"currency": "CNY", "taxonomy": "申万", "label": "A股"},
    "HK": {"currency": "HKD", "taxonomy": "恒生", "label": "港股"},
    "US": {"currency": "USD", "taxonomy": "GICS", "label": "美股"},
}


def normalize_market(value: str) -> str:
    market = str(value or "").strip().upper()
    aliases = {"A": "CN", "ASHARE": "CN", "A-SHARE": "CN", "CHINA": "CN"}
    market = aliases.get(market, market)
    if market not in MARKET_META:
        raise ValueError("market must be one of CN, HK, US")
    return market


def normalize_symbol(market: str, value: str) -> str:
    market = normalize_market(market)
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if market == "CN":
        if symbol.isdigit() and len(symbol) == 6:
            symbol += ".SH" if symbol.startswith(("5", "6", "9")) else ".SZ"
        if not (symbol.endswith(".SH") or symbol.endswith(".SZ")):
            raise ValueError("CN symbols must use 600519.SH / 000001.SZ format")
    elif market == "HK":
        raw = symbol[:-3] if symbol.endswith(".HK") else symbol
        if not raw.isdigit():
            raise ValueError("HK symbols must use 00700.HK format")
        symbol = f"{raw.zfill(5)}.HK"
    else:
        if symbol.endswith(".US"):
            symbol = symbol[:-3]
        symbol = f"{symbol}.US"
    return symbol


class ResearchWorkspaceStore:
    """Thread-safe local workspace database.

    Large run artifacts remain in the existing run directories. This database
    only contains queryable product state and references to those artifacts.
    """

    SCHEMA_VERSION = 8

    def __init__(self, db_path: Path | None = None, *, seed: bool = False) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.RLock()
        self._init_db()
        if seed:
            self._seed_if_empty()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_runs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, market TEXT,
                    symbol TEXT, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    linked_run_id TEXT, started_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_research_runs_started
                    ON research_runs(started_at DESC);
                CREATE TABLE IF NOT EXISTS research_evidence (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, market TEXT NOT NULL,
                    section TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
                    data_as_of TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES research_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_research_evidence_lookup
                    ON research_evidence(market, section, data_as_of DESC);
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id TEXT PRIMARY KEY, market TEXT NOT NULL, as_of TEXT NOT NULL,
                    status TEXT NOT NULL, summary TEXT NOT NULL,
                    metrics_json TEXT NOT NULL, risks_json TEXT NOT NULL,
                    source_status TEXT NOT NULL DEFAULT 'sample', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_snapshots_latest
                    ON market_snapshots(market, as_of DESC);
                CREATE TABLE IF NOT EXISTS macro_briefs (
                    id TEXT PRIMARY KEY, market TEXT NOT NULL, as_of TEXT NOT NULL,
                    headline TEXT NOT NULL, stance TEXT NOT NULL, summary TEXT NOT NULL,
                    themes_json TEXT NOT NULL, risks_json TEXT NOT NULL,
                    source_status TEXT NOT NULL DEFAULT 'sample', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_macro_briefs_latest
                    ON macro_briefs(market, as_of DESC);
                CREATE TABLE IF NOT EXISTS sector_scores (
                    id TEXT PRIMARY KEY, market TEXT NOT NULL, taxonomy TEXT NOT NULL,
                    sector_code TEXT NOT NULL, sector_name TEXT NOT NULL, as_of TEXT NOT NULL,
                    momentum REAL NOT NULL, earnings REAL NOT NULL, fund_flow REAL NOT NULL,
                    breadth REAL NOT NULL, valuation REAL NOT NULL, risk REAL NOT NULL,
                    base_score REAL NOT NULL, agent_adjustment REAL NOT NULL,
                    agent_reason TEXT NOT NULL, final_score REAL NOT NULL, rank INTEGER NOT NULL,
                    source_status TEXT NOT NULL DEFAULT 'sample', created_at TEXT NOT NULL,
                    UNIQUE(market, taxonomy, sector_code, as_of)
                );
                CREATE INDEX IF NOT EXISTS idx_sector_scores_rank
                    ON sector_scores(market, as_of DESC, rank);
                CREATE TABLE IF NOT EXISTS securities (
                    market TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
                    currency TEXT NOT NULL, exchange TEXT NOT NULL, taxonomy TEXT NOT NULL,
                    sector_code TEXT NOT NULL, sector_name TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(market, symbol)
                );
                CREATE TABLE IF NOT EXISTS security_candidates (
                    id TEXT PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    as_of TEXT NOT NULL, industry_position REAL NOT NULL, growth REAL NOT NULL,
                    quality REAL NOT NULL, valuation REAL NOT NULL, momentum REAL NOT NULL,
                    liquidity REAL NOT NULL, base_score REAL NOT NULL, agent_adjustment REAL NOT NULL,
                    agent_reason TEXT NOT NULL, final_score REAL NOT NULL, rank INTEGER NOT NULL,
                    excluded INTEGER NOT NULL DEFAULT 0, exclusion_reason TEXT NOT NULL DEFAULT '',
                    source_status TEXT NOT NULL DEFAULT 'sample', created_at TEXT NOT NULL,
                    FOREIGN KEY(market, symbol) REFERENCES securities(market, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_rank
                    ON security_candidates(market, as_of DESC, rank);
                CREATE TABLE IF NOT EXISTS company_dossiers (
                    id TEXT PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    overview TEXT NOT NULL, bull_thesis TEXT NOT NULL, bear_thesis TEXT NOT NULL,
                    metrics_json TEXT NOT NULL, catalysts_json TEXT NOT NULL, risks_json TEXT NOT NULL,
                    data_as_of TEXT NOT NULL, source_status TEXT NOT NULL DEFAULT 'sample',
                    updated_at TEXT NOT NULL,
                    UNIQUE(market, symbol),
                    FOREIGN KEY(market, symbol) REFERENCES securities(market, symbol)
                );
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, report_type TEXT NOT NULL, title TEXT NOT NULL,
                    summary TEXT NOT NULL, content_md TEXT NOT NULL, market TEXT, symbol TEXT,
                    data_as_of TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT,
                    source_status TEXT NOT NULL DEFAULT 'sample', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reports_created
                    ON reports(created_at DESC);
                CREATE TABLE IF NOT EXISTS committees (
                    id TEXT PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    company_name TEXT NOT NULL, status TEXT NOT NULL, swarm_run_id TEXT,
                    created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_committees_created
                    ON committees(created_at DESC);
                CREATE TABLE IF NOT EXISTS committee_decisions (
                    committee_id TEXT PRIMARY KEY, direction TEXT NOT NULL,
                    position_cap REAL, target_low REAL, target_high REAL, stop_price REAL,
                    holding_period TEXT NOT NULL, confidence REAL NOT NULL,
                    review_triggers_json TEXT NOT NULL, evidence_date TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    FOREIGN KEY(committee_id) REFERENCES committees(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS committee_participant_outputs (
                    id TEXT PRIMARY KEY, committee_id TEXT NOT NULL,
                    task_id TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
                    content TEXT NOT NULL, evidence_json TEXT NOT NULL,
                    data_as_of TEXT, updated_at TEXT NOT NULL,
                    UNIQUE(committee_id, task_id),
                    FOREIGN KEY(committee_id) REFERENCES committees(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS trade_plans (
                    id TEXT PRIMARY KEY, committee_id TEXT, market TEXT NOT NULL,
                    symbol TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
                    direction TEXT NOT NULL, position_cap REAL, entry_low REAL, entry_high REAL,
                    target_low REAL, target_high REAL, stop_price REAL,
                    triggers_json TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(committee_id) REFERENCES committees(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trade_plans_status
                    ON trade_plans(status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS portfolios (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, base_currency TEXT NOT NULL,
                    benchmark TEXT NOT NULL, initial_cash REAL NOT NULL,
                    cash_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_transactions (
                    id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, market TEXT NOT NULL,
                    symbol TEXT NOT NULL, name TEXT NOT NULL, side TEXT NOT NULL,
                    trade_date TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
                    fee REAL NOT NULL, currency TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_portfolio_transactions_order
                    ON portfolio_transactions(portfolio_id, trade_date, created_at);
                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, as_of TEXT NOT NULL,
                    positions_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(portfolio_id, as_of),
                    FOREIGN KEY(portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS portfolio_valuations (
                    id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, as_of TEXT NOT NULL,
                    currency_totals_json TEXT NOT NULL, base_currency TEXT NOT NULL,
                    base_currency_total REAL, aggregate_available INTEGER NOT NULL,
                    fx_evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(portfolio_id, as_of),
                    FOREIGN KEY(portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS exchange_rates (
                    base_currency TEXT NOT NULL, quote_currency TEXT NOT NULL,
                    rate REAL NOT NULL, as_of TEXT NOT NULL, source TEXT NOT NULL,
                    evidence TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(base_currency, quote_currency, as_of)
                );
                CREATE TABLE IF NOT EXISTS engine_runs (
                    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    strategy_line TEXT NOT NULL, market TEXT NOT NULL, as_of TEXT NOT NULL,
                    symbols_json TEXT NOT NULL, formula_version TEXT NOT NULL,
                    status TEXT NOT NULL, source_status TEXT NOT NULL DEFAULT 'unavailable',
                    message TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, completed_at TEXT,
                    profile_id TEXT, profile_version INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_engine_runs_lookup
                    ON engine_runs(strategy_line, market, as_of DESC, started_at DESC);
                CREATE TABLE IF NOT EXISTS feature_snapshots (
                    id TEXT PRIMARY KEY, engine_run_id TEXT NOT NULL, market TEXT NOT NULL,
                    subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                    data_as_of TEXT NOT NULL, available_at TEXT NOT NULL,
                    features_json TEXT NOT NULL, sources_json TEXT NOT NULL,
                    quality_flags_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_feature_snapshots_subject
                    ON feature_snapshots(market, subject_type, subject_id, data_as_of DESC);
                CREATE TABLE IF NOT EXISTS score_snapshots (
                    id TEXT PRIMARY KEY, engine_run_id TEXT NOT NULL, engine TEXT NOT NULL,
                    formula_version TEXT NOT NULL, strategy_line TEXT NOT NULL,
                    market TEXT NOT NULL, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                    data_as_of TEXT NOT NULL, available_at TEXT NOT NULL,
                    raw_features_json TEXT NOT NULL, normalized_features_json TEXT NOT NULL,
                    component_scores_json TEXT NOT NULL, base_score REAL,
                    coverage REAL NOT NULL, status TEXT NOT NULL,
                    quality_flags_json TEXT NOT NULL, evidence_ids_json TEXT NOT NULL,
                    confidence TEXT NOT NULL DEFAULT 'LOW',
                    missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    score_sources_json TEXT NOT NULL DEFAULT '[]',
                    provenance_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_score_snapshots_subject
                    ON score_snapshots(strategy_line, market, subject_type, subject_id, data_as_of DESC);
                CREATE TABLE IF NOT EXISTS regime_snapshots (
                    id TEXT PRIMARY KEY, engine_run_id TEXT NOT NULL, strategy_line TEXT NOT NULL,
                    market TEXT NOT NULL, regime TEXT NOT NULL, previous_regime TEXT,
                    score REAL, confidence REAL NOT NULL, coverage REAL NOT NULL,
                    triggers_json TEXT NOT NULL, data_as_of TEXT NOT NULL,
                    available_at TEXT NOT NULL, formula_version TEXT NOT NULL,
                    changed_at TEXT, created_at TEXT NOT NULL,
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_regime_snapshots_latest
                    ON regime_snapshots(strategy_line, market, data_as_of DESC, created_at DESC);
                CREATE TABLE IF NOT EXISTS strategy_signals (
                    id TEXT PRIMARY KEY, engine_run_id TEXT NOT NULL, strategy_line TEXT NOT NULL,
                    horizon TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    data_as_of TEXT NOT NULL, valid_from TEXT NOT NULL, valid_until TEXT NOT NULL,
                    direction TEXT NOT NULL, base_score REAL NOT NULL,
                    entry_low REAL, entry_high REAL, stop_price REAL,
                    target_low REAL, target_high REAL, position_cap REAL NOT NULL,
                    coverage REAL NOT NULL, formula_versions_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL, status TEXT NOT NULL,
                    invalidation_rules_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_signals_lookup
                    ON strategy_signals(strategy_line, market, horizon, data_as_of DESC, status);
                CREATE TABLE IF NOT EXISTS decision_chain_runs (
                    id TEXT PRIMARY KEY, engine_run_id TEXT NOT NULL UNIQUE,
                    strategy_line TEXT NOT NULL, market TEXT NOT NULL,
                    macro_snapshot_id TEXT, sector_score_id TEXT, candidate_score_id TEXT,
                    company_dossier_id TEXT, committee_id TEXT, timing_signal_id TEXT,
                    formula_versions_json TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS structured_committee_decisions (
                    id TEXT PRIMARY KEY, committee_id TEXT NOT NULL UNIQUE,
                    signal_id TEXT NOT NULL, strategy_line TEXT NOT NULL,
                    decision_status TEXT NOT NULL, direction TEXT NOT NULL,
                    position_cap REAL NOT NULL, entry_low REAL, entry_high REAL,
                    stop_price REAL, target_low REAL, target_high REAL,
                    holding_period TEXT NOT NULL, confidence REAL NOT NULL,
                    summary TEXT NOT NULL, review_triggers_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL, engine_run_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(committee_id) REFERENCES committees(id) ON DELETE CASCADE,
                    FOREIGN KEY(signal_id) REFERENCES strategy_signals(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS data_catalog (
                    id TEXT PRIMARY KEY, market TEXT NOT NULL, dataset TEXT NOT NULL,
                    partition_path TEXT NOT NULL, provider TEXT NOT NULL,
                    data_as_of TEXT NOT NULL, available_at TEXT NOT NULL,
                    row_count INTEGER NOT NULL, coverage REAL NOT NULL,
                    status TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(market, dataset, partition_path)
                );
                CREATE TABLE IF NOT EXISTS industry_taxonomy (
                    market TEXT NOT NULL, taxonomy TEXT NOT NULL, source_code TEXT NOT NULL,
                    canonical_code TEXT NOT NULL, canonical_name TEXT NOT NULL,
                    valid_from TEXT NOT NULL, valid_until TEXT,
                    source TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(market, taxonomy, source_code, valid_from)
                );
                CREATE TABLE IF NOT EXISTS macro_series (
                    series_id TEXT NOT NULL, observation_date TEXT NOT NULL,
                    release_date TEXT NOT NULL, vintage_id TEXT NOT NULL,
                    value REAL, unit TEXT NOT NULL, source TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '', release_status TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(series_id, observation_date, release_date, vintage_id)
                );
                CREATE INDEX IF NOT EXISTS idx_macro_series_pit
                    ON macro_series(series_id, release_date, observation_date);
                CREATE TABLE IF NOT EXISTS macro_snapshots (
                    id TEXT PRIMARY KEY, as_of TEXT NOT NULL, formula_version TEXT NOT NULL,
                    regime TEXT NOT NULL, score REAL, coverage REAL NOT NULL,
                    confidence TEXT NOT NULL, status TEXT NOT NULL,
                    axes_json TEXT NOT NULL, states_json TEXT NOT NULL,
                    missing_fields_json TEXT NOT NULL, sources_json TEXT NOT NULL,
                    provenance_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_macro_snapshots_asof
                    ON macro_snapshots(as_of DESC, created_at DESC);
                CREATE TABLE IF NOT EXISTS policy_events (
                    id TEXT PRIMARY KEY, document_number TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL, normalized_url TEXT NOT NULL,
                    content_hash TEXT NOT NULL, source TEXT NOT NULL,
                    published_at TEXT, fetched_at TEXT NOT NULL,
                    etag TEXT NOT NULL DEFAULT '', last_modified TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, content_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(normalized_url, content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_policy_events_status
                    ON policy_events(status, published_at DESC, fetched_at DESC);
                CREATE TABLE IF NOT EXISTS policy_classifications (
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
                    industry_code TEXT NOT NULL, industry_name TEXT NOT NULL,
                    direction INTEGER NOT NULL, strength INTEGER NOT NULL,
                    sensitivity REAL NOT NULL DEFAULT 1.0,
                    horizon_days INTEGER NOT NULL, evidence TEXT NOT NULL,
                    confidence REAL NOT NULL, classifier_version TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(event_id, industry_code, classifier_version),
                    FOREIGN KEY(event_id) REFERENCES policy_events(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sector_membership_snapshots (
                    as_of TEXT NOT NULL, sector_code TEXT NOT NULL,
                    sector_name TEXT NOT NULL, symbol TEXT NOT NULL,
                    source TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(as_of, sector_code, symbol)
                );
                CREATE TABLE IF NOT EXISTS value_refresh_jobs (
                    id TEXT PRIMARY KEY, modules_json TEXT NOT NULL, as_of TEXT NOT NULL,
                    status TEXT NOT NULL, current_module TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    results_json TEXT NOT NULL DEFAULT '{}', errors_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
                );
                """
            )
            engine_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(engine_runs)")}
            if "profile_id" not in engine_columns:
                self._conn.execute("ALTER TABLE engine_runs ADD COLUMN profile_id TEXT")
            if "profile_version" not in engine_columns:
                self._conn.execute("ALTER TABLE engine_runs ADD COLUMN profile_version INTEGER")
            score_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(score_snapshots)")}
            for column, declaration in (
                ("confidence", "TEXT NOT NULL DEFAULT 'LOW'"),
                ("missing_fields_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("score_sources_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("provenance_key", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in score_columns:
                    self._conn.execute(f"ALTER TABLE score_snapshots ADD COLUMN {column} {declaration}")
            policy_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(policy_classifications)")}
            if "sensitivity" not in policy_columns:
                self._conn.execute(
                    "ALTER TABLE policy_classifications ADD COLUMN sensitivity REAL NOT NULL DEFAULT 1.0"
                )
            macro_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(macro_snapshots)")}
            for column, declaration in (
                ("axis_coverage", "REAL NOT NULL DEFAULT 0"),
                ("series_coverage", "REAL NOT NULL DEFAULT 0"),
                ("series_count", "INTEGER NOT NULL DEFAULT 0"),
                ("series_total", "INTEGER NOT NULL DEFAULT 0"),
                ("release_verified_coverage", "REAL NOT NULL DEFAULT 0"),
                ("first_observed_count", "INTEGER NOT NULL DEFAULT 0"),
                ("missing_series_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in macro_columns:
                    self._conn.execute(f"ALTER TABLE macro_snapshots ADD COLUMN {column} {declaration}")
            for version in range(1, self.SCHEMA_VERSION + 1):
                self._conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                    (version, _now()),
                )
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
            self._conn.execute("PRAGMA optimize")
            self._conn.commit()

    def _seed_if_empty(self) -> None:
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
            if count:
                return
            now = _now()
            date = now[:10]
            market_rows = {
                "CN": ("政策预期与盈利修复并行，关注结构性机会。", {"index_change": 0.62, "turnover": "1.08万亿", "breadth": 58}, ["成交缩量", "业绩分化"]),
                "HK": ("估值修复延续，外部流动性仍是关键变量。", {"index_change": 0.38, "turnover": "1260亿", "breadth": 54}, ["汇率波动", "海外利率"]),
                "US": ("盈利韧性支撑风险偏好，估值拥挤度上升。", {"index_change": 0.44, "turnover": "常态", "breadth": 51}, ["估值偏高", "利率再定价"]),
            }
            macro = {
                "CN": ("政策托底，结构胜于总量", "neutral", ["财政发力", "信用企稳", "产业升级"]),
                "HK": ("流动性改善等待基本面确认", "neutral", ["南向资金", "美元利率", "互联网盈利"]),
                "US": ("软着陆交易仍占主导", "positive", ["盈利增长", "降息路径", "科技资本开支"]),
            }
            sector_seed = {
                "CN": [("SW801080", "电子", 86, 81, 74, 78, 61, 72), ("SW801150", "医药生物", 68, 73, 59, 65, 79, 76), ("SW801780", "银行", 62, 70, 71, 66, 84, 82), ("SW801160", "公用事业", 55, 64, 58, 61, 75, 88)],
                "HK": [("HSI-IT", "资讯科技业", 84, 82, 76, 74, 58, 70), ("HSI-HC", "医疗保健业", 69, 72, 61, 63, 77, 75), ("HSI-FIN", "金融业", 64, 68, 73, 67, 80, 83), ("HSI-PROP", "地产建筑业", 48, 45, 42, 46, 85, 52)],
                "US": [("GICS45", "信息技术", 88, 86, 72, 79, 52, 67), ("GICS35", "医疗保健", 66, 75, 62, 68, 73, 80), ("GICS40", "金融", 63, 71, 69, 64, 76, 78), ("GICS55", "公用事业", 52, 61, 55, 59, 70, 86)],
            }
            securities = [
                ("CN", "600519.SH", "贵州茅台", "CNY", "SSE", "SW801120", "食品饮料", [92, 77, 91, 68, 64, 95], 1680.0),
                ("CN", "300750.SZ", "宁德时代", "CNY", "SZSE", "SW801730", "电力设备", [88, 90, 82, 61, 79, 91], 248.6),
                ("HK", "00700.HK", "腾讯控股", "HKD", "HKEX", "HSI-IT", "资讯科技业", [94, 84, 89, 67, 82, 96], 518.0),
                ("HK", "09988.HK", "阿里巴巴-W", "HKD", "HKEX", "HSI-CD", "非必需性消费", [87, 78, 76, 81, 74, 95], 136.2),
                ("US", "AAPL.US", "Apple", "USD", "NASDAQ", "GICS45", "信息技术", [96, 73, 94, 58, 75, 98], 229.4),
                ("US", "MSFT.US", "Microsoft", "USD", "NASDAQ", "GICS45", "信息技术", [95, 88, 96, 62, 83, 98], 452.1),
            ]
            for market, (summary, metrics, risks) in market_rows.items():
                self._conn.execute(
                    "INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                    (_id("mkt"), market, date, "ready", summary, _dumps(metrics), _dumps(risks), "sample", now),
                )
                headline, stance, themes = macro[market]
                self._conn.execute(
                    "INSERT INTO macro_briefs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (_id("macro"), market, date, headline, stance, summary, _dumps(themes), _dumps(risks), "sample", now),
                )
                scored = []
                for code, name, momentum, earnings, flow, breadth, valuation, risk in sector_seed[market]:
                    base = momentum * .25 + earnings * .20 + flow * .15 + breadth * .15 + valuation * .15 + risk * .10
                    adjustment = 2.0 if momentum >= 80 else 0.0
                    scored.append((code, name, momentum, earnings, flow, breadth, valuation, risk, base, adjustment))
                scored.sort(key=lambda row: row[8] + row[9], reverse=True)
                for rank, row in enumerate(scored, 1):
                    code, name, momentum, earnings, flow, breadth, valuation, risk, base, adjustment = row
                    self._conn.execute(
                        "INSERT INTO sector_scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (_id("sector"), market, MARKET_META[market]["taxonomy"], code, name, date,
                         momentum, earnings, flow, breadth, valuation, risk, round(base, 2), adjustment,
                         "趋势与盈利预期共振" if adjustment else "保持中性观察", round(base + adjustment, 2), rank, "sample", now),
                    )
            by_market: dict[str, list[tuple[Any, ...]]] = {"CN": [], "HK": [], "US": []}
            for market, symbol, name, currency, exchange, sector_code, sector_name, dims, price in securities:
                self._conn.execute(
                    "INSERT INTO securities VALUES(?,?,?,?,?,?,?,?,?)",
                    (market, symbol, name, currency, exchange, MARKET_META[market]["taxonomy"], sector_code, sector_name, now),
                )
                industry, growth, quality, valuation, momentum, liquidity = dims
                base = industry * .20 + growth * .20 + quality * .20 + valuation * .15 + momentum * .15 + liquidity * .10
                adjustment = 2.0 if quality >= 90 else 1.0
                by_market[market].append((symbol, dims, base, adjustment, name, price))
                self._conn.execute(
                    "INSERT INTO company_dossiers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_id("dossier"), market, symbol,
                     f"{name}是{sector_name}代表性公司，当前档案为本地工作台示例快照。",
                     "行业地位、盈利质量与现金流构成主要支撑。",
                     "估值、竞争格局和宏观波动可能压缩安全边际。",
                     _dumps({"price": price, "roe": round(10 + quality / 5, 1), "growth": growth, "quality": quality, "valuation_score": valuation}),
                     _dumps(["财报验证", "行业需求改善"]), _dumps(["估值回撤", "盈利不及预期"]), date, "sample", now),
                )
            for market, rows in by_market.items():
                rows.sort(key=lambda row: row[2] + row[3], reverse=True)
                for rank, (symbol, dims, base, adjustment, name, _price) in enumerate(rows, 1):
                    industry, growth, quality, valuation, momentum, liquidity = dims
                    self._conn.execute(
                        "INSERT INTO security_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (_id("candidate"), market, symbol, date, industry, growth, quality, valuation, momentum, liquidity,
                         round(base, 2), adjustment, "质量与趋势提供正向修正", round(base + adjustment, 2), rank,
                         0, "", "sample", now),
                    )
            self._conn.execute(
                "INSERT INTO reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (_id("report"), "market", "三市场晨会摘要（示例）", "A股、港股、美股的最新本地示例快照。",
                 "# 三市场晨会摘要\n\n本报告用于展示恒值投资工作台结构。连接并刷新真实数据源后，系统将以带证据日期的研究结果替换示例内容。",
                 None, None, date, "bootstrap", None, "sample", now),
            )
            self._conn.commit()

    def remove_seed_data(self) -> dict[str, int]:
        """Remove explicitly marked sample rows without touching live or agent data."""
        tables = (
            "security_candidates",
            "company_dossiers",
            "reports",
            "sector_scores",
            "macro_briefs",
            "market_snapshots",
        )
        removed: dict[str, int] = {}
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for table in tables:
                    cursor = self._conn.execute(f"DELETE FROM {table} WHERE source_status='sample'")  # noqa: S608
                    removed[table] = max(0, cursor.rowcount)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return removed

    @staticmethod
    def _row(row: sqlite3.Row | None, json_fields: Iterable[str] = ()) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        for field in json_fields:
            value[field.removesuffix("_json")] = _loads(value.pop(field, None), [] if field.endswith("s_json") else {})
        for key in ("excluded",):
            if key in value:
                value[key] = bool(value[key])
        return value

    def latest_dashboard(self) -> dict[str, Any]:
        with self._lock:
            markets = []
            for market in MARKET_META:
                snapshot = self._row(self._conn.execute(
                    "SELECT * FROM market_snapshots WHERE market=? ORDER BY as_of DESC, created_at DESC LIMIT 1", (market,)
                ).fetchone(), ("metrics_json", "risks_json"))
                macro = self.latest_macro(market)
                sectors = self.list_sectors(market, limit=4)
                candidates = self.list_candidates(market, limit=3)
                markets.append({"market": market, "meta": MARKET_META[market], "snapshot": snapshot, "macro": macro, "sectors": sectors, "candidates": candidates})
            reports = self.list_reports(limit=5)
            runs = [dict(row) for row in self._conn.execute("SELECT * FROM research_runs ORDER BY started_at DESC LIMIT 5").fetchall()]
            return {"markets": markets, "reports": reports, "research_runs": runs, "generated_at": _now()}

    def latest_macro(self, market: str) -> dict[str, Any] | None:
        market = normalize_market(market)
        with self._lock:
            row = self._conn.execute("SELECT * FROM macro_briefs WHERE market=? ORDER BY as_of DESC, created_at DESC LIMIT 1", (market,)).fetchone()
            return self._row(row, ("themes_json", "risks_json"))

    def list_sectors(self, market: str, *, limit: int = 50) -> list[dict[str, Any]]:
        market = normalize_market(market)
        with self._lock:
            latest = self._conn.execute("SELECT MAX(as_of) FROM sector_scores WHERE market=?", (market,)).fetchone()[0]
            if not latest:
                return []
            return [dict(row) for row in self._conn.execute(
                "SELECT * FROM sector_scores WHERE market=? AND as_of=? ORDER BY rank LIMIT ?", (market, latest, limit)
            ).fetchall()]

    def list_candidates(self, market: str, *, limit: int = 50, query: str = "") -> list[dict[str, Any]]:
        market = normalize_market(market)
        with self._lock:
            latest = self._conn.execute("SELECT MAX(as_of) FROM security_candidates WHERE market=?", (market,)).fetchone()[0]
            if not latest:
                return []
            needle = f"%{query.strip()}%"
            rows = self._conn.execute(
                """SELECT c.*, s.name, s.currency, s.exchange, s.taxonomy, s.sector_name
                   FROM security_candidates c JOIN securities s USING(market, symbol)
                   WHERE c.market=? AND c.as_of=? AND (c.symbol LIKE ? OR s.name LIKE ?)
                   ORDER BY c.excluded, c.rank LIMIT ?""",
                (market, latest, needle, needle, limit),
            ).fetchall()
            return [self._row(row) for row in rows if row is not None]

    def get_dossier(self, market: str, symbol: str) -> dict[str, Any] | None:
        market, symbol = normalize_market(market), normalize_symbol(market, symbol)
        with self._lock:
            row = self._conn.execute(
                """SELECT d.*, s.name, s.currency, s.exchange, s.taxonomy, s.sector_name
                   FROM company_dossiers d JOIN securities s USING(market, symbol)
                   WHERE d.market=? AND d.symbol=?""", (market, symbol)
            ).fetchone()
            return self._row(row, ("metrics_json", "catalysts_json", "risks_json"))

    def upsert_tdx_dossier(self, overview: dict[str, Any]) -> dict[str, Any]:
        """Build a factual CN research dossier from the read-only TDX cache."""
        market = "CN"
        symbol = normalize_symbol(market, str(overview.get("code") or ""))
        name = str(overview.get("name") or symbol)
        quote = overview.get("quote") if isinstance(overview.get("quote"), dict) else {}
        finance = overview.get("fundamental") if isinstance(overview.get("fundamental"), dict) else {}
        sectors = overview.get("sectors") if isinstance(overview.get("sectors"), list) else []
        sector_names = [
            str(item.get("sector_name") or item.get("name") or "").strip()
            for item in sectors
            if isinstance(item, dict) and (item.get("sector_name") or item.get("name"))
        ]
        sector_codes = [
            str(item.get("sector_code") or item.get("code") or "").strip()
            for item in sectors
            if isinstance(item, dict) and (item.get("sector_code") or item.get("code"))
        ]
        primary_sector = sector_names[0] if sector_names else "未分类"
        primary_sector_code = sector_codes[0] if sector_codes else ""
        as_of = str(overview.get("as_of") or _now())
        data_as_of = as_of[:10]
        source_status = "stale" if bool((overview.get("cache") or {}).get("stale")) else "live"
        metrics = {
            "price": quote.get("price"), "change_pct": quote.get("change_pct"),
            "market_cap_100m": finance.get("market_cap_100m"),
            "revenue_10k": finance.get("revenue_10k"),
            "net_profit_10k": finance.get("net_profit_10k"), "eps": finance.get("eps"),
            "pe_ttm": finance.get("pe_ttm"), "pb_mrq": finance.get("pb_mrq"),
            "dividend_yield": finance.get("dividend_yield"), "report_date": finance.get("report_date"),
        }
        available = {key: value for key, value in metrics.items() if value not in (None, "", "--")}
        metric_text = "、".join(f"{key}={value}" for key, value in list(available.items())[:7]) or "核心财务快照尚未缓存"
        sector_text = "、".join(sector_names[:8]) or "板块关系尚未缓存"
        overview_text = (
            f"{name}（{symbol}）的只读通达信事实底稿。截至 {data_as_of}，"
            f"已取得的关键快照为：{metric_text}。所属板块包括：{sector_text}。"
            "本底稿用于后续多空验证，不直接构成买卖建议。"
        )
        bull_thesis = (
            "正向证据需围绕盈利质量、现金流、行业地位和估值持续验证。"
            f"当前可核验快照中，净利润为 {finance.get('net_profit_10k', '暂缺')} 万元，"
            f"股息率为 {finance.get('dividend_yield', '暂缺')}%。"
        )
        bear_thesis = (
            "主要反证包括业绩不及预期、估值收缩、板块景气转弱和关键价位失效。"
            f"当前专业财务历史{'可用' if overview.get('professional_finance_available') else '未下载'}，"
            "缺失项必须在形成买卖点前人工复核。"
        )
        catalysts = ["下一份定期报告及业绩预告", "分红、回购、解禁与重大公告", "所属行业相对强弱变化"]
        risks = ["完整历史三大报表与现金流趋势需复核", "实时行情不能替代基本面研究", "买卖点必须设置失效条件和仓位上限"]
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO securities
                   (market, symbol, name, currency, exchange, taxonomy, sector_code, sector_name, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market, symbol) DO UPDATE SET
                     name=excluded.name, sector_code=excluded.sector_code,
                     sector_name=excluded.sector_name, updated_at=excluded.updated_at""",
                (market, symbol, name, "CNY", "SH/SZ/BJ", "通达信", primary_sector_code, primary_sector, now),
            )
            self._conn.execute(
                """INSERT INTO company_dossiers
                   (id, market, symbol, overview, bull_thesis, bear_thesis, metrics_json,
                    catalysts_json, risks_json, data_as_of, source_status, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market, symbol) DO UPDATE SET
                     overview=excluded.overview, bull_thesis=excluded.bull_thesis,
                     bear_thesis=excluded.bear_thesis, metrics_json=excluded.metrics_json,
                     catalysts_json=excluded.catalysts_json, risks_json=excluded.risks_json,
                     data_as_of=excluded.data_as_of, source_status=excluded.source_status,
                     updated_at=excluded.updated_at""",
                (
                    _id("dossier"), market, symbol, overview_text, bull_thesis, bear_thesis,
                    _dumps(metrics), _dumps(catalysts), _dumps(risks), data_as_of,
                    source_status, now,
                ),
            )
            self._conn.commit()
        return self.get_dossier(market, symbol) or {}

    def upsert_company_dossier(
        self,
        *,
        market: str,
        symbol: str,
        name: str,
        exchange: str,
        sector_code: str,
        sector_name: str,
        overview: str,
        bull_thesis: str,
        bear_thesis: str,
        metrics: dict[str, Any],
        catalysts: list[str],
        risks: list[str],
        data_as_of: str,
        source_status: str,
    ) -> dict[str, Any]:
        """Upsert a factual HK/US company dossier from an external source."""
        market = normalize_market(market)
        if market not in {"HK", "US"}:
            raise ValueError("external company dossiers support HK and US only")
        symbol = normalize_symbol(market, symbol)
        if source_status not in {"live", "stale"}:
            raise ValueError("source_status must be live or stale")
        now = _now()
        date = self._data_date(data_as_of)
        with self._lock:
            self._conn.execute(
                """INSERT INTO securities
                   (market, symbol, name, currency, exchange, taxonomy, sector_code, sector_name, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market, symbol) DO UPDATE SET
                     name=excluded.name, currency=excluded.currency, exchange=excluded.exchange,
                     taxonomy=excluded.taxonomy, sector_code=excluded.sector_code,
                     sector_name=excluded.sector_name, updated_at=excluded.updated_at""",
                (market, symbol, name, MARKET_META[market]["currency"], exchange,
                 MARKET_META[market]["taxonomy"], sector_code, sector_name, now),
            )
            self._conn.execute(
                """INSERT INTO company_dossiers
                   (id, market, symbol, overview, bull_thesis, bear_thesis, metrics_json,
                    catalysts_json, risks_json, data_as_of, source_status, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market, symbol) DO UPDATE SET
                     overview=excluded.overview, bull_thesis=excluded.bull_thesis,
                     bear_thesis=excluded.bear_thesis, metrics_json=excluded.metrics_json,
                     catalysts_json=excluded.catalysts_json, risks_json=excluded.risks_json,
                     data_as_of=excluded.data_as_of, source_status=excluded.source_status,
                     updated_at=excluded.updated_at""",
                (_id("dossier"), market, symbol, overview, bull_thesis, bear_thesis,
                 _dumps(metrics), _dumps(catalysts), _dumps(risks), date, source_status, now),
            )
            self._conn.commit()
        return self.get_dossier(market, symbol) or {}

    def create_research_run(
        self,
        kind: str,
        market: str | None = None,
        symbol: str | None = None,
        *,
        message: str = "",
        status: str = "completed",
        linked_run_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"queued", "running", "completed", "failed", "cancelled"}:
            raise ValueError("invalid research run status")
        rid, now = _id("research"), _now()
        with self._lock:
            completed_at = now if status in {"completed", "failed", "cancelled"} else None
            self._conn.execute(
                "INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, kind, market, symbol, status, message, linked_run_id, now, completed_at),
            )
            self._conn.commit()
        return self.get_research_run(rid) or {}

    def get_research_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def update_research_run(self, run_id: str, status: str, message: str = "") -> dict[str, Any]:
        """Update one research run without creating a misleading duplicate run."""
        if status not in {"queued", "running", "completed", "failed", "cancelled"}:
            raise ValueError("invalid research run status")
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM research_runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError("research run not found")
            completed_at = _now() if status in {"completed", "failed", "cancelled"} else None
            self._conn.execute(
                """UPDATE research_runs
                   SET status=?, message=CASE WHEN ?='' THEN message ELSE ? END, completed_at=?
                   WHERE id=?""",
                (status, message, message, completed_at, run_id),
            )
            self._conn.commit()
        return self.get_research_run(run_id) or {}

    @staticmethod
    def _score(value: Any, field: str) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a number") from exc
        if not 0 <= score <= 100:
            raise ValueError(f"{field} must be between 0 and 100")
        return score

    @staticmethod
    def _adjustment(value: Any) -> float:
        try:
            adjustment = float(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("agent_adjustment must be a number") from exc
        if not -5 <= adjustment <= 5:
            raise ValueError("agent_adjustment must be between -5 and 5")
        return adjustment

    @staticmethod
    def _data_date(value: Any) -> str:
        date = str(value or "").strip()[:10]
        try:
            datetime.fromisoformat(date)
        except ValueError as exc:
            raise ValueError("data_as_of must be an ISO date") from exc
        return date

    def publish_research_results(self, run_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Atomically publish an Agent refresh into queryable workspace tables.

        The run determines both the required section and target markets. Live
        and stale publications require dated evidence; unavailable results are
        recorded on the run but never replace the last usable snapshot.
        """
        run = self.get_research_run(run_id)
        if not run:
            raise KeyError("research run not found")
        if run["status"] in {"completed", "failed", "cancelled"}:
            raise ValueError("research run is already terminal")
        kind = str(run["kind"])
        required_sections = {
            "macro": {"macro"},
            "sectors": {"sectors"},
            "screener": {"candidates"},
            "all": {"snapshot", "macro", "sectors", "candidates"},
        }.get(kind)
        if required_sections is None:
            raise ValueError("research run kind cannot publish workspace refresh results")
        expected_markets = {normalize_market(run["market"])} if run.get("market") else set(MARKET_META)
        if not isinstance(results, list) or not results:
            raise ValueError("results must contain one item per target market")
        actual_markets = {normalize_market(item.get("market", "")) for item in results}
        if actual_markets != expected_markets or len(results) != len(actual_markets):
            raise ValueError(f"results must cover exactly: {', '.join(sorted(expected_markets))}")

        prepared: list[dict[str, Any]] = []
        for raw in results:
            market = normalize_market(raw.get("market", ""))
            data_as_of = self._data_date(raw.get("data_as_of"))
            source_status = str(raw.get("source_status") or "").strip().lower()
            if source_status not in {"live", "stale", "unavailable"}:
                raise ValueError("source_status must be live, stale, or unavailable")
            evidence = raw.get("evidence") or []
            if not isinstance(evidence, list):
                raise ValueError("evidence must be an array")
            if source_status != "unavailable" and not evidence:
                raise ValueError(f"{market} requires at least one evidence item")
            normalized_evidence = []
            for item in evidence:
                if not isinstance(item, dict) or not str(item.get("source") or "").strip():
                    raise ValueError("each evidence item requires source")
                normalized_evidence.append({
                    "source": str(item["source"]).strip(),
                    "url": str(item.get("url") or "").strip(),
                    "data_as_of": self._data_date(item.get("data_as_of") or data_as_of),
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                })
            if source_status != "unavailable":
                missing = required_sections - set(raw)
                if missing:
                    raise ValueError(f"{market} result is missing sections: {', '.join(sorted(missing))}")
                for section in required_sections:
                    if section in {"sectors", "candidates"} and not isinstance(raw.get(section), list):
                        raise ValueError(f"{section} must be an array")
                    if section in {"sectors", "candidates"} and not raw.get(section):
                        raise ValueError(f"{section} cannot be empty unless source_status is unavailable")
                    if section in {"snapshot", "macro"} and not isinstance(raw.get(section), dict):
                        raise ValueError(f"{section} must be an object")
                    if section in {"snapshot", "macro"} and not raw.get(section):
                        raise ValueError(f"{section} cannot be empty unless source_status is unavailable")
            prepared.append({**raw, "market": market, "data_as_of": data_as_of,
                             "source_status": source_status, "evidence": normalized_evidence})

        now = _now()
        published: dict[str, list[str]] = {}
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("UPDATE research_runs SET status='running' WHERE id=?", (run_id,))
                self._conn.execute("DELETE FROM research_evidence WHERE run_id=?", (run_id,))
                for item in prepared:
                    market = item["market"]
                    date = item["data_as_of"]
                    status = item["source_status"]
                    published[market] = []
                    for section in required_sections:
                        for evidence in item["evidence"]:
                            self._conn.execute(
                                "INSERT INTO research_evidence VALUES(?,?,?,?,?,?,?,?,?)",
                                (_id("evidence"), run_id, market, section, evidence["source"],
                                 evidence["url"], evidence["data_as_of"], _dumps(evidence["metadata"]), now),
                            )
                    if status == "unavailable":
                        published[market].append("unavailable")
                        continue
                    if "snapshot" in required_sections:
                        value = item["snapshot"]
                        self._conn.execute("DELETE FROM market_snapshots WHERE market=? AND as_of=?", (market, date))
                        self._conn.execute(
                            "INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                            (_id("mkt"), market, date, str(value.get("status") or "ready"),
                             str(value.get("summary") or ""), _dumps(value.get("metrics") or {}),
                             _dumps(value.get("risks") or []), status, now),
                        )
                        published[market].append("snapshot")
                    if "macro" in required_sections:
                        value = item["macro"]
                        self._conn.execute("DELETE FROM macro_briefs WHERE market=? AND as_of=?", (market, date))
                        self._conn.execute(
                            "INSERT INTO macro_briefs VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (_id("macro"), market, date, str(value.get("headline") or ""),
                             str(value.get("stance") or "neutral"), str(value.get("summary") or ""),
                             _dumps(value.get("themes") or []), _dumps(value.get("risks") or []), status, now),
                        )
                        published[market].append("macro")
                    if "sectors" in required_sections:
                        rows = []
                        for value in item["sectors"]:
                            dims = [self._score(value.get(key), key) for key in
                                    ("momentum", "earnings", "fund_flow", "breadth", "valuation", "risk")]
                            base = dims[0] * .25 + dims[1] * .20 + dims[2] * .15 + dims[3] * .15 + dims[4] * .15 + dims[5] * .10
                            adjustment = self._adjustment(value.get("agent_adjustment"))
                            rows.append((base + adjustment, value, dims, base, adjustment))
                        rows.sort(key=lambda row: row[0], reverse=True)
                        self._conn.execute("DELETE FROM sector_scores WHERE market=? AND as_of=?", (market, date))
                        for rank, (final, value, dims, base, adjustment) in enumerate(rows, 1):
                            self._conn.execute(
                                "INSERT INTO sector_scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (_id("sector"), market, MARKET_META[market]["taxonomy"],
                                 str(value.get("sector_code") or ""), str(value.get("sector_name") or ""), date,
                                 *dims, round(base, 2), adjustment, str(value.get("agent_reason") or ""),
                                 round(final, 2), rank, status, now),
                            )
                        published[market].append("sectors")
                    if "candidates" in required_sections:
                        rows = []
                        for value in item["candidates"]:
                            symbol = normalize_symbol(market, str(value.get("symbol") or ""))
                            dims = [self._score(value.get(key), key) for key in
                                    ("industry_position", "growth", "quality", "valuation", "momentum", "liquidity")]
                            base = dims[0] * .20 + dims[1] * .20 + dims[2] * .20 + dims[3] * .15 + dims[4] * .15 + dims[5] * .10
                            adjustment = self._adjustment(value.get("agent_adjustment"))
                            rows.append((bool(value.get("excluded")), base + adjustment, symbol, value, dims, base, adjustment))
                        rows.sort(key=lambda row: (row[0], -row[1]))
                        self._conn.execute("DELETE FROM security_candidates WHERE market=? AND as_of=?", (market, date))
                        for rank, (excluded, final, symbol, value, dims, base, adjustment) in enumerate(rows, 1):
                            self._conn.execute(
                                """INSERT INTO securities VALUES(?,?,?,?,?,?,?,?,?)
                                   ON CONFLICT(market, symbol) DO UPDATE SET name=excluded.name,
                                     currency=excluded.currency, exchange=excluded.exchange,
                                     taxonomy=excluded.taxonomy, sector_code=excluded.sector_code,
                                     sector_name=excluded.sector_name, updated_at=excluded.updated_at""",
                                (market, symbol, str(value.get("name") or symbol), MARKET_META[market]["currency"],
                                 str(value.get("exchange") or ""), MARKET_META[market]["taxonomy"],
                                 str(value.get("sector_code") or ""), str(value.get("sector_name") or ""), now),
                            )
                            self._conn.execute(
                                "INSERT INTO security_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (_id("candidate"), market, symbol, date, *dims, round(base, 2), adjustment,
                                 str(value.get("agent_reason") or ""), round(final, 2), rank, int(excluded),
                                 str(value.get("exclusion_reason") or ""), status, now),
                            )
                        published[market].append("candidates")
                message = "结构化研究结果已写入工作台；已保留来源与数据日期。"
                self._conn.execute(
                    "UPDATE research_runs SET status='completed', message=?, completed_at=? WHERE id=?",
                    (message, now, run_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {"run": self.get_research_run(run_id), "published": published}

    def list_research_evidence(self, market: str, section: str, data_as_of: str) -> list[dict[str, Any]]:
        """Return evidence attached to the newest publication for one section/date."""
        market = normalize_market(market)
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, source, url, data_as_of, metadata_json, run_id, created_at
                   FROM research_evidence
                   WHERE market=? AND section=? AND data_as_of<=?
                   ORDER BY created_at DESC""",
                (market, section, self._data_date(data_as_of)),
            ).fetchall()
            if not rows:
                return []
            latest_run = rows[0]["run_id"]
            return [
                {
                    "id": row["id"],
                    "source": row["source"],
                    "url": row["url"],
                    "data_as_of": row["data_as_of"],
                    "metadata": _loads(row["metadata_json"], {}),
                    "run_id": row["run_id"],
                }
                for row in rows
                if row["run_id"] == latest_run
            ]

    def list_reports(self, *, limit: int = 100, report_type: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if report_type:
                rows = self._conn.execute("SELECT * FROM reports WHERE report_type=? ORDER BY created_at DESC LIMIT ?", (report_type, limit)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
            return dict(row) if row else None

    def create_company_report(self, market: str, symbol: str) -> dict[str, Any]:
        dossier = self.get_dossier(market, symbol)
        if not dossier:
            raise KeyError("security dossier not found")
        report_id, now = _id("report"), _now()
        metrics = "\n".join(f"- {key}: {value}" for key, value in dossier.get("metrics", {}).items())
        catalysts = "\n".join(f"- {item}" for item in dossier.get("catalysts", []))
        risks = "\n".join(f"- {item}" for item in dossier.get("risks", []))
        content = (
            f"# {dossier['name']}深度研究底稿\n\n"
            f"> 数据日期：{dossier['data_as_of']}；来源状态：{dossier['source_status']}。"
            "本文是研究工作底稿，不构成自动交易指令。\n\n"
            f"## 公司与数据概览\n\n{dossier['overview']}\n\n"
            f"## 正向证据与待验证逻辑\n\n{dossier['bull_thesis']}\n\n"
            f"## 反向证据与风险逻辑\n\n{dossier['bear_thesis']}\n\n"
            f"## 核心指标\n\n{metrics or '- 暂无'}\n\n"
            f"## 催化剂检查清单\n\n{catalysts or '- 暂无'}\n\n"
            f"## 风险检查清单\n\n{risks or '- 暂无'}"
        )
        with self._lock:
            self._conn.execute("INSERT INTO reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                report_id, "company", f"{dossier['name']}深度研究底稿", dossier["overview"], content,
                dossier["market"], dossier["symbol"], dossier["data_as_of"], "company_research", dossier["id"], dossier["source_status"], now,
            ))
            self._conn.commit()
        return self.get_report(report_id) or {}

    def create_committee(self, market: str, symbol: str, company_name: str, swarm_run_id: str | None) -> dict[str, Any]:
        market, symbol = normalize_market(market), normalize_symbol(market, symbol)
        cid, now = _id("committee"), _now()
        with self._lock:
            self._conn.execute("INSERT INTO committees VALUES(?,?,?,?,?,?,?,?)", (cid, market, symbol, company_name, "running" if swarm_run_id else "draft", swarm_run_id, now, None))
            self._conn.commit()
        return self.get_committee(cid) or {}

    def attach_committee_run(self, committee_id: str, swarm_run_id: str) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE committees SET swarm_run_id=?,status='running' WHERE id=?",
                (swarm_run_id, committee_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("committee not found")
            self._conn.commit()
        return self.get_committee(committee_id) or {}

    def list_committees(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute("SELECT * FROM committees ORDER BY created_at DESC").fetchall()]

    def get_committee(self, committee_id: str) -> dict[str, Any] | None:
        with self._lock:
            committee = self._conn.execute("SELECT * FROM committees WHERE id=?", (committee_id,)).fetchone()
            if not committee:
                return None
            result = dict(committee)
            decision = self._conn.execute("SELECT * FROM committee_decisions WHERE committee_id=?", (committee_id,)).fetchone()
            result["decision"] = self._row(decision, ("review_triggers_json",))
            outputs = self._conn.execute(
                "SELECT * FROM committee_participant_outputs WHERE committee_id=? ORDER BY updated_at, task_id",
                (committee_id,),
            ).fetchall()
            result["participants"] = [self._row(row, ("evidence_json",)) for row in outputs]
            return result

    def upsert_committee_participant_output(
        self,
        committee_id: str,
        task_id: str,
        role: str,
        status: str,
        content: str = "",
        *,
        evidence: list[dict[str, Any]] | None = None,
        data_as_of: str | None = None,
    ) -> dict[str, Any]:
        if not self.get_committee(committee_id):
            raise KeyError("committee not found")
        now = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM committee_participant_outputs WHERE committee_id=? AND task_id=?",
                (committee_id, task_id),
            ).fetchone()
            output_id = existing[0] if existing else _id("participant")
            self._conn.execute(
                """INSERT INTO committee_participant_outputs
                   (id, committee_id, task_id, role, status, content, evidence_json, data_as_of, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(committee_id, task_id) DO UPDATE SET
                     role=excluded.role, status=excluded.status, content=excluded.content,
                     evidence_json=excluded.evidence_json, data_as_of=excluded.data_as_of,
                     updated_at=excluded.updated_at""",
                (output_id, committee_id, task_id, role, status, content, _dumps(evidence or []), data_as_of, now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM committee_participant_outputs WHERE id=?", (output_id,)).fetchone()
            return self._row(row, ("evidence_json",)) or {}

    def update_committee_status(self, committee_id: str, status: str, final_report: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            current = self.get_committee(committee_id)
            if not current:
                return None
            terminal = status in {"completed", "failed", "cancelled"}
            self._conn.execute("UPDATE committees SET status=?, completed_at=? WHERE id=?", (status, _now() if terminal else None, committee_id))
            if status == "completed":
                if final_report:
                    now = _now()
                    self._conn.execute("INSERT INTO reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                        _id("report"), "committee", f"{current['company_name']}投资委员会报告",
                        final_report[:240], final_report, current["market"], current["symbol"],
                        now[:10], "committee", committee_id, "agent", now,
                    ))
                self._conn.commit()
                return self.get_committee(committee_id)
            if status == "completed" and not current.get("decision"):
                summary = final_report or "投委会已完成，等待人工复核详细结论。"
                self._conn.execute("INSERT INTO committee_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    committee_id, "wait", None, None, None, None, "待复核", 0,
                    _dumps(["基本面发生重大变化", "价格触及关键区间"]), _now()[:10], summary,
                ))
                plan_id, now = _id("plan"), _now()
                self._conn.execute("INSERT INTO trade_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    plan_id, committee_id, current["market"], current["symbol"], current["company_name"], "draft", "wait",
                    None, None, None, None, None, None, _dumps(["等待人工确认"]), summary[:1000], now, now,
                ))
                report_id = _id("report")
                self._conn.execute("INSERT INTO reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    report_id, "committee", f"{current['company_name']}投资委员会决议", summary[:240], summary,
                    current["market"], current["symbol"], _now()[:10], "committee", committee_id, "agent", now,
                ))
            self._conn.commit()
        return self.get_committee(committee_id)

    def list_trade_plans(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute("SELECT * FROM trade_plans WHERE status=? ORDER BY updated_at DESC", (status,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM trade_plans ORDER BY updated_at DESC").fetchall()
            return [self._row(row, ("triggers_json",)) for row in rows if row is not None]

    def create_trade_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        market = normalize_market(payload.get("market", ""))
        symbol = normalize_symbol(market, payload.get("symbol", ""))
        status = payload.get("status", "draft")
        if status not in {"draft", "active", "triggered", "closed", "cancelled"}:
            raise ValueError("invalid trade plan status")
        pid, now = _id("plan"), _now()
        values = (pid, payload.get("committee_id"), market, symbol, payload.get("name") or symbol, status,
                  payload.get("direction", "wait"), payload.get("position_cap"), payload.get("entry_low"), payload.get("entry_high"),
                  payload.get("target_low"), payload.get("target_high"), payload.get("stop_price"), _dumps(payload.get("triggers", [])),
                  payload.get("notes", ""), now, now)
        with self._lock:
            self._conn.execute("INSERT INTO trade_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM trade_plans WHERE id=?", (pid,)).fetchone()
            return self._row(row, ("triggers_json",)) or {}

    def update_trade_plan(self, plan_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"status", "direction", "position_cap", "entry_low", "entry_high", "target_low", "target_high", "stop_price", "notes"}
        fields, values = [], []
        for key in allowed:
            if key in payload:
                if key == "status" and payload[key] not in {"draft", "active", "triggered", "closed", "cancelled"}:
                    raise ValueError("invalid trade plan status")
                fields.append(f"{key}=?")
                values.append(payload[key])
        if "triggers" in payload:
            fields.append("triggers_json=?")
            values.append(_dumps(payload["triggers"]))
        if not fields:
            return next((p for p in self.list_trade_plans() if p["id"] == plan_id), None)
        fields.append("updated_at=?")
        values.extend([_now(), plan_id])
        with self._lock:
            self._conn.execute(f"UPDATE trade_plans SET {', '.join(fields)} WHERE id=?", values)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM trade_plans WHERE id=?", (plan_id,)).fetchone()
            return self._row(row, ("triggers_json",))

    def list_portfolios(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM portfolios ORDER BY updated_at DESC").fetchall()
            result = []
            for row in rows:
                item = self._row(row, ("cash_json",)) or {}
                item["position_count"] = len(self.portfolio_positions(item["id"]))
                result.append(item)
            return result

    def create_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("portfolio name is required")
        base = str(payload.get("base_currency", "CNY")).upper()
        if base not in {"CNY", "HKD", "USD"}:
            raise ValueError("base_currency must be CNY, HKD or USD")
        initial = float(payload.get("initial_cash", 0) or 0)
        pid, now = _id("portfolio"), _now()
        cash = payload.get("cash") or {base: initial}
        with self._lock:
            self._conn.execute("INSERT INTO portfolios VALUES(?,?,?,?,?,?,?,?)", (pid, name, base, str(payload.get("benchmark", "")), initial, _dumps(cash), now, now))
            self._conn.commit()
        return self.get_portfolio(pid) or {}

    def get_portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
            return self._row(row, ("cash_json",))

    def update_portfolio(self, portfolio_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"name", "benchmark", "base_currency"}
        fields, values = [], []
        for key in allowed:
            if key in payload:
                fields.append(f"{key}=?")
                values.append(payload[key])
        if not fields:
            return self.get_portfolio(portfolio_id)
        fields.append("updated_at=?")
        values.extend([_now(), portfolio_id])
        with self._lock:
            self._conn.execute(f"UPDATE portfolios SET {', '.join(fields)} WHERE id=?", values)
            self._conn.commit()
        return self.get_portfolio(portfolio_id)

    def delete_portfolio(self, portfolio_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM portfolios WHERE id=?", (portfolio_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def add_transaction(self, portfolio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            raise KeyError("portfolio not found")
        market = normalize_market(payload.get("market", ""))
        symbol = normalize_symbol(market, payload.get("symbol", ""))
        side = str(payload.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        quantity, price = float(payload.get("quantity", 0)), float(payload.get("price", 0))
        fee = float(payload.get("fee", 0) or 0)
        if quantity <= 0 or price <= 0 or fee < 0:
            raise ValueError("quantity and price must be positive; fee cannot be negative")
        currency = str(payload.get("currency") or MARKET_META[market]["currency"]).upper()
        if side == "sell":
            positions = {p["symbol"]: p for p in self.portfolio_positions(portfolio_id)}
            if quantity > positions.get(symbol, {}).get("quantity", 0) + 1e-9:
                raise ValueError("sell quantity exceeds current position")
        txid, now = _id("tx"), _now()
        values = (txid, portfolio_id, market, symbol, payload.get("name") or symbol, side,
                  str(payload.get("trade_date") or now[:10]), quantity, price, fee, currency, str(payload.get("notes", "")), now)
        cash = dict(portfolio.get("cash") or {})
        current_cash = float(cash.get(currency, 0) or 0)
        cash_delta = -(quantity * price + fee) if side == "buy" else quantity * price - fee
        cash[currency] = current_cash + cash_delta
        with self._lock:
            self._conn.execute("INSERT INTO portfolio_transactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            self._conn.execute("UPDATE portfolios SET cash_json=?, updated_at=? WHERE id=?", (_dumps(cash), now, portfolio_id))
            self._conn.commit()
            return dict(self._conn.execute("SELECT * FROM portfolio_transactions WHERE id=?", (txid,)).fetchone())

    def import_transactions(self, portfolio_id: str, csv_text: str) -> dict[str, Any]:
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        required = {"market", "symbol", "name", "side", "trade_date", "quantity", "price", "fee", "currency"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"CSV missing columns: {', '.join(missing)}")
        imported, errors = [], []
        for line, row in enumerate(reader, 2):
            try:
                imported.append(self.add_transaction(portfolio_id, row))
            except (ValueError, KeyError) as exc:
                errors.append({"line": line, "error": str(exc)})
        return {"imported": len(imported), "errors": errors}

    def _latest_price(self, market: str, symbol: str) -> float | None:
        row = self._conn.execute("SELECT metrics_json FROM company_dossiers WHERE market=? AND symbol=?", (market, symbol)).fetchone()
        metrics = _loads(row[0], {}) if row else {}
        price = metrics.get("price")
        return float(price) if isinstance(price, (int, float)) else None

    def record_exchange_rate(
        self,
        base_currency: str,
        quote_currency: str,
        rate: float,
        as_of: str,
        *,
        source: str,
        evidence: str,
    ) -> None:
        """Persist a dated FX observation used for auditable aggregation."""
        base, quote = base_currency.upper(), quote_currency.upper()
        if base not in {"CNY", "HKD", "USD"} or quote not in {"CNY", "HKD", "USD"}:
            raise ValueError("unsupported currency")
        if base == quote or rate <= 0 or not as_of or not source or not evidence:
            raise ValueError("FX rate requires distinct currencies, positive rate, date, source and evidence")
        with self._lock:
            self._conn.execute(
                """INSERT INTO exchange_rates VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(base_currency, quote_currency, as_of) DO UPDATE SET
                     rate=excluded.rate, source=excluded.source,
                     evidence=excluded.evidence, created_at=excluded.created_at""",
                (base, quote, float(rate), as_of, source, evidence, _now()),
            )
            self._conn.commit()

    def _fx_rate(self, currency: str, base_currency: str, as_of: str) -> tuple[float, dict[str, Any]] | None:
        if currency == base_currency:
            return 1.0, {"base_currency": currency, "quote_currency": base_currency, "rate": 1.0, "as_of": as_of, "source": "identity"}
        direct = self._conn.execute(
            "SELECT * FROM exchange_rates WHERE base_currency=? AND quote_currency=? AND as_of=?",
            (currency, base_currency, as_of),
        ).fetchone()
        if direct:
            value = dict(direct)
            return float(value["rate"]), value
        inverse = self._conn.execute(
            "SELECT * FROM exchange_rates WHERE base_currency=? AND quote_currency=? AND as_of=?",
            (base_currency, currency, as_of),
        ).fetchone()
        if inverse:
            value = dict(inverse)
            value["inverted"] = True
            return 1.0 / float(value["rate"]), value
        return None

    def portfolio_positions(self, portfolio_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM portfolio_transactions WHERE portfolio_id=? ORDER BY trade_date, created_at", (portfolio_id,)).fetchall()
            states: dict[tuple[str, str], dict[str, Any]] = {}
            for raw in rows:
                tx = dict(raw)
                key = (tx["market"], tx["symbol"])
                state = states.setdefault(key, {"market": tx["market"], "symbol": tx["symbol"], "name": tx["name"], "currency": tx["currency"], "quantity": 0.0, "cost": 0.0, "realized_pnl": 0.0})
                if tx["side"] == "buy":
                    state["quantity"] += tx["quantity"]
                    state["cost"] += tx["quantity"] * tx["price"] + tx["fee"]
                else:
                    avg = state["cost"] / state["quantity"] if state["quantity"] else 0
                    state["realized_pnl"] += tx["quantity"] * tx["price"] - tx["fee"] - avg * tx["quantity"]
                    state["quantity"] -= tx["quantity"]
                    state["cost"] -= avg * tx["quantity"]
            result = []
            for state in states.values():
                if state["quantity"] <= 1e-9:
                    continue
                state["average_cost"] = state["cost"] / state["quantity"]
                state["latest_price"] = self._latest_price(state["market"], state["symbol"])
                state["market_value"] = state["quantity"] * state["latest_price"] if state["latest_price"] is not None else None
                state["unrealized_pnl"] = state["market_value"] - state["cost"] if state["market_value"] is not None else None
                security = self._conn.execute(
                    "SELECT sector_code, sector_name, taxonomy FROM securities WHERE market=? AND symbol=?",
                    (state["market"], state["symbol"]),
                ).fetchone()
                if security:
                    state.update(dict(security))
                dossier = self._conn.execute(
                    "SELECT id FROM company_dossiers WHERE market=? AND symbol=?",
                    (state["market"], state["symbol"]),
                ).fetchone()
                state["dossier_id"] = dossier[0] if dossier else None
                result.append(state)
            return sorted(result, key=lambda row: (row["currency"], -(row["market_value"] or 0)))

    def portfolio_analytics(self, portfolio_id: str, *, as_of: str | None = None) -> dict[str, Any]:
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            raise KeyError("portfolio not found")
        as_of = as_of or _now()[:10]
        positions = self.portfolio_positions(portfolio_id)
        subtotals: dict[str, float] = {}
        for position in positions:
            if position["market_value"] is not None:
                subtotals[position["currency"]] = subtotals.get(position["currency"], 0) + position["market_value"]
        cash = portfolio.get("cash", {})
        for currency, value in cash.items():
            subtotals[currency] = subtotals.get(currency, 0) + float(value)
        fx_evidence: list[dict[str, Any]] = []
        converted: list[float] = []
        aggregate_available = True
        for currency, total in subtotals.items():
            fx = self._fx_rate(currency, portfolio["base_currency"], as_of)
            if fx is None:
                aggregate_available = False
                break
            rate, evidence = fx
            converted.append(total * rate)
            fx_evidence.append(evidence)
        base_total = sum(converted) if aggregate_available else None
        concentration = []
        for currency in subtotals:
            total = subtotals[currency]
            for position in positions:
                if position["currency"] == currency and position["market_value"] is not None and total:
                    concentration.append({"symbol": position["symbol"], "currency": currency, "weight": position["market_value"] / total})
        sector_values: dict[str, dict[str, Any]] = {}
        for position in positions:
            value = position.get("market_value")
            sector = position.get("sector_name") or "未分类"
            if value is None:
                continue
            key = f"{position['market']}:{sector}"
            entry = sector_values.setdefault(key, {"market": position["market"], "sector": sector, "currency": position["currency"], "market_value": 0.0})
            entry["market_value"] += value
        pending_plans = [
            plan for plan in self.list_trade_plans()
            if plan["status"] in {"draft", "active", "triggered"}
            and any(position["market"] == plan["market"] and position["symbol"] == plan["symbol"] for position in positions)
        ]
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO position_snapshots VALUES(?,?,?,?,?)
                   ON CONFLICT(portfolio_id, as_of) DO UPDATE SET
                     positions_json=excluded.positions_json, created_at=excluded.created_at""",
                (_id("positions"), portfolio_id, as_of, _dumps(positions), now),
            )
            self._conn.execute(
                """INSERT INTO portfolio_valuations VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(portfolio_id, as_of) DO UPDATE SET
                     currency_totals_json=excluded.currency_totals_json,
                     base_currency=excluded.base_currency,
                     base_currency_total=excluded.base_currency_total,
                     aggregate_available=excluded.aggregate_available,
                     fx_evidence_json=excluded.fx_evidence_json,
                     created_at=excluded.created_at""",
                (_id("valuation"), portfolio_id, as_of, _dumps(subtotals), portfolio["base_currency"], base_total, int(aggregate_available), _dumps(fx_evidence), now),
            )
            self._conn.commit()
            history = self._conn.execute(
                "SELECT as_of, base_currency_total FROM portfolio_valuations WHERE portfolio_id=? AND aggregate_available=1 AND base_currency_total IS NOT NULL ORDER BY as_of",
                (portfolio_id,),
            ).fetchall()
        peak = None
        max_drawdown = 0.0
        for row in history:
            total = float(row["base_currency_total"])
            peak = total if peak is None else max(peak, total)
            if peak:
                max_drawdown = min(max_drawdown, total / peak - 1)
        return {
            "portfolio": portfolio, "positions": positions, "subtotals": subtotals,
            "as_of": as_of, "base_currency_total": base_total, "aggregate_available": aggregate_available,
            "aggregate_warning": None if aggregate_available else "缺少同日汇率证据，组合仅按币种分项展示。",
            "fx_evidence": fx_evidence,
            "concentration": sorted(concentration, key=lambda row: row["weight"], reverse=True),
            "risk_alerts": [f"{row['symbol']} 权重超过 20%" for row in concentration if row["weight"] > .2],
            "sector_exposure": sorted(sector_values.values(), key=lambda row: row["market_value"], reverse=True),
            "performance": {
                "total_return": (base_total / portfolio["initial_cash"] - 1) if base_total is not None and portfolio["initial_cash"] else None,
                "max_drawdown": max_drawdown if history else None,
            },
            "correlation": {"status": "unavailable", "reason": "需要至少两个带日期的组合估值点和证券收益序列。"},
            "pending_trade_plans": pending_plans,
        }
