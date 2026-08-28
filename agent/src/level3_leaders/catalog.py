"""TongDaXin research-industry hierarchy from the logged-in client cache.

The vendor exposes groups 16/17/18 as research-industry level 1, level 2 and
terminal ("level 3") industries.  The same authoritative hierarchy is stored
in ``T0002/hq_cache/tdxzs3.cfg`` and stock memberships in ``tdxhy.cfg``.
Reading those files keeps page requests offline and makes one stock belong to
exactly one terminal TDX industry before any finer business clustering runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tdx_data.store import TdxDataStore


MARKET_SUFFIX = {"0": ".SZ", "1": ".SH", "2": ".BJ"}


class TdxResearchIndustryCatalog:
    def __init__(self, tdx_home: str | Path) -> None:
        self.home = Path(tdx_home)
        self.index_file = self.home / "T0002" / "hq_cache" / "tdxzs3.cfg"
        self.membership_file = self.home / "T0002" / "hq_cache" / "tdxhy.cfg"
        self._nodes: list[dict[str, Any]] | None = None
        self._members: dict[str, list[str]] | None = None

    @staticmethod
    def _lines(path: Path) -> list[str]:
        if not path.is_file():
            raise RuntimeError(f"通达信研究行业文件不存在：{path}")
        return path.read_bytes().decode("gbk", errors="replace").splitlines()

    @property
    def data_as_of(self) -> str:
        timestamp = max(self.index_file.stat().st_mtime, self.membership_file.stat().st_mtime)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

    def nodes(self) -> list[dict[str, Any]]:
        if self._nodes is not None:
            return list(self._nodes)
        raw: list[dict[str, Any]] = []
        for line in self._lines(self.index_file):
            fields = line.split("|")
            if len(fields) < 6 or not fields[1].startswith("881") or not fields[5].startswith("X"):
                continue
            digits = len(fields[5]) - 1
            if digits not in {2, 4, 6}:
                continue
            raw.append({
                "industry_code": f"{fields[1]}.SH",
                "industry_name": fields[0].strip(),
                "tdx_class_code": fields[5],
                "level": digits // 2,
                "is_terminal": fields[4] == "1",
            })
        by_class = {row["tdx_class_code"]: row for row in raw}
        result: list[dict[str, Any]] = []
        for row in raw:
            class_code = row["tdx_class_code"]
            level1 = by_class.get(class_code[:3])
            level2 = by_class.get(class_code[:5]) if row["level"] >= 2 else None
            item = {
                **row,
                "level1_code": level1["industry_code"] if level1 else None,
                "level1_name": level1["industry_name"] if level1 else None,
                "level2_code": level2["industry_code"] if level2 else None,
                "level2_name": level2["industry_name"] if level2 else None,
                "raw_industry_level": (
                    "TDX_RESEARCH_LEVEL_3" if row["level"] == 3
                    else "TDX_RESEARCH_LEVEL_2_LEAF" if row["is_terminal"] and row["level"] == 2
                    else f"TDX_RESEARCH_LEVEL_{row['level']}"
                ),
                "as_of": self.data_as_of,
            }
            result.append(item)
        self._nodes = sorted(result, key=lambda item: item["industry_code"])
        return list(self._nodes)

    def terminal_industries(self) -> list[dict[str, Any]]:
        member_counts = {code: len(symbols) for code, symbols in self.memberships().items()}
        return [{
            **row,
            "industry_level": "TDX_RESEARCH_TERMINAL",
            "member_count": member_counts.get(row["industry_code"], 0),
        } for row in self.nodes() if row["is_terminal"]]

    def memberships(self) -> dict[str, list[str]]:
        if self._members is not None:
            return {key: list(value) for key, value in self._members.items()}
        industry_by_class = {
            row["tdx_class_code"]: row["industry_code"]
            for row in self.nodes() if row["is_terminal"]
        }
        result: dict[str, list[str]] = {code: [] for code in industry_by_class.values()}
        for line in self._lines(self.membership_file):
            fields = line.split("|")
            if len(fields) < 6 or fields[0] not in MARKET_SUFFIX or len(fields[1]) != 6:
                continue
            industry_code = industry_by_class.get(fields[5])
            if not industry_code:
                continue
            result[industry_code].append(f"{fields[1]}{MARKET_SUFFIX[fields[0]]}")
        self._members = {key: sorted(set(value)) for key, value in result.items()}
        return {key: list(value) for key, value in self._members.items()}

    def members(self, industry_code: str) -> list[str]:
        code = industry_code.strip().upper()
        return list(self.memberships().get(code, []))

    def sync_cache(self, store: TdxDataStore) -> dict[str, int]:
        """Atomically expose the hierarchy to the existing TDX SQLite cache."""
        nodes = self.nodes()
        terminal = self.terminal_industries()
        memberships = self.memberships()
        store.replace_dataset("research_industry_hierarchy", [
            {
                "key": row["industry_code"],
                "category": f"LEVEL_{row['level']}",
                "name": row["industry_name"],
                "payload": row,
            }
            for row in nodes
        ])
        member_rows = []
        terminal_by_code = {row["industry_code"]: row for row in terminal}
        for industry_code, symbols in memberships.items():
            industry = terminal_by_code[industry_code]
            for symbol in symbols:
                member_rows.append({
                    "key": f"{industry_code}:{symbol}",
                    "category": industry_code,
                    "name": symbol,
                    "payload": {
                        "industry_code": industry_code,
                        "industry_name": industry["industry_name"],
                        "stock_code": symbol,
                    },
                })
        store.replace_dataset("research_terminal_industry_members", member_rows)
        return {
            "levels": len(nodes),
            "level1": sum(row["level"] == 1 for row in nodes),
            "level2": sum(row["level"] == 2 for row in nodes),
            "level3": sum(row["level"] == 3 for row in nodes),
            "terminal": len(terminal),
            "memberships": len(member_rows),
        }


