"""Small internal models; serialized output remains plain frozen JSON."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PDFPageMetadata:
    number: int
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class PDFMetadata:
    path: Path
    source_sha256: str
    page_count: int
    pages: tuple[PDFPageMetadata, ...]
    embedded_title: str | None


@dataclass(frozen=True, slots=True)
class ConversionIssue:
    severity: str
    code: str
    message: str
    block_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.block_id is not None:
            result["block_id"] = self.block_id
        return result
