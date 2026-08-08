"""Frozen Week 3 runtime contracts for evidence-backed chart extraction.

These objects are process-local interfaces.  They deliberately are not JSON
Schemas and must never be passed directly to the Renderer.  Numeric values can
enter an :class:`ExtractionProposal` only by reference to a ``NumericFact``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import isfinite


class CandidateTriggerCode(StrEnum):
    """Stable, sample-independent reasons for emitting a visual candidate."""

    OUTLINE_SUGGESTION = "candidate.outline_suggestion"
    COMPLETE_TABLE = "candidate.complete_table"
    COMPARABLE_NUMBERS = "candidate.comparable_numbers"
    TIME_SERIES = "candidate.time_series"
    CATEGORY_COMPARISON = "candidate.category_comparison"
    COMPOSITION = "candidate.composition"
    METRIC_KEYWORD = "candidate.metric_keyword"
    LABELS_COLOCATED = "candidate.labels_colocated"


class CandidateRejectionCode(StrEnum):
    """Stable reasons for rejecting an evidence unit during candidate search."""

    SINGLE_NUMBER = "reject.single_number"
    ADMINISTRATIVE_NUMBERS_ONLY = "reject.administrative_numbers_only"
    SINGLE_POINT_SIGNAL = "reject.single_point_signal"
    MIXED_METRIC_OR_UNIT = "reject.mixed_metric_or_unit"
    INCOMPLETE_TABLE = "reject.incomplete_table"
    NON_COMPOSITION_PERCENTAGES = "reject.non_composition_percentages"
    MISSING_LABEL = "reject.missing_label"
    OUT_OF_SCOPE = "reject.out_of_scope"


class MetricGroupRejectionCode(StrEnum):
    """Stable Phase 1 reasons for rejecting a proposed metric group."""

    MIXED_METRIC = "reject.mixed_metric"
    MIXED_MEASURE_KIND = "reject.mixed_measure_kind"
    MIXED_UNIT_FAMILY = "reject.mixed_unit_family"
    MIXED_UNIT_SCALE = "reject.mixed_unit_scale"
    MIXED_CURRENCY = "reject.mixed_currency"
    MIXED_ENTITY = "reject.mixed_entity"
    MIXED_SCOPE = "reject.mixed_scope"
    MIXED_SCENARIO = "reject.mixed_scenario"
    INVALID_FORECAST_BOUNDARY = "reject.invalid_forecast_boundary"
    INCOMPLETE_METRIC_TYPING = "reject.incomplete_metric_typing"


_TRIGGER_CODES = frozenset(code.value for code in CandidateTriggerCode)
_CHART_INTENTS = frozenset({"trend", "comparison", "composition"})
_MVP_CHART_TYPES = frozenset({"line", "column", "bar", "pie"})


class MeasureKind(StrEnum):
    AMOUNT = "amount"
    RATIO = "ratio"
    GROWTH_RATE = "growth_rate"
    SHARE = "share"
    PER_SHARE = "per_share"
    MULTIPLE = "multiple"
    COUNT = "count"
    UNKNOWN = "unknown"


class UnitFamily(StrEnum):
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    MULTIPLE = "multiple"
    COUNT = "count"
    CAPACITY = "capacity"
    VOLUME = "volume"
    DIMENSIONLESS = "dimensionless"
    UNKNOWN = "unknown"


class ScenarioKind(StrEnum):
    ACTUAL = "actual"
    ESTIMATE = "estimate"
    GUIDANCE = "guidance"
    TARGET = "target"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    """A scored, data-free candidate located in block/table evidence."""

    candidate_id: str
    slide_id: str | None
    visual_type: str
    chart_intent: str | None
    evidence_refs: tuple[tuple[str, str], ...]
    trigger_ids: tuple[str, ...]
    score: float
    excerpt: str

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        if self.visual_type not in {"chart", "table"}:
            raise ValueError("visual_type must be chart or table")
        if self.chart_intent is not None and self.chart_intent not in _CHART_INTENTS:
            raise ValueError("unsupported chart_intent")
        if not self.evidence_refs:
            raise ValueError("evidence_refs must not be empty")
        for kind, identity in self.evidence_refs:
            if kind not in {"block", "table"} or not identity:
                raise ValueError("candidate evidence must be a block or table with an id")
        if not self.trigger_ids or any(code not in _TRIGGER_CODES for code in self.trigger_ids):
            raise ValueError("trigger_ids must contain only frozen candidate reason codes")
        if not isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be a finite number between 0 and 1")
        if not self.excerpt.strip():
            raise ValueError("excerpt must not be empty")


@dataclass(frozen=True, slots=True)
class NumericFact:
    """One deterministic numeric fact and its exact native source location.

    Block facts use ``start``/``end`` character offsets.  Complete-table facts
    use zero-based ``row_index``/``column_index`` coordinates.  The two locator
    forms are mutually exclusive so a fact is always traceable to one value.
    """

    fact_id: str
    source_kind: str
    source_id: str
    raw_value: str
    normalized_value: Decimal
    unit: str
    label: str | None
    period: str | None
    start: int | None
    end: int | None
    row_index: int | None = None
    column_index: int | None = None
    entity_id: str = "document"
    entity_name: str = ""
    entity_type: str = "company"
    metric_key: str = "unknown"
    metric_label: str = ""
    measure_kind: str = MeasureKind.UNKNOWN.value
    unit_family: str = UnitFamily.UNKNOWN.value
    unit_scale: str = "1"
    currency: str = ""
    scope: str = "consolidated"
    scope_label: str = ""
    scenario: str = ScenarioKind.UNKNOWN.value

    def __post_init__(self) -> None:
        if not self.fact_id.startswith("fact_"):
            raise ValueError("fact_id must start with fact_")
        if self.source_kind not in {"block", "table"}:
            raise ValueError("source_kind must be block or table")
        if not self.source_id or not self.raw_value:
            raise ValueError("source_id and raw_value must not be empty")
        if not self.normalized_value.is_finite():
            raise ValueError("normalized_value must be finite")
        if self.measure_kind not in {item.value for item in MeasureKind}:
            raise ValueError("unsupported measure_kind")
        if self.unit_family not in {item.value for item in UnitFamily}:
            raise ValueError("unsupported unit_family")
        if self.scenario not in {item.value for item in ScenarioKind}:
            raise ValueError("unsupported scenario")
        if not self.entity_id or not self.entity_type or not self.metric_key or not self.scope:
            raise ValueError("entity, metric_key, and scope identifiers must not be empty")
        try:
            scale = Decimal(self.unit_scale)
        except Exception as exc:
            raise ValueError("unit_scale must be a positive finite decimal") from exc
        if not scale.is_finite() or scale <= 0:
            raise ValueError("unit_scale must be a positive finite decimal")

        has_span = self.start is not None or self.end is not None
        has_cell = self.row_index is not None or self.column_index is not None
        if has_span and has_cell:
            raise ValueError("a numeric fact cannot use both span and table-cell locators")
        if self.source_kind == "block":
            if self.start is None or self.end is None or self.start < 0 or self.end <= self.start:
                raise ValueError("block facts require a valid start/end span")
            if has_cell:
                raise ValueError("block facts cannot use table-cell coordinates")
        else:
            if (
                self.row_index is None
                or self.column_index is None
                or self.row_index < 0
                or self.column_index < 0
            ):
                raise ValueError("table facts require non-negative row/column coordinates")
            if has_span:
                raise ValueError("table facts cannot use block spans")


@dataclass(frozen=True, slots=True)
class MetricGroup:
    """A semantically compatible set of facts approved for one chart."""

    group_id: str
    candidate_id: str
    intent: str
    metric_key: str
    measure_kind: str
    unit_family: str
    unit: str
    unit_scale: str
    currency: str
    scope: str
    fact_ids: tuple[str, ...]
    scenarios: tuple[str, ...]
    forecast_start_index: int | None = None

    def __post_init__(self) -> None:
        if not self.group_id.startswith("metric_group_"):
            raise ValueError("group_id must start with metric_group_")
        if self.intent not in _CHART_INTENTS:
            raise ValueError("unsupported MetricGroup intent")
        if not self.fact_ids or len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("MetricGroup facts must be non-empty and unique")
        if self.metric_key == "unknown" or self.measure_kind == "unknown":
            raise ValueError("MetricGroup requires typed metric semantics")
        if self.unit_family == "unknown" or not self.unit_scale or not self.scope:
            raise ValueError("MetricGroup requires typed unit and scope semantics")
        if len(self.scenarios) != len(self.fact_ids):
            raise ValueError("MetricGroup scenarios must align with fact_ids")
        if self.forecast_start_index is not None and not (
            0 <= self.forecast_start_index < len(self.fact_ids)
        ):
            raise ValueError("forecast_start_index is outside MetricGroup facts")


@dataclass(frozen=True, slots=True)
class ProposedSeries:
    """A proposed chart series containing fact references, never values."""

    name: str
    fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("series name must not be empty")
        if not self.fact_ids or any(not fact_id.startswith("fact_") for fact_id in self.fact_ids):
            raise ValueError("series data points must be fact_id references")


@dataclass(frozen=True, slots=True)
class ExtractionProposal:
    """Data-free chart structure awaiting deterministic verification."""

    candidate_id: str
    chart_type: str
    title: str
    unit: str
    category_labels: tuple[str, ...]
    series: tuple[ProposedSeries, ...]

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        if self.chart_type not in _MVP_CHART_TYPES:
            raise ValueError("chart_type is outside the Week 3 MVP")
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if not self.category_labels or any(not label.strip() for label in self.category_labels):
            raise ValueError("category_labels must contain non-empty labels")
        if not self.series:
            raise ValueError("series must not be empty")
        if any(len(item.fact_ids) != len(self.category_labels) for item in self.series):
            raise ValueError("each series must map one fact_id to each category")
