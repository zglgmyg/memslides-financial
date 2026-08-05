"""Resolve claim-to-unit mappings to numbered citation sources."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def build_pdf_source_numbers(source_catalog: Mapping[str, Any]) -> dict[str, int]:
    """Number citation IDs in their PDF appendix order."""

    return {
        str(cite_id): number
        for number, cite_id in enumerate(source_catalog, start=1)
    }


def resolve_page_citation_sources(
    claim_mappings: Sequence[Mapping[str, Any]],
    citation_units: Sequence[Mapping[str, Any]],
    source_catalog: Mapping[str, Mapping[str, Any]],
    source_numbers: Mapping[str, int],
) -> dict[str, list[dict[str, Any]]]:
    """Resolve one page's mappings with PDF appendix source numbers."""

    units_by_id = {str(unit["unit_id"]): unit for unit in citation_units}
    claim_references: list[dict[str, Any]] = []

    for mapping in claim_mappings:
        reference_numbers: list[int] = []
        for unit_id in mapping.get("citation_unit_ids", []):
            for cite_id_value in units_by_id[str(unit_id)].get("cite_ids", []):
                cite_id = str(cite_id_value)
                if cite_id not in source_catalog:
                    continue
                number = source_numbers[cite_id]
                if number not in reference_numbers:
                    reference_numbers.append(number)

        reference_numbers.sort()

        claim_references.append(
            {
                "html_claim_id": str(mapping["html_claim_id"]),
                "reference_numbers": reference_numbers,
            }
        )

    return {"claim_references": claim_references}
