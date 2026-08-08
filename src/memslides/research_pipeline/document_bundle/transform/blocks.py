"""Convert every readable MinerU content-list field into queryable blocks."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memslides.research_pipeline.document_bundle.errors import DocumentBundleError
from memslides.research_pipeline.document_bundle.models import ConversionIssue, PDFPageMetadata


FIGURE_CAPTION = re.compile(r"^\s*图\s*\d+\s*[:：]")
SOURCE_NOTE = re.compile(r"^\s*资料来源\s*[:：]")


@dataclass(slots=True)
class BlockBuildResult:
    blocks: list[dict[str, Any]]
    item_links: dict[int, dict[str, Any]]
    raw_block_count: int
    issues: list[ConversionIssue]


def load_content_list(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentBundleError("raw/content_list.json is not valid UTF-8 JSON") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise DocumentBundleError("raw/content_list.json must be a JSON array of objects")
    return payload


def _actual_bbox(
    raw_bbox: object, page: PDFPageMetadata
) -> tuple[list[float] | None, str | None]:
    if (
        not isinstance(raw_bbox, list)
        or len(raw_bbox) != 4
        or any(not isinstance(value, (int, float)) for value in raw_bbox)
    ):
        return None, "missing_or_invalid_content_list_bbox"
    x0, y0, x1, y1 = (float(value) for value in raw_bbox)
    converted = [
        round(x0 * page.width / 1000.0, 4),
        round(y0 * page.height / 1000.0, 4),
        round(x1 * page.width / 1000.0, 4),
        round(y1 * page.height / 1000.0, 4),
    ]
    if x0 < 0 or y0 < 0 or x1 > 1000 or y1 > 1000 or x0 >= x1 or y0 >= y1:
        return converted, "content_list_bbox_out_of_range"
    return converted, None


def _text_value(item: dict[str, Any]) -> str:
    value = item.get("text", item.get("content", ""))
    return value if isinstance(value, str) else ""


def _vector_figure_regions(
    content_items: list[dict[str, Any]],
) -> list[dict[str, int | float]]:
    regions: list[dict[str, int | float]] = []
    for caption_index, caption in enumerate(content_items):
        caption_bbox = caption.get("bbox")
        if (
            caption.get("type") != "text"
            or not FIGURE_CAPTION.match(_text_value(caption))
            or not isinstance(caption_bbox, list)
            or len(caption_bbox) != 4
        ):
            continue
        page_idx = caption.get("page_idx")
        if not isinstance(page_idx, int):
            continue
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for note_index, note in enumerate(content_items):
            note_bbox = note.get("bbox")
            if (
                note.get("page_idx") == page_idx
                and note.get("type") == "text"
                and SOURCE_NOTE.match(_text_value(note))
                and isinstance(note_bbox, list)
                and len(note_bbox) == 4
                and note_bbox[1] > caption_bbox[3]
            ):
                candidates.append((float(note_bbox[1]), note_index, note))
        if not candidates:
            continue
        _, note_index, note = min(candidates)
        note_bbox = note["bbox"]
        regions.append(
            {
                "page_idx": page_idx,
                "caption_index": caption_index,
                "note_index": note_index,
                "body_y0": float(caption_bbox[3]),
                "body_y1": float(note_bbox[1]),
            }
        )
    return regions


def _normalized_type(
    item: dict[str, Any], item_index: int, vector_regions: list[dict[str, int | float]]
) -> str:
    source_type = str(item.get("type") or "unknown")
    for region in vector_regions:
        if item_index == region["caption_index"]:
            return "image_caption"
        if item_index == region["note_index"]:
            return "image_footnote"
        bbox = item.get("bbox")
        if (
            item.get("page_idx") == region["page_idx"]
            and isinstance(bbox, list)
            and len(bbox) == 4
            and bbox[1] >= region["body_y0"]
            and bbox[3] <= region["body_y1"]
        ):
            return "figure_text"
    if source_type == "text":
        level = item.get("text_level")
        return "heading" if isinstance(level, int) and level > 0 else "paragraph"
    return {
        "title": "heading",
        "image": "figure",
        "chart": "figure",
        "table": "table",
        "equation": "formula",
        "interline_equation": "formula",
        "inline_equation": "formula",
    }.get(source_type, source_type)


def _values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str)]
    return []


def _primary_text(item: dict[str, Any]) -> str:
    source_type = item.get("type")
    if source_type == "table":
        value = item.get("table_body")
    elif source_type == "image":
        value = item.get("text", "")
    else:
        value = item.get("text", item.get("content", ""))
    return value if isinstance(value, str) else ""


def build_blocks(
    content_items: list[dict[str, Any]], pages: tuple[PDFPageMetadata, ...]
) -> BlockBuildResult:
    page_lookup = {page.number: page for page in pages}
    counters: defaultdict[int, int] = defaultdict(int)
    blocks: list[dict[str, Any]] = []
    item_links: dict[int, dict[str, Any]] = {}
    issues: list[ConversionIssue] = []
    vector_regions = _vector_figure_regions(content_items)

    def emit(
        *,
        item_index: int,
        page_number: int,
        block_type: str,
        text_raw: str,
        bbox: list[float] | None,
        source_type: str,
        source_field: str,
        source_field_index: int | None = None,
        text_level: object = None,
    ) -> str:
        counters[page_number] += 1
        block_id = f"p{page_number:03d}-b{counters[page_number]:03d}"
        block: dict[str, Any] = {
            "id": block_id,
            "page": page_number,
            "type": block_type,
            "text_raw": text_raw,
            "bbox": bbox,
            "parser_order": item_index,
            "reading_order": None,
            "section_id": None,
            "source_type": source_type,
            "source_content_index": item_index,
            "source_field": source_field,
        }
        if source_field_index is not None:
            block["source_field_index"] = source_field_index
        if isinstance(text_level, int):
            block["text_level"] = text_level
        blocks.append(block)
        return block_id

    raw_block_count = 0
    for item_index, item in enumerate(content_items):
        raw_page = item.get("page_idx")
        if not isinstance(raw_page, int) or raw_page + 1 not in page_lookup:
            raise DocumentBundleError(
                f"content_list item {item_index} has invalid page_idx={raw_page!r}"
            )
        page_number = raw_page + 1
        bbox, bbox_issue = _actual_bbox(item.get("bbox"), page_lookup[page_number])
        source_type = str(item.get("type") or "unknown")
        primary_id = emit(
            item_index=item_index,
            page_number=page_number,
            block_type=_normalized_type(item, item_index, vector_regions),
            text_raw=_primary_text(item),
            bbox=bbox,
            source_type=source_type,
            source_field="table_body" if source_type == "table" else "text",
            text_level=item.get("text_level"),
        )
        raw_block_count += 1
        if bbox_issue:
            issues.append(
                ConversionIssue(
                    "error", bbox_issue, f"Invalid bbox on content item {item_index}", primary_id
                )
            )
        links: dict[str, Any] = {"primary_block_id": primary_id, "caption_block_ids": [], "footnote_block_ids": []}

        caption_field = (
            "table_caption"
            if source_type == "table"
            else "image_caption"
            if source_type == "image"
            else "chart_caption"
            if source_type == "chart"
            else None
        )
        footnote_field = (
            "table_footnote"
            if source_type == "table"
            else "image_footnote"
            if source_type == "image"
            else "chart_footnote"
            if source_type == "chart"
            else None
        )
        if caption_field:
            for value_index, text in enumerate(_values(item.get(caption_field))):
                block_id = emit(
                    item_index=item_index,
                    page_number=page_number,
                    block_type=f"{source_type}_caption",
                    text_raw=text,
                    bbox=bbox,
                    source_type=source_type,
                    source_field=caption_field,
                    source_field_index=value_index,
                )
                links["caption_block_ids"].append(block_id)
                raw_block_count += 1
        if footnote_field:
            for value_index, text in enumerate(_values(item.get(footnote_field))):
                block_id = emit(
                    item_index=item_index,
                    page_number=page_number,
                    block_type=f"{source_type}_footnote",
                    text_raw=text,
                    bbox=bbox,
                    source_type=source_type,
                    source_field=footnote_field,
                    source_field_index=value_index,
                )
                links["footnote_block_ids"].append(block_id)
                raw_block_count += 1
        item_links[item_index] = links

    return BlockBuildResult(blocks, item_links, raw_block_count, issues)
