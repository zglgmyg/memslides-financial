"""Read-only PDF fingerprint and geometry extraction."""

from __future__ import annotations

import hashlib
from pathlib import Path

import fitz

from memslides.research_pipeline.document_bundle.models import PDFMetadata, PDFPageMetadata


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pdf_metadata(path: Path) -> PDFMetadata:
    if not path.is_file():
        raise FileNotFoundError(path)
    fingerprint = sha256_file(path)
    with fitz.open(path) as document:
        pages = tuple(
            PDFPageMetadata(
                number=index + 1,
                width=float(page.rect.width),
                height=float(page.rect.height),
            )
            for index, page in enumerate(document)
        )
        raw_title = document.metadata.get("title") if document.metadata else None
    embedded_title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else None
    return PDFMetadata(path, fingerprint, len(pages), pages, embedded_title)
