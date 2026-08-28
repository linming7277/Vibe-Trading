"""Official periodic-report materials for deterministic company research."""

from .service import DisclosureMaterialService, get_disclosure_material_service
from .store import DisclosureMaterialStore

__all__ = ["DisclosureMaterialService", "DisclosureMaterialStore", "get_disclosure_material_service"]
