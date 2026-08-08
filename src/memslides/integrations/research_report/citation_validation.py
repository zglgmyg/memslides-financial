"""Validate Markdown citation IDs against PDF appendix sources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def _unique_cite_ids(citation_units: Sequence[Mapping[str, Any]]) -> list[str]:
    cite_ids: list[str] = []
    seen: set[str] = set()
    for unit in citation_units:
        for value in unit.get("cite_ids", []):
            cite_id = str(value)
            if cite_id and cite_id not in seen:
                seen.add(cite_id)
                cite_ids.append(cite_id)
    return cite_ids


def validate_citation_sources(
    citation_units: Sequence[Mapping[str, Any]],
    source_catalog: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Compare citation-unit IDs with the PDF source catalog."""

    markdown_cite_ids = _unique_cite_ids(citation_units)
    source_ids = set(source_catalog)
    markdown_ids = set(markdown_cite_ids)
    return {
        "verified": [cite_id for cite_id in markdown_cite_ids if cite_id in source_ids],
        "source_missing": [
            cite_id for cite_id in markdown_cite_ids if cite_id not in source_ids
        ],
        "unused_sources": [
            cite_id for cite_id in source_catalog if cite_id not in markdown_ids
        ],
    }


def write_citation_validation_report(
    citation_units_path: str | Path,
    source_catalog_path: str | Path,
    output_path: str | Path,
) -> Path:
    """Read citation artifacts and write their validation report."""

    units = json.loads(Path(citation_units_path).resolve().read_text(encoding="utf-8"))
    catalog = json.loads(
        Path(source_catalog_path).resolve().read_text(encoding="utf-8")
    )
    report = validate_citation_sources(units, catalog)
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination
