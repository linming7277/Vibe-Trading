"""跟踪清单服务：纳入/移出/清单视图，附当前价与档位富集。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.company_tracking.store import CompanyTrackingStore
from src.config.paths import get_runtime_root


class CompanyTrackingService:
    def __init__(self, *, store: CompanyTrackingStore | None = None) -> None:
        self.store = store or CompanyTrackingStore()

    def close(self) -> None:
        self.store.close()

    def _entry_anchor(self, stock_code: str, entry_date: str, *, db_path: str) -> float | None:
        """进入重点研究当日的参考价：优先读当日池快照的现价，不可得回退最新行情。"""
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT payload_json FROM company_low_value_leader_pool_snapshots "
                    "WHERE stock_code=? AND source_as_of=? LIMIT 1",
                    (stock_code.upper(), entry_date),
                ).fetchone()
            finally:
                conn.close()
            if row:
                payload = json.loads(row["payload_json"] or "{}")
                price = payload.get("current_price")
                if isinstance(price, (int, float)):
                    return float(price)
        except Exception:  # noqa: BLE001 - 锚点缺失时回退现价，不阻断
            pass
        latest, _as_of = self._latest_price(stock_code)
        return latest

    @staticmethod
    def _latest_price(stock_code: str) -> tuple[float | None, str]:
        """现价：读行情缓存（盘中每 5 分钟刷新）；缺数据返回 (None, 原因)。"""
        from src.tdx_data.store import TdxDataStore

        store = TdxDataStore()
        try:
            row = store.get_record("quotes", stock_code.upper())
        finally:
            store.close()
        if not row:
            return None, "暂无行情缓存"
        payload = row.get("payload") or {}
        price = payload.get("price")
        as_of = str(payload.get("data_as_of") or "")
        if price is None:
            return None, "行情未含价格"
        return float(price), as_of

    def add(self, *, market: str = "CN", stock_code: str, company_name: str,
            tier: str = "", reasons: list[str] | None = None, note: str = "",
            source: str = "manual", added_date: str | None = None,
            now: datetime | None = None) -> dict[str, Any]:
        code = stock_code.strip().upper()
        if not code:
            raise ValueError("stock_code is required")
        price, price_as_of = self._latest_price(code)
        result = self.store.add(
            market=market, stock_code=code, company_name=company_name.strip(),
            source=source, tier=tier, reasons=reasons or [], price=price, note=note,
            added_date=added_date, now=now,
        )
        return {
            "status": "added", "reactivated": result.get("reactivated", False),
            "source": result.get("source"),
            "stock_code": code, "company_name": company_name.strip(),
            "added_price": result.get("added_price"),
            "price_as_of": price_as_of,
            "note": ("参考价为最近行情价格" if price is not None else "参考价待首个收盘数据补齐"),
        }

    def remove(self, stock_code: str, *, note: str = "", now: datetime | None = None) -> dict[str, Any]:
        removed = self.store.remove(stock_code.strip().upper(), note=note, now=now)
        return {"removed": removed, "stock_code": stock_code.strip().upper()}

    def list_items(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now or datetime.now().astimezone()
        items = self.store.list_active()
        focus_tiers = self._focus_tier_map()
        enriched: list[dict[str, Any]] = []
        for item in items:
            code = str(item["stock_code"])
            price, price_as_of = self._latest_price(code)
            added_price = item.get("added_price")
            change = None
            if price is not None and isinstance(added_price, (int, float)) and added_price:
                change = round((price / float(added_price) - 1) * 100, 2)
            added_day = str(item.get("added_date") or str(item.get("created_at") or "")[:10])
            tier = focus_tiers.get(code)
            enriched.append({
                "stock_code": code,
                "company_name": item.get("company_name") or code,
                "added_date": added_day,
                "days_tracked": self._days(added_day, now_dt),
                "added_tier": str(item.get("added_tier") or ""),
                "added_price": added_price,
                "current_price": price,
                "price_as_of": price_as_of,
                "change_pct": change,
                "current_tier": tier,
                "source": str(item.get("source") or "manual"),
                "added_reasons": item.get("added_reasons") or [],
            })
        enriched.sort(key=lambda item: item.get("change_pct") is not None and item["change_pct"] < 0,
                      reverse=False)
        return {
            "items": enriched,
            "total": len(enriched),
            "generated_at": now_dt.isoformat(timespec="seconds"),
        }

    @staticmethod
    def _days(added_day: str, now_dt: datetime) -> int | None:
        if not added_day:
            return None
        try:
            return (now_dt.date() - date.fromisoformat(added_day)).days
        except ValueError:
            return None

    def sync_focus_a(self, a_items: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
        """A 档（重点研究）默认自动跟踪：进入纳入、退出移出；manual 行不受影响。

        重新进入 A 档的自动行会以最新价重新起算；被人工移出（note=manual）
        的行即使仍在 A 档也保持移出状态。
        """
        now_dt = now or datetime.now().astimezone()
        db_path = str(Path(get_runtime_root()) / "research.db")
        keep: set[str] = set()
        added = anchored = 0
        for item in a_items:
            code = str(item.get("stock_code") or "").strip().upper()
            if not code:
                continue
            keep.add(code)
            existing = self.store.get(code)
            entry_date = str(item.get("entered_at") or "")[:10]
            if existing and existing.get("status") == "active" and existing.get("source") == "auto"                     and existing.get("added_date") == entry_date:
                continue  # 已锚定到当前进入日，无需处理
            if existing and existing.get("status") == "removed" and existing.get("removed_note") == "manual":
                continue  # 人工移出的尊重人工决定
            price = self._entry_anchor(code, entry_date, db_path=db_path)
            if price is not None:
                anchored += 1
            else:
                price, _as_of = self._latest_price(code)
            self.store.add(
                market="CN", stock_code=code,
                company_name=str(item.get("company_name") or code),
                source="auto", tier="A",
                reasons=[str(r) for r in (item.get("focus_reasons") or [])],
                price=price, note="", added_date=entry_date or None, now=now_dt,
            )
            added += 1
        removed = self.store.auto_remove_leavers(keep, note="已退出重点研究", now=now_dt)
        return {"added": added, "removed": removed, "kept": len(keep), "anchored": anchored}

    @staticmethod
    def _focus_tier_map() -> dict[str, str]:
        """今日 Focus 分档映射（A/B/C）；读取失败按空处理，不阻断清单。"""
        try:
            from src.focus_selection import get_focus_selection_service

            selection = get_focus_selection_service().get_focus_selection()
        except Exception:
            return {}
        tiers: dict[str, str] = {}
        for tier_key, label in (("A", "重点研究"), ("B", "继续观察"), ("C", "暂缓优先")):
            for item in selection.get(tier_key) or []:
                tiers[str(item.get("stock_code") or "").upper()] = label
        return tiers


_service: CompanyTrackingService | None = None


def get_company_tracking_service() -> CompanyTrackingService:
    global _service
    if _service is None:
        _service = CompanyTrackingService()
    return _service
