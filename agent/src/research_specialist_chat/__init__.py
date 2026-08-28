"""Read-only Feishu chat adapters for dedicated research specialists."""

from .service import (
    ROLE_SPECS,
    ResearchSpecialistChatService,
    SpecialistBrief,
    get_research_specialist_chat_service,
)

__all__ = [
    "ROLE_SPECS",
    "ResearchSpecialistChatService",
    "SpecialistBrief",
    "get_research_specialist_chat_service",
]
