from app.tools import academic, catalog, finance, lms, protocols
from app.tools.base import Tool

TOOL_NAMES: tuple[str, ...] = (
    "get_academic_profile", "list_course_results", "get_curriculum_progress",
    "get_financial_summary", "list_invoices", "list_agreements",
    "list_protocols", "get_protocol", "draft_protocol",
    "get_lms_activity", "get_catalog",
)  # fmt: skip


def build_tools() -> tuple[Tool, ...]:
    return academic.TOOLS + finance.TOOLS + protocols.TOOLS + lms.TOOLS + catalog.TOOLS
