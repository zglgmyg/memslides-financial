"""Formal validation.json generation for DocumentBundle v0.1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memslides.research_pipeline.document_bundle.models import ConversionIssue, PDFMetadata


FROZEN_TOP_LEVEL_FIELDS = {
    "document",
    "pages",
    "blocks",
    "sections",
    "tables",
    "figures",
    "reading_order",
}
REQUIRED_RAW_FILES = {"layout.json", "content_list.json", "model.json", "document.md"}


def validate_document_bundle(
    document: dict[str, Any],
    bundle_directory: Path,
    pdf_metadata: PDFMetadata,
    raw_block_count: int,
    inherited_issues: list[ConversionIssue],
) -> dict[str, Any]:
    issues = [issue.as_dict() for issue in inherited_issues]

    def report(severity: str, code: str, message: str, **context: Any) -> None:
        issue = {"severity": severity, "code": code, "message": message}
        issue.update(context)
        issues.append(issue)

    if set(document) != FROZEN_TOP_LEVEL_FIELDS:
        report("error", "invalid_top_level_fields", "document.json top-level fields changed")

    declared_pages = document.get("document", {}).get("page_count")
    if declared_pages != pdf_metadata.page_count:
        report("error", "page_count_mismatch", "PDF and document page counts differ")

    blocks = document.get("blocks", [])
    block_ids = [block.get("id") for block in blocks]
    block_id_set = set(block_ids)
    duplicate_ids = sorted(
        {block_id for block_id in block_ids if block_ids.count(block_id) > 1 and block_id is not None}
    )
    if duplicate_ids:
        report("error", "duplicate_block_ids", "Duplicate block IDs detected")

    reading_order = document.get("reading_order", [])
    missing_block_ids = sorted(block_id_set - set(reading_order))
    unknown_reading_ids = sorted(set(reading_order) - block_id_set)
    if missing_block_ids or unknown_reading_ids or len(reading_order) != len(blocks):
        report("error", "invalid_reading_order", "Reading order does not cover blocks exactly once")

    page_lookup = {page["page"]: page for page in document.get("pages", [])}
    for block in blocks:
        page = page_lookup.get(block.get("page"))
        if page is None:
            report("error", "invalid_block_page", "Block references an unknown page", block_id=block.get("id"))
            continue
        bbox = block.get("bbox")
        valid_bbox = (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(value, (int, float)) for value in bbox)
            and 0 <= bbox[0] < bbox[2] <= page["width"]
            and 0 <= bbox[1] < bbox[3] <= page["height"]
        )
        if not valid_bbox:
            report("error", "invalid_bbox", "Block bbox is not valid PDF page coordinates", block_id=block.get("id"))

    for page in document.get("pages", []):
        expected_page_ids = {block["id"] for block in blocks if block.get("page") == page.get("page")}
        actual_page_ids = page.get("block_ids", [])
        if set(actual_page_ids) != expected_page_ids or len(actual_page_ids) != len(expected_page_ids):
            report("error", "invalid_page_reference", "Page block references are incomplete or invalid", page=page.get("page"))

    section_ids = {section.get("id") for section in document.get("sections", [])}
    for section in document.get("sections", []):
        references = [section.get("title_block_id"), *section.get("content_block_ids", [])]
        if any(reference not in block_id_set for reference in references):
            report("error", "invalid_section_reference", "Section references unknown blocks", section_id=section.get("id"))
        parent = section.get("parent_id")
        if parent is not None and parent not in section_ids:
            report("error", "invalid_section_parent", "Section parent does not exist", section_id=section.get("id"))
    for block in blocks:
        section_id = block.get("section_id")
        if section_id is not None and section_id not in section_ids:
            report("error", "invalid_block_section", "Block references an unknown section", block_id=block.get("id"))

    for table in document.get("tables", []):
        table_block_refs = [
            table.get("title_block_id"),
            *table.get("caption_block_ids", []),
            *table.get("footnote_block_ids", []),
            *table.get("continuation_block_ids", []),
        ]
        if any(reference is not None and reference not in block_id_set for reference in table_block_refs):
            report("error", "invalid_table_reference", "Table references unknown blocks", table_id=table.get("id"))
        for fragment in table.get("fragments", []):
            crop_path = fragment.get("crop_path")
            if not crop_path or not (bundle_directory / crop_path).is_file():
                report("error", "missing_table_asset", "Table crop is missing", table_id=table.get("id"))
        if table.get("status") == "image_only" and not table.get("fragments"):
            report("error", "image_only_without_crop", "Image-only table has no crop", table_id=table.get("id"))

    for figure in document.get("figures", []):
        figure_block_refs = [
            figure.get("caption_block_id"),
            *figure.get("caption_block_ids", []),
            *figure.get("footnote_block_ids", []),
        ]
        if any(reference is not None and reference not in block_id_set for reference in figure_block_refs):
            report("error", "invalid_figure_reference", "Figure references unknown blocks", figure_id=figure.get("id"))
        asset_path = figure.get("asset_path")
        if not asset_path or not (bundle_directory / asset_path).is_file():
            report("error", "missing_figure_asset", "Figure crop is missing", figure_id=figure.get("id"))

    raw_directory = bundle_directory / "raw"
    missing_raw = sorted(name for name in REQUIRED_RAW_FILES if not (raw_directory / name).is_file())
    if missing_raw:
        report("error", "missing_raw_artifacts", "Required raw artifacts are missing", files=missing_raw)

    error_count = sum(issue.get("severity") == "error" for issue in issues)
    warning_count = sum(issue.get("severity") == "warning" for issue in issues)
    status = "failed" if error_count else "needs_review" if warning_count else "passed"
    tables = document.get("tables", [])
    return {
        "status": status,
        "page_count": {"expected": pdf_metadata.page_count, "actual": declared_pages},
        "block_coverage": {
            "raw_block_count": raw_block_count,
            "structured_block_count": len(blocks),
            "missing_block_ids": missing_block_ids,
            "duplicate_block_ids": duplicate_ids,
        },
        "tables": {
            "detected": len(tables),
            "complete": sum(table.get("status") == "complete" for table in tables),
            "image_only": sum(table.get("status") == "image_only" for table in tables),
        },
        "figures": {"detected": len(document.get("figures", []))},
        "parser": {
            "name": "MinerU",
            "api_version": "v4",
            "model_version": "vlm",
            "fallback_used": False,
        },
        "issues": issues,
    }
