"""Deterministic Visualization generation from Document Intelligence.

No LLM output is trusted for values, table cells, figure IDs, or asset paths.
Every emitted value is extracted from a validated DocumentBundle snapshot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from lxml import html

from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot
from .audit import (
    FactBinding,
    chart_fact_bindings,
    table_fact_bindings,
)
from .contracts import ExtractionProposal, MetricGroup
from .extraction import (
    LLMExtractionAdapter,
    map_extraction_proposal,
    proposal_from_table,
)
from .numeric_facts import build_numeric_fact_ledger
from .metric_grouping import MetricGroupError, build_metric_group
from .planning import VisualizationPlan
from .verification import (
    VisualizationVerificationError,
    assemble_verified_chart,
    assemble_verified_table,
)


_CITATION_RE = re.compile(r"\[\^[^\]]+\]")
_MARKDOWN_RE = re.compile(r"[*_`~]+")
_PERIOD_RE = re.compile(r"((?:19|20)\d{2})(?:年|年末|[AE])?")
_UNITS = ("亿元", "百万元", "万元", "%", "倍", "颗")


@dataclass(frozen=True, slots=True)
class GenerationIssue:
    slide_id: str
    visualization_id: str
    visual_type: str
    reason: str

    def format(self) -> str:
        return (
            "[visualization-warning]\n"
            f"slide_id={self.slide_id}\n"
            f"required_visual={self.visual_type}\n"
            f"visualization_id={self.visualization_id}\n"
            f"reason={self.reason}"
        )


@dataclass(frozen=True, slots=True)
class VisualizationArtifact:
    slide_id: str
    visualization_id: str
    sources: tuple[dict[str, str], ...]
    data: dict[str, Any]
    fact_bindings: tuple[FactBinding, ...] = ()
    metric_group: MetricGroup | None = None

    @property
    def candidate_id(self) -> str:  # backward-compatible public attribute
        return self.visualization_id


def _clean(value: object) -> str:
    return _MARKDOWN_RE.sub("", _CITATION_RE.sub("", str(value or ""))).strip()


def _number(value: object) -> float | None:
    text = _clean(value).replace(",", "").replace("~", "")
    for unit in _UNITS:
        text = text.replace(unit, "")
    match = re.search(r"[+\-]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _table_grid(table: Mapping[str, Any]) -> tuple[list[str], list[list[str]]]:
    if table.get("status") != "complete":
        return [], []
    structure = table.get("structure_raw")
    if not isinstance(structure, Mapping):
        return [], []
    if structure.get("format") == "grid":
        return (
            [_clean(value) for value in structure.get("columns", [])],
            [[_clean(cell) for cell in row] for row in structure.get("rows", []) if isinstance(row, list)],
        )
    if structure.get("format") != "html":
        return [], []
    try:
        root = html.fromstring(str(structure.get("content") or ""))
    except (TypeError, ValueError):
        return [], []
    values = [
        [" ".join(cell.text_content().split()) for cell in row.xpath("./th|./td")]
        for row in root.xpath(".//tr")
    ]
    values = [row for row in values if row]
    return (values[0], values[1:]) if values else ([], [])


def _terms(value: str) -> set[str]:
    result = {token.casefold() for token in re.findall(r"[A-Za-z0-9]+", value) if len(token) >= 2}
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        result.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return result


def _table_text(table: Mapping[str, Any], snapshot: DocumentIntelligenceSnapshot) -> str:
    columns, rows = _table_grid(table)
    caption = " ".join(
        str(snapshot.blocks_by_id.get(str(identity), {}).get("text_raw") or "")
        for identity in table.get("caption_block_ids", [])
    )
    return " ".join([caption, *columns, *(str(cell) for row in rows for cell in row)])


def _score(query: str, value: str) -> int:
    return len(_terms(query) & _terms(value))


def _candidate_tables(
    plan: VisualizationPlan,
    snapshot: DocumentIntelligenceSnapshot,
    *,
    allow_section_fallback: bool = True,
) -> list[Mapping[str, Any]]:
    explicit: list[Mapping[str, Any]] = []
    section_ids: set[str] = set()
    for kind, identity in plan.evidence_refs:
        evidence = snapshot.evidence(kind, identity)
        if evidence and evidence.section_id:
            section_ids.add(evidence.section_id)
        if kind == "table" and identity in snapshot.tables_by_id:
            explicit.append(snapshot.tables_by_id[identity])
        elif kind == "block":
            explicit.extend(
                snapshot.tables_by_id[table_id]
                for table_id in snapshot.block_table_ids.get(identity, ())
                if table_id in snapshot.tables_by_id
            )
    if explicit or not allow_section_fallback:
        return list({str(item.get("id")): item for item in explicit}.values())
    query = plan.purpose + " " + " ".join(plan.data_requirement.values())
    values = [
        table for table in snapshot.tables_by_id.values()
        if not section_ids or str(table.get("section_id") or "") in section_ids
    ]
    ranked = sorted(
        values,
        key=lambda item: _score(query, _table_text(item, snapshot)),
        reverse=True,
    )
    if not plan.evidence_refs:
        return ranked
    # LLM candidates commonly cite the supporting paragraph rather than the
    # native table. Reconcile only within that paragraph's section and only
    # when the semantic winner is strong and unambiguous. The emitted
    # Visualization still records the selected native table ID.
    if not ranked:
        return []
    scores = [_score(query, _table_text(item, snapshot)) for item in ranked]
    minimum_score = 1 if len(ranked) == 1 else 2
    if scores[0] < minimum_score:
        return []
    if len(scores) > 1 and scores[0] - scores[1] < 2:
        return []
    return [ranked[0]]


def _candidate_blocks(
    plan: VisualizationPlan, snapshot: DocumentIntelligenceSnapshot
) -> list[Mapping[str, Any]]:
    explicit = [
        snapshot.blocks_by_id[identity]
        for kind, identity in plan.evidence_refs
        if kind == "block" and identity in snapshot.blocks_by_id
    ]
    if explicit:
        return explicit
    section_ids = {
        evidence.section_id
        for kind, identity in plan.evidence_refs
        if (evidence := snapshot.evidence(kind, identity)) is not None and evidence.section_id
    }
    query = plan.purpose + " " + " ".join(plan.data_requirement.values())
    blocks = [
        block for block in snapshot.blocks_by_id.values()
        if str(block.get("type")) in {"paragraph", "blockquote"}
        and (not section_ids or str(block.get("section_id") or "") in section_ids)
    ]
    return sorted(blocks, key=lambda item: _score(query, str(item.get("text_raw") or "")), reverse=True)


def _native_sources(*refs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"kind": kind, "id": identity} for kind, identity in dict.fromkeys(refs)]


def _chart_type(plan: VisualizationPlan, categories: Sequence[str]) -> str:
    temporal = sum(bool(_PERIOD_RE.search(value)) for value in categories) >= 2
    if temporal:
        return "line"
    if plan.chart_intent == "composition":
        return "pie"
    return "bar" if plan.chart_intent == "comparison" else "column"


def _chart_from_table(
    plan: VisualizationPlan,
    table: Mapping[str, Any],
) -> dict[str, Any] | None:
    columns, rows = _table_grid(table)
    period_indices = [index for index, value in enumerate(columns) if _PERIOD_RE.search(value)]
    categories: list[str] = []
    series: list[dict[str, Any]] = []
    query = plan.purpose + " " + " ".join(plan.data_requirement.values())
    if len(period_indices) >= 2:
        period_indices = period_indices[:8]
        categories = [columns[index] for index in period_indices]
        ranked = sorted(rows, key=lambda row: _score(query, str(row[0]) if row else ""), reverse=True)
        for row in ranked:
            if not row:
                continue
            values = [_number(row[index]) if index < len(row) else None for index in period_indices]
            if sum(value is not None for value in values) >= 2:
                series.append({"name": _clean(row[0]) or "Series", "values": values})
            if len(series) == 3:
                break
    elif len(columns) >= 2:
        usable = [row for row in rows[:8] if row]
        categories = [_clean(row[0]) for row in usable]
        for column_index, column in enumerate(columns[1:4], start=1):
            values = [_number(row[column_index]) if column_index < len(row) else None for row in usable]
            if sum(value is not None for value in values) >= 2:
                series.append({"name": column or "Value", "values": values})
    if not categories or not series:
        return None
    identity = str(table.get("id"))
    return {
        "chart_type": _chart_type(plan, categories),
        "title": plan.purpose or "Data chart",
        "unit": next((unit for unit in _UNITS if unit in _table_text_for_unit(columns, rows, plan)), ""),
        "categories": categories,
        "series": series,
        "source_refs": list(plan.source_refs),
        "sources": _native_sources(("table", identity)),
        "note": f"Extracted from DocumentBundle table {identity}",
    }


def _table_text_for_unit(columns: Sequence[str], rows: Sequence[Sequence[str]], plan: VisualizationPlan) -> str:
    return " ".join([*columns, *(str(cell) for row in rows for cell in row), plan.purpose])


def _paragraph_pairs(text: str) -> tuple[list[str], list[float], str]:
    clean = _clean(text)
    units = "|".join(re.escape(unit) for unit in _UNITS)
    pairs = re.findall(
        rf"((?:19|20)\d{{2}})(?:年|年末|[AE])?[^,，。；]{{0,24}}?([+\-]?\d+(?:\.\d+)?)\s*({units})",
        clean,
    )
    if len(pairs) < 2:
        return [], [], ""
    unit = pairs[0][2]
    unique = list(dict.fromkeys((year, float(value)) for year, value, item_unit in pairs if item_unit == unit))[:8]
    return [year for year, _ in unique], [value for _, value in unique], unit


def _image(
    plan: VisualizationPlan,
    snapshot: DocumentIntelligenceSnapshot,
    kind: str,
    identity: str,
    asset_path: object,
) -> dict[str, Any] | None:
    relative = Path(str(asset_path or ""))
    if not str(asset_path or "") or relative.is_absolute() or ".." in relative.parts:
        return None
    resolved = (snapshot.bundle_directory / relative).resolve()
    bundle = snapshot.bundle_directory.resolve()
    if bundle not in resolved.parents or not resolved.is_file():
        return None
    return {
        "type": "image",
        "title": plan.purpose or "Source figure",
        "source": {"kind": kind, "id": identity},
        "asset_path": relative.as_posix(),
        "source_refs": list(plan.source_refs),
        "sources": _native_sources((kind, identity)),
    }


def generate_from_plans(
    plans: Sequence[VisualizationPlan],
    snapshot: DocumentIntelligenceSnapshot,
    schema: Mapping[str, Any],
    *,
    llm_adapter: LLMExtractionAdapter | None = None,
) -> tuple[list[VisualizationArtifact], list[GenerationIssue]]:
    artifacts: list[VisualizationArtifact] = []
    issues: list[GenerationIssue] = []
    validator = Draft202012Validator(schema)
    ledger = build_numeric_fact_ledger(snapshot)
    for plan in plans:
        data: dict[str, Any] | None = None
        verification_failure: str | None = None
        proposal: ExtractionProposal | None = None
        metric_group: MetricGroup | None = None
        selected_table: Mapping[str, Any] | None = None
        if plan.visual_type == "image":
            figure_ref = next((ref for ref in plan.evidence_refs if ref[0] == "figure"), None)
            if figure_ref:
                figure = snapshot.figures_by_id[figure_ref[1]]
                data = _image(plan, snapshot, "figure", figure_ref[1], figure.get("asset_path"))
        elif plan.visual_type == "table":
            for table in _candidate_tables(plan, snapshot):
                identity = str(table.get("id"))
                if table.get("status") != "complete":
                    fragments = table.get("fragments", [])
                    if fragments:
                        data = _image(plan, snapshot, "table", identity, fragments[0].get("crop_path"))
                    if data:
                        break
                    continue
                columns, rows = _table_grid(table)
                if columns and rows:
                    try:
                        data = assemble_verified_table(plan, table, ledger, schema)
                        selected_table = table
                    except VisualizationVerificationError as exc:
                        verification_failure = str(exc)
                    break
        elif plan.visual_type == "chart":
            if plan.evidence_refs:
                proposal = map_extraction_proposal(
                    plan,
                    snapshot,
                    ledger,
                    llm_adapter=llm_adapter,
                )
                allowed_sources = list(plan.evidence_refs)
                for kind, identity in plan.evidence_refs:
                    if kind == "block":
                        allowed_sources.extend(
                            ("table", table_id)
                            for table_id in snapshot.block_table_ids.get(identity, ())
                        )
                if proposal is None:
                    for table in _candidate_tables(
                        plan,
                        snapshot,
                        allow_section_fallback=False,
                    ):
                        proposal = proposal_from_table(plan, table, ledger)
                        if proposal is not None:
                            allowed_sources.append(("table", str(table.get("id") or "")))
                            break
                if proposal is not None:
                    try:
                        metric_group = build_metric_group(plan, proposal, ledger)
                        data = assemble_verified_chart(
                            plan,
                            proposal,
                            ledger,
                            schema,
                            allowed_sources=tuple(dict.fromkeys(allowed_sources)),
                            metric_group=metric_group,
                        )
                    except (MetricGroupError, VisualizationVerificationError) as exc:
                        verification_failure = str(exc)
            else:
                verification_failure = (
                    "reject.missing_evidence_scope: published charts require "
                    "native evidence, NumericFact, and MetricGroup verification"
                )
        if data is None:
            reason = (
                f"verification_failed: {verification_failure}"
                if verification_failure
                else "no_traceable_source_data"
            )
            issues.append(
                GenerationIssue(
                    plan.slide_id,
                    plan.visualization_id,
                    plan.visual_type,
                    reason,
                )
            )
            continue
        errors = list(validator.iter_errors(data))
        if errors:
            raise ValueError(f"generated Visualization JSON is invalid: {errors[0].message}")
        sources = tuple(dict(item) for item in data.get("sources", []))
        fact_bindings: tuple[FactBinding, ...] = ()
        if "chart_type" in data and proposal is not None:
            fact_bindings = chart_fact_bindings(proposal)
        elif "columns" in data and selected_table is not None:
            fact_bindings = table_fact_bindings(
                selected_table,
                ledger,
            )
        artifacts.append(
            VisualizationArtifact(
                plan.slide_id,
                plan.visualization_id,
                sources,
                data,
                fact_bindings,
                metric_group,
            )
        )
    return artifacts, issues
