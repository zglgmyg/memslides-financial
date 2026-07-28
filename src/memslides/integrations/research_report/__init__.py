"""Bridge audited research-report artifacts into a MemSlides workspace."""

from .adapter import AdaptationResult, ResearchReportAdapterError, adapt_research_report

__all__ = ["AdaptationResult", "ResearchReportAdapterError", "adapt_research_report"]

