"""Fetch and index official CNINFO periodic-report source materials."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pypdfium2 as pdfium

from src.config.paths import get_runtime_root

from .store import DisclosureMaterialStore


CNINFO_BASE = "https://www.cninfo.com.cn"
CNINFO_SEARCH = f"{CNINFO_BASE}/new/information/topSearch/query"
CNINFO_REPORTS = f"{CNINFO_BASE}/new/hisAnnouncement/query"
PERIODIC_CATEGORIES = {
    "ANNUAL": "category_ndbg_szsh",
    "SEMIANNUAL": "category_bndbg_szsh",
    "Q3": "category_sjdbg_szsh",
    "Q1": "category_yjdbg_szsh",
}
MATERIAL_DEFINITIONS = {
    "ACCOUNTS_RECEIVABLE_AGEING": ("应收账款账龄", "按账龄", "账龄组合"),
    "RECEIVABLES_IMPAIRMENT": ("坏账准备", "预期信用损失", "信用减值损失"),
    "CUSTOMER_CONCENTRATION": ("前五名客户", "客户集中度", "客户销售额"),
    "BUSINESS_PRODUCT_STRUCTURE": ("分产品", "分行业", "分部信息", "主营业务分"),
    "PPP_COLLECTION": ("特许经营", "PPP", "可用性服务费", "政府付费"),
    "DEBT_MATURITY": ("债务到期", "一年内到期", "借款到期"),
    "GUARANTEES_CONTINGENCIES": ("对外担保", "担保余额", "或有事项", "未决诉讼"),
}
# CNINFO has used both “第一季度” and “一季度” (and the equivalent Q3
# forms) across publication years.  Treat them as the same report kind rather
# than silently dropping recent filings and selecting an older title that
# happens to use the long form.
REPORT_TITLE = re.compile(r"(?P<year>20\d{2})年(?P<kind>年度|半年度|第一季度|一季度|第三季度|三季度)报告")


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _as_date(value: int | float | str | None) -> str | None:
    try:
        milliseconds = int(value or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).date().isoformat() if milliseconds else None


def _report_metadata(title: str) -> tuple[str, str | None] | None:
    if "摘要" in title:
        return None
    match = REPORT_TITLE.search(title)
    if not match:
        return None
    suffix = {
        "年度": "12-31", "半年度": "06-30", "第一季度": "03-31", "一季度": "03-31",
        "第三季度": "09-30", "三季度": "09-30",
    }[match["kind"]]
    kind = {
        "年度": "ANNUAL", "半年度": "SEMIANNUAL", "第一季度": "Q1", "一季度": "Q1",
        "第三季度": "Q3", "三季度": "Q3",
    }[match["kind"]]
    return kind, f"{match['year']}-{suffix}"


class CninfoClient:
    """Small, direct official-disclosure client; no public proxy is used."""

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) hzstock-research/1.0",
        "Referer": f"{CNINFO_BASE}/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def _post(self, url: str, data: dict[str, str]) -> Any:
        # Official disclosure sites are intentionally contacted directly.  A
        # local proxy can alter or block the source and must not be treated as
        # an alternate data provider.
        with httpx.Client(timeout=self.timeout, trust_env=False, headers=self.headers) as client:
            response = client.post(url, data=data)
            response.raise_for_status()
            return response.json()

    def resolve_company(self, stock_code: str) -> dict[str, str]:
        rows = self._post(CNINFO_SEARCH, {"keyWord": stock_code.split(".")[0], "maxSecNum": "20", "maxListNum": "20"})
        code = stock_code.split(".")[0].upper()
        match = next((row for row in rows if str(row.get("code") or "").upper() == code), None)
        if not match or not match.get("orgId"):
            raise LookupError(f"cninfo_company_not_found:{stock_code}")
        return {"stock_code": code, "company_name": str(match.get("zwjc") or code), "org_id": str(match["orgId"])}

    def periodic_reports(self, company: dict[str, str], *, as_of: str | None = None) -> list[dict[str, Any]]:
        reports: dict[str, dict[str, Any]] = {}
        # CNINFO returns a different (and, for some categories, historical)
        # result set when exchange fields are blank.  These values mirror the
        # exchange selected by its own disclosure search form.
        org_id = str(company.get("org_id") or "").lower()
        plate = "sz" if org_id.startswith("gssz") else "sh" if org_id.startswith("gssh") else ""
        column = "szse" if plate == "sz" else "sse" if plate == "sh" else ""
        for category in PERIODIC_CATEGORIES.values():
            page = 1
            while True:
                payload = self._post(CNINFO_REPORTS, {
                    "pageNum": str(page), "pageSize": "30", "tabName": "fulltext",
                    "stock": f"{company['stock_code']},{company['org_id']}", "searchkey": "", "category": category,
                    "plate": plate, "column": column, "seDate": "", "sortName": "", "sortType": "", "isHLtitle": "true",
                })
                rows = payload.get("announcements") or []
                for raw in rows:
                    title = str(raw.get("announcementTitle") or "")
                    meta = _report_metadata(title)
                    announcement_date = _as_date(raw.get("announcementTime"))
                    if not meta or not announcement_date or (as_of and announcement_date > as_of):
                        continue
                    announcement_id = str(raw.get("announcementId") or "")
                    relative_url = str(raw.get("adjunctUrl") or "")
                    if not announcement_id or not relative_url.lower().endswith(".pdf"):
                        continue
                    reports[announcement_id] = {
                        "announcement_id": announcement_id, "title": title, "announcement_date": announcement_date,
                        "report_kind": meta[0], "report_period": meta[1],
                        "source_url": f"https://static.cninfo.com.cn/{relative_url.lstrip('/')}",
                    }
                if not payload.get("hasMore") or not rows:
                    break
                page += 1
        return sorted(reports.values(), key=lambda item: (item["announcement_date"], item["announcement_id"]), reverse=True)

    def download(self, source_url: str) -> bytes:
        with httpx.Client(timeout=self.timeout, trust_env=False, headers={"User-Agent": self.headers["User-Agent"]}) as client:
            response = client.get(source_url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not response.content.startswith(b"%PDF") and "pdf" not in content_type.lower():
                raise ValueError("cninfo_document_is_not_pdf")
            return response.content


class DisclosureMaterialService:
    def __init__(self, *, store: DisclosureMaterialStore | None = None, client: CninfoClient | None = None,
                 artifact_root: Path | None = None) -> None:
        self.store = store or DisclosureMaterialStore()
        self.client = client or CninfoClient()
        self.artifact_root = Path(artifact_root or (get_runtime_root() / "disclosures"))

    def close(self) -> None:
        self.store.close()

    @staticmethod
    def extract_materials(pages: list[str]) -> list[dict[str, Any]]:
        materials: list[dict[str, Any]] = []
        for material_type, keywords in MATERIAL_DEFINITIONS.items():
            excerpts: list[dict[str, Any]] = []
            for page_number, text in enumerate(pages, start=1):
                for keyword in keywords:
                    position = text.find(keyword)
                    if position < 0:
                        continue
                    start, end = max(0, position - 140), min(len(text), position + len(keyword) + 240)
                    excerpt = " ".join(text[start:end].split())
                    excerpts.append({"page": page_number, "keyword": keyword, "text": excerpt})
                    break
                if len(excerpts) >= 3:
                    break
            materials.append({
                "material_type": material_type,
                "status": "FOUND" if excerpts else "NOT_FOUND_IN_DOCUMENT",
                "keywords": list(keywords), "excerpts": excerpts,
            })
        return materials

    @staticmethod
    def _pdf_pages(path: Path) -> list[str]:
        document = pdfium.PdfDocument(str(path))
        pages: list[str] = []
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    pages.append(text_page.get_text_range() or "")
                finally:
                    page.close()
        finally:
            document.close()
        return pages

    def sync_periodic_reports(self, stock_code: str, *, as_of: str | None = None,
                              max_documents_per_kind: int = 2) -> dict[str, Any]:
        if max_documents_per_kind < 1 or max_documents_per_kind > 8:
            raise ValueError("max_documents_per_kind must be between 1 and 8")
        company = self.client.resolve_company(stock_code.upper())
        all_reports = self.client.periodic_reports(company, as_of=as_of)
        selected: list[dict[str, Any]] = []
        by_kind: dict[str, int] = {}
        for report in all_reports:
            kind = report["report_kind"]
            if by_kind.get(kind, 0) >= max_documents_per_kind:
                continue
            by_kind[kind] = by_kind.get(kind, 0) + 1
            selected.append(report)
        synced = reused = failed = 0
        failures: list[dict[str, str]] = []
        for report in selected:
            existing = self.store.get_document_by_announcement(report["announcement_id"])
            # A ready, hash-addressable local artifact is already the same
            # immutable official filing.  Do not download it again on each
            # daily preparation run.
            if existing and str(existing.get("extraction_status") or "") == "READY" and existing.get("text_sha256"):
                reused += 1
                continue
            try:
                pdf = self.client.download(report["source_url"])
                directory = self.artifact_root / company["stock_code"]
                directory.mkdir(parents=True, exist_ok=True)
                pdf_path = directory / f"{report['announcement_id']}.pdf"
                pdf_path.write_bytes(pdf)
                pages = self._pdf_pages(pdf_path)
                text = "\n\f\n".join(pages)
                text_path = directory / f"{report['announcement_id']}.txt"
                text_path.write_text(text, encoding="utf-8")
                document = self.store.save_document({
                    **company, **report, "pdf_path": str(pdf_path), "pdf_sha256": _sha256(pdf),
                    "text_path": str(text_path), "text_sha256": _sha256(text), "page_count": len(pages),
                    "extraction_status": "READY", "extraction_error": "",
                })
                self.store.save_materials(document["id"], company["stock_code"], self.extract_materials(pages))
                synced += 1
            except Exception as exc:
                failed += 1
                failures.append({"announcement_id": report["announcement_id"], "error": f"{type(exc).__name__}:{exc}"})
                self.store.save_document({
                    **company, **report, "pdf_path": None, "pdf_sha256": None, "text_path": None, "text_sha256": None,
                    "page_count": None, "extraction_status": "FAILED", "extraction_error": failures[-1]["error"],
                })
        return {
            "stock_code": company["stock_code"], "company_name": company["company_name"], "org_id": company["org_id"],
            "as_of": as_of, "available_reports": len(all_reports), "selected_reports": len(selected),
            "synced": synced, "reused": reused, "failed": failed, "failures": failures,
            "documents": self.store.list_documents(company["stock_code"], as_of=as_of),
            "materials": self.store.list_materials(company["stock_code"], as_of=as_of),
        }

    def get_materials(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        return {
            "stock_code": stock_code.upper(), "as_of": as_of,
            "documents": self.store.list_documents(stock_code, as_of=as_of),
            "materials": self.store.list_materials(stock_code, as_of=as_of),
        }


_service: DisclosureMaterialService | None = None


def get_disclosure_material_service() -> DisclosureMaterialService:
    global _service
    if _service is None:
        _service = DisclosureMaterialService()
    return _service
