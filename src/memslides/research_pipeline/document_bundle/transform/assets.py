"""Render deterministic 300 DPI figure and table crops from PDF bboxes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from memslides.research_pipeline.document_bundle.models import ConversionIssue, PDFPageMetadata
from memslides.research_pipeline.document_bundle.transform.blocks import (
    FIGURE_CAPTION,
    SOURCE_NOTE,
    _text_value,
    _vector_figure_regions,
)


def _render_crop(
    document: fitz.Document,
    page_number: int,
    bbox: list[float] | None,
    target: Path,
) -> str | None:
    if bbox is None:
        return "missing_bbox_for_asset"
    page = document[page_number - 1]
    clip = fitz.Rect(bbox) & page.rect
    if clip.is_empty or clip.is_infinite or clip.width <= 0 or clip.height <= 0:
        return "invalid_bbox_for_asset"
    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), clip=clip, alpha=False)
    pixmap.save(target)
    return None


def _vector_asset_bbox(
    page: fitz.Page,
    caption_bbox: list[float],
    note_bbox: list[float],
    body_bboxes: list[list[float]],
) -> list[float] | None:
    drawing_rects = [
        drawing["rect"]
        for drawing in page.get_drawings()
        if drawing["rect"].y0 >= caption_bbox[3] - 2
        and drawing["rect"].y1 <= note_bbox[1] + 2
        and drawing["rect"].y1 > caption_bbox[3]
    ]
    if drawing_rects:
        union = drawing_rects[0]
        for rect in drawing_rects[1:]:
            union |= rect
        return [round(union.x0, 4), round(union.y0, 4), round(union.x1, 4), round(union.y1, 4)]
    if body_bboxes:
        return [
            min(bbox[0] for bbox in body_bboxes),
            min(bbox[1] for bbox in body_bboxes),
            max(bbox[2] for bbox in body_bboxes),
            max(bbox[3] for bbox in body_bboxes),
        ]
    return None


def _table_continuation(
    content_items: list[dict[str, Any]], table_index: int
) -> tuple[list[int], int] | None:
    table = content_items[table_index]
    table_bbox = table.get("bbox")
    page_idx = table.get("page_idx")
    if (
        not isinstance(page_idx, int)
        or not isinstance(table_bbox, list)
        or len(table_bbox) != 4
        or table_bbox[3] < 850
        or not table.get("table_caption")
    ):
        return None
    next_page = page_idx + 1
    notes: list[tuple[float, int]] = []
    for index, item in enumerate(content_items):
        bbox = item.get("bbox")
        if (
            item.get("page_idx") == next_page
            and item.get("type") == "text"
            and SOURCE_NOTE.match(_text_value(item))
            and isinstance(bbox, list)
            and len(bbox) == 4
            and bbox[1] <= 250
        ):
            notes.append((float(bbox[1]), index))
    if not notes:
        return None
    note_y, note_index = min(notes)
    candidates: list[int] = []
    for index, item in enumerate(content_items):
        bbox = item.get("bbox")
        text = _text_value(item)
        if (
            item.get("page_idx") == next_page
            and item.get("type") == "text"
            and text
            and isinstance(bbox, list)
            and len(bbox) == 4
            and bbox[1] < note_y
            and bbox[3] <= 250
            and bbox[0] >= table_bbox[0] - 100
            and bbox[2] <= table_bbox[2] + 100
        ):
            candidates.append(index)
    if len(candidates) < 2:
        return None
    return candidates, note_index


def build_assets(
    pdf_path: Path,
    bundle_directory: Path,
    content_items: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    item_links: dict[int, dict[str, Any]],
    pages: tuple[PDFPageMetadata, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ConversionIssue]]:
    block_lookup = {block["id"]: block for block in blocks}
    page_lookup = {page.number: page for page in pages}
    tables: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    issues: list[ConversionIssue] = []
    vector_regions = {
        int(region["caption_index"]): region
        for region in _vector_figure_regions(content_items)
    }

    with fitz.open(pdf_path) as document:
        for item_index, item in enumerate(content_items):
            source_type = item.get("type")
            vector_region = vector_regions.get(item_index)
            if source_type not in {"table", "image", "chart"} and vector_region is None:
                continue
            links = item_links[item_index]
            primary = block_lookup[links["primary_block_id"]]
            page_number = primary["page"]
            bbox = primary["bbox"]
            caption_ids = links["caption_block_ids"]
            footnote_ids = links["footnote_block_ids"]
            section_id = primary.get("section_id")

            if source_type == "table":
                table_id = f"table-{len(tables) + 1:03d}"
                relative_path = f"assets/tables/{table_id}-page-{page_number}.png"
                crop_issue = _render_crop(
                    document, page_number, bbox, bundle_directory / relative_path
                )
                table_body = item.get("table_body")
                has_structure = isinstance(table_body, str) and bool(table_body)
                table_issues: list[str] = []
                if crop_issue:
                    table_issues.append(crop_issue)
                    issues.append(
                        ConversionIssue(
                            "error",
                            crop_issue,
                            f"Could not render crop for {table_id}",
                            primary["id"],
                        )
                    )
                fragments = [
                    {
                        "page": page_number,
                        "bbox": bbox,
                        "crop_path": relative_path if crop_issue is None else None,
                    }
                ]
                continuation = _table_continuation(content_items, item_index)
                continuation_block_ids: list[str] = []
                if continuation:
                    candidate_indices, note_index = continuation
                    candidate_blocks = [
                        block_lookup[item_links[index]["primary_block_id"]]
                        for index in candidate_indices
                    ]
                    note_block = block_lookup[item_links[note_index]["primary_block_id"]]
                    continuation_block_ids = [block["id"] for block in candidate_blocks]
                    for block in candidate_blocks:
                        block["type"] = "table_continuation"
                    note_block["type"] = "table_footnote"
                    footnote_ids = [*footnote_ids, note_block["id"]]
                    next_page = page_number + 1
                    next_page_meta = page_lookup[next_page]
                    raw_bboxes = [content_items[index]["bbox"] for index in candidate_indices]
                    continuation_bbox = [
                        bbox[0],
                        round(max(0.0, min(raw[1] for raw in raw_bboxes) * next_page_meta.height / 1000.0 - 4), 4),
                        bbox[2],
                        round(min(next_page_meta.height, max(raw[3] for raw in raw_bboxes) * next_page_meta.height / 1000.0 + 4), 4),
                    ]
                    continuation_path = f"assets/tables/{table_id}-page-{next_page}.png"
                    continuation_issue = _render_crop(
                        document,
                        next_page,
                        continuation_bbox,
                        bundle_directory / continuation_path,
                    )
                    fragments.append(
                        {
                            "page": next_page,
                            "bbox": continuation_bbox,
                            "crop_path": continuation_path if continuation_issue is None else None,
                        }
                    )
                    table_issues.append("cross_page_structure_unavailable")
                    issues.append(
                        ConversionIssue(
                            "warning",
                            "cross_page_structure_unavailable",
                            f"{table_id} has verified continuation crops but no complete editable structure",
                            primary["id"],
                        )
                    )
                    if continuation_issue:
                        table_issues.append(continuation_issue)
                        issues.append(
                            ConversionIssue(
                                "error",
                                continuation_issue,
                                f"Could not render continuation crop for {table_id}",
                                primary["id"],
                            )
                        )

                table = {
                    "id": table_id,
                    "title_block_id": caption_ids[0] if caption_ids else None,
                    "caption_block_ids": caption_ids,
                    "footnote_block_ids": footnote_ids,
                    "section_id": section_id,
                    "fragments": fragments,
                    "structure_raw": (
                        {"format": "html", "content": table_body}
                        if has_structure and continuation is None
                        else None
                    ),
                    "status": "complete" if has_structure and continuation is None else "image_only",
                    "issues": table_issues,
                    "continuation_block_ids": continuation_block_ids,
                    "parser_asset_path": item.get("img_path"),
                    "source_content_index": item_index,
                }
                tables.append(table)
            else:
                source = "pdf_bbox_render"
                parser_asset_path = item.get("img_path")
                if vector_region is not None:
                    note_index = int(vector_region["note_index"])
                    note_block = block_lookup[item_links[note_index]["primary_block_id"]]
                    caption_ids = [primary["id"]]
                    footnote_ids = [note_block["id"]]
                    body_blocks = [
                        block
                        for block in blocks
                        if block.get("page") == page_number
                        and block.get("type") == "figure_text"
                        and block.get("bbox") is not None
                        and block["bbox"][1] >= primary["bbox"][3]
                        and block["bbox"][3] <= note_block["bbox"][1]
                    ]
                    bbox = _vector_asset_bbox(
                        document[page_number - 1],
                        primary["bbox"],
                        note_block["bbox"],
                        [block["bbox"] for block in body_blocks],
                    )
                    source = "pdf_vector_region_render"
                    parser_asset_path = None
                figure_id = f"fig-{len(figures) + 1:03d}"
                relative_path = f"assets/figures/{figure_id}.png"
                crop_issue = _render_crop(
                    document, page_number, bbox, bundle_directory / relative_path
                )
                if crop_issue:
                    issues.append(
                        ConversionIssue(
                            "error",
                            crop_issue,
                            f"Could not render crop for {figure_id}",
                            primary["id"],
                        )
                    )
                figures.append(
                    {
                        "id": figure_id,
                        "page": page_number,
                        "section_id": section_id,
                        "caption_block_id": caption_ids[0] if caption_ids else None,
                        "caption_block_ids": caption_ids,
                        "footnote_block_ids": footnote_ids,
                        "bbox": bbox,
                        "asset_path": relative_path if crop_issue is None else None,
                        "source": source,
                        "parser_asset_path": parser_asset_path,
                        "source_content_index": item_index,
                        "issues": [crop_issue] if crop_issue else [],
                    }
                )

    # Do not guess that adjacent fragments are one logical table. Report likely
    # continuations for explicit review instead.
    for previous, current in zip(tables, tables[1:]):
        previous_fragment = previous["fragments"][-1]
        current_fragment = current["fragments"][0]
        previous_page = previous_fragment["page"]
        current_page = current_fragment["page"]
        previous_bbox = previous_fragment["bbox"]
        current_bbox = current_fragment["bbox"]
        if (
            current_page == previous_page + 1
            and previous_bbox is not None
            and current_bbox is not None
            and previous_bbox[3] >= page_lookup[previous_page].height * 0.85
            and current_bbox[1] <= page_lookup[current_page].height * 0.2
            and current["title_block_id"] is None
        ):
            code = "possible_cross_page_table_unresolved"
            previous["issues"].append(code)
            current["issues"].append(code)
            issues.append(
                ConversionIssue(
                    "warning",
                    code,
                    f"Review whether {previous['id']} and {current['id']} are one logical table",
                )
            )

    return tables, figures, issues
