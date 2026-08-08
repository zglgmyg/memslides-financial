"""Build and verify semantically compatible chart fact groups."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .contracts import ExtractionProposal, MetricGroup, NumericFact, ScenarioKind
from .metric_semantics import classify_metric, metric_keys
from .numeric_facts import NumericFactLedger
from .numeric_facts import period_labels
from .planning import VisualizationPlan


class MetricGroupError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _typed(fact: NumericFact, plan: VisualizationPlan) -> dict[str, str]:
    label_inferred = classify_metric(
        label=fact.label,
        context="",
        unit=fact.unit,
        period=fact.period,
    )
    purpose_keys = metric_keys(plan.purpose)
    purpose_inferred = classify_metric(
        label=None,
        context=plan.purpose if len(purpose_keys) == 1 else "",
        unit=fact.unit,
        period=fact.period,
    )
    inferred_metric = (
        label_inferred.metric_key
        if label_inferred.metric_key != "unknown"
        else purpose_inferred.metric_key
    )
    return {
        "metric_key": fact.metric_key if fact.metric_key != "unknown" else inferred_metric,
        "measure_kind": fact.measure_kind if fact.measure_kind != "unknown" else label_inferred.measure_kind,
        "unit_family": fact.unit_family if fact.unit_family != "unknown" else label_inferred.unit_family,
        "unit_scale": fact.unit_scale or label_inferred.unit_scale,
        "currency": fact.currency or label_inferred.currency,
        "scenario": fact.scenario if fact.scenario != "unknown" else label_inferred.scenario,
    }


def _one(values: set[str], code: str, label: str) -> str:
    if len(values) != 1:
        raise MetricGroupError(code, f"facts contain incompatible {label}: {sorted(values)}")
    return next(iter(values))


def _scenario_boundary(values: list[str]) -> int | None:
    estimate_kinds = {
        ScenarioKind.ESTIMATE.value,
        ScenarioKind.GUIDANCE.value,
        ScenarioKind.TARGET.value,
    }
    boundary = next((index for index, value in enumerate(values) if value in estimate_kinds), None)
    if boundary is None:
        if any(value != ScenarioKind.ACTUAL.value for value in values):
            raise MetricGroupError(
                "reject.incomplete_metric_typing",
                "trend facts require known actual or forecast scenarios",
            )
        return None
    if any(value != ScenarioKind.ACTUAL.value for value in values[:boundary]) or any(
        value not in estimate_kinds for value in values[boundary:]
    ):
        raise MetricGroupError(
            "reject.invalid_forecast_boundary",
            "historical and forecast facts must form one ordered boundary",
        )
    return boundary


def build_metric_group(
    plan: VisualizationPlan,
    proposal: ExtractionProposal,
    ledger: NumericFactLedger,
) -> MetricGroup:
    """Reject any proposal that combines incompatible typed facts."""

    series_facts: list[list[NumericFact]] = []
    for series in proposal.series:
        resolved: list[NumericFact] = []
        for fact_id in series.fact_ids:
            fact = ledger.get(fact_id)
            if fact is None:
                raise MetricGroupError("reject.unknown_fact", f"unknown fact_id {fact_id}")
            resolved.append(fact)
        series_facts.append(resolved)
    facts = [fact for series in series_facts for fact in series]
    semantics = [_typed(fact, plan) for fact in facts]
    metric_key = _one(
        {item["metric_key"] for item in semantics},
        "reject.mixed_metric",
        "metrics",
    )
    if metric_key == "unknown":
        raise MetricGroupError(
            "reject.incomplete_metric_typing",
            "metric_key is unknown",
        )
    measure_kind = _one(
        {item["measure_kind"] for item in semantics},
        "reject.mixed_measure_kind",
        "measure kinds",
    )
    if measure_kind == "unknown":
        raise MetricGroupError(
            "reject.incomplete_metric_typing",
            "measure_kind is unknown",
        )
    unit_family = _one(
        {item["unit_family"] for item in semantics},
        "reject.mixed_unit_family",
        "unit families",
    )
    if unit_family == "unknown":
        raise MetricGroupError(
            "reject.incomplete_metric_typing",
            "unit_family is unknown",
        )
    unit_scale = _one(
        {item["unit_scale"] for item in semantics},
        "reject.mixed_unit_scale",
        "unit scales",
    )
    currency = _one(
        {item["currency"] for item in semantics},
        "reject.mixed_currency",
        "currencies",
    )
    unit = _one({fact.unit for fact in facts}, "reject.mixed_unit", "units")

    has_period_axis = all(period_labels(label) for label in proposal.category_labels) and all(
        fact.period for fact in facts
    )
    intent = (
        "composition"
        if proposal.chart_type == "pie"
        else "trend"
        if proposal.chart_type == "line" or has_period_axis
        else plan.chart_intent or "comparison"
    )
    scenario_rows: list[list[str]] = []
    semantic_index = 0
    for facts_in_series in series_facts:
        row = [semantics[semantic_index + offset]["scenario"] for offset in range(len(facts_in_series))]
        semantic_index += len(facts_in_series)
        scenario_rows.append(row)

    forecast_start_index: int | None = None
    if intent == "trend":
        for facts_in_series in series_facts:
            if len({fact.entity_id for fact in facts_in_series}) != 1:
                raise MetricGroupError("reject.mixed_entity", "one trend series must use one entity")
            if len({(fact.scope, fact.scope_label) for fact in facts_in_series}) != 1:
                raise MetricGroupError("reject.mixed_scope", "one trend series must use one scope")
        boundaries = {_scenario_boundary(row) for row in scenario_rows}
        if len(boundaries) != 1:
            raise MetricGroupError(
                "reject.invalid_forecast_boundary",
                "all trend series must share the same forecast boundary",
            )
        forecast_start_index = next(iter(boundaries))
    else:
        scenario = _one(
            {value for row in scenario_rows for value in row},
            "reject.mixed_scenario",
            "scenarios",
        )
        if scenario == ScenarioKind.UNKNOWN.value:
            raise MetricGroupError(
                "reject.incomplete_metric_typing",
                "scenario is unknown",
            )
        periods = {fact.period for fact in facts if fact.period}
        if intent == "comparison" and len(periods) > 1:
            raise MetricGroupError(
                "reject.mixed_period",
                f"comparison facts use different periods: {sorted(periods)}",
            )
        if intent == "composition" and measure_kind != "share":
            raise MetricGroupError(
                "reject.mixed_measure_kind",
                "composition requires share facts",
            )

    fact_ids = tuple(fact.fact_id for fact in facts)
    payload = json.dumps(
        {
            "candidate_id": proposal.candidate_id,
            "intent": intent,
            "metric_key": metric_key,
            "measure_kind": measure_kind,
            "unit_family": unit_family,
            "unit_scale": unit_scale,
            "currency": currency,
            "facts": fact_ids,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    group_id = "metric_group_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return MetricGroup(
        group_id=group_id,
        candidate_id=proposal.candidate_id,
        intent=intent,
        metric_key=metric_key,
        measure_kind=measure_kind,
        unit_family=unit_family,
        unit=unit,
        unit_scale=unit_scale,
        currency=currency,
        scope=_one({fact.scope for fact in facts}, "reject.mixed_scope", "scope kinds"),
        fact_ids=fact_ids,
        scenarios=tuple(value for row in scenario_rows for value in row),
        forecast_start_index=forecast_start_index,
    )


def serialize_metric_group(group: MetricGroup) -> dict[str, Any]:
    value = asdict(group)
    value["fact_ids"] = list(group.fact_ids)
    value["scenarios"] = list(group.scenarios)
    return value
