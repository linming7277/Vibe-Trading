"""Boss-facing next-session outlook projection (read-only).

独立解说投影：只读已有序列，产出四段式「下一交易日前瞻」（走势/资金/板块），
供日报正文、飞书卡片与 Excel 共用。边界：

- 只读投影，绝不写回、也绝不被 L3 / 低估池 / Focus / 价格区消费；
- 不出现交易表述；预测处于 SHADOW 时必须降级为「把握低·影子」；
- 老板可见文案禁止因子编号（M1/A1 等），模型原始摘要只进 debug 字段；
- 缺数写「资料不足」；信用轴（shibor 等）不冒充资金流；板块只能来自
  通达信申万一级行业相对收益，缺序列时不得回退低估池行业。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

_DIRECTION_ZH = {"WEAKER": "偏弱", "STRONGER": "偏强", "RANGE_BOUND": "中性", "NEUTRAL": "中性"}
_BENCHMARK = "000300.SH"
_FACTOR_CODE = re.compile(r"\b[MA]\d{1,2}\b")
_TRADING_LANGUAGE = re.compile(r"买入|卖出|加仓|减仓|仓位|止盈|止损|建仓")


def _runtime_db(name: str) -> Path:
    from src.config.paths import get_runtime_root

    return get_runtime_root() / name


def _connect(name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{_runtime_db(name).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


from src.tdx_data.day_file import load_sw1_index_bars


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def load_index_bars(as_of: str) -> list[dict[str, Any]]:
    """沪深300 日线（date/ amounts），PIT 截断到 as_of。"""
    try:
        conn = _connect("research.db")
        try:
            rows = conn.execute(
                "SELECT trade_date, close, amount FROM forecast_index_bars "
                "WHERE code=? AND trade_date<=? ORDER BY trade_date",
                (_BENCHMARK, as_of.replace("-", "")),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 读库失败按缺数降级
        return []
    return [dict(r) for r in rows]


def load_forecast(as_of: str) -> dict[str, Any] | None:
    """取目标交易日在 as_of 之后、最新的市场方向预测；仅方向标签与置信。"""
    try:
        conn = _connect("research.db")
        try:
            row = conn.execute(
                "SELECT id, status, market_direction, target_trade_date, structured_payload_json "
                "FROM macro_market_forecasts WHERE status IN ('SHADOW','OFFICIAL') AND target_trade_date>? "
                "ORDER BY created_at DESC LIMIT 1",
                (as_of,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    try:
        structured = json.loads(row["structured_payload_json"] or "{}")
    except (TypeError, ValueError):
        structured = {}
    market = dict((structured.get("model_output") or {}).get("market") or {})
    return {
        "id": row["id"],
        "status": row["status"],
        "direction": str(row["market_direction"] or market.get("direction") or "").upper(),
        "target_trade_date": row["target_trade_date"],
        "model_summary": str(market.get("summary") or ""),
        "invalidation_conditions": [str(i) for i in (market.get("invalidation_conditions") or [])],
    }


def load_breadth(as_of: str) -> dict[str, int] | None:
    """P 日涨跌家数，来自本地报价缓存的涨跌幅符号。"""
    try:
        conn = sqlite3.connect(f"file:{_runtime_db('tdx_data.db').as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT payload_json FROM records WHERE dataset='quotes'"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    cutoff = as_of.replace("-", "")
    tally: dict[str, list[int]] = {}
    for row in rows:
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            continue
        day = str(payload.get("data_as_of") or "").replace("-", "")[:8]
        if not day or day > cutoff:
            continue
        try:
            change = float(payload.get("change_pct"))
        except (TypeError, ValueError):
            continue
        tally.setdefault(day, [0, 0])
        if change > 0:
            tally[day][0] += 1
        elif change < 0:
            tally[day][1] += 1
    if not tally or not any(any(signals) for signals in tally.values()):
        return None
    day = max(tally)
    advancers, decliners = tally[day]
    if not advancers and not decliners:
        return None
    return {"advancers": advancers, "decliners": decliners, "day": day}




def _iso(day: str) -> str:
    day = str(day).replace("-", "")
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else str(day)




def load_breadth_from_bars(as_of: str, day_p: str, day_prev: str) -> dict[str, Any] | None:
    """报价快照缺 P 日时的兜底：用已缓存日线的收盘对比计涨跌家数。

    该缓存只覆盖研究宇宙（约千只），是**有偏样本**，调用方必须在文案里
    标注样本数，不能冒充全市场宽度。
    """
    if not day_p or not day_prev:
        return None
    day_p, day_prev = _iso(day_p), _iso(day_prev)
    try:
        conn = sqlite3.connect(f"file:{_runtime_db('tdx_data.db').as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT stock_code, trade_date, close FROM adjusted_daily_bars "
                "WHERE market='CN' AND trade_date IN (?,?)",
                (day_p, day_prev),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    closes: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        closes[row["stock_code"]][row["trade_date"]] = float(row["close"])
    advancers = decliners = 0
    for code, pair in closes.items():
        base, last = pair.get(day_prev), pair.get(day_p)
        if not base or not last:
            continue
        if last > base:
            advancers += 1
        elif last < base:
            decliners += 1
    if not advancers and not decliners:
        return None
    return {
        "advancers": advancers, "decliners": decliners, "day": day_p,
        "sample": advancers + decliners, "partial": True,
    }


def _tape(bars: list[dict[str, Any]], forecast: dict[str, Any] | None) -> dict[str, Any]:
    if len(bars) < 21 or not bars[-1].get("close"):
        return {
            "direction": "资料不足", "confidence": "低", "shadow": bool(forecast),
            "fact": "", "invalidation": "",
        }
    closes = [float(b["close"]) for b in bars]
    r1 = closes[-1] / closes[-2] - 1
    r5 = closes[-1] / closes[-6] - 1
    r20 = closes[-1] / closes[-21] - 1
    if forecast and forecast["direction"] in _DIRECTION_ZH:
        direction = _DIRECTION_ZH[forecast["direction"]]
    elif r5 < -0.01 or r20 < -0.02:
        direction = "偏弱"
    elif r5 > 0.01 or r20 > 0.02:
        direction = "偏强"
    else:
        direction = "中性"
    shadow = bool(forecast and forecast["status"] == "SHADOW")
    confidence = "低" if shadow or not forecast else "中"
    close_p = closes[-1]
    if direction == "偏强":
        invalidation = f"作废：沪深300收盘低于P日收盘价{close_p:.2f}且成交额低于P日。"
    else:
        invalidation = f"作废：沪深300收盘高于P日收盘价{close_p:.2f}且成交额高于P日。"
    fact = f"沪深300收{close_p:.2f}，近1日{_pct(r1)}、近5日{_pct(r5)}、近20日{_pct(r20)}。"
    return {
        "direction": direction, "confidence": confidence, "shadow": shadow,
        "fact": fact, "invalidation": invalidation, "close_p": close_p,
        "r1": r1, "r5": r5, "r20": r20,
    }


_FLOW_CLEAR_EDGE = 1.5  # 一侧家数达到另一侧 1.5 倍才算「明显更多」


def _flow(bars: list[dict[str, Any]], breadth: dict[str, int] | None) -> dict[str, Any]:
    """资金段措辞（固定规则，不作为任何筛选输入）：

    - 只有成交额：<0.8 成交清淡 / 0.8–1.2 成交平淡，方向不明 / >1.2 成交活跃；
    - 宽度+成交额都有：跌多且(清淡|平淡)→离场观望，涨多且活跃→资金偏进场，
      一侧明显但另一侧不配合→板块轮动，无明显一边→方向不明。
    """
    missing = ["两融", "北向"]  # 本地无这两条序列，出现时才会被移出 missing
    signals: list[str] = []
    breadth_edge: str | None = None  # "up"/"down"=明显一边, "flat"=无明显一边, None=缺数据
    if breadth and (breadth["advancers"] or breadth["decliners"]):
        adv, dec = breadth["advancers"], breadth["decliners"]
        sample_note = f"（缓存样本{breadth['sample']}只）" if breadth.get("partial") else ""
        signals.append(f"涨{adv}家/跌{dec}家{sample_note}")
        if adv >= dec * _FLOW_CLEAR_EDGE:
            breadth_edge = "up"
        elif dec >= adv * _FLOW_CLEAR_EDGE:
            breadth_edge = "down"
        else:
            breadth_edge = "flat"
    else:
        missing.insert(0, "涨跌家数")
    volume_class: str | None = None
    amounts = [float(b["amount"]) for b in bars if b.get("amount")]
    if len(amounts) >= 20 and amounts[-1] > 0:
        ratio = amounts[-1] / (sum(amounts[-21:-1]) / 20)
        volume_class = "清淡" if ratio < 0.8 else "活跃" if ratio > 1.2 else "平淡"
        signals.append(f"沪深300成交额为20日均量的{ratio:.2f}倍")
    else:
        missing.insert(0, "成交额")

    if breadth_edge is None and volume_class is None:
        return {"status": "资料不足", "basis": "", "missing": missing}
    if volume_class is None:
        # 只有宽度没有量：明显一边偏轮动，均衡则方向不明
        status = "板块轮动" if breadth_edge in {"up", "down"} else "方向不明"
    elif breadth_edge is None:
        status = {"清淡": "成交清淡", "平淡": "成交平淡，方向不明", "活跃": "成交活跃"}[volume_class]
    elif breadth_edge == "down" and volume_class in {"清淡", "平淡"}:
        status = "离场观望"
    elif breadth_edge == "up" and volume_class == "活跃":
        status = "资金偏进场"
    elif breadth_edge == "flat":
        status = "方向不明"
    else:
        status = "板块轮动"
    basis = "，".join(signals)
    return {"status": status, "basis": basis, "missing": missing}


_SW1_MIN_USABLE = 20  # 30 个申万一级里可用（末根==P日）少于该数 → 整段资料不足


def _sectors(bars: list[dict[str, Any]], benchmark_r5: float | None,
             sw1_bars: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """申万一级行业指数近 5 个交易日相对沪深300 的强 3 / 弱 3。

    只读 sw1_index_bars 小表（EOD 由 tdx_data.day_file.ingest_sw1_index_bars
    从通达信 .day 导入）；可用（末根==P日且根数足够）不足 20 个时整段资料
    不足——禁止回退成员股合成或低估池行业。
    """
    gap = {"status": "资料不足", "strong": [], "weak": [], "character": "",
           "missing": ["申万一级行业指数日线"]}
    if len(bars) < 6 or benchmark_r5 is None:
        return gap
    table = sw1_bars if sw1_bars is not None else load_sw1_index_bars()
    if not table:
        return gap
    day_p = _iso(bars[-1]["trade_date"])
    scored: list[dict[str, Any]] = []
    for entry in table.values():
        rows = entry["rows"]
        if len(rows) < 10 or _iso(rows[-1][0]) != day_p:
            continue
        closes = [close for _date, close, _amount in rows[-5:]]
        r5 = closes[-1] / closes[0] - 1
        amount5 = sum(amount for _date, _close, amount in rows[-5:])
        amount_prev5 = sum(amount for _date, _close, amount in rows[-10:-5])
        scored.append({
            "name": entry["name"], "excess": r5 - benchmark_r5,
            "rising": amount5 > amount_prev5,
        })
    if len(scored) < _SW1_MIN_USABLE:
        return gap
    scored.sort(key=lambda item: item["excess"])
    weak = scored[:3]
    strong = list(reversed(scored[-3:]))
    rising = sum(1 for item in strong if item["rising"]) >= 2
    character = "强板块是放量" if rising else "强板块是抗跌"
    return {"status": "ready", "strong": strong, "weak": weak,
            "character": character, "missing": [], "day_p": day_p}


def _sector_line(sectors: dict[str, Any]) -> str:
    if sectors["status"] != "ready":
        return "【板块】资料不足。"
    strong = "、".join(f"{item['name']}{_pct(item['excess'])}" for item in sectors["strong"])
    weak = "、".join(f"{item['name']}{_pct(item['excess'])}" for item in sectors["weak"])
    return f"【板块】相对最强：{strong}；最弱：{weak}。{sectors['character']}。"


def _assert_boss_safe(text: str) -> str:
    if _FACTOR_CODE.search(text):
        raise ValueError("outlook text must not contain factor codes")
    if _TRADING_LANGUAGE.search(text):
        raise ValueError("outlook text must not contain trading language")
    return text


def build_next_session_outlook(as_of: str, *, index_bars: list[dict[str, Any]] | None = None,
                               forecast: dict[str, Any] | None = None,
                               breadth: dict[str, int] | None = None,
                               sw1_bars: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """四段式下一交易日前瞻（只读）。缺数降级，绝不进入任何筛选链。"""
    bars = index_bars if index_bars is not None else load_index_bars(as_of)
    forecast = forecast if forecast is not None else load_forecast(as_of)
    breadth = breadth if breadth is not None else load_breadth(as_of)
    if breadth is None and len(bars) >= 2:
        # 报价快照已被次日覆盖时，退到已缓存日线的有偏样本，并显式标注。
        bar_dates = [str(b["trade_date"]) for b in bars]
        breadth = load_breadth_from_bars(as_of, bar_dates[-1], bar_dates[-2])

    tape = _tape(bars, forecast)
    flow = _flow(bars, breadth)
    benchmark_r5 = tape.get("r5")
    sectors = _sectors(bars, benchmark_r5, sw1_bars)

    if tape["direction"] == "资料不足":
        tape_line = "【走势】资料不足。"
    else:
        grip = f"把握{tape['confidence']}" + ("·影子" if tape["shadow"] else "")
        tape_line = f"【走势】基准{tape['direction']}（{grip}）。{tape['fact']}{tape['invalidation']}"
    if flow["status"] == "资料不足":
        flow_line = "【资金】资料不足。"
    else:
        flow_line = f"【资金】{flow['status']}——{flow['basis']}。"
    text = "\n".join([
        _assert_boss_safe(tape_line),
        _assert_boss_safe(flow_line),
        _assert_boss_safe(_sector_line(sectors)),
        "以上不改今天 Focus 名单。",
    ])
    return {
        "as_of": as_of,
        "available": tape["direction"] != "资料不足" or flow["status"] != "资料不足",
        "shadow": tape["shadow"],
        "confidence": tape["confidence"],
        "tape": tape, "flow": flow, "sectors": sectors,
        "text": text,
        "missing": sorted(set(flow.get("missing") or []) | set(sectors.get("missing") or [])),
        "debug": {
            "forecast_id": (forecast or {}).get("id"),
            "forecast_status": (forecast or {}).get("status"),
            "target_trade_date": (forecast or {}).get("target_trade_date"),
            "raw_model_summary": (forecast or {}).get("model_summary"),
        },
    }
