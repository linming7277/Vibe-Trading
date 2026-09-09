"""市场事件 P1：涨停/定增写入既有事件表 + 日报消费（任务书 §六 覆盖）。

涨停夹具写 tmp `.day` 文件（本机通达信日线形状，tdx_home 注入）；
定增/事件走 tmp research.db。不连飞书、不依赖 C:\\zd_zyb、0 LLM。
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

from src.investment_research_supervisor.daily_brief_service import merge_market_event_lines
from src.value_strategy.market_events import (
    LIMIT_UP_EVENT_TYPE,
    PRIVATE_PLACEMENT_EVENT_TYPE,
    ingest_market_events,
    list_market_event_lines,
    scan_limit_ups,
)

AS_OF = "2026-09-08"
AS_OF_INT = 20260908
PREV_INT = 20260907

_DAY = struct.Struct("<IIIIIfII")
_AMOUNT = 200_000_000.0  # 远超中位数门槛的常态成交额（元）


class World:
    """tmp 三件套：tdx_home（vipdoc）+ tdx_data.db（ST 名单）+ research.db（事件/公告）。"""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "tdx"
        for exchange in ("sh", "sz", "bj"):
            (self.home / "vipdoc" / exchange / "lday").mkdir(parents=True, exist_ok=True)
        self.tdx_db = tmp_path / "tdx_data.db"
        conn = sqlite3.connect(str(self.tdx_db))
        conn.executescript("""
            CREATE TABLE snapshot_records (
                snapshot_id TEXT, dataset TEXT, record_key TEXT, category TEXT,
                name TEXT, payload_json TEXT, updated_at TEXT);
        """)
        conn.commit()
        conn.close()
        self.research = tmp_path / "research.db"
        conn = sqlite3.connect(str(self.research))
        conn.executescript("""
            CREATE TABLE value_strategy_state_events (
                id TEXT PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, market TEXT NOT NULL,
                stock_code TEXT NOT NULL, event_type TEXT NOT NULL, category TEXT NOT NULL,
                severity TEXT NOT NULL, direction TEXT, before_value TEXT, after_value TEXT,
                before_state_json TEXT NOT NULL, after_state_json TEXT NOT NULL,
                primary_reason TEXT NOT NULL, reasons_json TEXT NOT NULL, cautions_json TEXT NOT NULL,
                trigger_dimension TEXT NOT NULL, source_refs_json TEXT NOT NULL,
                transition_batch_id TEXT NOT NULL, status TEXT NOT NULL,
                research_as_of TEXT, occurred_at TEXT NOT NULL,
                acknowledged_at TEXT, closed_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE company_action_events (
                id TEXT PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL UNIQUE, market TEXT NOT NULL,
                stock_code TEXT NOT NULL, event_type TEXT NOT NULL, event_status TEXT NOT NULL,
                event_stage TEXT NOT NULL, announcement_date TEXT, title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '');
        """)
        conn.commit()
        conn.close()

    def write_day(self, code: str, bars: list[tuple[int, float, float]]) -> None:
        """写 .day：[(yyyymmdd, close, amount)]，OHLC=收盘（价格×100 打包）。"""
        exchange = "sh" if code.endswith(".SH") else "sz"
        path = self.home / "vipdoc" / exchange / "lday" / f"{exchange}{code[:6]}.day"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            for date, close, amount in bars:
                packed = int(round(close * 100))
                handle.write(_DAY.pack(date, packed, packed, packed, packed,
                                       float(amount), 1000, 0))

    def add_st_name(self, code: str, name: str) -> None:
        conn = sqlite3.connect(str(self.tdx_db))
        conn.execute(
            "INSERT INTO snapshot_records VALUES('s','securities',?,'sec',?,'{}',datetime('now'))",
            (code, name))
        conn.commit()
        conn.close()

    def add_placement(self, code: str, day: str) -> None:
        conn = sqlite3.connect(str(self.research))
        conn.execute(
            "INSERT INTO company_action_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"ca_{code}_{day}", f"k_{code}_{day}", f"f_{code}_{day}", "CN", code,
             PRIVATE_PLACEMENT_EVENT_TYPE, "ANNOUNCED", "公告", day,
             f"{code} 定增公告", "摘要"))
        conn.commit()
        conn.close()

    def ingest(self) -> dict:
        return ingest_market_events(AS_OF, tdx_home=self.home, tdx_db_path=self.tdx_db,
                                    research_db_path=self.research)

    def event_rows(self) -> list[tuple[str, str]]:
        conn = sqlite3.connect(str(self.research))
        rows = conn.execute(
            "SELECT event_type, stock_code FROM value_strategy_state_events").fetchall()
        conn.close()
        return rows


def _world_with_limit(code: str, *, prev: float = 10.0, today: float = 10.97,
                      tmp_path: Path | None = None) -> World:
    world = World(tmp_path or Path("/tmp") / "mke")
    world.write_day(code, [(PREV_INT, prev, _AMOUNT), (AS_OF_INT, today, _AMOUNT)])
    return world


# --- 1. 主板 10% 贴板 + 额过门槛 → 1 条 LIMIT_UP ---------------------------------

def test_1_main_board_near_limit(tmp_path: Path) -> None:
    world = _world_with_limit("000001.SZ", tmp_path=tmp_path)
    hits = scan_limit_ups(AS_OF, tdx_home=world.home, tdx_db_path=world.tdx_db)
    assert [hit["stock_code"] for hit in hits] == ["000001.SZ"]
    result = world.ingest()
    assert result["limit_up"] == 1 and result["written"] == 1
    assert world.event_rows() == [(LIMIT_UP_EVENT_TYPE, "000001.SZ")]


def test_1b_idempotent_rerun_zero_new(tmp_path: Path) -> None:
    world = _world_with_limit("000001.SZ", tmp_path=tmp_path)
    first = world.ingest()
    second = world.ingest()
    assert first["written"] == 1 and second["written"] == 0  # 重跑 0 新增
    assert len(world.event_rows()) == 1


# --- 2. 300xxx 19% 不算、20% 算 ----------------------------------------------------

def test_2_gem_threshold(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.write_day("300001.SZ", [(PREV_INT, 10.0, _AMOUNT), (AS_OF_INT, 11.90, _AMOUNT)])  # +19% 不够
    world.write_day("300002.SZ", [(PREV_INT, 10.0, _AMOUNT), (AS_OF_INT, 12.02, _AMOUNT)])  # +20.2% 达标
    hits = {hit["stock_code"] for hit in scan_limit_ups(AS_OF, tdx_home=world.home,
                                                        tdx_db_path=world.tdx_db)}
    assert "300001.SZ" not in hits
    assert "300002.SZ" in hits


# --- 3. ST、北交所、零成交 → 0 条 ---------------------------------------------------

def test_3_st_bj_zero_amount_excluded(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.add_st_name("000004.SZ", "ST测试")
    world.write_day("000004.SZ", [(PREV_INT, 10.0, _AMOUNT), (AS_OF_INT, 10.97, _AMOUNT)])  # ST 贴板
    # 北交所：bj 目录天然不扫；即便错位写进 sz 目录，4/8 开头前缀也排除
    world.write_day("833001.SZ", [(PREV_INT, 9.0, _AMOUNT), (AS_OF_INT, 11.7, _AMOUNT)])
    world.write_day("000005.SZ", [(PREV_INT, 10.0, 0.0), (AS_OF_INT, 10.97, 0.0)])          # 零成交贴板
    assert scan_limit_ups(AS_OF, tdx_home=world.home, tdx_db_path=world.tdx_db) == []


# --- 4. 定增公告日口径 ---------------------------------------------------------------

def test_4_private_placement_same_day_only(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.add_placement("600001.SH", AS_OF)
    world.add_placement("600002.SH", "2026-09-05")  # 非公告日
    result = world.ingest()
    assert result["private_placement"] == 1
    assert world.event_rows() == [(PRIVATE_PLACEMENT_EVENT_TYPE, "600001.SH")]


# --- 5. 事件源空 → 无事件行 -----------------------------------------------------------

def test_5_empty_event_source_no_lines(tmp_path: Path) -> None:
    world = World(tmp_path)
    assert list_market_event_lines(AS_OF, db_path=world.research) == []


# --- 6. 价格条件先占位：6 行价格 + 5 事件 → 只再占 2 行（总 8） -------------------------

def test_6_merge_price_first_total_cap8_event_cap3() -> None:
    price_lines = [{"sentence": f"价格条件{i}"} for i in range(6)]
    events = [{"sentence": f"涨停：某公司 00000{i}.SZ", "market_event": True} for i in range(5)]
    merged = merge_market_event_lines(price_lines, events)
    assert len(merged) == 8                                   # 总预算 8
    assert merged[:6] == price_lines                          # 价格条件先占位
    assert sum(1 for item in merged if item.get("market_event")) == 2
    few = merge_market_event_lines([{"sentence": "p"}], events)
    assert sum(1 for item in few if item.get("market_event")) == 3  # 事件上限 3
    assert len(few) == 4


def test_limit_up_lines_sorted_by_amount_desc(tmp_path: Path) -> None:
    """3 条涨停额 1亿/10亿/3亿 → 日报事件第一行是 10 亿那只。"""
    world = World(tmp_path)
    world.write_day("000011.SZ", [(PREV_INT, 10.0, _AMOUNT), (AS_OF_INT, 10.97, 100_000_000.0)])   # 1 亿
    world.write_day("000020.SZ", [(PREV_INT, 10.0, _AMOUNT), (AS_OF_INT, 10.97, 1_000_000_000.0)]) # 10 亿
    world.write_day("000059.SZ", [(PREV_INT, 10.0, _AMOUNT), (AS_OF_INT, 10.97, 300_000_000.0)])   # 3 亿
    world.ingest()
    lines = list_market_event_lines(AS_OF, max_lines=3, db_path=world.research)
    assert [line["stock_code"] for line in lines] == ["000020.SZ", "000059.SZ", "000011.SZ"]
    assert lines[0]["sentence"].startswith("涨停：") and lines[0]["stock_code"] == "000020.SZ"


def test_limit_up_missing_amount_sorts_last(tmp_path: Path) -> None:
    """缺 amount 的涨停排最后（排尾），有额的在前。"""
    world = World(tmp_path)
    world.write_day("000011.SZ", [(PREV_INT, 10.0, _AMOUNT), (AS_OF_INT, 10.97, 100_000_000.0)])
    world.ingest()  # 000011.SZ 1 亿（带额）
    conn = sqlite3.connect(str(world.research))
    conn.execute(
        "INSERT INTO value_strategy_state_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("vse_x", "LIMIT_UP:999999.SZ:2026-09-08", "CN", "999999.SZ", "LIMIT_UP",
         "MARKET_EVENT", "INFO", None, None, "9.9", "{}", "{}", "测试", "[]", "[]",
         "market_event", "[]", "b", "OPEN", "2026-09-08",
         "2026-09-08T20:00:00+00:00", None, None,
         "2026-09-08T20:00:00+00:00", "2026-09-08T20:00:00+00:00"))
    conn.commit()
    conn.close()
    lines = list_market_event_lines(AS_OF, max_lines=3, db_path=world.research)
    assert [line["stock_code"] for line in lines] == ["000011.SZ", "999999.SZ"]
    assert lines[-1]["stock_code"] == "999999.SZ"  # 缺额排最后


def test_list_market_event_lines_cap(tmp_path: Path) -> None:
    world = World(tmp_path)
    for i in range(1, 6):
        world.add_placement(f"60000{i}.SH", AS_OF)
    world.ingest()
    lines = list_market_event_lines(AS_OF, max_lines=3, db_path=world.research)
    assert len(lines) == 3
    assert all(line["sentence"].startswith("定增：") for line in lines)
