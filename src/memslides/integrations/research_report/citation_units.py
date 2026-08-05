"""Build citation units from a parsed research-report JSON file."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from memslides.research_pipeline.document_parser.parse_report import clean_inline_text


_CITATION_RE = re.compile(r"\[\^cite_id:([^\]]+)\]")
_CITATION_GROUP_RE = re.compile(r"(?:\[\^cite_id:[^\]]+\])+")
_SENTENCE_BOUNDARIES = "。！？!?"
_POST_CITATION_SEPARATORS = "。！？；，、：.!?;,)）】]"


def _unit_start(raw_text: str, cursor: int, citation_start: int, citation_end: int) -> int:
    preceding = raw_text[cursor:citation_start]
    sentence_boundary = max(
        (preceding.rfind(character) for character in _SENTENCE_BOUNDARIES),
        default=-1,
    )
    start = cursor + sentence_boundary + 1

    if raw_text[citation_end : citation_end + 1] in {"）", ")"}:
        parenthetical = raw_text[start:citation_start]
        opening = max(parenthetical.rfind("（"), parenthetical.rfind("("))
        closing = max(parenthetical.rfind("）"), parenthetical.rfind(")"))
        if opening > closing:
            start += opening + 1
    return start


def _next_cursor(raw_text: str, citation_end: int) -> int:
    cursor = citation_end
    while cursor < len(raw_text) and (
        raw_text[cursor].isspace()
        or raw_text[cursor] in _POST_CITATION_SEPARATORS
    ):
        cursor += 1
    if raw_text[cursor : cursor + 2] in {"**", "__"}:
        cursor += 2
    return cursor


def _unit_text(raw_text: str, start: int, citation_start: int, citation_end: int) -> str:
    punctuation_end = citation_end
    while (
        punctuation_end < len(raw_text)
        and raw_text[punctuation_end] in _POST_CITATION_SEPARATORS
    ):
        punctuation_end += 1
    text = clean_inline_text(
        raw_text[start:citation_start] + raw_text[citation_end:punctuation_end]
    )
    text = re.sub(r"^(?:\*{1,2}|_{1,2})+\s*", "", text)
    return re.sub(r"\s*(?:\*{1,2}|_{1,2})+$", "", text)


def build_citation_units(
    parsed_document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return citation units extracted from parsed-document blocks."""

    units: list[dict[str, Any]] = []
    for block in parsed_document.get("blocks", []):
        block_id = str(block.get("block_id", ""))
        raw_text = str(block.get("raw_text", ""))
        cursor = 0
        unit_number = 0

        for citation_group in _CITATION_GROUP_RE.finditer(raw_text):
            start = _unit_start(
                raw_text,
                cursor,
                citation_group.start(),
                citation_group.end(),
            )
            text = _unit_text(
                raw_text,
                start,
                citation_group.start(),
                citation_group.end(),
            )
            cite_ids = _CITATION_RE.findall(citation_group.group(0))
            if text and cite_ids:
                unit_number += 1
                units.append(
                    {
                        "unit_id": f"{block_id}_cu_{unit_number:03d}",
                        "block_id": block_id,
                        "text": text,
                        "cite_ids": cite_ids,
                    }
                )
            cursor = _next_cursor(raw_text, citation_group.end())

    return units


def write_citation_units(
    parsed_json_path: str | Path,
    output_path: str | Path,
) -> Path:
    """Read parsed JSON and write its citation units."""

    parsed_path = Path(parsed_json_path).resolve()
    destination = Path(output_path).resolve()
    parsed_document = json.loads(parsed_path.read_text(encoding="utf-8"))
    units = build_citation_units(parsed_document)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(units, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination
