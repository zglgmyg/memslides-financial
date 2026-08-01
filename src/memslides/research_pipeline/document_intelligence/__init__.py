"""Deterministic structural intelligence over a canonical DocumentBundle."""

from .chunking import generate_chunks
from .figures import build_figure_inventory
from .index import build_snapshot
from .loader import load_document_intelligence
from .models import DocumentIntelligenceSnapshot, EvidenceRef, IntelligenceChunk

__all__ = [
    "DocumentIntelligenceSnapshot",
    "EvidenceRef",
    "IntelligenceChunk",
    "build_snapshot",
    "build_figure_inventory",
    "generate_chunks",
    "load_document_intelligence",
]
