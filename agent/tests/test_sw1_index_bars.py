"""SW1 index bar reader/ingest + outlook sector source isolation.

用临时目录假 .day 文件（按校准过的 32 字节小端布局写入），不依赖本机
C:\\zd_zyb。规则：缺文件或末根日期 ≠ P 日 → 可用数压不足 20 → 前瞻
板块段「资料不足」，绝不回退成员股合成或低估池行业。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.tdx_data import day_file
from src.tdx_data.day_file import ingest_sw1_index_bars, read_lday


def _bar(date_int: int, close: float, amount: float) -> bytes:
    scaled = round(close * 100)
    return struct.pack("<IIIIIfII", date_int, scaled, scaled + 10, scaled - 10, scaled, amount, 1000, 0)


def _write_lday(path: Path, bars: list[tuple[int, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(_bar(date, close, amount) for date, close, amount in bars))


def test_reader_parses_known_date_and_close(tmp_path: Path) -> None:
    path = tmp_path / "sh881441.day"
    _write_lday(path, [(20260905, 1120.86, 2.4e10), (20260908, 1123.54, 2.28e10)])
    rows = read_lday(path)
    assert len(rows) == 2
    assert rows[-1]["date"] == "2026-09-08"
    assert rows[-1]["close"] == pytest.approx(1123.54)
    assert rows[-1]["amount"] == pytest.approx(2.28e10)
    assert rows[0]["open"] == pytest.approx(1120.86)


def test_reader_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_lday(tmp_path / "sh000000.day") == []


def test_ingest_only_imports_given_l1(tmp_path: Path) -> None:
    home = tmp_path / "tdx"
    store = tmp_path / "tdx_data.db"
    l1 = {"881441.SH": "交通运输", "881015.SH": "化工", "881318.SH": "电子"}
    bars = [(2026090 + i, 1000.0 + i) for i in range(1, 4)]
    bars = [(int(f"2026090{i}") if i < 9 else 20260908, 1000.0 + i, 2.0e10) for i in range(1, 9)]
    bars[-1] = (20260908, 1123.54, 2.28e10)
    for code in l1:
        _write_lday(home / "vipdoc" / "sh" / "lday" / f"sh{code[:6]}.day", bars)
    result = ingest_sw1_index_bars("2026-09-08", l1=l1, tdx_home=home, store_path=store)
    # 只有 3 个传入的 L1 被导入；880/399/L2/L3 一概不碰
    assert result["expected"] == 3 and result["usable"] == 3
    assert result["ok"] is False  # 可用 3 < 20：ok=False，outlook 整段资料不足
    loaded = day_file.load_sw1_index_bars(store_path=store)
    assert set(loaded) == set(l1)
    last_row = loaded["881441.SH"]["rows"][-1]
    assert last_row[:2] == ("2026-09-08", pytest.approx(1123.54))
    assert last_row[2] == pytest.approx(2.28e10, rel=1e-6)


def test_ingest_records_missing_file_and_stale(tmp_path: Path) -> None:
    home = tmp_path / "tdx"
    store = tmp_path / "tdx_data.db"
    l1 = {"881441.SH": "交通运输", "881015.SH": "化工", "881318.SH": "电子"}
    fresh = [(20260901 + i, 1000.0 + i, 2.0e10) for i in range(1, 9)]
    fresh[-1] = (20260908, 1005.69, 2.1e10)
    _write_lday(home / "vipdoc" / "sh" / "lday" / "sh881441.day", fresh)
    stale = [(20260901 + i, 1000.0 + i, 2.0e10) for i in range(1, 8)]  # 末根 09-07
    stale.append((20260907, 1004.0, 2.0e10))
    _write_lday(home / "vipdoc" / "sh" / "lday" / "sh881015.day", stale)
    # 881318 故意不写文件
    result = ingest_sw1_index_bars("2026-09-08", l1=l1, tdx_home=home, store_path=store)
    assert result["missing_files"] == ["881318.SH"]
    assert result["date_mismatch"] == ["881015.SH"]
    assert result["ok"] is False


def test_sectors_reads_table_not_member_bars(tmp_path: Path) -> None:
    """有表时板块段只依赖小表；成员股合成路径已删除。"""
    import src.investment_research_supervisor.next_session_outlook as outlook

    assert not hasattr(outlook, "load_member_returns")
    assert not hasattr(outlook, "load_sector_universe")
    assert not hasattr(outlook, "adjusted_daily_bars")

    index_bars = [{"trade_date": f"202609{day:02d}", "close": 4600.0 - day, "amount": 5e11}
                  for day in range(1, 9)]
    dates = ([f"2026-08-{day:02d}" for day in range(26, 32)]
             + [f"2026-09-{day:02d}" for day in range(1, 9)])
    table = {}
    for index in range(25):
        code = f"881{index:03d}.SH"
        rows = [(day, 1000.0 + index + i, 2.0e10 + i * 1e8) for i, day in enumerate(dates)]
        table[code] = {"name": f"行业{index}", "rows": rows}
    sectors = outlook._sectors(index_bars, benchmark_r5=0.0, sw1_bars=table)
    assert sectors["status"] == "ready"
    assert len(sectors["strong"]) == 3 and len(sectors["weak"]) == 3


def test_sector_text_insufficient_on_missing_table(tmp_path: Path) -> None:
    from src.investment_research_supervisor.next_session_outlook import build_next_session_outlook

    outlook = build_next_session_outlook(
        "2026-09-08",
        index_bars=[{"trade_date": f"202609{day:02d}", "close": 4600.0 - day, "amount": 5e11}
                    for day in range(1, 9)],
        forecast={"id": "f", "status": "SHADOW", "direction": "WEAKER",
                  "target_trade_date": "2026-09-09", "model_summary": "", "invalidation_conditions": []},
        breadth={"advancers": 100, "decliners": 50, "day": "20260908"},
        sw1_bars={},
    )
    assert "【板块】资料不足。" in outlook["text"]
    for pool_name in ("餐饮", "旅游酒店", "白酒"):
        assert pool_name not in outlook["text"]
