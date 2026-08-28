"""Read service for Company Thesis version-change audit history."""

from __future__ import annotations

from pathlib import Path

from src.research_workspace.store import normalize_market, normalize_symbol

from .history_store import CompanyThesisHistoryRepository


class CompanyThesisHistoryService:
    def __init__(self, *, repository: CompanyThesisHistoryRepository | None = None,
                 db_path: Path | None = None) -> None:
        self.repository = repository or CompanyThesisHistoryRepository(db_path)

    def close(self) -> None:
        self.repository.close()

    def get_history_by_id(self, history_id: str):
        return self.repository.get_history_by_id(history_id)

    def list_history_for_company(self, market: str, stock_code: str):
        normalized_market = normalize_market(market)
        normalized_stock_code = normalize_symbol(normalized_market, stock_code)
        return self.repository.list_history_for_company(normalized_market, normalized_stock_code)

    def list_history_for_thesis(self, thesis_id: str):
        return self.repository.list_history_for_thesis(str(thesis_id or "").strip())


_service: CompanyThesisHistoryService | None = None


def get_company_thesis_history_service() -> CompanyThesisHistoryService:
    global _service
    if _service is None:
        _service = CompanyThesisHistoryService()
    return _service
