"""Canonical PDF/Markdown to DocumentBundle conversion layer."""

__version__ = "0.1.0"

from .bundle import build_from_raw, parse_pdf
from .markdown import build_from_markdown

__all__ = ["build_from_markdown", "build_from_raw", "parse_pdf"]
