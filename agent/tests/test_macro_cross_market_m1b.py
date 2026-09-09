"""Macro Cross-Market Free Official Sources M1-B（任务书 §二十七 27 项测试）。

覆盖：Treasury 解析/失败/证书、FRED mapping 与禁用序列、ChinaMoney 解析
与 TLS 诚实失败、主备一致性、source identity、PIT 截止、月频标注、
幂等、错误隔离、bundle additive、Regime/Daily Brief/Value Line 零影响。
全部离线（fetcher/tool 均注入），不触网、不写生产库。
"""

from __future__ import annotations

import ssl
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.macro_data import chinamoney, features, fred_source, refresh, treasury
from src.macro_data.features import build_cross_market_context, fx_official_mid_ready
from src.macro_data.identity import (
    ALL_IDENTITIES, BANNED_FRED_SERIES, PIT_FORWARD_CAPTURED, PIT_HISTORICAL_BACKFILL,
)
from src.macro_data.refresh import (
    _row, run_refresh, treasury_fred_crosscheck,
)
from src.macro_data.store import CrossMarketStore
from src.macro_forecast.bundle import build_bundle_payload
from src.macro_forecast.contracts import build_timeline

SH = ZoneInfo("Asia/Shanghai")
DAYS = ["20260525", "20260526", "20260527", "20260528", "20260529",
        "20260601", "20260602", "20260603", "20260604", "20260605"]


def _ts(day: str, hour: int = 15, minute: int = 0) -> str:
    return f"{day}T{hour:02d}:{minute:02d}:00+08:00"


# ---------------------------------------------------------------------------
# §1-5 Treasury fetcher
# ---------------------------------------------------------------------------

_TREASURY_CSV = (
    "Date,1 Mo,2 Mo,3 Mo,2 Yr,10 Yr,30 Yr\n"
    "09/04/2026,3.79,3.90,3.91,4.37,4.78,5.24\n"
    "09/03/2026,3.83,3.91,3.89,4.34,4.77,5.25\n"
)


def _fake_fetch(body: bytes, status: int = 200, content_type: str = "text/csv"):
    def fetcher(url: str, timeout: float = 30.0):
        if status != 200:
            raise treasury.TreasuryFetchError(f"treasury http {status}")
        return status, content_type, body
    return fetcher


def test_1_treasury_csv_parse_2y() -> None:
    days = treasury.parse_yearly_csv(_TREASURY_CSV.encode("utf-8"))
    assert days[0].observation_date == "2026-09-04"
    assert days[0].us2y == pytest.approx(4.37)


def test_2_treasury_csv_parse_10y() -> None:
    days = treasury.parse_yearly_csv(_TREASURY_CSV.encode("utf-8"))
    assert days[0].us10y == pytest.approx(4.78)
    assert days[1].us10y == pytest.approx(4.77)


def test_3_treasury_empty_value_null_not_zero() -> None:
    body = ("Date,2 Yr,10 Yr\n09/04/2026,4.37,\n").encode("utf-8")
    days = treasury.parse_yearly_csv(body)
    assert days[0].us2y == pytest.approx(4.37)
    assert days[0].us10y is None  # 空值保持 null
    rows = refresh._row("us_treasury_2y", "2026-09-04", "2026-09-04", 4.37,
                        pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"),
                        source_url="x")
    assert rows["value"] == pytest.approx(4.37)


def test_4_treasury_http_failure_raises() -> None:
    with pytest.raises(treasury.TreasuryFetchError):
        treasury.fetch_year(2026, fetcher=_fake_fetch(b"", status=500))
    with pytest.raises(treasury.TreasuryFetchError):
        treasury.fetch_year(2026, fetcher=lambda url, timeout: (_ for _ in ()).throw(
            treasury.TreasuryFetchError("treasury request failed: unreachable")))


def test_5_treasury_cert_verify_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, ssl.SSLContext] = {}

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/csv"}
        def read(self) -> bytes:
            return _TREASURY_CSV.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *args): return False

    def fake_urlopen(request, timeout=None, context=None):
        captured["ctx"] = context
        return FakeResponse()

    monkeypatch.setattr(treasury.urllib.request, "urlopen", fake_urlopen)
    treasury.fetch_year(2026)
    context = captured["ctx"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    source = Path(treasury.__file__).read_text(encoding="utf-8")
    assert "_create_unverified_context" not in source  # 无任何关闭校验的降级
    assert "check_hostname = False" not in source


# ---------------------------------------------------------------------------
# §6-11 FRED channel
# ---------------------------------------------------------------------------

class _FakeFredTool:
    def __init__(self, envelope: str) -> None:
        self._envelope = envelope
        self.calls: list[dict] = []

    def execute(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self._envelope


def _fred_envelope(native: str, observations: list[dict]) -> str:
    import json
    return json.dumps({"ok": True, "data": {"series_id": native,
                                            "observations": observations, "count": len(observations)}})


def test_6_7_8_9_fred_series_mapping_and_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.macro_data import fred_source

    assert fred_source.FRED_SERIES_MAP == {
        "us_treasury_2y_fred": "DGS2",
        "us_treasury_10y_fred": "DGS10",
        "wti_spot": "DCOILWTICO",
        "copper_world_monthly": "PCOPPUSDM",
    }
    cases = [
        ("us_treasury_2y_fred", "DGS2", [{"date": "2026-09-02", "value": 4.39}]),
        ("us_treasury_10y_fred", "DGS10", [{"date": "2026-09-02", "value": 4.79}]),
        ("wti_spot", "DCOILWTICO", [{"date": "2026-09-01", "value": 91.48},
                                    {"date": "2026-09-02", "value": "."}]),
        ("copper_world_monthly", "PCOPPUSDM", [{"date": "2026-06-01", "value": 13552.0}]),
    ]
    for key, native, observations in cases:
        fake = _FakeFredTool(_fred_envelope(native, observations))
        monkeypatch.setattr(fred_source, "_tool", lambda tool=fake: tool)
        monkeypatch.setattr(fred_source, "key_status", lambda: "PRESENT")
        result = fred_source.fetch_series(key, start_date="2026-01-01", end_date="2026-12-31")
        assert fake.calls[0]["series_id"] == native  # 正确 mapping
        assert result[-1].observation_date == observations[-1]["date"]
        assert result[-1].value is not None or observations[-1]["value"] == "."
        if observations[-1]["value"] == ".":
            assert result[-1].value is None  # FRED 缺测 "." → null


def test_10_11_banned_fred_series_rejected() -> None:
    for native in ("DCOILWTI", "GOLDAMGBD228NLBM", "GOLDPMGBD228NLBM"):
        assert native in BANNED_FRED_SERIES
        with pytest.raises(fred_source.BannedSeriesError):
            fred_source.guard_series(native)
    # 旧错误 ID 不在任何正式 mapping 里
    assert "DCOILWTI" not in fred_source.FRED_SERIES_MAP.values()
    assert "GOLDAMGBD228NLBM" not in fred_source.FRED_SERIES_MAP.values()


def test_fred_key_missing_blocked_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.macro_data import fred_source

    monkeypatch.setattr(fred_source, "key_status", lambda: "MISSING")
    with pytest.raises(fred_source.FredKeyMissing):
        fred_source.fetch_series("wti_spot", start_date="2026-01-01", end_date="2026-12-31")


# ---------------------------------------------------------------------------
# §12 primary/fallback + §13 source identity + §16 USD/CNY 定义分离
# ---------------------------------------------------------------------------

def _cross_rows() -> list[dict]:
    rows: list[dict] = []
    for index, (day, value) in enumerate([
        ("2026-09-01", 4.30), ("2026-09-02", 4.39), ("2026-09-04", 4.37),
    ]):
        rows.append(_row("us_treasury_2y", day, day, value,
                         pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"),
                         source_url="u"))
        rows.append(_row("us_treasury_10y", day, day, value + 0.41,
                         pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"),
                         source_url="u"))
        rows.append(_row("usd_cny_official_mid", day, day, 6.78 + index * 0.001,
                         pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08", 9, 20),
                         source_url="u", published_at=f"{day}T09:15:00+08:00",
                         published_at_precision="DATETIME_SCHEDULED"))
    return rows


def test_12_primary_fallback(tmp_path: Path) -> None:
    cutoff = datetime(2026, 9, 8, 16, 45, tzinfo=SH)
    now = datetime(2026, 9, 8, 16, 45, tzinfo=SH)
    primary_only = build_cross_market_context(_cross_rows(), cutoff=cutoff, now=now)
    assert primary_only["us_rates"]["treasury_2y"]["source_id"] == "treasury.gov.yield_curve"
    assert primary_only["us_rates"]["treasury_2y"]["fallback_used"] is False

    # 主源缺数据 → FRED fallback 接管，actual_source_used 保留 fred 身份
    fred_rows = [
        _row("us_treasury_2y_fred", "2026-09-02", "2026-09-03", 4.39,
             pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"), source_url="u"),
    ]
    ctx = build_cross_market_context(fred_rows, cutoff=cutoff, now=now)
    entry = ctx["us_rates"]["treasury_2y"]
    assert entry["source_id"] == "fred.dgs2"
    assert entry["actual_source_used"] == "fred.dgs2"
    assert entry["fallback_used"] is True and entry["fallback_reason"] == "PRIMARY_MISSING"

    # 主源陈旧 → 亦允许 fallback（PRIMARY_STALE）
    stale = [
        _row("us_treasury_2y", "2026-06-01", "2026-06-01", 4.20,
             pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-06-02"), source_url="u"),
        _row("us_treasury_2y_fred", "2026-09-02", "2026-09-03", 4.39,
             pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"), source_url="u"),
    ]
    ctx = build_cross_market_context(stale, cutoff=cutoff, now=now)
    entry = ctx["us_rates"]["treasury_2y"]
    assert entry["actual_source_used"] == "fred.dgs2"
    assert entry["fallback_reason"] == "PRIMARY_STALE"


def test_13_source_identity_preserved(tmp_path: Path) -> None:
    store = CrossMarketStore(tmp_path / "research.db")
    store._conn.execute("""CREATE TABLE IF NOT EXISTS macro_series (
        series_id TEXT NOT NULL, observation_date TEXT NOT NULL, release_date TEXT NOT NULL,
        vintage_id TEXT NOT NULL, value REAL, unit TEXT NOT NULL, source TEXT NOT NULL,
        source_url TEXT NOT NULL DEFAULT '', release_status TEXT NOT NULL,
        fetched_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(series_id, observation_date, release_date, vintage_id))""")
    row = _row("wti_spot", "2026-09-01", "2026-09-02", 91.48,
               pit_status=PIT_FORWARD_CAPTURED, captured_at=_ts("2026-09-08"),
               source_url="u")
    store.upsert_rows([row])
    back = store.read_rows(["wti_spot"])
    metadata = back[0]["metadata"]
    for field in ("source_id", "source_tier", "series_native_id", "definition_tag",
                  "frequency", "timezone", "unit", "observed_at", "published_at",
                  "published_at_precision", "captured_at", "captured_via", "pit_status",
                  "source_hash"):
        assert field in metadata, field
    assert metadata["series_native_id"] == "DCOILWTICO"
    assert metadata["definition_tag"] == "wti_spot_cushing_fob"
    assert metadata["pit_status"] == "FORWARD_CAPTURED"
    store.close()


def test_16_usd_cny_definition_separated(tmp_path: Path) -> None:
    # 官方中间价是独立 series_id，与旧 usd_cny / 市场价 / CNH 无交集
    assert "usd_cny_official_mid" in ALL_IDENTITIES
    assert "usd_cny" not in ALL_IDENTITIES
    assert "usd_cny_market" not in ALL_IDENTITIES
    assert "usd_cnh" not in ALL_IDENTITIES
    identity = ALL_IDENTITIES["usd_cny_official_mid"]
    assert identity.definition_tag == "cny_official_mid_rate"
    assert identity.series_native_id == "USD/CNY"
    row = _row("usd_cny_official_mid", "2026-09-07", "2026-09-07", 6.7795,
               pit_status=PIT_FORWARD_CAPTURED, captured_at=_ts("2026-09-08", 9, 20),
               source_url="u", published_at="2026-09-07T09:15:00+08:00",
               published_at_precision="DATETIME_SCHEDULED")
    assert row["metadata"]["definition_tag"] == "cny_official_mid_rate"
    # 未注册序列禁止入库（防止其它汇率混入同一 series_id）
    store = CrossMarketStore(tmp_path / "research.db")
    with pytest.raises(ValueError):
        store.upsert_rows([{**row, "series_id": "usd_cny"}])
    store.close()


# ---------------------------------------------------------------------------
# §14-15 ChinaMoney + §17-23 PIT / freshness / store 行为
# ---------------------------------------------------------------------------

def test_14_chinamoney_parse() -> None:
    payload = {"head": {"rep_code": "200"}, "records": [
        {"date": "2026-09-01", "values": ["6.7750"]},
        {"date": "2026-09-07", "values": ["6.7795"]},
    ]}
    days = chinamoney.parse_records(payload)
    assert days[-1].observation_date == "2026-09-07"
    assert days[-1].mid == pytest.approx(6.7795)
    published, precision = chinamoney.published_at_iso("2026-09-07")
    assert published == "2026-09-07T09:15:00+08:00"
    assert precision == "DATETIME_SCHEDULED"


def test_15_chinamoney_tls_failure_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    def tls_fail(url, timeout=None, context=None):
        raise chinamoney.ssl.SSLCertVerificationError(1, "certificate verify failed")

    monkeypatch.setattr(chinamoney.urllib.request, "urlopen", tls_fail)
    with pytest.raises(chinamoney.ChinaMoneyTlsBlocked) as excinfo:
        chinamoney.fetch_mid_range("2026-09-01", "2026-09-07")
    assert "TLS_CERTIFICATE_BLOCKED" in str(excinfo.value)
    source = Path(chinamoney.__file__).read_text(encoding="utf-8")
    assert "_create_unverified_context" not in source
    assert "check_hostname = False" not in source
    assert "create_default_context" in source  # 严格校验路径存在
    # 注入式 fetcher 的 TLS 失败同样不吞
    def injected(url, timeout=None):
        raise chinamoney.ChinaMoneyTlsBlocked("TLS_CERTIFICATE_BLOCKED")
    with pytest.raises(chinamoney.ChinaMoneyTlsBlocked):
        chinamoney.fetch_mid_range("2026-09-01", "2026-09-07", fetcher=injected)


def test_17_historical_backfill_not_forward_captured(tmp_path: Path) -> None:
    store = CrossMarketStore(_init_db(tmp_path))
    backfill = _row("us_treasury_2y", "2026-09-02", "2026-09-02", 4.39,
                    pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"),
                    source_url="u")
    store.upsert_rows([backfill])
    # 同 PK 的 forward 重抓：不得翻写 pit 证据属性（首次捕获优先）
    forward = {**backfill, "metadata": {**backfill["metadata"],
                                        "pit_status": PIT_FORWARD_CAPTURED}}
    store.upsert_rows([forward])
    rows = store.read_rows(["us_treasury_2y"])
    assert len(rows) == 1
    assert rows[0]["metadata"]["pit_status"] == PIT_HISTORICAL_BACKFILL
    store.close()


def test_18_pit_cutoff_excludes_future_capture() -> None:
    rows = _cross_rows()  # 全部 2026-09-08 18:00/09:20 抓取
    earlier_cutoff = datetime(2026, 9, 8, 8, 0, tzinfo=SH)  # 抓取发生之前
    ctx = build_cross_market_context(rows, cutoff=earlier_cutoff,
                                     now=earlier_cutoff)
    assert ctx["us_rates"]["treasury_2y"]["value"] is None
    assert ctx["status"] == "UNAVAILABLE"
    # 同日 cutoff 晚于抓取 → 可见
    later_cutoff = datetime(2026, 9, 8, 16, 45, tzinfo=SH)
    ctx = build_cross_market_context(rows, cutoff=later_cutoff, now=later_cutoff)
    assert ctx["us_rates"]["treasury_2y"]["value"] == pytest.approx(4.37)


def test_19_monthly_copper_labeled_monthly() -> None:
    rows = [
        _row("copper_world_monthly", "2026-07-01", "2026-08-15", 13600.0,
             pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"), source_url="u"),
        _row("copper_world_monthly", "2026-08-01", "2026-09-15", 13700.0,
             pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"), source_url="u"),
    ]
    ctx = build_cross_market_context(
        rows, cutoff=datetime(2026, 9, 16, 16, 45, tzinfo=SH),
        now=datetime(2026, 9, 16, 16, 45, tzinfo=SH))
    copper = ctx["commodities"]["copper_monthly"]
    assert copper["frequency"] == "MONTHLY"
    assert copper["value"] == pytest.approx(13700.0)
    assert copper["changes"]["mom_pct"] == pytest.approx(13700.0 / 13600.0 - 1.0, abs=1e-4)
    assert "chg_1d" not in copper["changes"]  # 月频不得伪装日频变化
    assert ALL_IDENTITIES["copper_world_monthly"].frequency == "MONTHLY"


def test_20_wti_publication_lag_respected() -> None:
    # WTI 观测 09-04（周五）→ 官方发布滞后 1 个工作日 → 09-07（周一）
    rows = [
        _row("wti_spot", "2026-09-04", "2026-09-07", 87.03,
             pit_status=PIT_FORWARD_CAPTURED, captured_at=_ts("2026-09-07", 20), source_url="u"),
    ]
    # 观测日当天中国 EOD：T 日现货尚未官方发布 → 不得用于 T 日预测
    cutoff_same_day = datetime(2026, 9, 4, 16, 45, tzinfo=SH)
    ctx = build_cross_market_context(rows, cutoff=cutoff_same_day, now=cutoff_same_day)
    assert ctx["commodities"]["wti_spot"]["value"] is None
    # 发布次日抓取后（T+1）→ 可见
    cutoff_after = datetime(2026, 9, 8, 16, 45, tzinfo=SH)
    ctx = build_cross_market_context(rows, cutoff=cutoff_after, now=cutoff_after)
    assert ctx["commodities"]["wti_spot"]["value"] == pytest.approx(87.03)


def test_21_curve_10y_2y() -> None:
    ctx = build_cross_market_context(
        _cross_rows(), cutoff=datetime(2026, 9, 8, 16, 45, tzinfo=SH),
        now=datetime(2026, 9, 8, 16, 45, tzinfo=SH))
    curve = ctx["us_rates"]["curve_10y_2y"]
    assert curve["value"] == pytest.approx((4.37 + 0.41) - 4.37)
    assert curve["unit"] == "percentage_points"


def _init_db(tmp_path: Path) -> Path:
    path = tmp_path / "research.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS macro_series (
        series_id TEXT NOT NULL, observation_date TEXT NOT NULL, release_date TEXT NOT NULL,
        vintage_id TEXT NOT NULL, value REAL, unit TEXT NOT NULL, source TEXT NOT NULL,
        source_url TEXT NOT NULL DEFAULT '', release_status TEXT NOT NULL,
        fetched_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(series_id, observation_date, release_date, vintage_id))""")
    conn.commit()
    conn.close()
    return path


def test_22_idempotent_refresh_zero_duplicates(tmp_path: Path) -> None:
    store = CrossMarketStore(_init_db(tmp_path))
    rows = _cross_rows()
    first = store.upsert_rows(rows)
    again = store.upsert_rows(rows)
    third = store.upsert_rows(list(reversed(rows)))
    assert first == len(rows)
    assert again == 0 and third == 0  # 重复 refresh 0 duplicate
    conn = sqlite3.connect(str(tmp_path / "research.db"))
    total = conn.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0]
    conn.close()
    assert total == len(rows)
    store.close()


def test_23_one_source_failure_partial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    ok_result = {"source": "chinamoney.ccpr", "status": "READY", "rows": 5, "inserted": 5, "error": None}

    def failing_treasury(*args, **kwargs):
        raise treasury.TreasuryFetchError("treasury http 500")

    monkeypatch.setattr(refresh, "refresh_treasury", failing_treasury)
    monkeypatch.setattr(refresh, "refresh_chinamoney", lambda *a, **k: dict(ok_result))
    monkeypatch.setattr(refresh, "refresh_fred_series",
                        lambda *a, **k: {"source": "fred", "status": "BLOCKED", "rows": 0,
                                         "inserted": 0, "error": "FRED_KEY_STATUS=MISSING"})
    summary = run_refresh(db, treasury_enabled=True, chinamoney_enabled=True, fred_enabled=True)
    assert summary["overall"] == "PARTIAL"  # treasury 失败不阻塞其它源
    statuses = {item["source"]: item["status"] for item in summary["sources"]}
    assert statuses["treasury.gov"] == "FAILED"
    assert statuses["chinamoney.ccpr"] == "READY"

    def all_fail(*args, **kwargs):
        raise treasury.TreasuryFetchError("down")
    monkeypatch.setattr(refresh, "refresh_treasury", all_fail)
    monkeypatch.setattr(refresh, "refresh_chinamoney", all_fail)
    monkeypatch.setattr(refresh, "refresh_fred_series", all_fail)
    assert run_refresh(db, treasury_enabled=True, chinamoney_enabled=True,
                       fred_enabled=True)["overall"] == "FAILED"


def test_treasury_fred_crosscheck_stats() -> None:
    treasury_rows = [
        _row("us_treasury_2y", f"2026-08-{day:02d}", f"2026-08-{day:02d}", 4.30 + day * 0.001,
             pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"), source_url="u")
        for day in range(1, 29)
    ]
    fred_rows = [
        _row("us_treasury_2y_fred", f"2026-08-{day:02d}", f"2026-08-{day:02d}", 4.30 + day * 0.001 + 0.01,
             pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"), source_url="u")
        for day in range(1, 29)
    ]
    report = treasury_fred_crosscheck(treasury_rows, fred_rows)
    leg = report["legs"]["us2y"]
    assert leg["matched_days"] == 28
    assert leg["mean_abs_diff"] == pytest.approx(0.01)
    assert leg["max_abs_diff"] == pytest.approx(0.01)
    assert leg["sufficient"] is False  # <30 共同交易日 → 不足，如实标注
    assert report["policy"].startswith("Treasury 为主")


# ---------------------------------------------------------------------------
# §24-27 写入边界 / bundle additive / Regime & Daily Brief 不变
# ---------------------------------------------------------------------------

def test_24_no_non_macro_writes(tmp_path: Path) -> None:
    store = CrossMarketStore(_init_db(tmp_path))
    row = _row("us_treasury_2y", "2026-09-02", "2026-09-02", 4.39,
               pit_status=PIT_HISTORICAL_BACKFILL, captured_at=_ts("2026-09-08"), source_url="u")
    for banned in ("focus_x", "risk_y", "thesis_z", "low_value_pool", "watchpoints"):
        with pytest.raises(ValueError):
            store.upsert_rows([{**row, "series_id": banned}])
    store.close()
    # run_refresh 摘要字段只含 macro 源信息
    summary = {"mode": "backfill", "pit_status": "HISTORICAL_BACKFILL", "overall": "READY",
               "sources": [{"source": "treasury.gov", "status": "READY"}]}
    assert all(key in {"mode", "pit_status", "ran_at", "overall", "sources",
                       "treasury_fred_crosscheck"} for key in summary)


def test_25_bundle_additive_field_only() -> None:
    timeline = build_timeline("20260608", DAYS + ["20260608"])
    base = dict(timeline=timeline, bars_map={}, series_rows=[], industry_rows=[],
                benchmark_code="000300.SH", macro_catalog=[], registry_version="t",
                bars_as_of="20260605")
    without = build_bundle_payload(**base)
    assert without["input_bundle_version"] == "macro-forecast-input-v1.1.0"
    assert without["cross_market_context"] == {"status": "UNAVAILABLE", "reason": "NOT_PROVIDED"}
    ctx = build_cross_market_context(
        _cross_rows(), cutoff=datetime(2026, 9, 8, 16, 45, tzinfo=SH),
        now=datetime(2026, 9, 8, 16, 45, tzinfo=SH))
    with_context = build_bundle_payload(**base, cross_market_context=ctx)
    # 既有字段保持不变（additive）
    for key in ("target", "market", "industries", "macro_facts", "macro_coverage",
                "macro_series_catalog", "schedule", "evaluation"):
        assert with_context[key] == without[key], key
    assert with_context["cross_market_context"]["status"] in {"OK", "PARTIAL"}
    # GAP_FX 条件化：中间价新鲜 → 消解 USDCNY_STALE 缺口
    assert "USDCNY_STALE_SINCE_2021_05" not in with_context["gaps"]
    assert "USDCNY_STALE_SINCE_2021_05" in without["gaps"]
    assert fx_official_mid_ready(with_context["cross_market_context"]) is True
    assert fx_official_mid_ready(None) is False


def _import_purity(banned_prefixes: tuple[str, ...]) -> list[str]:
    """子进程内干净导入 macro_data/bundle，检查不引入禁区模块。

    sys.modules 代理断言在同一 pytest 进程被其它套件污染后必然误报，
    因此必须进程隔离。
    """
    import os
    import subprocess

    code = (
        "import sys;"
        "import src.macro_data, src.macro_forecast.bundle;"
        "bad=[m for m in sys.modules if m.startswith($PREFIXES)];"
        "print('CONTAMINATED=' + ','.join(bad))"
    ).replace("$PREFIXES", repr(banned_prefixes))
    root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=root)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=env, timeout=120, cwd=root,
    )
    assert result.returncode == 0, result.stderr[-800:]
    for line in result.stdout.splitlines():
        if line.startswith("CONTAMINATED="):
            return [item for item in line.split("=", 1)[1].split(",") if item]
    raise AssertionError("purity probe produced no marker")


def test_26_macro_regime_unchanged() -> None:
    # macro_data 包不依赖 Regime/策略引擎模块（五轴公式零改动）
    assert _import_purity(("src.strategy_engines",)) == []
    assert not hasattr(features, "regime_score")
    assert not hasattr(features, "composite_score")


def test_27_daily_brief_and_value_line_untouched() -> None:
    # macro_data / bundle 构建路径不引入日报与值线模块
    assert _import_purity(
        ("src.investment_research_supervisor", "src.value_workspace")) == []
    # bundle 输出不含任何新日报段落字段
    timeline = build_timeline("20260608", DAYS + ["20260608"])
    payload = build_bundle_payload(timeline=timeline, bars_map={}, series_rows=[],
                                   industry_rows=[], benchmark_code="000300.SH",
                                   macro_catalog=[], registry_version="t",
                                   bars_as_of="20260605")
    assert "us_rates_brief" not in payload and "oil_section" not in payload
