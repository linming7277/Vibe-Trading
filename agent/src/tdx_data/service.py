"""Background update service for cached TongDaXin datasets."""

from __future__ import annotations

import math
import os
import statistics
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .client import TdxClient
from .store import TdxDataStore, utc_now


MODULES: dict[str, dict[str, str]] = {
    "quote": {"label": "实时行情", "description": "A股证券列表、全市场报价和市场宽度"},
    "rank": {"label": "榜单中心", "description": "涨跌榜、成交榜和通达信特色榜单"},
    "index": {"label": "指数数据", "description": "重点指数、成分股和跟踪ETF"},
    "sector": {"label": "板块行业", "description": "行业、概念、地区、风格板块及成分股"},
    "fund": {"label": "基金与新股", "description": "ETF、REITs、场内基金、可转债和申购信息"},
    "formula": {"label": "公式选股", "description": "指标、条件选股、专家系统和K线形态公式"},
    "history": {"label": "历史行情", "description": "本地K线可用性、交易日和重点指数历史"},
    "fundamental": {"label": "财务估值", "description": "全A股基础财务、扩展估值和业务信息"},
}

SPECIAL_RANKS = (
    "昨日涨停", "昨日连板", "昨日上榜", "融资增加", "最近多板", "昨日断板",
    "昨日突涨", "近期复牌", "高贝塔值", "持续增长", "近期强势", "近期弱势",
)

KEY_INDEXES = (
    "999999.SH", "399001.SZ", "899050.BJ", "399006.SZ", "000688.SH",
    "000300.SH", "000905.SH", "000852.SH", "000510.SH",
)

INDEX_DISPLAY = {
    "999999.SH": "上证指数", "399001.SZ": "深证成指", "899050.BJ": "北证50",
    "399006.SZ": "创业板指", "000688.SH": "科创50", "000300.SH": "沪深300",
    "000905.SH": "中证500", "000852.SH": "中证1000", "000510.SH": "中证A500",
}

RANK_SORT_FIELDS = {
    "涨幅榜": ("change_pct", True), "跌幅榜": ("change_pct", False),
    "成交量榜": ("volume_lots", True), "成交额榜": ("amount_10k", True),
    "换手率榜": ("turnover_rate", True), "市值榜": ("market_cap_100m", True),
    "PE榜": ("pe_ttm", False), "PB榜": ("pb_mrq", False), "股息率榜": ("dividend_yield", True),
}

QUOTE_BATCH_SIZE = 1_000
MIN_QUOTE_COVERAGE = 0.90
MIN_FUNDAMENTAL_COVERAGE = 0.90


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_payload(item: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    previous = _number(quote.get("LastClose"))
    price = _number(quote.get("Now"))
    volume_lots = _number(quote.get("Volume"))
    # Before the quote stream is ready (notably around client startup and
    # pre-open), TDX may return Now=0 for the entire market.  Zero is a missing
    # quote here, not a -100% move, and must never pass cache coverage checks.
    if price is not None and price <= 0:
        price = None
    if previous is not None and previous <= 0:
        previous = None
    change_pct = ((price / previous - 1) * 100) if price is not None and previous else None
    return {
        "code": item.get("Code"), "name": item.get("Name", ""),
        "last_close": previous, "price": price, "volume_lots": volume_lots,
        "amount_10k": round(price * volume_lots / 100, 2) if price is not None and volume_lots is not None else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "source": "通达信客户端", "data_as_of": datetime.now().astimezone().isoformat(),
        "raw": quote,
    }


def _fundamental_payload(stock: dict[str, Any], base: dict[str, Any], more: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": stock["Code"], "name": stock.get("Name") or base.get("Name", ""),
        "total_shares_10k": _number(base.get("J_zgb")), "float_shares_10k": _number(base.get("ActiveCapital")),
        "total_assets_10k": _number(base.get("J_zzc")), "net_assets_10k": _number(base.get("J_jzc")),
        "revenue_10k": _number(base.get("J_yysy")), "operating_profit_10k": _number(base.get("J_yyly")),
        "net_profit_10k": _number(base.get("J_jly")), "eps": _number(base.get("J_mgsy")),
        "bps": _number(base.get("J_mgjzc")), "shareholders": _number(base.get("J_gdrs")),
        "market_cap_100m": _number(more.get("Zsz")), "float_market_cap_100m": _number(more.get("Ltsz")),
        "pe_dynamic": _number(more.get("DynaPE")), "pe_ttm": _number(more.get("StaticPE_TTM")),
        "pb_mrq": _number(more.get("PB_MRQ")), "dividend_yield": _number(more.get("DYRatio")),
        "beta": _number(more.get("BetaValue")), "turnover_rate": _number(more.get("fHSL")),
        "main_business": more.get("MainBusiness", ""), "report_date": more.get("ReportDate", ""),
        "rd_expense_10k": _number(more.get("RDInputFee")), "staff_count": _number(more.get("StaffNum")),
        "base_raw": base, "extended_raw": more,
    }


class TdxDataService:
    def __init__(
        self,
        store: TdxDataStore | None = None,
        client: TdxClient | None = None,
    ) -> None:
        self.store = store or TdxDataStore()
        self.client = client or TdxClient()
        self.store.ensure_modules(MODULES)
        self._job_lock = threading.RLock()
        self._active_job: str | None = None
        self._formula_lock = threading.RLock()
        self._active_formula_scan: str | None = None
        self._collectors: dict[str, Callable[[Callable[[int, int, str], None]], dict[str, Any]]] = {
            "quote": self._collect_quote,
            "rank": self._collect_rank,
            "index": self._collect_index,
            "sector": self._collect_sector,
            "fund": self._collect_fund,
            "formula": self._collect_formula,
            "history": self._collect_history,
            "fundamental": self._collect_fundamental,
        }

    def status(self) -> dict[str, Any]:
        states = {item["module"]: item for item in self.store.module_states()}
        modules = []
        for code, meta in MODULES.items():
            modules.append({"code": code, **meta, **states.get(code, {})})
        active = self.store.get_job(self._active_job) if self._active_job else None
        return {
            "available": self.client.available,
            "tdx_home": str(self.client.home),
            "client_process_running": self._client_running(),
            "active_job": active,
            "modules": modules,
            "recent_jobs": self.store.latest_jobs(),
        }

    @staticmethod
    def _client_running() -> bool:
        if os.name != "nt":
            return False
        try:
            import subprocess
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq tdxw.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            return "tdxw.exe" in result.stdout.lower()
        except Exception:
            return False

    def start_update(self, module: str = "all") -> dict[str, Any]:
        if module != "all" and module not in MODULES:
            raise ValueError(f"未知数据模块：{module}")
        with self._job_lock:
            if self._active_job:
                active = self.store.get_job(self._active_job)
                if active and active["status"] in {"queued", "running"}:
                    raise RuntimeError(f"已有更新任务正在运行：{self._active_job}")
            job_id = f"tdx_{uuid.uuid4().hex[:16]}"
            self.store.create_job(job_id, module)
            self._active_job = job_id
            thread = threading.Thread(target=self._run_job, args=(job_id, module), daemon=True, name=f"tdx-update-{module}")
            thread.start()
            return self.store.get_job(job_id) or {"id": job_id, "module": module, "status": "queued"}

    def _run_job(self, job_id: str, requested: str) -> None:
        started = utc_now()
        modules = list(MODULES) if requested == "all" else [requested]
        module_errors: list[str] = []
        self.store.update_job(job_id, status="running", started_at=started, total=len(modules), message="正在连接通达信客户端")
        try:
            self.client.connect()
            for index, module in enumerate(modules):
                self.store.update_job(job_id, progress=index, message=f"正在更新：{MODULES[module]['label']}")
                self.store.set_module_state(
                    module, status="running", progress=0, total=0, message="正在读取通达信", error="", started_at=utc_now()
                )

                def progress(done: int, total: int, message: str) -> None:
                    self.store.set_module_state(module, progress=done, total=total, message=message)

                try:
                    result = self._collectors[module](progress)
                    self.store.set_module_state(
                        module, status="ready", progress=int(result.get("total", result.get("item_count", 0))),
                        total=int(result.get("total", result.get("item_count", 0))), item_count=int(result.get("item_count", 0)),
                        message=str(result.get("message", "更新完成")), metadata_json=result.get("metadata", {}),
                        error="", updated_at=utc_now(),
                    )
                except Exception as exc:
                    self.store.set_module_state(module, status="failed", message="更新失败，已保留上次成功缓存", error=str(exc), updated_at=utc_now())
                    if requested != "all":
                        raise
                    module_errors.append(f"{MODULES[module]['label']}：{exc}")
            if module_errors:
                self.store.update_job(
                    job_id, status="partial", progress=len(modules), message="部分模块更新失败，成功缓存已保留",
                    error="；".join(module_errors), completed_at=utc_now(),
                )
            else:
                self.store.update_job(
                    job_id, status="completed", progress=len(modules), message="通达信数据更新完成", completed_at=utc_now()
                )
        except Exception as exc:
            self.store.update_job(job_id, status="failed", error=str(exc), message="更新失败，已保留上次成功缓存", completed_at=utc_now())
        finally:
            with self._job_lock:
                if self._active_job == job_id:
                    self._active_job = None

    def _securities(self) -> list[dict[str, Any]]:
        rows = self.store.list_records("securities", limit=10_000)["items"]
        if rows:
            return [row["payload"] for row in rows]
        return list(self.client.call("get_stock_list", "5", list_type=1) or [])

    def _get_pricevol_batched(
        self,
        codes: list[str],
        *,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Read quotes in bounded batches and retry only failed batches once."""
        result: dict[str, dict[str, Any]] = {}
        total_batches = max(1, (len(codes) + QUOTE_BATCH_SIZE - 1) // QUOTE_BATCH_SIZE)
        for batch_index, start in enumerate(range(0, len(codes), QUOTE_BATCH_SIZE), 1):
            batch = codes[start:start + QUOTE_BATCH_SIZE]
            value = self.client.call("get_pricevol", batch) or {}
            if not value:
                value = self.client.call("get_pricevol", batch) or {}
            if isinstance(value, dict):
                result.update({str(code): quote for code, quote in value.items() if isinstance(quote, dict)})
            if progress:
                progress(batch_index, total_batches, f"正在获取实时行情 {min(start + len(batch), len(codes)):,}/{len(codes):,}")
        return result

    def _collect_quote(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        stocks = list(self.client.call("get_stock_list", "5", list_type=1) or [])
        if not stocks:
            raise RuntimeError("通达信未返回A股证券列表，已保留上次成功缓存")
        progress(0, max(1, (len(stocks) + QUOTE_BATCH_SIZE - 1) // QUOTE_BATCH_SIZE), f"已取得 {len(stocks):,} 只A股")
        codes = [row["Code"] for row in stocks]
        pricevol = self._get_pricevol_batched(codes, progress=progress)
        quotes = [_quote_payload(row, pricevol.get(row["Code"], {})) for row in stocks]
        valid = [row for row in quotes if row["price"] is not None and row["last_close"]]
        minimum = max(1, math.ceil(len(stocks) * MIN_QUOTE_COVERAGE))
        if len(valid) < minimum:
            raise RuntimeError(
                f"实时行情仅返回 {len(valid):,}/{len(stocks):,} 条有效报价，"
                "低于90%完整性门槛，已拒绝覆盖上次成功缓存"
            )
        up = sum(1 for row in valid if (row["change_pct"] or 0) > 0)
        down = sum(1 for row in valid if (row["change_pct"] or 0) < 0)
        flat = len(valid) - up - down
        changes = sorted(row["change_pct"] for row in valid if row["change_pct"] is not None)
        median = changes[len(changes) // 2] if changes else None
        # Validate every response before either dataset is replaced.  The two
        # atomic replacements happen only after a high-coverage snapshot exists.
        self.store.replace_dataset("securities", [
            {"key": row["Code"], "name": row.get("Name", ""), "payload": row} for row in stocks
        ])
        self.store.replace_dataset("quotes", [
            {"key": row["code"], "name": row["name"], "payload": row} for row in valid
        ])
        metadata = {"securities": len(stocks), "valid_quotes": len(valid), "up": up, "down": down, "flat": flat, "median_change_pct": median, "up_down_ratio": round(up / down, 4) if down else None}
        progress(len(codes), len(codes), "全市场行情已缓存")
        return {"item_count": len(valid), "total": len(codes), "metadata": metadata, "message": f"{len(valid):,} 条有效报价"}

    def _collect_rank(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        quote_rows = self.store.list_records("quotes", limit=10_000)["items"]
        if not quote_rows:
            self._collect_quote(lambda *_: None)
            quote_rows = self.store.list_records("quotes", limit=10_000)["items"]
        quotes = [row["payload"] for row in quote_rows if row["payload"].get("price") is not None]
        records: list[dict[str, Any]] = []
        for category, ordered in (
            ("涨幅榜", sorted(quotes, key=lambda row: row.get("change_pct") if row.get("change_pct") is not None else -10_000, reverse=True)),
            ("跌幅榜", sorted(quotes, key=lambda row: row.get("change_pct") if row.get("change_pct") is not None else 10_000)),
            ("成交量榜", sorted(quotes, key=lambda row: row.get("volume_lots") or 0, reverse=True)),
        ):
            for rank, row in enumerate(ordered[:200], 1):
                records.append({"key": f"{category}:{row['code']}", "category": category, "name": row["name"], "payload": {**row, "rank": rank}})
        sectors = list(self.client.call("get_sector_list", list_type=1) or [])
        lookup = {row.get("Name"): row for row in sectors}
        total = len(SPECIAL_RANKS)
        for index, name in enumerate(SPECIAL_RANKS, 1):
            block = lookup.get(name)
            if block:
                members = self.client.call("get_stock_list_in_sector", block["Code"], list_type=1) or []
                for rank, member in enumerate(members, 1):
                    code = member.get("Code", member if isinstance(member, str) else "")
                    member_name = member.get("Name", "") if isinstance(member, dict) else ""
                    records.append({"key": f"{name}:{code}", "category": name, "name": member_name, "payload": {"code": code, "name": member_name, "rank": rank, "block_code": block["Code"]}})
            progress(index, total, f"正在读取特色榜单：{name}")
        self.store.replace_dataset("ranks", records)
        counts: dict[str, int] = {}
        for row in records:
            counts[row["category"]] = counts.get(row["category"], 0) + 1
        return {"item_count": len(records), "total": total, "metadata": counts, "message": f"{len(counts)} 个榜单"}

    def _collect_index(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        groups = {"重点指数": "9", "沪深300": "23", "中证500": "24", "中证1000": "25", "国证2000": "26", "中证2000": "27", "中证A500": "28"}
        records: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        total = len(groups) + len(KEY_INDEXES)
        done = 0
        for category, code in groups.items():
            members = self.client.call("get_stock_list", code, list_type=1) or []
            for row in members:
                key = f"{category}:{row['Code']}"
                records.append({"key": key, "category": category, "name": row.get("Name", ""), "payload": row})
                if category == "重点指数":
                    seen[row["Code"]] = row
            done += 1
            progress(done, total, f"已读取{category}")
        quotes = self.client.call("get_pricevol", list(seen)) if seen else {}
        self.store.replace_dataset("indices", [
            {"key": code, "name": row.get("Name", ""), "payload": _quote_payload(row, (quotes or {}).get(code, {}))}
            for code, row in seen.items()
        ])
        etf_records = []
        for code in KEY_INDEXES:
            for row in self.client.call("get_trackzs_etf_info", code) or []:
                etf_records.append({"key": f"{code}:{row.get('Code')}", "category": code, "name": row.get("Name", ""), "payload": {"index_code": code, **row}})
            done += 1
            progress(done, total, f"已读取 {code} 跟踪ETF")
        self.store.replace_dataset("index_members", records)
        self.store.replace_dataset("index_etfs", etf_records)
        return {"item_count": len(records) + len(etf_records), "total": total, "metadata": {"key_indices": len(seen), "memberships": len(records), "tracking_etfs": len(etf_records)}, "message": f"{len(seen)} 个重点指数"}

    def _collect_sector(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        sectors = list(self.client.call("get_sector_list", list_type=1) or [])
        quotes = self.client.call("get_pricevol", [row["Code"] for row in sectors]) or {}
        sector_records = []
        member_records = []
        total = len(sectors)
        for index, row in enumerate(sectors, 1):
            members = self.client.call("get_stock_list_in_sector", row["Code"], list_type=1) or []
            payload = _quote_payload(row, quotes.get(row["Code"], {}))
            payload["member_count"] = len(members)
            sector_records.append({"key": row["Code"], "name": row.get("Name", ""), "category": row.get("Type", ""), "payload": payload})
            for member in members:
                code = member.get("Code", member if isinstance(member, str) else "")
                name = member.get("Name", "") if isinstance(member, dict) else ""
                member_records.append({"key": f"{row['Code']}:{code}", "category": row["Code"], "name": name, "payload": {"sector_code": row["Code"], "sector_name": row.get("Name", ""), "code": code, "name": name}})
            if index == 1 or index % 10 == 0 or index == total:
                progress(index, total, f"正在读取板块成分 {index}/{total}")
        self.store.replace_dataset("sectors", sector_records)
        self.store.replace_dataset("sector_members", member_records)
        return {"item_count": len(sector_records), "total": total, "metadata": {"sectors": len(sector_records), "memberships": len(member_records)}, "message": f"{len(sector_records)} 个板块"}

    def _collect_fund(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        groups = {"ETF": "31", "REITs": "30", "可转债": "32", "场内基金": "34"}
        records = []
        unique: dict[str, dict[str, Any]] = {}
        total = len(groups) + 2
        for index, (category, code) in enumerate(groups.items(), 1):
            rows = self.client.call("get_stock_list", code, list_type=1) or []
            for row in rows:
                records.append({"key": f"{category}:{row['Code']}", "category": category, "name": row.get("Name", ""), "payload": row})
                unique[row["Code"]] = row
            progress(index, total, f"已读取{category} {len(rows):,} 条")
        quotes = self.client.call("get_pricevol", list(unique)) or {}
        self.store.replace_dataset("funds", [{"key": row["key"], "category": row["category"], "name": row["name"], "payload": {**row["payload"], **_quote_payload(row["payload"], quotes.get(row["payload"]["Code"], {}))}} for row in records])
        progress(5, total, "正在读取可转债详情")
        bond_details = []
        bonds = [row for row in records if row["category"] == "可转债"]
        for row in bonds:
            detail = self.client.call("get_kzz_info", row["payload"]["Code"], field_list=[]) or {}
            if detail:
                bond_details.append({"key": row["payload"]["Code"], "name": row["name"], "payload": detail})
        self.store.replace_dataset("convertible_bonds", bond_details)
        ipo = self.client.call("get_ipo_info", ipo_type=2, ipo_date=1) or []
        self.store.replace_dataset("ipo", [{"key": row.get("Code", str(i)), "name": row.get("Name", ""), "payload": row} for i, row in enumerate(ipo)])
        progress(6, total, "基金、新股和可转债已缓存")
        counts = {category: sum(1 for row in records if row["category"] == category) for category in groups}
        return {"item_count": len(records), "total": total, "metadata": {**counts, "可转债详情": len(bond_details), "待申购": len(ipo)}, "message": f"{len(unique):,} 只不重复证券"}

    def _collect_formula(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        labels = {0: "技术指标", 1: "条件选股", 2: "专家系统", 3: "K线形态"}
        records = []
        for index, (formula_type, label) in enumerate(labels.items(), 1):
            rows = self.client.call("formula_get_all", formula_type=formula_type) or []
            for row in rows:
                records.append({"key": f"{formula_type}:{row.get('acCode')}", "category": label, "name": row.get("acName", ""), "payload": {"formula_type": formula_type, **row}})
            progress(index, len(labels), f"已读取{label} {len(rows)} 条")
        self.store.replace_dataset("formulas", records)
        counts = {label: sum(1 for row in records if row["category"] == label) for label in labels.values()}
        return {"item_count": len(records), "total": len(labels), "metadata": counts, "message": f"{len(records)} 个公式"}

    def _collect_history(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        home = Path(self.client.home)
        day_files = list((home / "vipdoc").glob("**/lday/*.day")) if (home / "vipdoc").exists() else []
        minute_files = list((home / "vipdoc").glob("**/minline/*.lc*")) if (home / "vipdoc").exists() else []
        progress(1, 2, "正在读取交易日")
        dates = self.client.call("get_trading_dates", market="SH", start_time="19900101", end_time="", count=5000) or []
        self.store.replace_dataset("trading_dates", [{"key": str(value), "payload": {"date": value}} for value in dates])
        availability = []
        for code in KEY_INDEXES:
            market_dir = "sh" if code.endswith(".SH") else "sz" if code.endswith(".SZ") else "bj"
            prefix = code.split(".")[0].lower()
            path = home / "vipdoc" / market_dir / "lday" / f"{market_dir}{prefix}.day"
            availability.append({"key": code, "name": code, "payload": {"code": code, "path": str(path), "available": path.is_file(), "size": path.stat().st_size if path.is_file() else 0}})
        self.store.replace_dataset("history_availability", availability)
        progress(2, 2, "历史数据可用性已检查")
        return {"item_count": len(day_files), "total": 2, "metadata": {"daily_files": len(day_files), "minute_files": len(minute_files), "trading_dates": len(dates), "professional_finance_files": len(list((home / 'vipdoc' / 'cw').glob('gpcw*.dat'))) if (home / 'vipdoc' / 'cw').exists() else 0}, "message": f"发现 {len(day_files):,} 个日线文件"}

    def _collect_fundamental(self, progress: Callable[[int, int, str], None]) -> dict[str, Any]:
        stocks = self._securities()
        total = len(stocks)
        records = []
        for index, stock in enumerate(stocks, 1):
            code = stock["Code"]
            base = self.client.call("get_stock_info", code, field_list=[]) or {}
            more = self.client.call("get_more_info", code, field_list=[]) or {}
            if base or more:
                payload = _fundamental_payload(stock, base, more)
                records.append({"key": code, "name": payload["name"], "payload": payload})
            if index == 1 or index % 25 == 0 or index == total:
                progress(index, total, f"正在更新全市场财务 {index:,}/{total:,}")
        minimum = max(1, math.ceil(total * MIN_FUNDAMENTAL_COVERAGE))
        if len(records) < minimum:
            raise RuntimeError(
                f"基础财务仅返回 {len(records):,}/{total:,} 只股票，"
                "低于90%完整性门槛，已拒绝覆盖上次成功缓存"
            )
        # Replace only after the full pass satisfies the completeness gate.
        # This prevents a crashed 5,000-stock refresh from leaving a mixed-date
        # cache that looks complete to the screener.
        self.store.replace_dataset("fundamentals", records)
        cw_dir = Path(self.client.home) / "vipdoc" / "cw"
        professional_files = len(list(cw_dir.glob("gpcw*.dat"))) if cw_dir.exists() else 0
        return {
            "item_count": len(records),
            "total": total,
            "metadata": {
                "updated": len(records),
                "coverage_pct": round(len(records) / total * 100, 2) if total else 0,
                "professional_finance_files": professional_files,
                # File presence is not the same as queryable row coverage.
                "professional_finance_available": professional_files > 0,
            },
            "message": f"{len(records):,} 只股票财务估值（覆盖 {len(records) / total:.1%}）" if total else "无A股证券",
        }

    def refresh_security(self, symbol: str) -> dict[str, Any]:
        code = symbol.strip().upper()
        base = self.client.call("get_stock_info", code, field_list=[]) or {}
        more = self.client.call("get_more_info", code, field_list=[]) or {}
        snapshot = self.client.call("get_market_snapshot", code, field_list=[]) or {}
        relations = self.client.call("get_relation", code) or []
        dividends = self.client.call("get_divid_factors", code, start_time="19900101", end_time="") or []
        capital = self.client.call("get_gb_info_by_date", code, start_date="19900101", end_date="") or []
        micro = self.client.call("get_exday_data", code, count=120) or []
        normalized = _fundamental_payload({"Code": code, "Name": base.get("Name", "")}, base, more)
        self.store.upsert_records("fundamentals", [{"key": code, "name": normalized["name"], "payload": normalized}])
        state = next((item for item in self.store.module_states() if item["module"] == "fundamental"), {})
        if state.get("status") != "ready":
            cached = self.store.count("fundamentals")
            self.store.set_module_state(
                "fundamental", status="partial", item_count=cached,
                message=f"已按需缓存 {cached} 只；全市场尚未更新", updated_at=utc_now(),
            )
        payload = {"code": code, "base_finance": base, "extended": more, "snapshot": snapshot, "relations": relations, "dividends": dividends, "capital": capital, "microstructure": micro, "updated_at": utc_now()}
        self.store.upsert_records("security_details", [{"key": code, "name": base.get("Name", ""), "payload": payload}])
        return payload

    def security_detail(self, symbol: str) -> dict[str, Any] | None:
        row = self.store.get_record("security_details", symbol.strip().upper())
        return row["payload"] if row else None

    def kline(self, symbol: str, *, period: str = "1d", count: int = 300, dividend_type: str = "front") -> dict[str, Any]:
        code = symbol.strip().upper()
        value = self.client.call("get_market_data", stock_list=[code], count=count, period=period, dividend_type=dividend_type, field_list=[], start_time="", end_time="", fill_data=True)
        payload = {"code": code, "period": period, "dividend_type": dividend_type, "data": value, "updated_at": utc_now()}
        self.store.upsert_records("klines", [{"key": f"{code}:{period}:{dividend_type}", "name": code, "payload": payload}])
        return payload

    def formula_detail(self, formula_type: int, code: str) -> Any:
        value = self.client.call("formula_get_info", formula_type=formula_type, formula_code=code)
        self.store.upsert_records("formula_details", [{"key": f"{formula_type}:{code}", "name": code, "payload": value}])
        return value

    def _records(self, dataset: str, limit: int = 100_000) -> list[dict[str, Any]]:
        return self.store.list_records(dataset, limit=limit)["items"]

    def _state(self, module: str) -> dict[str, Any]:
        return next((item for item in self.store.module_states() if item["module"] == module), {})

    def _quote_map(self) -> dict[str, dict[str, Any]]:
        return {item["key"]: item["payload"] for item in self._records("quotes", 10_000)}

    def _fundamental_map(self) -> dict[str, dict[str, Any]]:
        return {item["key"]: item["payload"] for item in self._records("fundamentals", 10_000)}

    def _security_sector_map(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for item in self._records("sector_members"):
            payload = item["payload"]
            result.setdefault(str(payload.get("code", "")), []).append({
                "code": payload.get("sector_code"), "name": payload.get("sector_name"),
            })
        return result

    def _joined_security_rows(self) -> list[dict[str, Any]]:
        quotes = self._quote_map()
        fundamentals = self._fundamental_map()
        sectors = self._security_sector_map()
        securities = {item["key"]: item["payload"] for item in self._records("securities", 10_000)}
        rows = []
        for code, quote in quotes.items():
            base = securities.get(code, {})
            financial = fundamentals.get(code, {})
            raw = financial.get("base_raw", {}) if isinstance(financial, dict) else {}
            rows.append({
                **quote, **{key: value for key, value in financial.items() if not key.endswith("_raw")},
                "code": code, "name": quote.get("name") or base.get("Name", ""),
                "sectors": sectors.get(code, []), "is_st": str(raw.get("IsSTGP", "0")) == "1",
                "is_quit": str(raw.get("IsQuitGP", "0")) == "1", "is_bj": code.endswith(".BJ"),
                "is_hs300": str(raw.get("BelongHS300", "0")) == "1",
                "is_margin": str(raw.get("BelongRZRQ", "0")) == "1",
                "is_connect": str(raw.get("BelongHSGT", "0")) == "1",
            })
        return rows

    def market_overview(self) -> dict[str, Any]:
        quote_rows = [item["payload"] for item in self._records("quotes", 10_000)]
        valid = [row for row in quote_rows if _number(row.get("price")) is not None and _number(row.get("last_close"))]
        changes = [float(row["change_pct"]) for row in valid if row.get("change_pct") is not None]
        up = sum(value > 0 for value in changes)
        down = sum(value < 0 for value in changes)
        flat = len(changes) - up - down
        bins = [(-100, -7, "≤-7%"), (-7, -3, "-7%~-3%"), (-3, 0, "-3%~0"), (0, 3, "0~3%"), (3, 7, "3%~7%"), (7, 100, "≥7%")]
        distribution = [{"label": label, "count": sum(low <= value < high for value in changes)} for low, high, label in bins]
        indices = []
        for item in self._records("indices", 1_000):
            payload = item["payload"]
            if item["key"] in INDEX_DISPLAY:
                indices.append({**payload, "name": INDEX_DISPLAY[item["key"]]})
        index_etfs = [item["payload"] for item in self._records("index_etfs", 1_000)]
        ranks = self.market_ranks(category="昨日涨停", limit=5)
        special_counts = {
            category: self.store.list_records("ranks", category=category, limit=1)["total"]
            for category in SPECIAL_RANKS
        }
        quote_state = self._state("quote")
        return {
            "source": "通达信客户端缓存", "as_of": quote_state.get("updated_at"),
            "client_running": self._client_running(), "indices": indices,
            "breadth": {"valid": len(valid), "up": up, "down": down, "flat": flat, "up_down_ratio": round(up / down, 4) if down else None, "median_change_pct": statistics.median(changes) if changes else None},
            "distribution": distribution,
            "activity": sorted(valid, key=lambda row: row.get("volume_lots") or 0, reverse=True)[:10],
            "index_etfs": index_etfs[:50], "limit_up_preview": ranks["items"],
            "special_counts": special_counts,
        }

    def market_ranks(
        self, *, category: str = "涨幅榜", query: str = "", sector: str = "",
        sort: str = "rank", direction: str = "asc", limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        special_categories = {item["category"] for item in self._records("ranks", 10_000)}
        rows: list[dict[str, Any]]
        if category in special_categories and category not in RANK_SORT_FIELDS:
            quote_map = self._quote_map()
            rows = []
            for item in self.store.list_records("ranks", category=category, limit=10_000)["items"]:
                payload = item["payload"]
                rows.append({**quote_map.get(str(payload.get("code", "")), {}), **payload})
        else:
            rows = self._joined_security_rows()
            field, reverse = RANK_SORT_FIELDS.get(category, ("change_pct", True))
            rows = [row for row in rows if _number(row.get(field)) is not None]
            if category in {"PE榜", "PB榜", "股息率榜"}:
                rows = [row for row in rows if (_number(row.get(field)) or 0) > 0]
            rows.sort(key=lambda row: _number(row.get(field)) or 0, reverse=reverse)
            for rank, row in enumerate(rows, 1):
                row["rank"] = rank
        if query:
            needle = query.strip().lower()
            rows = [row for row in rows if needle in str(row.get("code", "")).lower() or needle in str(row.get("name", "")).lower()]
        if sector:
            rows = [row for row in rows if any(sector in {str(value.get("code")), str(value.get("name"))} for value in row.get("sectors", []))]
        if sort != "rank":
            rows.sort(key=lambda row: _number(row.get(sort)) if _number(row.get(sort)) is not None else float("-inf"), reverse=direction == "desc")
        total = len(rows)
        financial_count = self.store.count("fundamentals")
        quote_count = self.store.count("quotes")
        return {
            "category": category, "categories": sorted(special_categories | set(RANK_SORT_FIELDS)),
            "total": total, "limit": limit, "offset": offset, "items": rows[offset:offset + limit],
            "coverage": {"quotes": quote_count, "fundamentals": financial_count, "fundamental_pct": round(financial_count / quote_count * 100, 2) if quote_count else 0},
            "as_of": self._state("rank").get("updated_at") or self._state("quote").get("updated_at"),
        }

    def sectors(self, *, category: str = "全部", query: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        quote_map = self._quote_map()
        member_rows = self._records("sector_members")
        by_sector: dict[str, list[str]] = {}
        for item in member_rows:
            payload = item["payload"]
            by_sector.setdefault(str(payload.get("sector_code", "")), []).append(str(payload.get("code", "")))
        rows = []
        for item in self._records("sectors", 2_000):
            payload = item["payload"]
            codes = by_sector.get(item["key"], [])
            member_quotes = [quote_map[code] for code in codes if code in quote_map]
            up = sum((_number(row.get("change_pct")) or 0) > 0 for row in member_quotes)
            down = sum((_number(row.get("change_pct")) or 0) < 0 for row in member_quotes)
            leaders = sorted(member_quotes, key=lambda row: _number(row.get("change_pct")) or -999, reverse=True)
            name = str(payload.get("name", item["name"]))
            kind = self._sector_kind(name, codes)
            rows.append({**payload, "category": kind, "up": up, "down": down, "breadth_pct": round(up / len(member_quotes) * 100, 2) if member_quotes else None, "leader": leaders[0] if leaders else None})
        if category != "全部":
            rows = [row for row in rows if row["category"] == category]
        if query:
            rows = [row for row in rows if query.lower() in str(row.get("name", "")).lower() or query.lower() in str(row.get("code", "")).lower()]
        rows.sort(key=lambda row: _number(row.get("change_pct")) or -999, reverse=True)
        total = len(rows)
        return {"categories": ["全部", "行业", "概念", "地区", "风格"], "total": total, "items": rows[offset:offset + limit], "as_of": self._state("sector").get("updated_at")}

    @staticmethod
    def _sector_kind(name: str, codes: list[str]) -> str:
        if name.endswith(("板块", "地区")) or any(token in name for token in ("北京", "上海", "广东", "浙江", "江苏", "贵州", "四川", "新疆", "西藏")):
            return "地区"
        if any(token in name for token in ("昨日", "近期", "高贝塔", "持续", "融资", "强势", "弱势", "涨停", "连板")):
            return "风格"
        if len(codes) < 80 and any(token in name for token in ("银行", "酿酒", "煤炭", "钢铁", "航空", "船舶", "证券", "保险", "医药", "化工")):
            return "行业"
        return "概念"

    def sector_detail(self, code: str) -> dict[str, Any] | None:
        sector = self.store.get_record("sectors", code.upper())
        if not sector:
            return None
        quote_map = self._quote_map()
        fundamental_map = self._fundamental_map()
        members = []
        for item in self.store.list_records("sector_members", category=code.upper(), limit=10_000)["items"]:
            payload = item["payload"]
            symbol = str(payload.get("code", ""))
            members.append({**payload, **quote_map.get(symbol, {}), **{key: value for key, value in fundamental_map.get(symbol, {}).items() if not key.endswith("_raw")}})
        members.sort(key=lambda row: _number(row.get("change_pct")) or -999, reverse=True)
        etfs = [item["payload"] for item in self._records("index_etfs", 1_000) if item["category"] == code.upper()]
        return {"sector": sector["payload"], "members": members, "member_count": len(members), "up": sum((_number(row.get("change_pct")) or 0) > 0 for row in members), "down": sum((_number(row.get("change_pct")) or 0) < 0 for row in members), "leaders": members[:10], "laggards": list(reversed(members[-10:])), "etfs": etfs, "as_of": sector["updated_at"]}

    def screener(self, filters: dict[str, Any]) -> dict[str, Any]:
        rows = self._joined_security_rows()
        query = str(filters.get("query", "")).strip().lower()
        if query:
            rows = [row for row in rows if query in str(row.get("code", "")).lower() or query in str(row.get("name", "")).lower()]
        if not bool(filters.get("include_st", False)):
            rows = [row for row in rows if not row["is_st"]]
        if not bool(filters.get("include_quit", False)):
            rows = [row for row in rows if not row["is_quit"]]
        if not bool(filters.get("include_bj", False)):
            rows = [row for row in rows if not row["is_bj"]]
        numeric_filters = {
            "min_price": ("price", ">="), "max_price": ("price", "<="),
            "min_change": ("change_pct", ">="), "max_change": ("change_pct", "<="),
            "min_turnover": ("turnover_rate", ">="), "max_pe": ("pe_ttm", "<="),
            "max_pb": ("pb_mrq", "<="), "min_dividend_yield": ("dividend_yield", ">="),
            "min_market_cap": ("market_cap_100m", ">="), "min_revenue": ("revenue_10k", ">="),
            "min_net_profit": ("net_profit_10k", ">="), "min_eps": ("eps", ">="),
        }
        for filter_name, (field, operator) in numeric_filters.items():
            threshold = _number(filters.get(filter_name))
            if threshold is None:
                continue
            if operator == ">=":
                rows = [row for row in rows if _number(row.get(field)) is not None and float(row[field]) >= threshold]
            else:
                rows = [row for row in rows if _number(row.get(field)) is not None and float(row[field]) <= threshold]
        sector = str(filters.get("sector", "")).strip()
        if sector:
            rows = [row for row in rows if any(sector in {str(item.get("code")), str(item.get("name"))} for item in row["sectors"])]
        for flag in ("is_hs300", "is_margin", "is_connect"):
            if bool(filters.get(flag, False)):
                rows = [row for row in rows if row[flag]]
        sort = str(filters.get("sort", "change_pct"))
        direction = str(filters.get("direction", "desc"))
        rows.sort(key=lambda row: _number(row.get(sort)) if _number(row.get(sort)) is not None else float("-inf"), reverse=direction == "desc")
        total = len(rows)
        limit = max(1, min(200, int(filters.get("limit", 50))))
        offset = max(0, int(filters.get("offset", 0)))
        return {"total": total, "items": rows[offset:offset + limit], "limit": limit, "offset": offset, "coverage": {"quotes": self.store.count("quotes"), "fundamentals": self.store.count("fundamentals")}, "as_of": self._state("quote").get("updated_at")}

    def funds(self, *, category: str = "ETF", query: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        if category == "新股/新债":
            rows = [item["payload"] for item in self._records("ipo", 1_000)]
        elif category == "可转债":
            rows = [item["payload"] for item in self._records("convertible_bonds", 1_000)]
        else:
            rows = [item["payload"] for item in self.store.list_records("funds", category=category, limit=10_000)["items"]]
        if category == "ETF":
            tracked = {
                str(item["payload"].get("Code", "")): item["payload"]
                for item in self._records("index_etfs", 10_000)
            }
            rows = [{**row, **tracked.get(str(row.get("Code", row.get("code", ""))), {})} for row in rows]
        if query:
            needle = query.lower()
            rows = [row for row in rows if needle in str(row.get("code", row.get("Code", row.get("KZZCode", "")))).lower() or needle in str(row.get("name", row.get("Name", row.get("KZZName", "")))).lower()]
        total = len(rows)
        return {"category": category, "categories": ["ETF", "REITs", "可转债", "场内基金", "新股/新债"], "total": total, "items": rows[offset:offset + limit], "as_of": self._state("fund").get("updated_at")}

    def search_securities(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        needle = query.strip().lower()
        if not needle:
            return []
        quote_map = self._quote_map()
        result = []
        for item in self._records("securities", 10_000):
            if needle in item["key"].lower() or needle in item["name"].lower():
                result.append({"code": item["key"], "name": item["name"], "quote": quote_map.get(item["key"]), "updated_at": item["updated_at"]})
                if len(result) >= limit:
                    break
        return result

    def security_overview(
        self, symbol: str, *, include_related: bool = True, include_history: bool = True,
    ) -> dict[str, Any] | None:
        """Read a company from cache, optionally omitting expensive related scans."""
        code = symbol.strip().upper()
        security = self.store.get_record("securities", code)
        if not security:
            return None
        quote = self.store.get_record("quotes", code)
        fundamental = self.store.get_record("fundamentals", code)
        detail = self.security_detail(code)
        sectors = [item["payload"] for item in self._records("sector_members") if item["payload"].get("code") == code] if include_related else []
        klines = [item["payload"] for item in self._records("klines", 10_000) if item["key"].startswith(f"{code}:")] if include_history else []
        cw_files = list((Path(self.client.home) / "vipdoc" / "cw").glob("gpcw*.dat")) if (Path(self.client.home) / "vipdoc" / "cw").exists() else []
        return {
            "code": code, "name": security["name"], "quote": quote["payload"] if quote else None,
            "fundamental": fundamental["payload"] if fundamental else None, "detail": detail,
            "sectors": sectors, "klines": klines, "professional_finance_available": bool(cw_files),
            "source": "通达信客户端缓存", "as_of": quote["updated_at"] if quote else security["updated_at"],
            "cache": {
                "quote_updated_at": quote["updated_at"] if quote else None,
                "fundamental_updated_at": fundamental["updated_at"] if fundamental else None,
                "detail_updated_at": detail.get("updated_at") if detail else None,
                "stale": not quote or (datetime.now().astimezone() - datetime.fromisoformat(quote["updated_at"])).total_seconds() > 900,
            },
        }

    def start_formula_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._formula_lock:
            if self._active_formula_scan:
                active = self.store.get_formula_scan(self._active_formula_scan)
                if active and active["status"] in {"queued", "running"}:
                    raise RuntimeError("已有公式扫描正在运行")
            scan_id = f"formula_{uuid.uuid4().hex[:16]}"
            scan = self.store.create_formula_scan(scan_id, payload)
            self._active_formula_scan = scan_id
            threading.Thread(target=self._run_formula_scan, args=(scan_id, payload), daemon=True, name="tdx-formula-scan").start()
            return scan

    def _run_formula_scan(self, scan_id: str, payload: dict[str, Any]) -> None:
        try:
            universe = str(payload.get("universe", "all"))
            if universe == "all":
                stocks = [item["key"] for item in self._records("securities", 10_000)]
            else:
                stocks = [str(item["payload"].get("code", "")) for item in self.store.list_records("sector_members", category=universe, limit=10_000)["items"]]
            stocks = [code for code in stocks if code]
            self.store.update_formula_scan(scan_id, status="running", total=len(stocks), message="正在执行通达信公式")
            result_rows: list[dict[str, Any]] = []
            quote_map = self._quote_map()
            batch_size = 200
            for start in range(0, len(stocks), batch_size):
                batch = stocks[start:start + batch_size]
                raw = self.client.call(
                    "formula_process_mul", formula_name=str(payload["formula_code"]),
                    formula_arg=str(payload.get("formula_args", "")), formula_type=int(payload["formula_type"]),
                    return_count=1, return_date=True, xsflag=-1, stock_list=batch,
                    stock_period=str(payload.get("period", "1d")), count=120, dividend_type=1,
                ) or {}
                for code, output in raw.items():
                    if code == "ErrorId" or not isinstance(output, dict):
                        continue
                    values = []
                    hit = False
                    for line, points in output.items():
                        point = points[-1] if isinstance(points, list) and points else {}
                        value = point.get("Value") if isinstance(point, dict) else point
                        date = point.get("Date") if isinstance(point, dict) else None
                        number = _number(value)
                        values.append({"line": line, "value": number if number is not None else value, "date": date})
                        if int(payload["formula_type"]) == 0:
                            hit = True
                        elif number is not None and number > 0:
                            hit = True
                    if hit:
                        result_rows.append({"code": code, **quote_map.get(code, {}), "signals": values})
                self.store.update_formula_scan(scan_id, progress=min(start + len(batch), len(stocks)), result_json=result_rows, message=f"已扫描 {min(start + len(batch), len(stocks))}/{len(stocks)}")
            self.store.update_formula_scan(scan_id, status="completed", progress=len(stocks), result_json=result_rows, message=f"扫描完成，命中 {len(result_rows)} 只", completed_at=utc_now())
        except Exception as exc:
            self.store.update_formula_scan(scan_id, status="failed", error=str(exc), message="扫描失败", completed_at=utc_now())
        finally:
            with self._formula_lock:
                if self._active_formula_scan == scan_id:
                    self._active_formula_scan = None


_service: TdxDataService | None = None
_service_lock = threading.Lock()


def get_tdx_service() -> TdxDataService:
    global _service
    with _service_lock:
        if _service is None:
            _service = TdxDataService()
        return _service
