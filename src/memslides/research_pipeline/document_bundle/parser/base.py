"""Parser protocol used by the bundle orchestration layer."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Parser(Protocol):
    """A strict parser that materializes the four required raw artifacts."""

    def parse_to_raw(self, pdf_path: Path, raw_directory: Path, data_id: str) -> None:
        """Parse *pdf_path* and save required artifacts under *raw_directory*."""
