"""Read-only runtime models for deterministic Document Intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: str
    id: str
    section_id: str | None
    page: int | None
    bbox: tuple[float, float, float, float] | None

    def prompt_ref(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}


@dataclass(frozen=True, slots=True)
class IntelligenceChunk:
    id: str
    ordinal: int
    section_id: str | None
    section_path: tuple[str, ...]
    block_ids: tuple[str, ...]
    table_ids: tuple[str, ...]
    figure_ids: tuple[str, ...]
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DocumentIntelligenceSnapshot:
    bundle_directory: Path
    document_json: Mapping[str, Any]
    metadata: Mapping[str, Any]
    blocks_by_id: Mapping[str, Mapping[str, Any]]
    sections_by_id: Mapping[str, Mapping[str, Any]]
    tables_by_id: Mapping[str, Mapping[str, Any]]
    figures_by_id: Mapping[str, Mapping[str, Any]]
    ordered_block_ids: tuple[str, ...]
    section_order: tuple[str, ...]
    section_paths: Mapping[str, tuple[str, ...]]
    evidence_by_key: Mapping[tuple[str, str], EvidenceRef]
    block_table_ids: Mapping[str, tuple[str, ...]]
    block_figure_ids: Mapping[str, tuple[str, ...]]

    def evidence(self, kind: str, evidence_id: str) -> EvidenceRef | None:
        return self.evidence_by_key.get((kind, evidence_id))

    @staticmethod
    def frozen(value: dict[Any, Any]) -> Mapping[Any, Any]:
        return MappingProxyType(value)
