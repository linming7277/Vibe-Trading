"""FRED channel for cross-market series (M1-B §九/§二)。

复用现有 ``FredMacroTool``（同一 observations 端点、同一节流与解析、
同一 ``FRED_API_KEY`` 配置）。禁用序列（DCOILWTI / GOLD*）在进入
任何网络调用之前即被拒绝。key 缺失时按任务书输出
FRED_KEY_STATUS=MISSING，不做任何硬编码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from src.macro_data.identity import BANNED_FRED_SERIES


class FredKeyMissing(RuntimeError):
    """FRED_API_KEY 未配置：FRED 通道整体 BLOCKED。"""


class BannedSeriesError(RuntimeError):
    """审计定案禁用的 FRED 原生序列（退役/已删除），禁止恢复。"""


class FredFetchError(RuntimeError):
    """FRED 请求/解析失败（不静默吞）。"""


@dataclass(frozen=True)
class FredObservation:
    observation_date: str  # ISO
    value: float | None


# 系列 key → FRED 原生 ID（正式 mapping，审计 V1 §九冻结）。
FRED_SERIES_MAP: dict[str, str] = {
    "us_treasury_2y_fred": "DGS2",
    "us_treasury_10y_fred": "DGS10",
    "wti_spot": "DCOILWTICO",
    "copper_world_monthly": "PCOPPUSDM",
}

KEY_STATUS_MISSING = "MISSING"
KEY_STATUS_PRESENT = "PRESENT"


def key_status() -> str:
    """只报告存在性，绝不回显 key 内容。"""
    from src.config.accessor import get_env_config

    configured = (getattr(get_env_config().data, "fred_api_key", "") or "").strip()
    return KEY_STATUS_PRESENT if configured else KEY_STATUS_MISSING


def guard_series(native_series_id: str) -> str:
    native = str(native_series_id or "").strip().upper()
    if native in BANNED_FRED_SERIES:
        raise BannedSeriesError(
            f"FRED 序列 {native} 已被审计 V1 定案禁用（退役/已删除），禁止恢复",
        )
    return native


def _tool():
    from src.tools.fred_macro_tool import FredMacroTool

    return FredMacroTool()


def fetch_series(series_key: str, *, start_date: str, end_date: str,
                 limit: int = 5000) -> list[FredObservation]:
    """经现有工具取一个正式 mapped 序列的观测窗口。"""
    native = guard_series(FRED_SERIES_MAP.get(series_key, ""))
    if key_status() == KEY_STATUS_MISSING:
        raise FredKeyMissing("FRED_API_KEY is not configured")
    envelope = json.loads(_tool().execute(
        series_id=native, start_date=start_date, end_date=end_date, limit=limit,
    ))
    if not envelope.get("ok"):
        raise FredFetchError(str(envelope.get("error") or "fred fetch failed"))
    observations = (envelope.get("data") or {}).get("observations") or []
    result: list[FredObservation] = []
    for row in observations:
        raw_date = str((row or {}).get("date") or "").strip()
        if not raw_date:
            continue
        raw_value = row.get("value")
        if raw_value is None or str(raw_value).strip() in {"", ".", "NA"}:
            value = None
        else:
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise FredFetchError(f"fred 值无法解析: {raw_value!r}") from exc
        result.append(FredObservation(observation_date=raw_date[:10], value=value))
    if not result:
        raise FredFetchError(f"fred 序列 {native} 窗口内无观测")
    return result
