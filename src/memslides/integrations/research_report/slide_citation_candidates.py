"""Limit citation-unit candidates to each slide's block evidence."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def build_slide_citation_candidates(
    slide_outline: Mapping[str, Any],
    citation_units: Sequence[Mapping[str, Any]],
    validation_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return eligible citation-unit IDs for every slide."""

    verified_cite_ids = {str(value) for value in validation_report.get("verified", [])}
    units_by_block: dict[str, list[str]] = {}
    for unit in citation_units:
        if not verified_cite_ids.intersection(
            str(value) for value in unit.get("cite_ids", [])
        ):
            continue
        block_id = str(unit.get("block_id", ""))
        units_by_block.setdefault(block_id, []).append(str(unit.get("unit_id", "")))

    result: list[dict[str, Any]] = []
    for slide in slide_outline.get("slides", []):
        candidate_unit_ids: list[str] = []
        seen: set[str] = set()
        for evidence_ref in slide.get("evidence_refs", []):
            if evidence_ref.get("kind") != "block":
                continue
            for unit_id in units_by_block.get(str(evidence_ref.get("id", "")), []):
                if unit_id not in seen:
                    seen.add(unit_id)
                    candidate_unit_ids.append(unit_id)
        result.append(
            {
                "slide_id": str(slide.get("slide_id", "")),
                "candidate_unit_ids": candidate_unit_ids,
            }
        )
    return result
