"""Deterministic fact, source, unit, shape, and Schema verification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import ExtractionProposal, MetricGroup, NumericFact
from .metric_grouping import MetricGroupError, build_metric_group
from .numeric_facts import (
    NumericFactLedger,
    parse_table_number,
    period_labels,
    table_grid,
)
from .planning import VisualizationPlan
from .metric_semantics import metric_keys


class VisualizationVerificationError(ValueError):
    """Raised when a proposal cannot be proven from registered numeric facts."""


_MISSING_VALUES = frozenset({"", "-", "--", "—", "N/A", "n/a", "NA", "null"})


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _validate_schema(data: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    errors = list(Draft202012Validator(schema).iter_errors(data))
    if errors:
        raise VisualizationVerificationError(
            f"verified Visualization JSON is invalid: {errors[0].message}"
        )


def _resolve_facts(
    proposal: ExtractionProposal,
    ledger: NumericFactLedger,
) -> tuple[list[list[NumericFact]], list[NumericFact]]:
    resolved_series: list[list[NumericFact]] = []
    flattened: list[NumericFact] = []
    seen: set[str] = set()
    for series in proposal.series:
        resolved: list[NumericFact] = []
        for fact_id in series.fact_ids:
            fact = ledger.get(fact_id)
            if fact is None:
                raise VisualizationVerificationError(f"unknown fact_id: {fact_id}")
            if fact_id in seen:
                raise VisualizationVerificationError(
                    f"fact_id is referenced more than once: {fact_id}"
                )
            seen.add(fact_id)
            resolved.append(fact)
            flattened.append(fact)
        if len(resolved) != len(proposal.category_labels):
            raise VisualizationVerificationError(
                "series length does not match category length"
            )
        resolved_series.append(resolved)
    return resolved_series, flattened


def _validate_chart_presentation(
    plan: VisualizationPlan,
    metric_group: MetricGroup,
    values_by_series: Sequence[Sequence[int | float]],
) -> None:
    """Reject charts that are numerically valid but misleading or unreadable."""
    flattened = [float(value) for series in values_by_series for value in series]
    if metric_group.measure_kind in {"ratio", "share"} and any(
        value < 0 or value > 100 for value in flattened
    ):
        raise VisualizationVerificationError(
            "reject.invalid_percentage_scale: ratio/share values must stay between 0 and 100"
        )

    if len(values_by_series) > 1:
        typical = []
        for series in values_by_series:
            magnitudes = sorted(abs(float(value)) for value in series if float(value) != 0)
            if magnitudes:
                typical.append(magnitudes[len(magnitudes) // 2])
        if len(typical) > 1 and min(typical) > 0 and max(typical) / min(typical) > 20:
            raise VisualizationVerificationError(
                "reject.incompatible_display_scale: series cannot share one readable axis"
            )

    purpose = " ".join([plan.purpose, *plan.data_requirement.values()])
    if "风险" in purpose:
        requested_metrics = set(metric_keys(purpose))
        if not requested_metrics or metric_group.metric_key not in requested_metrics:
            raise VisualizationVerificationError(
                "reject.purpose_metric_mismatch: risk chart does not quantify the named risk"
            )


def assemble_verified_chart(
    plan: VisualizationPlan,
    proposal: ExtractionProposal,
    ledger: NumericFactLedger,
    schema: Mapping[str, Any],
    *,
    allowed_sources: Sequence[tuple[str, str]] | None = None,
    metric_group: MetricGroup | None = None,
) -> dict[str, Any]:
    """Resolve fact IDs and assemble one schema-valid chart Visualization."""

    if proposal.candidate_id != plan.visualization_id:
        raise VisualizationVerificationError(
            "proposal candidate_id does not match visualization plan"
        )
    series_names = [series.name.strip() for series in proposal.series]
    if any(not name for name in series_names) or len(series_names) != len(
        set(series_names)
    ):
        raise VisualizationVerificationError(
            "chart series names must be non-empty and unique"
        )
    resolved_series, facts = _resolve_facts(proposal, ledger)
    if not facts:
        raise VisualizationVerificationError("proposal contains no numeric facts")

    allowed = set(allowed_sources if allowed_sources is not None else plan.evidence_refs)
    native_sources = list(
        dict.fromkeys((fact.source_kind, fact.source_id) for fact in facts)
    )
    if not allowed:
        raise VisualizationVerificationError("verified chart requires scoped evidence")
    if any(source not in allowed for source in native_sources):
        raise VisualizationVerificationError(
            "proposal references a fact outside the candidate evidence"
        )

    units = {fact.unit for fact in facts if fact.unit}
    if len(units) > 1:
        raise VisualizationVerificationError("proposal contains conflicting fact units")
    fact_unit = next(iter(units), "")
    if proposal.unit != fact_unit:
        raise VisualizationVerificationError(
            f"proposal unit {proposal.unit!r} does not match fact unit {fact_unit!r}"
        )
    try:
        metric_group = metric_group or build_metric_group(plan, proposal, ledger)
    except MetricGroupError as exc:
        raise VisualizationVerificationError(str(exc)) from exc
    proposal_fact_ids = tuple(
        fact_id for series in proposal.series for fact_id in series.fact_ids
    )
    if proposal_fact_ids != metric_group.fact_ids:
        raise VisualizationVerificationError(
            "proposal facts do not exactly match the verified MetricGroup"
        )

    values_by_series = [
        [_json_number(fact.normalized_value) for fact in series]
        for series in resolved_series
    ]
    _validate_chart_presentation(plan, metric_group, values_by_series)
    category_count = len(proposal.category_labels)
    if proposal.chart_type == "line":
        if category_count < 4 or any(
            not period_labels(label) for label in proposal.category_labels
        ):
            raise VisualizationVerificationError(
                "line chart requires at least four period-labelled categories"
            )
    elif proposal.chart_type in {"bar", "column"} and not 3 <= category_count <= 8:
        raise VisualizationVerificationError(
            "reject.invalid_category_count: bar or column chart requires "
            "between three and eight categories"
        )
    if proposal.chart_type == "pie":
        if len(values_by_series) != 1:
            raise VisualizationVerificationError("pie chart requires exactly one series")
        values = values_by_series[0]
        if not 3 <= len(values) <= 6 or any(value < 0 for value in values):
            raise VisualizationVerificationError(
                "pie chart requires three to six non-negative values"
            )
        if proposal.unit != "%" or not 95.0 <= sum(values) <= 105.0:
            raise VisualizationVerificationError(
                "pie chart percentages must share a total of approximately 100%"
            )

    data: dict[str, Any] = {
        "chart_type": proposal.chart_type,
        "title": proposal.title,
        "unit": proposal.unit,
        "categories": list(proposal.category_labels),
        "series": [
            {"name": series.name, "values": values}
            for series, values in zip(proposal.series, values_by_series)
        ],
        "source_refs": list(plan.source_refs),
        "sources": [
            {"kind": kind, "id": identity}
            for kind, identity in native_sources
        ],
        "note": "Assembled from verified Numeric Fact Ledger references",
    }
    if metric_group.forecast_start_index is not None:
        data["forecast_start_index"] = metric_group.forecast_start_index
    if not data["sources"]:
        raise VisualizationVerificationError("sources coverage must be 100%")
    _validate_schema(data, schema)
    return data


def assemble_verified_table(
    plan: VisualizationPlan,
    table: Mapping[str, Any],
    ledger: NumericFactLedger,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy one complete table while proving every numeric cell through the ledger."""

    table_id = str(table.get("id") or "")
    columns, rows = table_grid(table)
    if not table_id or not columns or not rows:
        raise VisualizationVerificationError("table is not complete or has no grid")

    selected_columns = columns[:6]
    output_rows: list[list[str | int | float | None]] = []
    for row_index, row in enumerate(rows[:8]):
        padded = (row + [""] * len(selected_columns))[: len(selected_columns)]
        output_row: list[str | int | float | None] = []
        for column_index, cell in enumerate(padded):
            parsed = parse_table_number(cell) if column_index > 0 else None
            if parsed is None:
                output_row.append(None if cell in _MISSING_VALUES else cell)
                continue
            fact = ledger.table_cell(table_id, row_index, column_index)
            if fact is None:
                raise VisualizationVerificationError(
                    f"numeric table cell has no fact: {table_id}[{row_index},{column_index}]"
                )
            raw_value, normalized, explicit_unit = parsed
            if normalized != fact.normalized_value or raw_value != fact.raw_value:
                raise VisualizationVerificationError(
                    f"table fact does not match source cell: {fact.fact_id}"
                )
            output_row.append(
                cell if explicit_unit else _json_number(fact.normalized_value)
            )

        output_rows.append(output_row)

    data: dict[str, Any] = {
        "title": plan.purpose or "Data table",
        "columns": [column or "Item" for column in selected_columns],
        "rows": output_rows,
        "source_refs": list(plan.source_refs),
        "sources": [{"kind": "table", "id": table_id}],
        "note": (
            f"Copied from complete DocumentBundle table {table_id}; "
            "all numeric cells are fact-audited"
        ),
    }
    _validate_schema(data, schema)
    return data
