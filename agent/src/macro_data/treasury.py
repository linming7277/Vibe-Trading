"""US Treasury official daily par yield curve fetcher (M1-B §五/§六)。

数据源：home.treasury.gov 官方 Daily Treasury Par Yield Curve Rates CSV
（无 key、HTTPS 严格证书校验、年度文件）。只解析并持久化 2 Yr / 10 Yr
两列；空值保持 null，绝不写 0；任何 HTTP/schema/解析失败显式抛错。
"""

from __future__ import annotations

import csv
import io
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT_S = 30.0

_YEAR_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"
    "/daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)
_REQUIRED_COLUMNS = ("Date", "2 Yr", "10 Yr")


class TreasuryFetchError(RuntimeError):
    """官方 CSV 抓取/解析失败（不静默吞）。"""


@dataclass(frozen=True)
class TreasuryDay:
    observation_date: str  # ISO YYYY-MM-DD
    us2y: float | None
    us10y: float | None


def _http_get(url: str, *, timeout: float = _TIMEOUT_S) -> tuple[int, str, bytes]:
    """严格证书校验的 HTTPS GET；返回 (status, content_type, body)。"""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = int(response.status)
            content_type = str(response.headers.get("Content-Type") or "")
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise TreasuryFetchError(f"treasury http {exc.code} for year file") from exc
    except ssl.SSLError as exc:
        raise TreasuryFetchError(f"treasury tls verification failed: {exc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TreasuryFetchError(f"treasury request failed: {exc}") from exc
    if status != 200:
        raise TreasuryFetchError(f"treasury unexpected status {status}")
    return status, content_type, body


def _parse_us_date(raw: str) -> str:
    text = str(raw or "").strip()
    try:
        parsed = datetime.strptime(text, "%m/%d/%Y")
    except ValueError as exc:
        raise TreasuryFetchError(f"treasury 日期无法解析: {raw!r}") from exc
    return parsed.date().isoformat()


def _parse_yield(raw: str | None) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise TreasuryFetchError(f"treasury 收益率无法解析: {raw!r}") from exc


def parse_yearly_csv(body: bytes) -> list[TreasuryDay]:
    """解析年度 CSV；schema 缺列/日期坏行显式报错，空收益率保持 None。"""
    text = body.decode("utf-8-sig", errors="strict")
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    missing = [column for column in _REQUIRED_COLUMNS if column not in header]
    if missing:
        raise TreasuryFetchError(f"treasury csv schema 缺列: {missing}")
    days: list[TreasuryDay] = []
    for row in reader:
        raw_date = str(row.get("Date") or "").strip()
        if not raw_date:
            continue
        us2y = _parse_yield(row.get("2 Yr"))
        us10y = _parse_yield(row.get("10 Yr"))
        if us2y is None and us10y is None:
            continue
        days.append(TreasuryDay(
            observation_date=_parse_us_date(raw_date), us2y=us2y, us10y=us10y,
        ))
    if not days:
        raise TreasuryFetchError("treasury csv 无有效数据行")
    return days


def fetch_year(year: int, *, timeout: float = _TIMEOUT_S,
               fetcher=_http_get) -> list[TreasuryDay]:
    _, content_type, body = fetcher(_YEAR_URL.format(year=year), timeout=timeout)
    if content_type and not content_type.lower().startswith("text/"):
        raise TreasuryFetchError(f"treasury 非文本响应: {content_type!r}")
    return parse_yearly_csv(body)


def fetch_history(start_year: int, end_year: int, *,
                  timeout: float = _TIMEOUT_S, fetcher=_http_get) -> list[TreasuryDay]:
    """按年度文件回补（有界：调用方限定年份区间）。"""
    if end_year < start_year:
        raise TreasuryFetchError("回补年份区间为空")
    days: list[TreasuryDay] = []
    for year in range(start_year, end_year + 1):
        days.extend(fetch_year(year, timeout=timeout, fetcher=fetcher))
    seen: set[str] = set()
    unique: list[TreasuryDay] = []
    for day in days:
        if day.observation_date in seen:
            continue
        seen.add(day.observation_date)
        unique.append(day)
    return sorted(unique, key=lambda item: item.observation_date)


def latest_observation_date(days: list[TreasuryDay]) -> date:
    return date.fromisoformat(max(item.observation_date for item in days))
