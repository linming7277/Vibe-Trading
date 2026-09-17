"""老板的跟踪清单：研究结论的人工批准与事后对账（闭环 MVP）。"""

from .service import CompanyTrackingService, get_company_tracking_service

__all__ = ["CompanyTrackingService", "get_company_tracking_service"]
