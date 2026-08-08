"""Generate deterministic, order-preserving chunks without semantic ranking."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .models import DocumentIntelligenceSnapshot, IntelligenceChunk


def _block_payload(block: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "id",
        "page",
        "type",
        "text_raw",
        "bbox",
        "line_start",
        "line_end",
        "section_id",
        "reading_order",
        "text_level",
    )
    return {key: block[key] for key in allowed if key in block}


def _related_payload(
    snapshot: DocumentIntelligenceSnapshot,
    block_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...], tuple[str, ...]]:
    table_ids = tuple(
        dict.fromkeys(
            table_id
            for block_id in block_ids
            for table_id in snapshot.block_table_ids.get(block_id, ())
        )
    )
    figure_ids = tuple(
        dict.fromkeys(
            figure_id
            for block_id in block_ids
            for figure_id in snapshot.block_figure_ids.get(block_id, ())
        )
    )
    tables = [dict(snapshot.tables_by_id[value]) for value in table_ids]
    figures = [dict(snapshot.figures_by_id[value]) for value in figure_ids]
    return tables, figures, table_ids, figure_ids


def _make_chunk(
    snapshot: DocumentIntelligenceSnapshot,
    ordinal: int,
    section_id: str | None,
    block_ids: list[str],
) -> IntelligenceChunk:
    tables, figures, table_ids, figure_ids = _related_payload(snapshot, block_ids)
    section = dict(snapshot.sections_by_id[section_id]) if section_id in snapshot.sections_by_id else None
    section_path = snapshot.section_paths.get(section_id, ()) if section_id else ()
    chunk_id = f"chunk-{ordinal:04d}"
    payload = {
        "chunk_id": chunk_id,
        "section": section,
        "section_path": list(section_path),
        "blocks": [_block_payload(snapshot.blocks_by_id[value]) for value in block_ids],
        "tables": tables,
        "figures": figures,
        "allowed_evidence_refs": [
            {"kind": "block", "id": value} for value in block_ids
        ]
        + [{"kind": "table", "id": value} for value in table_ids]
        + [{"kind": "figure", "id": value} for value in figure_ids],
    }
    return IntelligenceChunk(
        id=chunk_id,
        ordinal=ordinal,
        section_id=section_id,
        section_path=tuple(section_path),
        block_ids=tuple(block_ids),
        table_ids=table_ids,
        figure_ids=figure_ids,
        payload=payload,
    )


def generate_chunks(
    snapshot: DocumentIntelligenceSnapshot,
    max_chars: int,
) -> list[IntelligenceChunk]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    chunks: list[IntelligenceChunk] = []
    current_ids: list[str] = []
    current_section: str | None = None
    used = 0

    def flush() -> None:
        nonlocal current_ids, used
        if current_ids:
            chunks.append(_make_chunk(snapshot, len(chunks) + 1, current_section, current_ids))
            current_ids = []
            used = 0

    for block_id in snapshot.ordered_block_ids:
        block = snapshot.blocks_by_id[block_id]
        section_id = str(block["section_id"]) if block.get("section_id") is not None else None
        encoded = json.dumps(_block_payload(block), ensure_ascii=False, separators=(",", ":"))
        if current_ids and (section_id != current_section or used + len(encoded) > max_chars):
            flush()
        if not current_ids:
            current_section = section_id
        current_ids.append(block_id)
        used += len(encoded)
    flush()

    covered = [block_id for chunk in chunks for block_id in chunk.block_ids]
    if tuple(covered) != snapshot.ordered_block_ids:
        raise AssertionError("Chunk generation changed or omitted DocumentBundle reading order")
    return chunks
