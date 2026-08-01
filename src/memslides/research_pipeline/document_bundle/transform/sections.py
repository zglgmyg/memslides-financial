"""Build section hierarchy using only existing heading blocks."""

from __future__ import annotations

import re
from typing import Any


NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+")
STRUCTURAL_HEADINGS = {
    "目录",
    "插图目录",
    "表格目录",
    "分析师承诺",
    "评级说明",
    "免责声明",
}


def _heading_level(block: dict[str, Any]) -> int:
    match = NUMBERED_HEADING.match(block.get("text_raw", ""))
    if match:
        return match.group(1).count(".") + 1
    explicit = block.get("text_level")
    if isinstance(explicit, int) and explicit > 0:
        return 1
    return 1


def _base_section_id(block: dict[str, Any], ordinal: int) -> str:
    match = NUMBERED_HEADING.match(block.get("text_raw", ""))
    if match:
        return "sec-" + match.group(1).replace(".", "-")
    return f"sec-{ordinal:03d}"


def build_sections(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    heading_ordinal = 0
    cover_section_created = False

    for block in sorted(
        blocks,
        key=lambda value: value.get("reading_order")
        if isinstance(value.get("reading_order"), int)
        else value.get("parser_order", 0),
    ):
        text = block.get("text_raw", "")
        is_numbered = bool(NUMBERED_HEADING.match(text))
        is_structural = text.strip() in STRUCTURAL_HEADINGS
        is_cover_start = (
            block.get("page") == 1
            and block.get("type") == "heading"
            and not cover_section_created
        )
        is_section_heading = block.get("type") == "heading" and (
            is_numbered or is_structural or is_cover_start
        )
        if is_section_heading:
            heading_ordinal += 1
            if is_cover_start:
                cover_section_created = True
            level = _heading_level(block)
            while active and active[-1]["level"] >= level:
                active.pop()
            base_id = _base_section_id(block, heading_ordinal)
            section_id = base_id
            suffix = 2
            while section_id in used_ids:
                section_id = f"{base_id}-{suffix}"
                suffix += 1
            used_ids.add(section_id)
            section = {
                "id": section_id,
                "level": level,
                "title_block_id": block["id"],
                "parent_id": active[-1]["id"] if active else None,
                "child_section_ids": [],
                "content_block_ids": [],
            }
            if active:
                active[-1]["child_section_ids"].append(section_id)
            sections.append(section)
            active.append(section)

        if active:
            block["section_id"] = active[-1]["id"]
            for section in active:
                section["content_block_ids"].append(block["id"])

    return sections
