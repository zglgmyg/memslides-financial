"""Programmatic query interface over the frozen DocumentBundle output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DocumentBundleQuery:
    def __init__(self, bundle_directory: Path) -> None:
        self.bundle_directory = bundle_directory
        self.document: dict[str, Any] = json.loads(
            (bundle_directory / "document.json").read_text(encoding="utf-8")
        )
        self.validation: dict[str, Any] = json.loads(
            (bundle_directory / "validation.json").read_text(encoding="utf-8")
        )
        self._blocks = {block["id"]: block for block in self.document["blocks"]}
        self._sections = {section["id"]: section for section in self.document["sections"]}

    def block(self, block_id: str) -> dict[str, Any]:
        return self._blocks[block_id]

    def page_blocks(self, page: int) -> list[dict[str, Any]]:
        return [block for block in self.document["blocks"] if block["page"] == page]

    def section_blocks(self, section_id: str) -> list[dict[str, Any]]:
        return [self._blocks[block_id] for block_id in self._sections[section_id]["content_block_ids"]]

    def ordered_blocks(self) -> list[dict[str, Any]]:
        return [self._blocks[block_id] for block_id in self.document["reading_order"]]

    def paragraphs(self) -> list[dict[str, Any]]:
        return [block for block in self.document["blocks"] if block["type"] == "paragraph"]

    def tables(self) -> list[dict[str, Any]]:
        return list(self.document["tables"])

    def figures(self) -> list[dict[str, Any]]:
        return list(self.document["figures"])

    def source_location(self, block_id: str) -> dict[str, Any]:
        block = self.block(block_id)
        return {"page": block["page"], "bbox": block["bbox"]}

    def unresolved(self) -> list[dict[str, Any]]:
        return list(self.validation.get("issues", []))
