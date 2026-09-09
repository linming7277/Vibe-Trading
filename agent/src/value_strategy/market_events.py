"""市场事件 P1：涨停 / 定增 写入既有 value_strategy_state_events 表。

- 涨停：K 线源 = 本机通达信 vipdoc/{sh,sz}/lday/*.day（全市场、收盘后追加式
  落盘；adjusted_daily_bars 当日通常仅池内补数，不满足全市场口径）。
  口径 = 收盘 >= 前收×(1+幅度)×0.995（贴板 0.5% 内）且成交额 >= 当日全市场
  有成交股票成交额中位数 × 0.2。幅度：300/301/688 开头 20%，其余沪深主板
  10%。排除北交所（.BJ / bj 目录）、名称含 ST/*ST、停牌（成交额<=0）、无前收。
- 定增：research.db `company_action_events`，event_type='PRIVATE_PLACEMENT'
  且 announcement_date=P 日（公告日口径，非实施日）。
- 写入：复用既有事件表，upsert 键 event_key=`{event_type}:{stock_code}:{as_of}`
  （同键幂等，重跑 0 新增）；不触碰 Focus/低估池/L3/价格区各表。
- 事件源为空：返回 0 条，日报省略事件段；bitable 空源另有 fail-closed 门。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LIMIT_UP_EVENT_TYPE = "LIMIT_UP"
PRIVATE_PLACEMENT_EVENT_TYPE = "PRIVATE_PLACEMENT"
MARKET_EVENT_CATEGORY = "MARKET_EVENT"

# 涨停幅度：创业板 300/301 与科创板 688 为 20%，其余沪深主板 10%。
_LIMIT_UP_RATE_GEM_STAR = 0.20
_LIMIT_UP_RATE_MAIN = 0.10
# 贴板容差：收盘 >= 涨停价×0.995 视为涨停。
_LIMIT_UP_PRICE_TOLERANCE = 0.995
# 成交额门槛：当日全市场有成交股票成交额中位数的 20%（相对量纲，避免单位歧义）。
LIMIT_UP_AMOUNT_MEDIAN_FRACTION = 0.2
# 公告标题截断长度（日报定增行）。
_PLACEMENT_TITLE_LIMIT = 40

_BJ_SUFFIX = ".BJ"
_ST_MARK = "ST"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _dash(day: str) -> str:
    stamp = str(day).replace("-", "")
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) == 8 else stamp


def _is_gem_or_star(code: str) -> bool:
    return code[:3] in {"300", "301", "688"}


def _limit_up_rate(code: str) -> float:
    return _LIMIT_UP_RATE_GEM_STAR if _is_gem_or_star(code) else _LIMIT_UP_RATE_MAIN


def _load_st_codes(tdx_conn: sqlite3.Connection) -> set[str]:
    """名称含 ST/*ST 的股票代码集合（tdx_data.db snapshot_records dataset='securities'）。"""
    codes: set[str] = set()
    try:
        rows = tdx_conn.execute(
            "SELECT record_key, name FROM snapshot_records WHERE dataset='securities'"
        ).fetchall()
    except sqlite3.OperationalError:
        return codes
    for record_key, name in rows:
        name = str(name or "").upper()
        if _ST_MARK in name:
            codes.add(str(record_key or "").upper())
    return codes


def day_tail_universe(as_of: str, *, tdx_home: Path | str | None = None,
                      tdx_db_path: Path | str | None = None,
                      count: int = 2) -> dict[str, list[dict[str, Any]]]:
    """涨停/宽度共用的全市场有效样本：P 日有 bar 的沪深非北交非 ST 股票。

    返回 {code: 尾部 bars}（bars 含 P 日共 count 根）。排除：.BJ/4-8 前缀
    （北交所/三板）、名称含 ST/*ST、P 日零成交（停牌/零量）。
    """
    from src.tdx_data.day_file import default_tdx_home, read_lday_tail

    home = Path(tdx_home) if tdx_home else default_tdx_home()
    if tdx_db_path is None:
        from src.config.paths import get_runtime_root

        tdx_db_path = get_runtime_root() / "tdx_data.db"
    st_codes: set[str] = set()
    try:
        conn = sqlite3.connect(f"file:{Path(tdx_db_path).as_posix()}?mode=ro", uri=True)
        try:
            st_codes = _load_st_codes(conn)
        finally:
            conn.close()
    except sqlite3.OperationalError:
        st_codes = set()

    target_day = _dash(as_of)
    universe: dict[str, list[dict[str, Any]]] = {}
    for exchange in ("sh", "sz"):
        directory = home / "vipdoc" / exchange / "lday"
        try:
            paths = sorted(directory.glob("*.day"))
        except OSError:
            paths = []
        for path in paths:
            code = f"{path.stem[2:]}.{exchange.upper()}"
            if code.endswith(_BJ_SUFFIX) or code[:1] in {"4", "8"}:
                continue  # 北交所/三板未计入
            if code in st_codes:
                continue  # 名称含 ST/*ST 未计入
            bars = read_lday_tail(path, count=count)
            if len(bars) >= 2 and bars[-1]["date"] == target_day and bars[-1]["volume"] > 0:
                universe[code] = bars
    return universe


def scan_limit_ups(as_of: str, *, tdx_home: Path | str | None = None,
                   tdx_db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """只读扫描 P 日涨停（本机 .day 全市场日 K + 成交额中位数门槛）。"""
    tail_by_code = day_tail_universe(as_of, tdx_home=tdx_home, tdx_db_path=tdx_db_path,
                                     count=2)
    if not tail_by_code:
        return []

    amounts = [float(bars[-1]["amount"]) for bars in tail_by_code.values()
               if bars[-1]["amount"] > 0]
    median_amount = _median(amounts)
    if median_amount is None:
        return []
    threshold = median_amount * LIMIT_UP_AMOUNT_MEDIAN_FRACTION

    hits: list[dict[str, Any]] = []
    for code, bars in sorted(tail_by_code.items()):
        prev_bar, today_bar = bars[-2], bars[-1]
        prev_close, close = float(prev_bar["close"]), float(today_bar["close"])
        amount = float(today_bar["amount"])
        if prev_close <= 0:
            continue  # 无前收
        if amount <= 0 or amount < threshold:
            continue  # 低于额门槛
        limit_price = prev_close * (1 + _limit_up_rate(code))
        if close >= limit_price * _LIMIT_UP_PRICE_TOLERANCE:
            hits.append({
                "stock_code": code,
                "close": close,
                "limit_price": round(limit_price, 4),
                "amount": amount,
                "rate": _limit_up_rate(code),
            })
    return hits


def fetch_private_placements(as_of: str, *,
                             research_db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """读取公告日=P 日的定增公告（company_action_events，既有枚举 PRIVATE_PLACEMENT）。"""
    if research_db_path is None:
        from src.config.paths import get_runtime_root

        research_db_path = get_runtime_root() / "research.db"
    conn = sqlite3.connect(f"file:{Path(research_db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT stock_code, title, announcement_date FROM company_action_events
               WHERE event_type='PRIVATE_PLACEMENT' AND announcement_date=?
               ORDER BY stock_code""",
            (as_of,),
        ).fetchall()
        return [
            {
                "stock_code": str(row["stock_code"] or "").upper(),
                "title": str(row["title"] or ""),
                "announcement_date": str(row["announcement_date"] or ""),
            }
            for row in rows
        ]
    finally:
        conn.close()


def _name_map(tdx_db_path: Path | str | None) -> dict[str, str]:
    if not tdx_db_path:
        return {}
    names: dict[str, str] = {}
    try:
        conn = sqlite3.connect(f"file:{Path(tdx_db_path).as_posix()}?mode=ro", uri=True)
        try:
            for row in conn.execute(
                "SELECT record_key, name FROM records WHERE dataset='security_details'"
            ):
                names[str(row[0] or "").upper()] = str(row[1] or "")
        except sqlite3.OperationalError:
            return {}
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return {}
    return names


def _insert_market_event(conn: sqlite3.Connection, *, event_key: str, code: str,
                         event_type: str, as_of: str, after_value: str,
                         details: dict[str, Any], primary_reason: str,
                         reasons: list[str], source: str) -> bool:
    existing = conn.execute(
        "SELECT 1 FROM value_strategy_state_events WHERE event_key=?", (event_key,)
    ).fetchone()
    if existing:
        return False  # 幂等：重跑 0 新增，不覆盖已确认状态
    now = _now()
    conn.execute(
        """INSERT INTO value_strategy_state_events(
               id, event_key, market, stock_code, event_type, category, severity,
               direction, before_value, after_value, before_state_json,
               after_state_json, primary_reason, reasons_json, cautions_json,
               trigger_dimension, source_refs_json, transition_batch_id, status,
               research_as_of, occurred_at, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"vse_mkt_{uuid.uuid4().hex[:12]}", event_key, "CN", code, event_type,
            MARKET_EVENT_CATEGORY, "INFO", None, None, after_value, "{}",
            json.dumps(details, ensure_ascii=False), primary_reason,
            json.dumps(reasons, ensure_ascii=False), "[]", "market_event",
            json.dumps([source], ensure_ascii=False), f"mkt_events:{as_of}",
            "OPEN", as_of, now, now, now,
        ),
    )
    return True


def ingest_market_events(as_of: str, *, tdx_home: Path | str | None = None,
                         tdx_db_path: Path | str | None = None,
                         research_db_path: Path | str | None = None) -> dict[str, Any]:
    """涨停 + 定增 写入既有事件表（event_key 幂等，重跑 0 新增）。"""
    from src.value_strategy.event_store import ValueStrategyEventRepository

    limit_ups: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []
    try:
        limit_ups = scan_limit_ups(as_of, tdx_home=tdx_home, tdx_db_path=tdx_db_path)
        placements = fetch_private_placements(as_of, research_db_path=research_db_path)
    except Exception as exc:  # noqa: BLE001 - 事件段降级为空，不打死 EOD
        logger.error("market event scan failed for %s: %s", as_of, exc)
        return {"status": "FAILED", "as_of": as_of, "limit_up": 0,
                "private_placement": 0, "written": 0,
                "error": f"{type(exc).__name__}: {exc}"[:160]}

    name_by_code = _name_map(tdx_db_path)
    repository = ValueStrategyEventRepository(
        Path(research_db_path) if research_db_path else None)
    written = 0
    try:
        conn = repository._conn
        with conn:
            for hit in limit_ups:
                code = hit["stock_code"]
                name = name_by_code.get(code, "")
                if _insert_market_event(
                    conn,
                    event_key=f"{LIMIT_UP_EVENT_TYPE}:{code}:{as_of}", code=code,
                    event_type=LIMIT_UP_EVENT_TYPE, as_of=as_of,
                    after_value=f"{hit['close']}",
                    details={"company_name": name, "close": hit["close"],
                             "amount": hit["amount"], "rate": hit["rate"]},
                    primary_reason="当日收盘触及涨停口径（贴板 0.5% 内，额过门槛）",
                    reasons=[f"收盘 {hit['close']}，涨停价参考 {hit['limit_price']}"],
                    source="lday",
                ):
                    written += 1
            for item in placements:
                code = item["stock_code"]
                if not code:
                    continue
                name = name_by_code.get(code, "")
                title = item["title"][:_PLACEMENT_TITLE_LIMIT]
                if _insert_market_event(
                    conn,
                    event_key=f"{PRIVATE_PLACEMENT_EVENT_TYPE}:{code}:{as_of}", code=code,
                    event_type=PRIVATE_PLACEMENT_EVENT_TYPE, as_of=as_of,
                    after_value=title,
                    details={"company_name": name, "title": title,
                             "announcement_date": item["announcement_date"]},
                    primary_reason="公告日定增披露（公告事件，非买卖点）",
                    reasons=[f"公告日 {item['announcement_date']}"],
                    source="company_action_events",
                ):
                    written += 1
        return {
            "status": "COMPLETED", "as_of": as_of,
            "limit_up": len(limit_ups), "private_placement": len(placements),
            "written": written,
        }
    finally:
        repository.close()


def list_market_event_lines(research_as_of: str, *, max_lines: int = 3,
                            db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """日报「市场事件」行：涨停/定增，最多 max_lines 条；源空 → 空列表（段省略）。"""
    from src.value_strategy.event_store import ValueStrategyEventRepository

    repository = ValueStrategyEventRepository(Path(db_path) if db_path else None)
    try:
        rows = repository.list_events(
            market="CN", research_as_of=research_as_of, limit=200,
        )
    finally:
        repository.close()
    events = [row for row in rows
              if row.get("event_type") in (LIMIT_UP_EVENT_TYPE, PRIVATE_PLACEMENT_EVENT_TYPE)]
    # 涨停按成交额降序（缺额排最后）——老板先看市场在追谁；
    # 定增保持原顺序，排在涨停之后；合计仍受 max_lines 截断。
    limit_ups = [row for row in events if row.get("event_type") == LIMIT_UP_EVENT_TYPE]
    placements = [row for row in events if row.get("event_type") == PRIVATE_PLACEMENT_EVENT_TYPE]
    limit_ups.sort(
        key=lambda row: float((row.get("after_state") or {}).get("amount") or 0.0),
        reverse=True,
    )
    events = limit_ups + placements
    lines: list[dict[str, Any]] = []
    for event in events[:max_lines]:
        details = event.get("after_state") or {}
        code = str(event.get("stock_code") or "")
        name = str((details or {}).get("company_name") or "") or code
        if event.get("event_type") == LIMIT_UP_EVENT_TYPE:
            sentence = f"涨停：{name} {code}"
        else:
            title = str((details or {}).get("title") or "")[:_PLACEMENT_TITLE_LIMIT]
            sentence = f"定增：{name} {code} {title}".rstrip()
        lines.append({
            "stock_code": code,
            "event_type": event.get("event_type"),
            "sentence": sentence,
            "market_event": True,
        })
    return lines
