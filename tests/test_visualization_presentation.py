from __future__ import annotations

import pytest

from memslides.research_pipeline.visualization_generator.contracts import MetricGroup
from memslides.research_pipeline.visualization_generator.planning import VisualizationPlan
from memslides.research_pipeline.visualization_generator.verification import (
    VisualizationVerificationError,
    _validate_chart_presentation,
)


def _plan(purpose: str) -> VisualizationPlan:
    return VisualizationPlan(
        slide_id="slide_001",
        visualization_id="visual_001",
        visual_type="chart",
        purpose=purpose,
        chart_intent="trend",
        data_requirement={},
        evidence_refs=(("table", "table-001"),),
        source_refs=("src_001",),
    )


def _group(*, metric_key: str, measure_kind: str = "amount") -> MetricGroup:
    return MetricGroup(
        group_id="metric_group_001",
        candidate_id="visual_001",
        intent="trend",
        metric_key=metric_key,
        measure_kind=measure_kind,
        unit_family="dimensionless",
        unit="",
        unit_scale="1",
        currency="",
        scope="consolidated",
        fact_ids=("fact_001",),
        scenarios=("actual",),
    )


def test_rejects_ratio_values_outside_percentage_scale() -> None:
    with pytest.raises(VisualizationVerificationError, match="invalid_percentage_scale"):
        _validate_chart_presentation(
            _plan("市场份额"),
            _group(metric_key="market_share", measure_kind="ratio"),
            [[1342, 5.6, 23.8]],
        )


def test_rejects_series_that_cannot_share_a_readable_axis() -> None:
    with pytest.raises(VisualizationVerificationError, match="incompatible_display_scale"):
        _validate_chart_presentation(
            _plan("营业收入趋势"),
            _group(metric_key="revenue"),
            [[29136, 32983, 51494], [7, 10, 16]],
        )


def test_rejects_risk_chart_for_an_unrelated_metric() -> None:
    with pytest.raises(VisualizationVerificationError, match="purpose_metric_mismatch"):
        _validate_chart_presentation(
            _plan("原料供应及价格波动风险"),
            _group(metric_key="revenue"),
            [[29136, 32983, 51494]],
        )


def test_accepts_chart_that_quantifies_the_named_risk() -> None:
    _validate_chart_presentation(
        _plan("原料价格波动风险"),
        _group(metric_key="price"),
        [[820, 910, 860]],
    )
