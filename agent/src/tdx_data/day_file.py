"""Reader for TDX vipdoc/sh/lday `sh########.day` files (32 bytes per bar).

字节布局为小端 `<IIIIIfII`：日期(I) 开(I×100) 高(I×100) 低(I×100) 收(I×100)
成交额(F, 元) 成交量(I, 手) 保留(I)。已用已知样例校准：sh881441.day 末根
date=20260908、close=1123.54（价格 ÷100）。
"""

from __future__ import annotations

import os
import sqlite3
import struct
from pathlib import Path
from typing import Any

_BAR = struct.Struct("<IIIIIfII")


def _iso(day: str | int) -> str:
    stamp = str(day).replace("-", "")
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"


def default_tdx_home() -> Path:
    """通达信安装根目录；与 TdxClient 同一约定（HZ_TDX_HOME 可覆盖）。"""
    return Path(os.environ.get("HZ_TDX_HOME", r"C:\zd_zyb"))


def read_lday(path: str | Path) -> list[dict[str, Any]]:
    """读取一个 .day 文件；文件缺失/损坏返回空列表，不向调用方抛异常。"""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for offset in range(0, len(raw) - _BAR.size + 1, _BAR.size):
        date, o, h, low, close, amount, volume, _reserved = _BAR.unpack_from(raw, offset)
        out.append({
            "date": _iso(date), "open": o / 100, "high": h / 100,
            "low": low / 100, "close": close / 100, "amount": float(amount),
            "volume": int(volume),
        })
    return out


def read_lday_tail(path: str | Path, count: int = 2) -> list[dict[str, Any]]:
    """只读一个 .day 文件的最后 count 根（全市场扫描用，避免整文件读取）。

    文件缺失/不足 count 根时返回实际可得根数；损坏返回空列表。
    """
    try:
        size = Path(path).stat().st_size
    except OSError:
        return []
    usable = size - (size % _BAR.size)
    if usable <= 0:
        return []
    take = min(count, usable // _BAR.size)
    try:
        with Path(path).open("rb") as handle:
            handle.seek(usable - _BAR.size * take)
            raw = handle.read(_BAR.size * take)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for offset in range(0, len(raw) - _BAR.size + 1, _BAR.size):
        date, o, h, low, close, amount, volume, _reserved = _BAR.unpack_from(raw, offset)
        out.append({
            "date": _iso(date), "open": o / 100, "high": h / 100,
            "low": low / 100, "close": close / 100, "amount": float(amount),
            "volume": int(volume),
        })
    return out


def load_sw1_index_names(store_path: str | Path | None = None) -> dict[str, str]:
    """申万一级 code→name（tdx_data.db 的 research_industry_hierarchy，level=1）。"""
    db = Path(store_path) if store_path else _default_store()
    names: dict[str, str] = {}
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            for row in conn.execute(
                "SELECT payload_json FROM records WHERE dataset='research_industry_hierarchy'"
            ):
                import json

                payload = json.loads(row[0] or "{}")
                if str(payload.get("level")) == "1" and payload.get("industry_code"):
                    names[str(payload["industry_code"])] = str(payload.get("industry_name") or "")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 层级缺失时调用方按缺数降级
        return names
    return names


def _default_store() -> Path:
    from src.config.paths import get_runtime_root

    return get_runtime_root() / "tdx_data.db"


def load_sw1_index_bars(store_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """读 sw1_index_bars 小表：code -> {name, rows:[(ISO日期, 收盘, 成交额)]}。"""
    db = Path(store_path) if store_path else _default_store()
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT code, name, date, close, amount FROM sw1_index_bars ORDER BY code, date"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, Any]] = {}
    for code, name, date, close, amount in rows:
        entry = out.setdefault(str(code), {"name": str(name), "rows": []})
        entry["rows"].append((str(date), float(close), float(amount)))
    return out


def ingest_sw1_index_bars(as_of: str, *, l1: dict[str, str] | None = None,
                          tdx_home: str | Path | None = None,
                          store_path: str | Path | None = None,
                          last: int = 30, minimum_usable: int = 20) -> dict[str, Any]:
    """把申万一级指数日线从 .day 导入 sw1_index_bars 小表（复用同义表）。

    - 只处理传入/查到的申万一级 30 个代码，不导 L2/L3、不导 880/399；
    - ``l1`` 未传时从层级表自查；
    - 每个文件写最近 ``last`` 根；
    - 末根日期 ≠ as_of 的代码记入 date_mismatch；
    - 可用（末根==as_of）少于 ``minimum_usable`` 个 → ok=False。
    """
    target = _iso(as_of)
    names = dict(l1) if l1 is not None else load_sw1_index_names(store_path=store_path)
    home = Path(tdx_home) if tdx_home else default_tdx_home()
    db = Path(store_path) if store_path else _default_store()
    missing_files: list[str] = []
    date_mismatch: list[str] = []
    usable = 0
    rows_written = 0
    if names:
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sw1_index_bars ("
                "code TEXT NOT NULL, name TEXT NOT NULL, date TEXT NOT NULL, "
                "close REAL NOT NULL, amount REAL NOT NULL, PRIMARY KEY(code, date))"
            )
            for code, name in names.items():
                path = home / "vipdoc" / "sh" / "lday" / f"sh{code[:6]}.day"
                bars = read_lday(path)
                if not bars:
                    missing_files.append(code)
                    continue
                for bar in bars[-last:]:
                    conn.execute(
                        "INSERT OR REPLACE INTO sw1_index_bars(code, name, date, close, amount) "
                        "VALUES(?,?,?,?,?)", (code, name, bar["date"], bar["close"], bar["amount"]))
                rows_written += min(len(bars), last)
                if _iso(bars[-1]["date"].replace("-", "")) == target:
                    usable += 1
                else:
                    date_mismatch.append(code)
            conn.commit()
        finally:
            conn.close()
    return {
        "ok": bool(names) and usable >= minimum_usable,
        "rows": rows_written,
        "missing_files": missing_files,
        "date_mismatch": date_mismatch,
        "usable": usable,
        "expected": len(names),
    }
