"""ChinaMoney / CFETS USD-CNY official central parity fetcher (M1-B §七/§八)。

只取官方人民币对美元中间价（不是市场收盘、不是离岸 CNH、不是 ECB 交叉）。
TLS 严格证书校验：证书验证失败时诚实抛 ``ChinaMoneyTlsBlocked``，
绝不降级 verify=False；发布时刻按官方固定时刻表记录（每交易日
09:15:00 北京时间），precision=DATETIME_SCHEDULED 不冒充接口观测值。
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT_S = 25.0

_ENDPOINT = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
# CFETS 官方固定发布时刻：每交易日 09:15:00 北京时间。
_OFFICIAL_PUBLISH_TIME = time(9, 15, 0)
_PUBLISH_PRECISION = "DATETIME_SCHEDULED"


class ChinaMoneyFetchError(RuntimeError):
    """官方中间价接口失败（不静默吞）。"""


class ChinaMoneyTlsBlocked(ChinaMoneyFetchError):
    """证书校验失败：按任务书返回 TLS_CERTIFICATE_BLOCKED，不关闭校验。"""


@dataclass(frozen=True)
class MidRateDay:
    observation_date: str  # ISO
    mid: float | None


def _http_get_json(url: str, *, timeout: float = _TIMEOUT_S) -> dict:
    request = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    })
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = int(response.status)
            if status != 200:
                raise ChinaMoneyFetchError(f"chinamoney unexpected status {status}")
            body = response.read()
    except ssl.SSLCertVerificationError as exc:
        raise ChinaMoneyTlsBlocked(
            f"TLS_CERTIFICATE_BLOCKED: 官方站点证书无法通过严格校验: {exc}",
        ) from exc
    except ssl.SSLError as exc:
        raise ChinaMoneyTlsBlocked(f"TLS_CERTIFICATE_BLOCKED: {exc}") from exc
    except urllib.error.HTTPError as exc:
        raise ChinaMoneyFetchError(f"chinamoney http {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ChinaMoneyFetchError(f"chinamoney request failed: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChinaMoneyFetchError(f"chinamoney 响应非合法 JSON: {exc}") from exc


def parse_records(payload: dict) -> list[MidRateDay]:
    """解析 CcprHisNew records：[{"date": "...", "values": ["6.7795"]}, ...]。"""
    records = payload.get("records")
    if not isinstance(records, list):
        raise ChinaMoneyFetchError("chinamoney 响应缺 records")
    days: list[MidRateDay] = []
    for record in records:
        raw_date = str((record or {}).get("date") or "").strip()
        if not raw_date:
            continue
        try:
            observation = date.fromisoformat(raw_date[:10])
        except ValueError as exc:
            raise ChinaMoneyFetchError(f"chinamoney 日期无法解析: {raw_date!r}") from exc
        values = (record or {}).get("values") or []
        mid: float | None = None
        if values:
            text = str(values[0] or "").strip()
            if text:
                try:
                    mid = float(text)
                except ValueError as exc:
                    raise ChinaMoneyFetchError(
                        f"chinamoney 中间价无法解析: {values[0]!r}",
                    ) from exc
        days.append(MidRateDay(observation_date=observation.isoformat(), mid=mid))
    if not days:
        raise ChinaMoneyFetchError("chinamoney 区间内无中间价记录")
    return days


def build_query(start_date: str, end_date: str) -> str:
    params = urllib.parse.urlencode({
        "lang": "CN",
        "startDate": start_date,
        "endDate": end_date,
        "currency": "USD/CNY",
    })
    return f"{_ENDPOINT}?{params}"


def fetch_mid_range(start_date: str, end_date: str, *,
                    timeout: float = _TIMEOUT_S,
                    fetcher=_http_get_json) -> list[MidRateDay]:
    payload = fetcher(build_query(start_date, end_date), timeout=timeout)
    rep_code = str((payload.get("head") or {}).get("rep_code") or "")
    if rep_code and rep_code != "200":
        raise ChinaMoneyFetchError(f"chinamoney rep_code={rep_code}")
    return parse_records(payload)


def published_at_iso(observation_date: str) -> tuple[str, str]:
    """官方固定时刻表语义：obs 当日 09:15:00 北京时间 + precision 标记。"""
    day = date.fromisoformat(observation_date)
    stamp = datetime.combine(
        day, _OFFICIAL_PUBLISH_TIME, tzinfo=timezone(timedelta(hours=8)),
    )
    return stamp.isoformat(), _PUBLISH_PRECISION
