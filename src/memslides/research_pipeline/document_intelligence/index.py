"""Build deterministic indexes and source-evidence relationships."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .models import DocumentIntelligenceSnapshot, EvidenceRef


def _mapping(values: list[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        identity = str(value.get("id") or "")
        if not identity:
            raise ValueError(f"{label} item is missing id")
        if identity in result:
            raise ValueError(f"Duplicate {label} id: {identity}")
        result[identity] = value
    return result


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(item, (int, float)) for item in value):
        return None
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def _section_paths(
    sections: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for section_id in sections:
        chain: list[str] = []
        current: str | None = section_id
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise ValueError(f"Section hierarchy contains a cycle at {current}")
            seen.add(current)
            section = sections.get(current)
            if section is None:
                raise ValueError(f"Unknown section in hierarchy: {current}")
            chain.append(current)
            parent = section.get("parent_id")
            current = str(parent) if parent is not None else None
        result[section_id] = tuple(reversed(chain))
    return result


def _relationship_block_ids(value: Mapping[str, Any]) -> set[str]:
    result = {
        str(item)
        for key in (
            "caption_block_ids",
            "footnote_block_ids",
            "continuation_block_ids",
        )
        for item in value.get(key, [])
        if item
    }
    for key in ("title_block_id", "caption_block_id", "source_block_id"):
        if value.get(key):
            result.add(str(value[key]))
    return result


def build_snapshot(
    document: Mapping[str, Any], bundle_directory: Path
) -> DocumentIntelligenceSnapshot:
    blocks = _mapping(list(document.get("blocks", [])), "block")
    sections = _mapping(list(document.get("sections", [])), "section")
    tables = _mapping(list(document.get("tables", [])), "table")
    figures = _mapping(list(document.get("figures", [])), "figure")
    ordered_ids = tuple(str(value) for value in document.get("reading_order", []))
    if len(ordered_ids) != len(blocks) or set(ordered_ids) != set(blocks):
        raise ValueError("DocumentBundle reading_order must cover every block exactly once")

    paths = _section_paths(sections)
    evidence: dict[tuple[str, str], EvidenceRef] = {}
    for block_id, block in blocks.items():
        evidence[("block", block_id)] = EvidenceRef(
            "block",
            block_id,
            str(block["section_id"]) if block.get("section_id") is not None else None,
            int(block["page"]) if isinstance(block.get("page"), int) else None,
            _bbox(block.get("bbox")),
        )

    block_tables: dict[str, list[str]] = {block_id: [] for block_id in blocks}
    block_figures: dict[str, list[str]] = {block_id: [] for block_id in blocks}
    blocks_by_source_index: dict[int, list[str]] = {}
    for block_id, block in blocks.items():
        source_index = block.get("source_content_index")
        if isinstance(source_index, int):
            blocks_by_source_index.setdefault(source_index, []).append(block_id)
    for kind, values, relationships in (
        ("table", tables, block_tables),
        ("figure", figures, block_figures),
    ):
        for identity, value in values.items():
            fragments = value.get("fragments", []) if kind == "table" else []
            page = value.get("page")
            bbox = value.get("bbox")
            if fragments:
                page = fragments[0].get("page")
                bbox = fragments[0].get("bbox")
            evidence[(kind, identity)] = EvidenceRef(
                kind,
                identity,
                str(value["section_id"]) if value.get("section_id") is not None else None,
                int(page) if isinstance(page, int) else None,
                _bbox(bbox),
            )
            for block_id in sorted(_relationship_block_ids(value)):
                if block_id in relationships:
                    relationships[block_id].append(identity)
            source_index = value.get("source_content_index")
            if isinstance(source_index, int):
                for block_id in blocks_by_source_index.get(source_index, []):
                    relationships[block_id].append(identity)
            for block_id, block in blocks.items():
                if block.get(f"{kind}_id") == identity:
                    relationships[block_id].append(identity)

    return DocumentIntelligenceSnapshot(
        bundle_directory=bundle_directory,
        document_json=MappingProxyType(dict(document)),
        metadata=MappingProxyType(dict(document.get("document", {}))),
        blocks_by_id=MappingProxyType(blocks),
        sections_by_id=MappingProxyType(sections),
        tables_by_id=MappingProxyType(tables),
        figures_by_id=MappingProxyType(figures),
        ordered_block_ids=ordered_ids,
        section_order=tuple(sections),
        section_paths=MappingProxyType(paths),
        evidence_by_key=MappingProxyType(evidence),
        block_table_ids=MappingProxyType(
            {key: tuple(dict.fromkeys(value)) for key, value in block_tables.items()}
        ),
        block_figure_ids=MappingProxyType(
            {key: tuple(dict.fromkeys(value)) for key, value in block_figures.items()}
        ),
    )
