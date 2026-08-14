"""Append-oriented internal paper accounts, orders, fills, positions and NAV."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class PaperTradingStore:
    """A broker-independent ledger with idempotent signal submission."""

    SCHEMA_VERSION = 2

    MARKET_RULES = {
        "CN": {"buy_lot": 100, "commission_rate": .0003, "minimum_commission": 5.0, "sell_stamp_rate": .0005},
        "HK": {"buy_lot": None, "commission_rate": .0003, "minimum_commission": 0.0, "sell_stamp_rate": .001},
    }

    DEFAULT_ACCOUNTS = (
        ("paper_value_cn", "A股价值", "value", "long", "CN", "CNY", 1_000_000.0),
        ("paper_value_hk", "港股价值", "value", "long", "HK", "HKD", 1_000_000.0),
        ("paper_emotion_short", "情绪短线", "emotion", "short", "CN", "CNY", 1_000_000.0),
        ("paper_emotion_swing", "情绪波段", "emotion", "swing", "CN", "CNY", 1_000_000.0),
    )

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "paper.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._init_db()
        self._ensure_defaults()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    strategy_line TEXT NOT NULL, horizon TEXT NOT NULL,
                    market TEXT NOT NULL, currency TEXT NOT NULL,
                    initial_cash REAL NOT NULL, cash REAL NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, signal_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
                    quantity REAL NOT NULL, limit_price REAL,
                    status TEXT NOT NULL, submitted_at TEXT NOT NULL,
                    expires_at TEXT, rejection_reason TEXT NOT NULL DEFAULT '',
                    board_lot INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(account_id) REFERENCES paper_accounts(id) ON DELETE RESTRICT,
                    UNIQUE(account_id, signal_id)
                );
                CREATE TABLE IF NOT EXISTS paper_fills (
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, quantity REAL NOT NULL,
                    price REAL NOT NULL, fee REAL NOT NULL, currency TEXT NOT NULL,
                    filled_at TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    execution_key TEXT,
                    FOREIGN KEY(order_id) REFERENCES paper_orders(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS paper_cash_ledger (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    reference_id TEXT, amount REAL NOT NULL, balance_after REAL NOT NULL,
                    currency TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES paper_accounts(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS paper_positions (
                    account_id TEXT NOT NULL, symbol TEXT NOT NULL,
                    quantity REAL NOT NULL, average_cost REAL NOT NULL,
                    realized_pnl REAL NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, symbol),
                    FOREIGN KEY(account_id) REFERENCES paper_accounts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS paper_nav_snapshots (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, as_of TEXT NOT NULL,
                    cash REAL NOT NULL, market_value REAL NOT NULL, nav REAL NOT NULL,
                    prices_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES paper_accounts(id) ON DELETE CASCADE,
                    UNIQUE(account_id, as_of)
                );
                CREATE TABLE IF NOT EXISTS paper_signal_links (
                    signal_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    committee_id TEXT NOT NULL, decision_id TEXT NOT NULL,
                    order_id TEXT, created_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES paper_accounts(id) ON DELETE RESTRICT
                );
                """
            )
            order_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(paper_orders)")}
            if "board_lot" not in order_columns:
                self._conn.execute("ALTER TABLE paper_orders ADD COLUMN board_lot INTEGER NOT NULL DEFAULT 1")
            fill_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(paper_fills)")}
            if "execution_key" not in fill_columns:
                self._conn.execute("ALTER TABLE paper_fills ADD COLUMN execution_key TEXT")
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_fills_execution_key ON paper_fills(execution_key) WHERE execution_key IS NOT NULL"
            )
            self._conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?)", (self.SCHEMA_VERSION, _now()))
            self._conn.commit()

    def _ensure_defaults(self) -> None:
        now = _now()
        with self._lock:
            self._conn.executemany(
                """INSERT OR IGNORE INTO paper_accounts
                   (id,name,strategy_line,horizon,market,currency,initial_cash,cash,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [(*row, row[-1], "active", now, now) for row in self.DEFAULT_ACCOUNTS],
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute("SELECT * FROM paper_accounts ORDER BY id").fetchall()]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone())

    def create_account(self, *, name: str, strategy_line: str, horizon: str, market: str, currency: str, initial_cash: float) -> dict[str, Any]:
        if initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        account_id, now = _id("paper"), _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO paper_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (account_id, name, strategy_line, horizon, market, currency, initial_cash, initial_cash, "active", now, now),
            )
            self._conn.execute(
                "INSERT INTO paper_cash_ledger VALUES(?,?,?,?,?,?,?,?)",
                (_id("cash"), account_id, "initial_deposit", None, initial_cash, initial_cash, currency, now),
            )
            self._conn.commit()
        return self.get_account(account_id) or {}

    def submit_approved_signal(
        self,
        *,
        account_id: str,
        signal: dict[str, Any],
        committee_id: str,
        decision_id: str,
        quantity: float,
        limit_price: float | None,
        board_lot: int | None = None,
        submitted_at: str | None = None,
    ) -> dict[str, Any]:
        account = self.get_account(account_id)
        if not account:
            raise KeyError("paper account not found")
        if signal.get("status") != "approved":
            raise ValueError("only committee-approved signals can enter paper trading")
        if account["strategy_line"] != signal.get("strategy_line") or account["horizon"] != signal.get("horizon") or account["market"] != signal.get("market"):
            raise ValueError("signal does not match paper account mandate")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        submitted = submitted_at or _now()
        submission_day = date.fromisoformat(submitted[:10])
        valid_from = date.fromisoformat(str(signal["valid_from"])[:10])
        valid_until = date.fromisoformat(str(signal["valid_until"])[:10])
        if submission_day < valid_from or submission_day > valid_until:
            raise ValueError("signal is not valid on the submission date")
        if int(quantity) != quantity:
            raise ValueError("paper quantity must be a whole number of shares")
        configured_lot = self.MARKET_RULES[account["market"]]["buy_lot"]
        effective_lot = int(board_lot or configured_lot or 0)
        if account["market"] == "HK" and effective_lot <= 0:
            raise ValueError("HK paper orders require the security board lot")
        if effective_lot <= 0 or int(quantity) % effective_lot:
            raise ValueError(f"quantity must be a multiple of board lot {effective_lot}")
        with self._lock:
            existing = self._conn.execute("SELECT * FROM paper_orders WHERE account_id=? AND signal_id=?", (account_id, signal["id"])).fetchone()
            if existing:
                return dict(existing)
            order_id, now = _id("order"), submitted
            self._conn.execute(
                """INSERT INTO paper_orders
                   (id,account_id,signal_id,symbol,side,order_type,quantity,limit_price,status,submitted_at,expires_at,rejection_reason,board_lot)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (order_id, account_id, signal["id"], signal["symbol"], signal["direction"], "limit" if limit_price else "next_open", quantity, limit_price, "submitted", now, signal.get("valid_until"), "", effective_lot),
            )
            self._conn.execute(
                "INSERT INTO paper_signal_links VALUES(?,?,?,?,?,?)",
                (signal["id"], account_id, committee_id, decision_id, order_id, now),
            )
            self._conn.commit()
            return dict(self._conn.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone())

    def record_fill(self, *, order_id: str, quantity: float, price: float, fee: float | None = None, metadata: dict[str, Any] | None = None, execution_key: str | None = None, filled_at: str | None = None) -> dict[str, Any]:
        if quantity <= 0 or price <= 0 or (fee is not None and fee < 0):
            raise ValueError("invalid fill values")
        metadata = dict(metadata or {})
        fill_time = filled_at or _now()
        with self._lock:
            if execution_key:
                existing_fill = self._conn.execute("SELECT * FROM paper_fills WHERE execution_key=?", (execution_key,)).fetchone()
                if existing_fill:
                    return dict(existing_fill)
            order = self._conn.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                raise KeyError("paper order not found")
            account = self._conn.execute("SELECT * FROM paper_accounts WHERE id=?", (order["account_id"],)).fetchone()
            fill_day = date.fromisoformat(fill_time[:10])
            if order["expires_at"] and fill_day > date.fromisoformat(str(order["expires_at"])[:10]):
                self._conn.execute("UPDATE paper_orders SET status='expired',rejection_reason='signal expired' WHERE id=?", (order_id,))
                self._conn.commit()
                raise ValueError("signal expired before fill")
            if metadata.get("suspended") or metadata.get("one_price_limit") or metadata.get("stale_quote"):
                raise ValueError("security is not executable under current market conditions")
            if order["order_type"] == "next_open" and fill_day <= date.fromisoformat(str(order["submitted_at"])[:10]):
                raise ValueError("next-open orders cannot fill on the submission date")
            if order["limit_price"] is not None:
                if order["side"] == "buy" and price > order["limit_price"]:
                    raise ValueError("buy fill exceeds limit price")
                if order["side"] == "sell" and price < order["limit_price"]:
                    raise ValueError("sell fill is below limit price")
            filled = self._conn.execute("SELECT COALESCE(SUM(quantity),0) FROM paper_fills WHERE order_id=?", (order_id,)).fetchone()[0]
            if filled + quantity > order["quantity"] + 1e-9:
                raise ValueError("fill exceeds order quantity")
            if account["market"] == "CN" and order["side"] == "sell":
                settled_buys = self._conn.execute(
                    """SELECT COALESCE(SUM(f.quantity),0) FROM paper_fills f JOIN paper_orders o ON o.id=f.order_id
                       WHERE o.account_id=? AND o.symbol=? AND o.side='buy' AND date(f.filled_at)<date(?)""",
                    (order["account_id"], order["symbol"], fill_time),
                ).fetchone()[0]
                prior_sells = self._conn.execute(
                    """SELECT COALESCE(SUM(f.quantity),0) FROM paper_fills f JOIN paper_orders o ON o.id=f.order_id
                       WHERE o.account_id=? AND o.symbol=? AND o.side='sell'""",
                    (order["account_id"], order["symbol"]),
                ).fetchone()[0]
                if quantity > settled_buys - prior_sells + 1e-9:
                    raise ValueError("A-share T+1 settlement blocks this sell fill")
            if fee is None:
                rules = self.MARKET_RULES[account["market"]]
                commission = max(float(rules["minimum_commission"]), quantity * price * float(rules["commission_rate"]))
                stamp = quantity * price * float(rules["sell_stamp_rate"]) if order["side"] == "sell" else 0.0
                fee = round(commission + stamp, 4)
                metadata["fee_model"] = f"paper-{account['market'].lower()}-v1"
            signed_cash = -(quantity * price + fee) if order["side"] == "buy" else quantity * price - fee
            new_cash = account["cash"] + signed_cash
            if new_cash < -1e-9:
                raise ValueError("insufficient paper cash")
            position = self._conn.execute("SELECT * FROM paper_positions WHERE account_id=? AND symbol=?", (order["account_id"], order["symbol"])).fetchone()
            old_qty = position["quantity"] if position else 0.0
            old_cost = position["average_cost"] if position else 0.0
            realized = position["realized_pnl"] if position else 0.0
            if order["side"] == "buy":
                new_qty = old_qty + quantity
                average_cost = (old_qty * old_cost + quantity * price + fee) / new_qty
            else:
                if quantity > old_qty + 1e-9:
                    raise ValueError("paper account cannot sell more than it owns")
                new_qty = old_qty - quantity
                realized += quantity * (price - old_cost) - fee
                average_cost = old_cost if new_qty else 0.0
            now, fill_id = fill_time, _id("fill")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """INSERT INTO paper_fills
                       (id,order_id,quantity,price,fee,currency,filled_at,metadata_json,execution_key)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (fill_id, order_id, quantity, price, fee, account["currency"], now, json.dumps(metadata, ensure_ascii=False, sort_keys=True), execution_key),
                )
                self._conn.execute(
                    """INSERT INTO paper_positions VALUES(?,?,?,?,?,?)
                       ON CONFLICT(account_id,symbol) DO UPDATE SET quantity=excluded.quantity,
                       average_cost=excluded.average_cost,realized_pnl=excluded.realized_pnl,updated_at=excluded.updated_at""",
                    (order["account_id"], order["symbol"], new_qty, average_cost, realized, now),
                )
                self._conn.execute("UPDATE paper_accounts SET cash=?,updated_at=? WHERE id=?", (new_cash, now, order["account_id"]))
                self._conn.execute("INSERT INTO paper_cash_ledger VALUES(?,?,?,?,?,?,?,?)", (_id("cash"), order["account_id"], "fill", fill_id, signed_cash, new_cash, account["currency"], now))
                new_filled = filled + quantity
                self._conn.execute("UPDATE paper_orders SET status=? WHERE id=?", ("filled" if new_filled >= order["quantity"] - 1e-9 else "partially_filled", order_id))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return dict(self._conn.execute("SELECT * FROM paper_fills WHERE id=?", (fill_id,)).fetchone())

    def list_orders(self, account_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute("SELECT * FROM paper_orders WHERE account_id=? ORDER BY submitted_at DESC", (account_id,)).fetchall()]

    def list_positions(self, account_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute("SELECT * FROM paper_positions WHERE account_id=? AND quantity<>0 ORDER BY symbol", (account_id,)).fetchall()]

    def nav(self, account_id: str, prices: dict[str, float] | None = None) -> dict[str, Any]:
        account = self.get_account(account_id)
        if not account:
            raise KeyError("paper account not found")
        prices = prices or {}
        positions = self.list_positions(account_id)
        market_value = sum(row["quantity"] * float(prices.get(row["symbol"], row["average_cost"])) for row in positions)
        return {"account_id": account_id, "currency": account["currency"], "cash": account["cash"], "market_value": round(market_value, 4), "nav": round(account["cash"] + market_value, 4), "positions": positions}
