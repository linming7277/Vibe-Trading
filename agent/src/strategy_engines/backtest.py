"""Deterministic, no-lookahead replay for raw and committee-approved signals."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd


@dataclass(frozen=True)
class ReplayConfig:
    market: str
    initial_cash: float = 1_000_000.0
    execution_price: str = "open"
    slippage_bps: float = 5.0
    commission_rate: float = .0003
    minimum_commission: float = 5.0
    sell_stamp_rate: float = .0005
    default_board_lot: int = 100


def _fee(notional: float, side: str, config: ReplayConfig) -> float:
    commission = max(config.minimum_commission, notional * config.commission_rate)
    stamp = notional * config.sell_stamp_rate if side == "sell" else 0.0
    return commission + stamp


def replay_signals(
    signals: Iterable[dict[str, Any]],
    bars: pd.DataFrame,
    *,
    config: ReplayConfig,
    approved_signal_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Replay at next available open/VWAP; never at the signal-day close.

    ``approved_signal_ids=None`` is the raw deterministic baseline.  Passing a
    set produces the committee-approved comparison without changing any engine
    score or signal field.
    """
    required = {"date", "symbol", config.execution_price, "close"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"bars missing required columns: {sorted(missing)}")
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame = frame.sort_values(["date", "symbol"], kind="stable")
    candidates = [dict(item) for item in signals if approved_signal_ids is None or item["id"] in approved_signal_ids]
    cash = float(config.initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    traded_signals: set[str] = set()
    trades: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    slip = config.slippage_bps / 10_000

    for day, daily in frame.groupby("date", sort=True):
        by_symbol = {str(row["symbol"]): row for _, row in daily.iterrows()}
        for signal in candidates:
            if signal["id"] in traded_signals or signal.get("direction") != "buy":
                continue
            if not (pd.Timestamp(signal["valid_from"]).date() <= day <= pd.Timestamp(signal["valid_until"]).date()):
                continue
            row = by_symbol.get(str(signal["symbol"]))
            if row is None or bool(row.get("suspended", False)) or bool(row.get("one_price_limit", False)):
                continue
            price = float(row[config.execution_price]) * (1 + slip)
            if signal.get("entry_high") is not None and price > float(signal["entry_high"]):
                continue
            if signal.get("entry_low") is not None and price < float(signal["entry_low"]):
                continue
            lot = int(row.get("board_lot") or config.default_board_lot)
            budget = config.initial_cash * min(float(signal["position_cap"]), 1.0)
            quantity = math.floor(budget / price / lot) * lot
            if quantity <= 0:
                continue
            cost = quantity * price
            fee = _fee(cost, "buy", config)
            if cost + fee > cash:
                quantity = math.floor((cash - config.minimum_commission) / price / lot) * lot
                cost = quantity * price
                fee = _fee(cost, "buy", config) if quantity else 0
            if quantity <= 0:
                continue
            cash -= cost + fee
            positions[str(signal["symbol"])] = {
                "quantity": quantity, "entry_price": price, "entry_date": day,
                "signal": signal,
            }
            traded_signals.add(signal["id"])
            trades.append({"signal_id": signal["id"], "symbol": signal["symbol"], "side": "buy", "date": day.isoformat(), "price": price, "quantity": quantity, "fee": fee})

        for symbol, position in list(positions.items()):
            row = by_symbol.get(symbol)
            if row is None or bool(row.get("suspended", False)):
                continue
            signal = position["signal"]
            # A-share exits are never allowed on the entry date (T+1).
            if config.market == "CN" and day <= position["entry_date"]:
                continue
            close = float(row["close"])
            reason = None
            if signal.get("stop_price") is not None and close <= float(signal["stop_price"]):
                reason = "stop"
            elif signal.get("target_low") is not None and close >= float(signal["target_low"]):
                reason = "target"
            elif day >= pd.Timestamp(signal["valid_until"]).date():
                reason = "expired"
            if reason is None or bool(row.get("one_price_limit", False)):
                continue
            price = float(row[config.execution_price]) * (1 - slip)
            notional = position["quantity"] * price
            fee = _fee(notional, "sell", config)
            cash += notional - fee
            trades.append({"signal_id": signal["id"], "symbol": symbol, "side": "sell", "date": day.isoformat(), "price": price, "quantity": position["quantity"], "fee": fee, "reason": reason})
            del positions[symbol]

        market_value = sum(
            position["quantity"] * float(by_symbol[symbol]["close"])
            for symbol, position in positions.items() if symbol in by_symbol
        )
        nav_rows.append({"date": day.isoformat(), "cash": cash, "market_value": market_value, "nav": cash + market_value})

    final_nav = nav_rows[-1]["nav"] if nav_rows else config.initial_cash
    return {
        "market": config.market,
        "initial_cash": config.initial_cash,
        "final_nav": round(final_nav, 4),
        "return_pct": round((final_nav / config.initial_cash - 1) * 100, 6),
        "trades": trades,
        "nav": nav_rows,
        "signal_count": len(candidates),
        "entered_signal_count": len(traded_signals),
    }


def compare_raw_and_committee(
    signals: Iterable[dict[str, Any]],
    bars: pd.DataFrame,
    *,
    approved_signal_ids: set[str],
    config: ReplayConfig,
) -> dict[str, Any]:
    materialized = [dict(item) for item in signals]
    raw = replay_signals(materialized, bars, config=config)
    approved = replay_signals(materialized, bars, config=config, approved_signal_ids=approved_signal_ids)
    return {
        "raw": raw,
        "committee": approved,
        "agent_value_add_pct": round(approved["return_pct"] - raw["return_pct"], 6),
    }
