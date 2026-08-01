"""DocumentBundle v0.1 orchestration and deterministic serialization."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from memslides.research_pipeline.document_bundle.errors import RawArtifactError
from memslides.research_pipeline.document_bundle.parser.base import Parser
from memslides.research_pipeline.document_bundle.pdf_metadata import read_pdf_metadata
from memslides.research_pipeline.document_bundle.transform.assets import build_assets
from memslides.research_pipeline.document_bundle.transform.blocks import build_blocks, load_content_list
from memslides.research_pipeline.document_bundle.transform.reading_order import assign_reading_order
from memslides.research_pipeline.document_bundle.transform.sections import build_sections
from memslides.research_pipeline.document_bundle.validation import REQUIRED_RAW_FILES, validate_document_bundle


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
        temporary = Path(output.name)
    temporary.replace(path)


def _ensure_bundle_directories(bundle_directory: Path) -> None:
    (bundle_directory / "assets" / "figures").mkdir(parents=True, exist_ok=True)
    (bundle_directory / "assets" / "tables").mkdir(parents=True, exist_ok=True)
    (bundle_directory / "raw").mkdir(parents=True, exist_ok=True)


def _copy_existing_raw(source: Path, target: Path) -> None:
    missing = sorted(name for name in REQUIRED_RAW_FILES if not (source / name).is_file())
    if missing:
        raise RawArtifactError(f"Missing required raw artifacts: {missing}")
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(REQUIRED_RAW_FILES):
        if source.resolve() != target.resolve():
            shutil.copyfile(source / name, target / name)
        if (target / name).stat().st_size == 0:
            raise RawArtifactError(f"Required raw artifact is empty: {name}")
    for name in ("layout.json", "content_list.json", "model.json"):
        try:
            json.loads((target / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RawArtifactError(f"Required raw artifact is not valid UTF-8 JSON: {name}") from exc


def _source_title(content_items: list[dict[str, Any]], embedded_title: str | None, fallback: str) -> str:
    if embedded_title:
        return embedded_title
    for item in content_items:
        if item.get("type") == "title" and isinstance(item.get("text"), str) and item["text"]:
            return item["text"]
    cover_headings = [
        item
        for item in content_items
        if item.get("page_idx") == 0
        and isinstance(item.get("text_level"), int)
        and item["text_level"] > 0
        and isinstance(item.get("text"), str)
        and item["text"]
        and isinstance(item.get("bbox"), list)
        and len(item["bbox"]) == 4
        and item["bbox"][1] < 200
    ]
    if cover_headings:
        # The lower cover heading is the report title; the upper heading is
        # typically the company/report-type label. Both remain queryable blocks.
        return max(cover_headings, key=lambda item: item["bbox"][1])["text"]
    return fallback


def build_from_raw(
    pdf_path: Path,
    source_raw_directory: Path,
    bundle_directory: Path,
    data_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _ensure_bundle_directories(bundle_directory)
    raw_directory = bundle_directory / "raw"
    _copy_existing_raw(source_raw_directory, raw_directory)
    metadata = read_pdf_metadata(pdf_path)
    content_items = load_content_list(raw_directory / "content_list.json")
    block_result = build_blocks(content_items, metadata.pages)
    reading_order = assign_reading_order(block_result.blocks)
    sections = build_sections(block_result.blocks)

    pages: list[dict[str, Any]] = []
    for page in metadata.pages:
        pages.append(
            {
                "id": f"p{page.number:03d}",
                "page": page.number,
                "width": round(page.width, 4),
                "height": round(page.height, 4),
                "block_ids": [
                    block["id"]
                    for block in sorted(
                        block_result.blocks, key=lambda value: value["reading_order"]
                    )
                    if block["page"] == page.number
                ],
            }
        )

    tables, figures, asset_issues = build_assets(
        pdf_path,
        bundle_directory,
        content_items,
        block_result.blocks,
        block_result.item_links,
        metadata.pages,
    )
    document: dict[str, Any] = {
        "document": {
            "id": data_id,
            "title": _source_title(content_items, metadata.embedded_title, pdf_path.stem),
            "page_count": metadata.page_count,
            "source_sha256": metadata.source_sha256,
            "source_file": pdf_path.name,
            "source_format": "pdf",
        },
        "pages": pages,
        "blocks": block_result.blocks,
        "sections": sections,
        "tables": tables,
        "figures": figures,
        "reading_order": reading_order,
    }
    validation = validate_document_bundle(
        document,
        bundle_directory,
        metadata,
        block_result.raw_block_count,
        [*block_result.issues, *asset_issues],
    )
    _write_json_atomic(bundle_directory / "document.json", document)
    _write_json_atomic(bundle_directory / "validation.json", validation)
    return document, validation


def parse_pdf(
    pdf_path: Path,
    output_root: Path,
    data_id: str,
    parser: Parser,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    bundle_directory = output_root / pdf_path.stem / "document_bundle"
    _ensure_bundle_directories(bundle_directory)
    parser.parse_to_raw(pdf_path, bundle_directory / "raw", data_id)
    document, validation = build_from_raw(
        pdf_path, bundle_directory / "raw", bundle_directory, data_id
    )
    return bundle_directory, document, validation
