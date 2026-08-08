"""Numeric provenance audit for generated Visualization artifacts.

The audit is a sidecar contract.  It does not add fact identifiers or other
internal extraction fields to the public Visualization Schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ExtractionProposal, NumericFact
from .metric_grouping import serialize_metric_group
from .numeric_facts import NumericFactLedger, parse_table_number, table_grid


class NumericAuditError(ValueError):
    """Raised when a rendered value cannot be reconciled to its source fact."""


@dataclass(frozen=True, slots=True)
class FactBinding:
    """Bind one fact to its deterministic location in Visualization JSON."""

    fact_id: str
    output_path: tuple[str | int, ...]


def chart_fact_bindings(
    proposal: ExtractionProposal,
) -> tuple[FactBinding, ...]:
    return tuple(
        FactBinding(
            fact_id=fact_id,
            output_path=("series", series_index, "values", point_index),
        )
        for series_index, series in enumerate(proposal.series)
        for point_index, fact_id in enumerate(series.fact_ids)
    )


def table_fact_bindings(
    table: Mapping[str, Any],
    ledger: NumericFactLedger,
    *,
    max_rows: int = 8,
    max_columns: int = 6,
) -> tuple[FactBinding, ...]:
    table_id = str(table.get("id") or "")
    columns, rows = table_grid(table)
    selected_column_count = min(len(columns), max_columns)
    bindings: list[FactBinding] = []
    for row_index, row in enumerate(rows[:max_rows]):
        for column_index, cell in enumerate(row[:selected_column_count]):
            if column_index == 0 or parse_table_number(cell) is None:
                continue
            fact = ledger.table_cell(table_id, row_index, column_index)
            if fact is None:
                raise NumericAuditError(
                    f"numeric table cell has no fact binding: "
                    f"{table_id}[{row_index},{column_index}]"
                )
            bindings.append(
                FactBinding(
                    fact_id=fact.fact_id,
                    output_path=("rows", row_index, column_index),
                )
            )
    return tuple(bindings)


def serialize_numeric_fact(fact: NumericFact) -> dict[str, Any]:
    locator: dict[str, int]
    if fact.source_kind == "block":
        locator = {"start": int(fact.start), "end": int(fact.end)}
    else:
        locator = {
            "row_index": int(fact.row_index),
            "column_index": int(fact.column_index),
        }
    return {
        "fact_id": fact.fact_id,
        "source": {"kind": fact.source_kind, "id": fact.source_id},
        "raw_value": fact.raw_value,
        "normalized_value": str(fact.normalized_value),
        "unit": fact.unit,
        "label": fact.label,
        "period": fact.period,
        "entity": {
            "id": fact.entity_id,
            "name": fact.entity_name,
            "type": fact.entity_type,
        },
        "metric_key": fact.metric_key,
        "metric_label": fact.metric_label,
        "measure_kind": fact.measure_kind,
        "unit_family": fact.unit_family,
        "unit_scale": fact.unit_scale,
        "currency": fact.currency,
        "scope": {"kind": fact.scope, "label": fact.scope_label},
        "scenario": fact.scenario,
        "source_locator": locator,
    }


def serialize_numeric_fact_ledger(
    ledger: NumericFactLedger,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "fact_count": len(ledger.facts),
        "facts": [serialize_numeric_fact(fact) for fact in ledger.facts],
    }


def serialize_metric_group_catalog(artifacts: Iterable[Any]) -> dict[str, Any]:
    groups = [
        serialize_metric_group(artifact.metric_group)
        for artifact in artifacts
        if getattr(artifact, "metric_group", None) is not None
    ]
    return {
        "schema_version": "1.0.0",
        "group_count": len(groups),
        "groups": groups,
    }


def _resolve_path(
    value: Mapping[str, Any],
    path: Sequence[str | int],
) -> Any:
    current: Any = value
    for part in path:
        try:
            current = current[part]
        except (KeyError, IndexError, TypeError) as exc:
            display = ".".join(str(item) for item in path)
            raise NumericAuditError(
                f"Visualization output path does not exist: {display}"
            ) from exc
    return current


def _output_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise NumericAuditError(f"output value is not numeric: {value!r}")
    if isinstance(value, (int, float, Decimal)):
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise NumericAuditError(
                f"output value is not numeric: {value!r}"
            ) from exc
        if not result.is_finite():
            raise NumericAuditError(f"output value is not finite: {value!r}")
        return result
    parsed = parse_table_number(value)
    if parsed is None:
        raise NumericAuditError(f"output value is not numeric: {value!r}")
    return parsed[1]


def audit_visualization_artifacts(
    artifacts: Iterable[Any],
    ledger: NumericFactLedger,
) -> dict[str, Any]:
    """Prove every bound output value equals its registered Decimal fact."""

    visualization_results: list[dict[str, Any]] = []
    audited_values = 0
    for artifact in artifacts:
        bindings = tuple(getattr(artifact, "fact_bindings", ()))
        data = artifact.data
        table_rows = data.get("rows", [])
        table_has_numeric_values = (
            "columns" in data
            and isinstance(table_rows, list)
            and any(
                parse_table_number(cell) is not None
                for row in table_rows
                if isinstance(row, list)
                for cell in row[1:]
            )
        )
        is_numeric_visual = "chart_type" in data or table_has_numeric_values
        if is_numeric_visual and not bindings:
            raise NumericAuditError(
                f"Visualization {artifact.visualization_id} has no fact bindings"
            )
        entries: list[dict[str, Any]] = []
        for binding in bindings:
            fact = ledger.get(binding.fact_id)
            if fact is None:
                raise NumericAuditError(
                    f"unknown fact_id in audit binding: {binding.fact_id}"
                )
            output_value = _resolve_path(data, binding.output_path)
            output_decimal = _output_decimal(output_value)
            if output_decimal != fact.normalized_value:
                raise NumericAuditError(
                    f"numeric audit mismatch for {fact.fact_id}: "
                    f"expected {fact.normalized_value}, got {output_decimal}"
                )
            entries.append(
                {
                    **serialize_numeric_fact(fact),
                    "output_path": list(binding.output_path),
                    "output_value": output_value,
                    "status": "matched",
                }
            )
            audited_values += 1
        visualization_results.append(
            {
                "slide_id": artifact.slide_id,
                "visualization_id": artifact.visualization_id,
                "audited_value_count": len(entries),
                "status": "passed",
                "metric_group": (
                    serialize_metric_group(artifact.metric_group)
                    if getattr(artifact, "metric_group", None) is not None
                    else None
                ),
                "entries": entries,
            }
        )
    return {
        "schema_version": "1.0.0",
        "status": "passed",
        "summary": {
            "visualization_count": len(visualization_results),
            "audited_value_count": audited_values,
            "mismatch_count": 0,
        },
        "visualizations": visualization_results,
    }
