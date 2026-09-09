"""预测目标标的注册表：冻结 V1 Universe（任务卡1 §3；主规格 §5.2 §7.2 §7.4）。

冻结范围（V1，2026-09-07 依据本地 tdx_data.db sectors 587 板块 + 重点指数）：
- PRIMARY_BENCHMARK：沪深300 价格指数 000300.SH（§7.1 评价基准，不得替换）；
- REFERENCE：8 个背景指数（上证/深成/创业板/中证500/中证1000/科创50/中证A500/北证50/中证全指）；
- INDUSTRY_L2：881xxx 通达信研究末级行业 128 个（§7.2 默认行业目录）；
- THEME：8805xx-8809xx 经济主题/概念板块（V1 只分类留档，历史日线后补）；
- STYLE/REGION/SPECIAL：明确排除出预测目标；
- CROSS_MARKET：审计结论留档（V1 无可用海外原始指数 → DOMESTIC_LIMITED）。

注册表按版本不可变冻结（freeze 后同版本不可重写；重分类 = 新版本）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

REGISTRY_VERSION = "forecast-instrument-registry-v1.0.0"
UNIVERSE_FROZEN_AS_OF = "2026-09-07"

# 主规格 §7.1：大盘 = 沪深300 价格指数日收盘；禁止 ETF/期货/全收益替代。
PRIMARY_BENCHMARK = "000300.SH"

REFERENCE_INDEXES: tuple[tuple[str, str], ...] = (
    ("999999.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("000688.SH", "科创50"),
    ("000510.SH", "中证A500"),
    ("899050.BJ", "北证50"),
    ("000985.SH", "中证全指"),
)

# 板块数值区间分类（依据 2026-09-04 板块快照核对）。
_REGION_RANGE = range(880200, 880300)
_INDUSTRY_RANGE = range(881000, 881500)
_THEME_RANGES = (range(880500, 880800), range(880900, 881000))
_SPECIAL_CODES = {"880081.SH", "880082.SH"}
# 8808xx 原则上是风格/事件板块，个别经济主题放行：
_ECONOMIC_THEME_ALLOWLIST = {"880875.SH": "中小银行", "880899.SH": "钴金属"}

ROLE_PRIMARY_BENCHMARK = "PRIMARY_BENCHMARK"
ROLE_REFERENCE = "REFERENCE"
ROLE_INDUSTRY_TARGET = "INDUSTRY_TARGET"
ROLE_THEME_TARGET = "THEME_TARGET"
ROLE_EXCLUDED = "EXCLUDED"
ROLE_CROSS_MARKET_AUDIT = "CROSS_MARKET_AUDIT"

STATUS_ACTIVE = "ACTIVE"
STATUS_EXCLUDED = "EXCLUDED"
STATUS_GAP = "GAP"

# 跨市场最小集审计（V1，最多 6；只登记既有数据源结论，不新增源）。
CROSS_MARKET_AUDIT: tuple[dict[str, str], ...] = (
    {"instrument_id": "HSI", "name_zh": "恒生指数", "class": "OVERSEAS_EQUITY",
     "finding": "TDX hk_quotes 仅有个股/ETF，无原始指数序列", "status": STATUS_GAP},
    {"instrument_id": "DJI", "name_zh": "道琼斯工业指数", "class": "OVERSEAS_EQUITY",
     "finding": "TDX us_quotes 仅含 ETF（如 DJIA.US 为 BuyWrite ETF），无原始指数", "status": STATUS_GAP},
    {"instrument_id": "IXIC", "name_zh": "纳斯达克综合指数", "class": "OVERSEAS_EQUITY",
     "finding": "同上，仅杠杆/行业 ETF", "status": STATUS_GAP},
    {"instrument_id": "SPX", "name_zh": "标普500指数", "class": "OVERSEAS_EQUITY",
     "finding": "同上，仅 ETF", "status": STATUS_GAP},
    {"instrument_id": "USDCNY", "name_zh": "美元兑人民币中间价", "class": "FX",
     "finding": "macro_series usd_cny 最后观测 2021-05-13，来源停更", "status": STATUS_GAP},
)

# 宏观序列目录（本地 macro_series 18 序列 + 1 缺失，主规格 §5.2 注册字段）。
MACRO_SERIES_CATALOG: tuple[dict[str, str], ...] = (
    {"series_id": "gdp_yoy", "name_zh": "GDP 同比", "group": "growth", "frequency": "quarterly", "unit": "%"},
    {"series_id": "industrial_output_yoy", "name_zh": "工业增加值同比", "group": "growth", "frequency": "monthly", "unit": "%"},
    {"series_id": "retail_sales_yoy", "name_zh": "社会消费品零售同比", "group": "growth", "frequency": "monthly", "unit": "%"},
    {"series_id": "exports_yoy", "name_zh": "出口同比", "group": "growth", "frequency": "monthly", "unit": "%"},
    {"series_id": "fixed_asset_investment_yoy", "name_zh": "固定资产投资同比", "group": "growth", "frequency": "monthly", "unit": "%"},
    {"series_id": "pmi_manufacturing", "name_zh": "制造业 PMI", "group": "growth", "frequency": "monthly", "unit": "指数"},
    {"series_id": "cpi_yoy", "name_zh": "CPI 同比", "group": "prices", "frequency": "monthly", "unit": "%"},
    {"series_id": "ppi_yoy", "name_zh": "PPI 同比", "group": "prices", "frequency": "monthly", "unit": "%"},
    {"series_id": "m1_yoy", "name_zh": "M1 同比", "group": "credit_liquidity", "frequency": "monthly", "unit": "%"},
    {"series_id": "m2_yoy", "name_zh": "M2 同比", "group": "credit_liquidity", "frequency": "monthly", "unit": "%"},
    {"series_id": "new_rmb_loans_yoy", "name_zh": "新增人民币贷款同比", "group": "credit_liquidity", "frequency": "monthly", "unit": "%"},
    {"series_id": "lpr_1y", "name_zh": "1年期 LPR", "group": "credit_liquidity", "frequency": "monthly", "unit": "%"},
    {"series_id": "lpr_5y", "name_zh": "5年期以上 LPR", "group": "credit_liquidity", "frequency": "monthly", "unit": "%"},
    {"series_id": "shibor_3m", "name_zh": "SHIBOR 3个月", "group": "credit_liquidity", "frequency": "daily", "unit": "%"},
    {"series_id": "shibor_overnight", "name_zh": "SHIBOR 隔夜", "group": "credit_liquidity", "frequency": "daily", "unit": "%"},
    {"series_id": "usd_cny", "name_zh": "美元兑人民币", "group": "fx", "frequency": "daily", "unit": "元"},
    {"series_id": "a_share_breadth_20d", "name_zh": "A股 20日宽度", "group": "market_internal", "frequency": "daily", "unit": "比值"},
    {"series_id": "csi_all_share_risk_appetite", "name_zh": "中证全指风险偏好", "group": "market_internal", "frequency": "daily", "unit": "指数"},
    {"series_id": "social_financing_increment", "name_zh": "社融增量", "group": "credit_liquidity", "frequency": "monthly", "unit": "亿元",
     "note": "本地 0 条，序列缺失（缺口）"},
)

# 参与正式大盘方向输入的宏观组（§5.3：至少两个组各有一项可用）。
DIRECTION_MACRO_GROUPS = ("growth", "prices", "credit_liquidity")


def classify_board(code: str, name: str) -> tuple[str, str]:
    """返回 (entity_type, reason)。纯函数，便于测试锁定分类口径。"""
    normalized = str(code or "").strip().upper()
    digits = normalized.replace(".SH", "").replace(".SZ", "")
    try:
        number = int(digits)
    except ValueError:
        return "UNCLASSIFIED", f"板块代码无法解析数值：{code}"
    if normalized in _SPECIAL_CODES:
        return "SPECIAL", "终端特色趋势板块，非经济分组"
    if number in _REGION_RANGE:
        return "REGION", "地域板块（8802xx）"
    if number in _INDUSTRY_RANGE:
        return "INDUSTRY_L2", "通达信研究末级行业（881xxx）"
    if normalized in _ECONOMIC_THEME_ALLOWLIST:
        return "THEME", "8808xx 内经济主题白名单"
    if any(number in rng for rng in _THEME_RANGES):
        return "THEME", "概念/主题板块（8805xx-8807xx、8809xx）"
    if 880800 <= number < 880900:
        return "STYLE", "风格/事件板块（8808xx）"
    return "UNCLASSIFIED", f"板块代码不在已知区间：{code}"


def _instrument_id(code: str) -> str:
    return f"tdx:{code.upper()}"


def build_registry_rows(sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 tdx_data.db sectors 行（code/name）构造注册行。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code in (PRIMARY_BENCHMARK, *(item[0] for item in REFERENCE_INDEXES)):
        display = {"000300.SH": "沪深300", **{c: n for c, n in REFERENCE_INDEXES}}[code]
        role = ROLE_PRIMARY_BENCHMARK if code == PRIMARY_BENCHMARK else ROLE_REFERENCE
        rows.append({
            "instrument_id": _instrument_id(code), "code": code, "name_zh": display,
            "entity_type": "INDEX_BENCHMARK" if role == ROLE_PRIMARY_BENCHMARK else "INDEX_REFERENCE",
            "role": role, "status": STATUS_ACTIVE,
            "classification_reason": "重点指数（tdx_data.py KEY_INDEXES/INDEX_DISPLAY）",
            "history_target_days": 500 if role == ROLE_PRIMARY_BENCHMARK else 500,
        })
        seen.add(code)
    for raw in sectors:
        code = str(raw.get("code") or raw.get("Code") or "").upper()
        name = str(raw.get("name") or raw.get("Name") or "").strip()
        if not code or code in seen:
            continue
        entity_type, reason = classify_board(code, name)
        if entity_type == "INDUSTRY_L2":
            role, status, target_days = ROLE_INDUSTRY_TARGET, STATUS_ACTIVE, 250
        elif entity_type == "THEME":
            role, status, target_days = ROLE_THEME_TARGET, STATUS_ACTIVE, 0  # V1 仅分类留档
        else:
            role, status, target_days = ROLE_EXCLUDED, STATUS_EXCLUDED, 0
        rows.append({
            "instrument_id": _instrument_id(code), "code": code, "name_zh": name,
            "entity_type": entity_type, "role": role, "status": status,
            "classification_reason": reason, "history_target_days": target_days,
        })
        seen.add(code)
    for item in CROSS_MARKET_AUDIT:
        rows.append({
            "instrument_id": f"audit:{item['instrument_id']}", "code": "",
            "name_zh": item["name_zh"], "entity_type": f"CROSS_MARKET_{item['class']}",
            "role": ROLE_CROSS_MARKET_AUDIT, "status": item["status"],
            "classification_reason": item["finding"], "history_target_days": 0,
        })
    return rows


class InstrumentRegistryStore:
    """research.db 内不可变注册表（additive 表，独立于既有业务）。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS forecast_instruments (
                    instrument_id TEXT NOT NULL,
                    registry_version TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name_zh TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    classification_reason TEXT NOT NULL,
                    history_target_days INTEGER NOT NULL DEFAULT 0,
                    frozen_as_of TEXT NOT NULL,
                    PRIMARY KEY (instrument_id, registry_version)
                );
                CREATE INDEX IF NOT EXISTS idx_forecast_instruments_role
                    ON forecast_instruments(registry_version, role, entity_type);
            """)

    def close(self) -> None:
        self._conn.close()

    def freeze(self, rows: list[dict[str, Any]], *, registry_version: str = REGISTRY_VERSION,
               frozen_as_of: str = UNIVERSE_FROZEN_AS_OF) -> dict[str, Any]:
        """冻结注册表：同版本已存在即拒绝重写（不可变）；新内容须升版本。"""
        existing = self._conn.execute(
            "SELECT COUNT(*) FROM forecast_instruments WHERE registry_version=?", (registry_version,),
        ).fetchone()[0]
        if existing:
            return {"status": "ALREADY_FROZEN", "registry_version": registry_version,
                    "instrument_count": existing, "written": 0}
        with self._conn:
            self._conn.executemany(
                """INSERT INTO forecast_instruments(
                    instrument_id, registry_version, code, name_zh, entity_type, role, status,
                    classification_reason, history_target_days, frozen_as_of
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [
                    (row["instrument_id"], registry_version, row["code"], row["name_zh"],
                     row["entity_type"], row["role"], row["status"], row["classification_reason"],
                     int(row.get("history_target_days") or 0), frozen_as_of)
                    for row in rows
                ],
            )
        counts: dict[str, int] = {}
        for row in rows:
            counts[f"{row['entity_type']}"] = counts.get(row["entity_type"], 0) + 1
        return {"status": "FROZEN", "registry_version": registry_version,
                "instrument_count": len(rows), "written": len(rows), "entity_counts": counts}

    def load(self, *, registry_version: str = REGISTRY_VERSION) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM forecast_instruments WHERE registry_version=? ORDER BY instrument_id",
            (registry_version,),
        ).fetchall()
        return [dict(row) for row in rows]

    def industry_targets(self, *, registry_version: str = REGISTRY_VERSION) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM forecast_instruments WHERE registry_version=? AND role=? ORDER BY code",
            (registry_version, ROLE_INDUSTRY_TARGET),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_version(self) -> str | None:
        row = self._conn.execute(
            "SELECT registry_version FROM forecast_instruments ORDER BY frozen_as_of DESC, registry_version DESC LIMIT 1",
        ).fetchone()
        return row["registry_version"] if row else None
