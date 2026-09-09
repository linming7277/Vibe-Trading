"""宏观与次日行情前瞻：时间契约与 PIT 可见性规则（任务卡1 / 主规格 §4 §6）。

本模块只做确定性契约计算，不做任何 I/O：
- T/P/C 三个核心时间（aware datetime，拒绝 naive）；
- 交易日历纯函数（is/next/previous，周末节假日不得用 weekday 冒充）；
- 自然语言日期解析（今天/明天/下一交易日/最新）；
- 单条事实在 C 时点的可见性判定（§6.2 选择规则）；
- canonical JSON 序列化 + 指纹（§6.7 输入冻结）。

禁交易语言：本模块输出的任何文字不得包含交易动作。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

# 主规格 §4.1 / §4.2：产品默认值，不是交易所规定。
CUTOFF_CLOCK = time(8, 50)          # C：T 日 08:50 信息截止
PUBLISH_DEADLINE_CLOCK = time(9, 10)  # 正式发布截止
COLLECT_WINDOW_START = time(8, 40)   # 采集窗口开始
FORECAST_START_CLOCK = time(8, 55)   # 预测启动

# 主规格 §7.1 / §7.3：本版固定评价区间（冻结进输入包，不得事后调整重算历史）。
NEUTRAL_BAND = 0.003   # 大盘 ±0.30%
RELATIVE_BAND = 0.002  # 行业/板块超额 ±0.20 个百分点

# 主规格 §5.3：正式大盘方向预测最低输入。
MIN_BENCHMARK_CLOSINGS = 21
MIN_INDUSTRY_CLOSINGS = 21
MIN_MACRO_GROUPS = 2

# v1.1.0: additive cross_market_context（M1-B 官方跨市场源接入，仅新增字段）
INPUT_BUNDLE_VERSION = "macro-forecast-input-v1.1.0"
TIME_CONTRACT_VERSION = "macro-forecast-time-v1.0.0"
PIT_CONTRACT_VERSION = "macro-forecast-pit-v1.0.0"

# 日历状态（§4.3：无可验证日历记录 CALENDAR_UNAVAILABLE，禁止正式计分预测）。
CALENDAR_CONFIRMED = "CALENDAR_CONFIRMED"            # 日历内确认为交易日
CALENDAR_NOT_TRADING_DAY = "CALENDAR_NOT_TRADING_DAY"  # 日历内确认休市
CALENDAR_UNAVAILABLE = "CALENDAR_UNAVAILABLE"          # 本地无日历可查
CALENDAR_UNVERIFIED_NEXT_SESSION = "CALENDAR_UNVERIFIED_NEXT_SESSION"  # 日历未覆盖未来日

# PIT 状态（§6.1/§6.2；与宏观线既有 first_observed_only 语义对齐）。
STRICT_PIT = "STRICT_PIT"                          # 精确发布时间 + 抓取时间均可证 <= C
FORWARD_OBSERVED = "FORWARD_OBSERVED"  # 仅知系统首次见到时间（保守边界）
CONSERVATIVE_UPPER_BOUND = "CONSERVATIVE_UPPER_BOUND"    # 仅日期级发布，按当日末保守处理
UNVERIFIED = "UNVERIFIED"                          # 无发布时间也无首次抓取证明 → 只能进缺口

RELEASE_PRECISION_DATETIME = "DATETIME"
RELEASE_PRECISION_DATE = "DATE"


def require_aware(value: datetime, label: str) -> datetime:
    """拒绝 naive datetime：时间契约必须带时区（§4.1）。"""
    if not isinstance(value, datetime):
        raise TypeError(f"{label} 必须是 datetime，得到 {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} 必须带时区（aware datetime），不接受 naive 时间")
    return value


def to_shanghai(value: datetime) -> datetime:
    return require_aware(value, "timestamp").astimezone(SHANGHAI)


# ---------------------------------------------------------------------------
# 交易日历纯函数（输入为 YYYYMMDD 键列表，与 value_strategy.trading_calendar 同口径）
# ---------------------------------------------------------------------------

def _norm_day(value: object) -> str:
    text = str(value or "").strip().replace("-", "")[:8]
    try:
        date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    except ValueError:
        return ""
    return text


def _sorted_days(days: list[str]) -> list[str]:
    return sorted({_norm_day(item) for item in days if _norm_day(item)})


def is_trading_day(day: object, days: list[str]) -> bool | None:
    """三态：日历覆盖区间内 True/False；区间外或日历缺失返回 None。"""
    key = _norm_day(day)
    normalized = _sorted_days(days)
    if not key or not normalized:
        return None
    if key in normalized:
        return True
    if normalized[0] <= key <= normalized[-1]:
        return False  # 日历区间内的缺失日 = 休市（周末/节假日）
    return None


def is_non_trading_day(day: object, days: list[str]) -> bool | None:
    """日历内确认休市才返回 True；日历未覆盖返回 None。"""
    key = _norm_day(day)
    normalized = _sorted_days(days)
    if not normalized or not key:
        return None
    return key not in normalized


def next_trading_day(day: object, days: list[str]) -> str | None:
    """严格晚于 day 的下一个日历内交易日；日历不覆盖返回 None。"""
    key = _norm_day(day)
    normalized = _sorted_days(days)
    if not key:
        return None
    for item in normalized:
        if item > key:
            return item
    return None


def previous_trading_day(day: object, days: list[str]) -> str | None:
    key = _norm_day(day)
    normalized = _sorted_days(days)
    if not key:
        return None
    floor = None
    for item in normalized:
        if item >= key:
            break
        floor = item
    return floor


# ---------------------------------------------------------------------------
# 自然语言日期解析（§4.4）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceResolution:
    keyword: str
    target_date: str | None      # YYYYMMDD
    calendar_status: str
    explanation: str
    holiday_noted: bool = False


def resolve_reference(keyword: str, today: object, days: list[str]) -> ReferenceResolution:
    """今天/明天/下一交易日/最新 → 目标交易日与解释（不允许拿 P 日冒充 T 日）。"""
    normalized = _sorted_days(days)
    today_key = _norm_day(today)
    if not today_key:
        return ReferenceResolution(keyword, None, CALENDAR_UNAVAILABLE, "无法解析当前日期")
    word = str(keyword or "").strip().lower()

    if word == "今天":
        if today_key in normalized:
            return ReferenceResolution(word, today_key, CALENDAR_CONFIRMED, f"今日 {today_key} 为交易日")
        upcoming = next_trading_day(today_key, normalized)
        if upcoming is None:
            return ReferenceResolution(word, None, CALENDAR_UNAVAILABLE, "今日休市且日历未覆盖下一交易日")
        return ReferenceResolution(
            word, upcoming, CALENDAR_CONFIRMED,
            f"今日 {today_key} 休市，下一交易日为 {upcoming}", holiday_noted=True,
        )

    if word in {"明天", "下一交易日", "下一个交易日"}:
        upcoming = next_trading_day(today_key, normalized)
        if upcoming is None:
            return ReferenceResolution(word, None, CALENDAR_UNAVAILABLE, "日历未覆盖当前日之后的交易日")
        return ReferenceResolution(word, upcoming, CALENDAR_CONFIRMED, f"当前日 {today_key} 之后的下一交易日为 {upcoming}")

    if word == "最新":
        floor = today_key if today_key in normalized else previous_trading_day(today_key, normalized)
        if floor is None:
            return ReferenceResolution(word, None, CALENDAR_UNAVAILABLE, "日历内无任何交易日")
        return ReferenceResolution(word, floor, CALENDAR_CONFIRMED, f"最近一个日历内交易日为 {floor}（正式报告须另看是否已发布）")

    return ReferenceResolution(keyword, None, CALENDAR_UNAVAILABLE, f"未支持的日期词：{keyword}（支持 今天/明天/下一交易日/最新）")


# ---------------------------------------------------------------------------
# ForecastTimeline：T / P / C（§4.1）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForecastTimeline:
    target_date: str        # YYYYMMDD，T
    previous_date: str      # YYYYMMDD，P（T 的上一实际交易日）
    cutoff_at: datetime     # C：T 日 08:50 Asia/Shanghai（aware）
    publish_deadline: datetime
    calendar_status: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def target_iso(self) -> str:
        return f"{self.target_date[:4]}-{self.target_date[4:6]}-{self.target_date[6:8]}"

    @property
    def previous_iso(self) -> str:
        return f"{self.previous_date[:4]}-{self.previous_date[4:6]}-{self.previous_date[6:8]}" if self.previous_date else ""


def _compose(day: str, clock: time) -> datetime:
    return datetime.combine(
        date(int(day[:4]), int(day[4:6]), int(day[6:8])), clock, tzinfo=SHANGHAI,
    )


def build_timeline(target: object, days: list[str]) -> ForecastTimeline:
    """由交易日历构造 T/P/C；T 非交易日或日历缺失时如实标注（§4.3）。"""
    normalized = _sorted_days(days)
    target_key = _norm_day(target)
    if not target_key:
        raise ValueError(f"无法解析目标日：{target}")
    notes: list[str] = []
    if not normalized:
        return ForecastTimeline(
            target_key, "", _compose(target_key, CUTOFF_CLOCK), _compose(target_key, PUBLISH_DEADLINE_CLOCK),
            CALENDAR_UNAVAILABLE, ("本地交易日历为空，禁止生成正式计分预测",),
        )
    if target_key not in normalized:
        if normalized[0] <= target_key <= normalized[-1]:
            # 日历覆盖区间内的缺失日 → 日历内确认休市
            return ForecastTimeline(
                target_key, previous_trading_day(target_key, normalized) or "",
                _compose(target_key, CUTOFF_CLOCK), _compose(target_key, PUBLISH_DEADLINE_CLOCK),
                CALENDAR_NOT_TRADING_DAY, (f"目标日 {target_key} 在日历内为休市日",),
            )
        if target_key < normalized[0]:
            return ForecastTimeline(
                target_key, "", _compose(target_key, CUTOFF_CLOCK), _compose(target_key, PUBLISH_DEADLINE_CLOCK),
                CALENDAR_UNAVAILABLE, ("日历未回溯覆盖目标日，P 无法确定",),
            )
        # 目标晚于日历最后已知交易日（生产常态：日历只到昨收）→ 未验证的候选 T
        return ForecastTimeline(
            target_key, previous_trading_day(target_key, normalized) or "",
            _compose(target_key, CUTOFF_CLOCK), _compose(target_key, PUBLISH_DEADLINE_CLOCK),
            CALENDAR_UNVERIFIED_NEXT_SESSION,
            (
                "日历尚未覆盖目标日；按候选 T 处理，正式计分预测前须复核（§4.3）",
                "P 为日历内最后已知交易日；日历更新后若出现更近交易日，重新冻结将生成新版本输入包",
            ),
        )
    previous = previous_trading_day(target_key, normalized)
    if previous is None:
        notes.append("日历内无 T 之前交易日，P 无法确定")
    return ForecastTimeline(
        target_key, previous or "", _compose(target_key, CUTOFF_CLOCK), _compose(target_key, PUBLISH_DEADLINE_CLOCK),
        CALENDAR_CONFIRMED, tuple(notes),
    )


# ---------------------------------------------------------------------------
# PIT 可见性（§6.1 / §6.2 / §6.3）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactVisibility:
    usable: bool
    pit_status: str
    visible_at: datetime | None   # 本系统可证的可见时间上界
    reason: str


def _end_of_day_shanghai(day_text: str) -> datetime | None:
    key = _norm_day(day_text)
    if not key:
        return None
    return datetime.combine(
        date(int(key[:4]), int(key[4:6]), int(key[6:8])), time(23, 59, 59), tzinfo=SHANGHAI,
    )


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return require_aware(value, "timestamp")
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # 日期级文本（YYYY-MM-DD / YYYYMMDD）按当日零点 naive 拒绝：
        # 无时刻的发布时间不能冒充精确时间。
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def visibility_decision(
    *,
    cutoff: datetime,
    release_time_precision: str,
    source_released_at: object = None,
    first_seen_at: object = None,
    content_hash: object = None,
) -> FactVisibility:
    """§6.2 正式前向预测选择规则（单条事实）。

    - 必须可证 captured_at <= C；精确发布时间可用时还须 source_released_at <= C。
    - 仅日期级发布：视发布当日末为可见上界（同日盘前不可用）；有内容哈希与
      实际首次抓取时间时，取二者较晚者作保守边界（首次抓取可证"本系统已知"）。
    - 无发布时间也无首次抓取证明：UNVERIFIED，只能进资料缺口。
    """
    require_aware(cutoff, "cutoff")
    captured = _parse_timestamp(first_seen_at)
    released = _parse_timestamp(source_released_at)
    precision = str(release_time_precision or RELEASE_PRECISION_DATE).upper()

    if precision == RELEASE_PRECISION_DATETIME and released is not None:
        if captured is None:
            return FactVisibility(False, UNVERIFIED, None, "有精确发布时间但无抓取证明")
        visible_at = max(released, captured)
        return FactVisibility(
            visible_at <= cutoff, STRICT_PIT if visible_at <= cutoff else STRICT_PIT,
            visible_at,
            "精确发布时间+抓取时间" if visible_at <= cutoff else "可见时间晚于截止 C",
        )

    # 日期级发布：保守上界 = 发布日末；再与首次抓取取较晚者。
    boundaries: list[datetime] = []
    has_release_date = source_released_at is not None
    if has_release_date:
        end = _end_of_day_shanghai(str(source_released_at))
        if end is not None:
            boundaries.append(end)
    if captured is not None:
        boundaries.append(captured)
    if not boundaries:
        return FactVisibility(False, UNVERIFIED, None, "无发布时间、无抓取证明，只能进资料缺口")
    visible_at = max(boundaries)
    # 状态按证据类型：有日期级发布 → 保守上界；仅抓取证明 → 前向观察。
    status = CONSERVATIVE_UPPER_BOUND if has_release_date else FORWARD_OBSERVED
    return FactVisibility(
        visible_at <= cutoff, status, visible_at,
        "日期级发布按保守边界" if visible_at <= cutoff else "保守可见边界晚于截止 C",
    )


# 单位/口径可比性（§6.5；T09/T10 在选择层挡住混比与负基数误读）
_PCT_POINT_UNITS = {"%", "pct", "percent", "百分点"}
_RATE_UNITS = {"bp", "bps", "基点"}


def comparable_periods(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, str]:
    """同一序列才可比：单位、频率、季调、口径一致（§6.5）。"""
    for key in ("unit", "frequency", "seasonal_adjustment"):
        if str(a.get(key) or "") != str(b.get(key) or ""):
            return False, f"{key} 不一致：{a.get(key)!r} vs {b.get(key)!r}"
    return True, "同口径"


def direction_with_guards(value: float | None, previous: float | None) -> dict[str, Any]:
    """变化方向 + 负基数护栏（T10：不把负基数同比自动解读为改善）。"""
    if value is None or previous is None:
        return {"direction": None, "delta": None, "negative_base_guard": False}
    delta = round(float(value) - float(previous), 10)
    direction = "UP" if delta > 0 else ("DOWN" if delta < 0 else "FLAT")
    return {
        "direction": direction,
        "delta": delta,
        "negative_base_guard": (float(previous) < 0 or float(value) < 0),
    }


def surprise_allowed(consensus_captured_at: object, actual_release_at: object) -> bool:
    """§6.4：SURPRISE 只在一致预期先于实际发布被抓取时才允许。"""
    consensus = _parse_timestamp(consensus_captured_at)
    actual = _parse_timestamp(actual_release_at)
    return consensus is not None and actual is not None and consensus < actual


# ---------------------------------------------------------------------------
# canonical JSON + 指纹（§6.7）
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None  # 缺失用 null，不用 0（§5.4）
        if value == 0:
            return 0.0  # 归一 -0.0
        return round(value, 12)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def canonical_json(payload: Any) -> str:
    return json.dumps(_normalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(payload: Any) -> str:
    return "mfi_" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:40]
