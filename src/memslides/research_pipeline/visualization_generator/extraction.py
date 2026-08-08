"""Rule-first structure mapping from numeric facts to fact-only proposals."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot

from .contracts import ExtractionProposal, NumericFact, ProposedSeries
from .numeric_facts import NumericFactLedger, period_labels, table_grid
from .planning import VisualizationPlan


class LLMExtractionAdapter(Protocol):
    """Optional semantic mapper; implementations may return only fact references."""

    def propose(
        self,
        plan: VisualizationPlan,
        facts: tuple[NumericFact, ...],
    ) -> ExtractionProposal | None: ...


def _uniform_unit(facts: Sequence[NumericFact]) -> str | None:
    units = {fact.unit for fact in facts if fact.unit}
    if len(units) > 1:
        return None
    return next(iter(units), "")


def _series_name(facts: Sequence[NumericFact], fallback: str) -> str:
    labels = [fact.label for fact in facts if fact.label]
    if labels and len(set(labels)) == 1:
        return str(labels[0])
    return fallback or "Value"


def _chart_type(plan: VisualizationPlan, intent: str, categories: Sequence[str]) -> str:
    if intent == "composition":
        return "pie"
    if intent == "trend" and sum(bool(period_labels(value)) for value in categories) >= 4:
        return "line"
    return "bar" if plan.chart_intent == "comparison" else "column"


def proposal_from_block(
    plan: VisualizationPlan,
    block: Mapping[str, Any],
    ledger: NumericFactLedger,
) -> ExtractionProposal | None:
    """Map one block's facts to a single-series chart proposal."""

    source_id = str(block.get("id") or "")
    facts = list(ledger.for_source("block", source_id))
    if len(facts) < 2:
        return None
    facts.sort(key=lambda fact: (fact.start if fact.start is not None else -1))
    unit = _uniform_unit(facts)
    if unit is None:
        return None

    period_facts = [fact for fact in facts if fact.period]
    label_facts = [fact for fact in facts if fact.label]
    intent = plan.chart_intent
    if intent == "composition":
        selected = label_facts
        categories = [str(fact.label) for fact in selected]
    elif len(period_facts) >= 2 and len({fact.period for fact in period_facts}) == len(period_facts):
        selected = period_facts
        categories = [str(fact.period) for fact in selected]
        intent = "trend"
    elif len(label_facts) >= 2 and len({fact.label for fact in label_facts}) == len(label_facts):
        selected = label_facts
        categories = [str(fact.label) for fact in selected]
        intent = intent or "comparison"
    else:
        return None
    if len(selected) < 2 or len(categories) != len(set(categories)):
        return None
    intent = intent or "comparison"
    return ExtractionProposal(
        candidate_id=plan.visualization_id,
        chart_type=_chart_type(plan, intent, categories),
        title=plan.purpose or "Data chart",
        unit=unit,
        category_labels=tuple(categories),
        series=(
            ProposedSeries(
                name=_series_name(selected, plan.data_requirement.get("y") or plan.purpose),
                fact_ids=tuple(fact.fact_id for fact in selected),
            ),
        ),
    )


def _table_facts_by_coordinate(
    table_id: str,
    ledger: NumericFactLedger,
) -> dict[tuple[int, int], NumericFact]:
    return {
        (fact.row_index, fact.column_index): fact
        for fact in ledger.for_source("table", table_id)
        if fact.row_index is not None and fact.column_index is not None
    }


def proposal_from_table(
    plan: VisualizationPlan,
    table: Mapping[str, Any],
    ledger: NumericFactLedger,
) -> ExtractionProposal | None:
    """Map one complete table to a fact-only chart proposal."""

    table_id = str(table.get("id") or "")
    columns, rows = table_grid(table)
    coordinates = _table_facts_by_coordinate(table_id, ledger)
    if not table_id or not columns or not rows or len(coordinates) < 2:
        return None

    period_columns = [
        index
        for index, column in enumerate(columns)
        if index > 0 and period_labels(column)
    ]
    period_groups: dict[str, list[int]] = {}
    for column_index in period_columns:
        units = {
            fact.unit
            for (row_index, fact_column), fact in coordinates.items()
            if fact_column == column_index and fact.unit
        }
        if len(units) <= 1:
            period_groups.setdefault(next(iter(units), ""), []).append(column_index)
    compatible_period_groups = [
        indices for indices in period_groups.values() if len(indices) >= 2
    ]
    period_columns = (
        max(compatible_period_groups, key=len)
        if compatible_period_groups
        else []
    )
    selected_facts: list[NumericFact] = []
    categories: list[str] = []
    series: list[ProposedSeries] = []
    intent = plan.chart_intent

    if intent == "composition":
        composition: tuple[int, list[tuple[int, NumericFact]]] | None = None
        for column_index in range(1, len(columns)):
            column_facts = [
                (row_index, fact)
                for row_index, row in enumerate(rows)
                if row
                and not re.search(r"合计|总计|total", str(row[0]), re.IGNORECASE)
                and (fact := coordinates.get((row_index, column_index))) is not None
            ]
            if (
                3 <= len(column_facts) <= 6
                and all(fact.unit == "%" and fact.normalized_value >= 0 for _, fact in column_facts)
                and 95 <= sum(fact.normalized_value for _, fact in column_facts) <= 105
            ):
                composition = (column_index, column_facts)
                break
        if composition is None:
            return None
        column_index, column_facts = composition
        categories = [str(rows[row_index][0]) for row_index, _ in column_facts]
        selected_facts = [fact for _, fact in column_facts]
        series = [
            ProposedSeries(
                name=columns[column_index] or plan.purpose or "Composition",
                fact_ids=tuple(fact.fact_id for fact in selected_facts),
            )
        ]
    elif len(period_columns) >= 2:
        categories = [
            period_labels(columns[index])[0]
            if len(period_labels(columns[index])) == 1
            else columns[index]
            for index in period_columns
        ]
        compatible_series: dict[
            tuple[str, str, str, str, str, str],
            list[tuple[ProposedSeries, list[NumericFact]]],
        ] = {}
        for row_index, row in enumerate(rows):
            row_facts = [
                coordinates.get((row_index, column_index))
                for column_index in period_columns
            ]
            if all(row_facts):
                concrete = [fact for fact in row_facts if fact is not None]
                signature = (
                    concrete[0].metric_key,
                    concrete[0].measure_kind,
                    concrete[0].unit_family,
                    concrete[0].unit_scale,
                    concrete[0].currency,
                    concrete[0].scope,
                )
                if all(
                    (
                        fact.metric_key,
                        fact.measure_kind,
                        fact.unit_family,
                        fact.unit_scale,
                        fact.currency,
                        fact.scope,
                    )
                    == signature
                    for fact in concrete
                ):
                    compatible_series.setdefault(signature, []).append(
                        (
                            ProposedSeries(
                                name=str(row[0] if row else "Series"),
                                fact_ids=tuple(fact.fact_id for fact in concrete),
                            ),
                            concrete,
                        )
                    )
        usable_groups = {
            key: values
            for key, values in compatible_series.items()
            if key[1] != "unknown"
        }
        if usable_groups:
            chosen = max(
                usable_groups.values(),
                key=lambda values: (len(values), len(values[0][1])),
            )[:3]
            series = [item for item, _ in chosen]
            selected_facts = [fact for _, group in chosen for fact in group]
        intent = "trend"
    else:
        usable_rows = [
            (row_index, row)
            for row_index, row in enumerate(rows)
            if row and str(row[0]).strip()
        ][:8]
        categories = [str(row[0]) for _, row in usable_rows]
        numeric_columns = [
            column_index
            for column_index in range(1, len(columns))
            if sum(
                (row_index, column_index) in coordinates
                for row_index, _ in usable_rows
            )
            >= 2
        ]
        numeric_columns = numeric_columns[:3]
        for column_index in numeric_columns:
            column_facts = [
                coordinates.get((row_index, column_index))
                for row_index, _ in usable_rows
            ]
            if not all(column_facts):
                continue
            concrete = [fact for fact in column_facts if fact is not None]
            if concrete[0].measure_kind == "unknown":
                continue
            signature = (
                concrete[0].metric_key,
                concrete[0].measure_kind,
                concrete[0].unit_family,
                concrete[0].unit_scale,
                concrete[0].currency,
            )
            if any(
                (
                    fact.metric_key,
                    fact.measure_kind,
                    fact.unit_family,
                    fact.unit_scale,
                    fact.currency,
                )
                != signature
                for fact in concrete
            ):
                continue
            selected_facts.extend(concrete)
            series.append(
                ProposedSeries(
                    name=columns[column_index] or "Value",
                    fact_ids=tuple(fact.fact_id for fact in concrete),
                )
            )
            # A comparison chart must not silently mix different metrics.
            break
        intent = intent or "comparison"

    if not categories or not series or not selected_facts:
        return None
    unit = _uniform_unit(selected_facts)
    if unit is None:
        return None
    if any(len(item.fact_ids) != len(categories) for item in series):
        return None
    return ExtractionProposal(
        candidate_id=plan.visualization_id,
        chart_type=_chart_type(plan, intent, categories),
        title=plan.purpose or "Data chart",
        unit=unit,
        category_labels=tuple(categories),
        series=tuple(series),
    )


def _scoped_facts(
    plan: VisualizationPlan,
    snapshot: DocumentIntelligenceSnapshot,
    ledger: NumericFactLedger,
) -> tuple[NumericFact, ...]:
    result: list[NumericFact] = []
    for kind, identity in plan.evidence_refs:
        result.extend(ledger.for_source(kind, identity))
        if kind == "block":
            for table_id in snapshot.block_table_ids.get(identity, ()):
                result.extend(ledger.for_source("table", table_id))
    return tuple(dict.fromkeys(result))


def map_extraction_proposal(
    plan: VisualizationPlan,
    snapshot: DocumentIntelligenceSnapshot,
    ledger: NumericFactLedger,
    *,
    llm_adapter: LLMExtractionAdapter | None = None,
) -> ExtractionProposal | None:
    """Use deterministic rules first, then an optional fact-only LLM adapter."""

    if plan.visual_type != "chart":
        return None
    for kind, identity in plan.evidence_refs:
        if kind == "table" and identity in snapshot.tables_by_id:
            proposal = proposal_from_table(plan, snapshot.tables_by_id[identity], ledger)
        elif kind == "block" and identity in snapshot.blocks_by_id:
            proposal = proposal_from_block(plan, snapshot.blocks_by_id[identity], ledger)
        else:
            proposal = None
        if proposal is not None:
            return proposal
    if llm_adapter is None:
        return None
    facts = _scoped_facts(plan, snapshot, ledger)
    if len(facts) < 2:
        return None
    proposal = llm_adapter.propose(plan, facts)
    if proposal is not None and proposal.candidate_id != plan.visualization_id:
        raise ValueError("LLM proposal candidate_id does not match the visualization plan")
    return proposal
