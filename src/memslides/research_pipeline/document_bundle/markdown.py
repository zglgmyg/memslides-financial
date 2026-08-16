"""Build the canonical DocumentBundle shape from Markdown/plain text."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from memslides.research_pipeline.document_bundle.errors import DocumentBundleError
from memslides.research_pipeline.document_parser.parse_report import parse_file


_SUPPORTED_IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


def _materialize_pdf_figure(
    *,
    chart_id: str,
    matched: dict[str, Any],
    bundle_directory: Path,
    pdf_bundle_directory: Path,
) -> tuple[str, dict[str, Any]]:
    pdf_figure_id = str(matched.get("id"))
    relative = Path(str(matched.get("asset_path") or ""))
    source = (pdf_bundle_directory / relative).resolve()
    pdf_root = pdf_bundle_directory.resolve()
    if (
        not relative.as_posix()
        or relative.is_absolute()
        or pdf_root not in source.parents
        or not source.is_file()
    ):
        raise DocumentBundleError(
            f"Matched PDF figure has no usable asset: {pdf_figure_id}"
        )
    destination = (
        bundle_directory
        / "assets"
        / "figures"
        / f"{chart_id}{source.suffix.casefold()}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination.relative_to(bundle_directory).as_posix(), matched


def _paired_pdf_figures(
    parsed_blocks: list[dict[str, Any]],
    pdf_document: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    chart_blocks = [
        block
        for block in parsed_blocks
        if block.get("type") == "image"
        and str(block.get("url") or "").startswith("chart:")
    ]
    if pdf_document is None:
        return {}
    pdf_figures = sorted(
        pdf_document.get("figures", []),
        key=lambda figure: (
            int(figure.get("page") or 0),
            int(figure.get("source_content_index") or 0),
            str(figure.get("id") or ""),
        ),
    )
    if len(chart_blocks) != len(pdf_figures):
        raise DocumentBundleError(
            "Markdown chart count does not match PDF figure count: "
            f"{len(chart_blocks)} != {len(pdf_figures)}"
        )

    pairs: dict[str, dict[str, Any]] = {}
    chart_ids: set[str] = set()
    for chart_block, pdf_figure in zip(chart_blocks, pdf_figures):
        reference = str(chart_block.get("url") or "")
        chart_id = reference.removeprefix("chart:").strip()
        if not chart_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in chart_id
        ):
            raise DocumentBundleError(f"Invalid Markdown chart ID: {chart_id!r}")
        if chart_id in chart_ids:
            raise DocumentBundleError(f"Duplicate Markdown chart ID: {chart_id}")
        chart_ids.add(chart_id)
        pairs[str(chart_block["block_id"])] = pdf_figure
    return pairs


def _materialize_markdown_image(
    source_path: Path,
    bundle_directory: Path,
    reference: object,
    figure_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Copy a safe local Markdown image into the canonical bundle.

    Remote URLs are retained as unavailable references without network access.
    Absolute paths and parent traversal are rejected as input errors.
    """

    raw_reference = str(reference or "").strip()
    context = {"figure_id": figure_id, "source_reference": raw_reference}
    if not raw_reference:
        return None, {
            "severity": "warning",
            "code": "empty_markdown_image_reference",
            "message": "Markdown image has no source reference",
            **context,
        }
    parsed = urlsplit(raw_reference)
    if parsed.scheme.casefold() in {"http", "https"}:
        return None, {
            "severity": "warning",
            "code": "remote_markdown_image_unavailable",
            "message": "Remote Markdown images are not downloaded by default",
            **context,
        }
    if parsed.scheme or parsed.netloc:
        return None, {
            "severity": "warning",
            "code": "unsupported_markdown_image_reference",
            "message": (
                "Markdown image reference uses a non-file scheme and cannot "
                "be materialized"
            ),
            **context,
        }
    relative = Path(unquote(parsed.path))
    if relative.is_absolute() or ".." in relative.parts:
        return None, {
            "severity": "error",
            "code": "unsafe_markdown_image_path",
            "message": "Markdown image path must stay inside the source directory",
            **context,
        }
    source_root = source_path.parent.resolve()
    resolved = (source_root / relative).resolve()
    if resolved != source_root and source_root not in resolved.parents:
        return None, {
            "severity": "error",
            "code": "unsafe_markdown_image_path",
            "message": "Markdown image path escapes the source directory",
            **context,
        }
    if not resolved.is_file():
        return None, {
            "severity": "warning",
            "code": "missing_markdown_image",
            "message": "Markdown image file does not exist",
            **context,
        }
    suffix = resolved.suffix.casefold()
    if suffix not in _SUPPORTED_IMAGE_SUFFIXES:
        return None, {
            "severity": "warning",
            "code": "unsupported_markdown_image_format",
            "message": "Markdown image format is not renderer-supported",
            **context,
        }
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    destination = (
        bundle_directory
        / "assets"
        / "figures"
        / f"{figure_id}-{digest[:12]}{suffix}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolved, destination)
    return destination.relative_to(bundle_directory).as_posix(), None


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
        temporary = Path(output.name)
    temporary.replace(path)


def _html_table(columns: list[Any], rows: list[list[Any]]) -> str:
    from html import escape

    values = [columns, *rows]
    return "<table>" + "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in values
    ) + "</table>"


def build_from_markdown(
    source_path: Path,
    bundle_directory: Path,
    data_id: str | None = None,
    *,
    source_format: str = "auto",
    pdf_bundle_directory: Path | None = None,
    pdf_document: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a line-located DocumentBundle without inventing PDF coordinates."""

    parsed = parse_file(source_path, source_format=source_format)
    paired_figures = (
        _paired_pdf_figures(parsed["blocks"], pdf_document)
        if pdf_bundle_directory is not None
        else {}
    )
    bundle_directory.mkdir(parents=True, exist_ok=True)
    (bundle_directory / "assets" / "figures").mkdir(parents=True, exist_ok=True)
    (bundle_directory / "assets" / "tables").mkdir(parents=True, exist_ok=True)
    raw_directory = bundle_directory / "raw"
    raw_directory.mkdir(parents=True, exist_ok=True)
    raw_bytes = source_path.read_bytes()
    (raw_directory / "document.md").write_bytes(raw_bytes)

    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    image_issues: list[dict[str, Any]] = []
    section_ids: dict[tuple[str, ...], str] = {}
    sections: list[dict[str, Any]] = []

    for index, source in enumerate(parsed["blocks"]):
        path = source.get("section_path", [])
        key = tuple(str(item.get("heading_block_id")) for item in path)
        section_id: str | None = None
        if key:
            for depth in range(1, len(key) + 1):
                partial = key[:depth]
                if partial not in section_ids:
                    new_id = f"sec-{len(section_ids) + 1:03d}"
                    section_ids[partial] = new_id
                    sections.append(
                        {
                            "id": new_id,
                            "level": depth,
                            "title_block_id": partial[-1],
                            "parent_id": section_ids.get(partial[:-1]),
                            "child_section_ids": [],
                            "content_block_ids": [],
                        }
                    )
                    if depth > 1:
                        parent = next(item for item in sections if item["id"] == section_ids[partial[:-1]])
                        parent["child_section_ids"].append(new_id)
            section_id = section_ids[key]

        block = {
            "id": source["block_id"],
            "page": 1,
            "type": source["type"],
            "text_raw": source.get("raw_text", ""),
            "line_start": source["line_start"],
            "line_end": source["line_end"],
            "bbox": None,
            "parser_order": index,
            "reading_order": index,
            "section_id": section_id,
            "source_type": "markdown",
        }
        if source["type"] == "heading":
            block["text_raw"] = source.get("text", block["text_raw"])
            block["text_level"] = source.get("level", 1)
        blocks.append(block)
        if section_id:
            next(item for item in sections if item["id"] == section_id)["content_block_ids"].append(block["id"])

        if source["type"] == "table":
            table_id = f"table-{len(tables) + 1:03d}"
            tables.append(
                {
                    "id": table_id,
                    "title_block_id": None,
                    "caption_block_ids": [],
                    "footnote_block_ids": [],
                    "section_id": section_id,
                    "fragments": [],
                    "structure_raw": {
                        "format": "grid",
                        "columns": source.get("columns", []),
                        "rows": source.get("rows", []),
                        "content": _html_table(source.get("columns", []), source.get("rows", [])),
                    },
                    "status": "complete",
                    "issues": [],
                    "continuation_block_ids": [],
                    "source_block_id": block["id"],
                }
            )
            block["table_id"] = table_id
        elif source["type"] == "image":
            figure_id = f"fig-{len(figures) + 1:03d}"
            raw_reference = str(source.get("url") or "")
            matched_pdf_figure: dict[str, Any] | None = None
            if (
                raw_reference.startswith("chart:")
                and pdf_bundle_directory is not None
                and pdf_document is not None
            ):
                chart_id = raw_reference.removeprefix("chart:").strip()
                asset_path, matched_pdf_figure = _materialize_pdf_figure(
                    chart_id=chart_id,
                    matched=paired_figures[str(source["block_id"])],
                    bundle_directory=bundle_directory,
                    pdf_bundle_directory=pdf_bundle_directory,
                )
                image_issue = None
            else:
                asset_path, image_issue = _materialize_markdown_image(
                    source_path,
                    bundle_directory,
                    source.get("url"),
                    figure_id,
                )
            if image_issue is not None:
                image_issues.append(image_issue)
            figures.append(
                {
                    "id": figure_id,
                    "page": 1,
                    "section_id": section_id,
                    "caption_block_id": None,
                    "caption_block_ids": [],
                    "footnote_block_ids": [],
                    "bbox": None,
                    "asset_path": asset_path,
                    "asset_available": asset_path is not None,
                    "alt_text": source.get("alt_text"),
                    "source_reference": source.get("url"),
                    "markdown_chart_id": (
                        raw_reference.removeprefix("chart:").strip()
                        if matched_pdf_figure
                        else None
                    ),
                    "source": "paired_pdf" if matched_pdf_figure else "markdown_reference",
                    "source_block_id": block["id"],
                    "pdf_page": matched_pdf_figure.get("page") if matched_pdf_figure else None,
                    "pdf_bbox": matched_pdf_figure.get("bbox") if matched_pdf_figure else None,
                    "pdf_figure_id": matched_pdf_figure.get("id") if matched_pdf_figure else None,
                    "issues": [],
                }
            )

    identity = data_id or parsed["document"]["document_id"]
    document = {
        "document": {
            "id": identity,
            "title": parsed["document"].get("title"),
            "page_count": 1,
            "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "source_file": source_path.name,
            "source_format": parsed["document"]["source_format"],
            "location_model": "line_range",
        },
        "pages": [{"id": "p001", "page": 1, "width": None, "height": None, "block_ids": [b["id"] for b in blocks]}],
        "blocks": blocks,
        "sections": sections,
        "tables": tables,
        "figures": figures,
        "reading_order": [block["id"] for block in blocks],
    }
    error_count = sum(issue["severity"] == "error" for issue in image_issues)
    warning_count = sum(issue["severity"] == "warning" for issue in image_issues)
    validation = {
        "status": "failed" if error_count else "needs_review" if warning_count else "passed",
        "page_count": {"expected": 1, "actual": 1},
        "block_coverage": {
            "raw_block_count": len(blocks),
            "structured_block_count": len(blocks),
            "missing_block_ids": [],
            "duplicate_block_ids": [],
        },
        "tables": {"detected": len(tables), "complete": len(tables), "image_only": 0},
        "figures": {
            "detected": len(figures),
            "available": sum(figure.get("asset_available") is True for figure in figures),
            "unavailable": sum(figure.get("asset_available") is not True for figure in figures),
        },
        "parser": {"name": "parse_report", "api_version": None, "model_version": None, "fallback_used": False},
        "issues": image_issues,
    }
    _write_json_atomic(bundle_directory / "document.json", document)
    _write_json_atomic(bundle_directory / "validation.json", validation)
    return document, validation
