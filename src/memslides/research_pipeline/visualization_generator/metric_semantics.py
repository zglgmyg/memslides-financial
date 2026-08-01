"""Deterministic minimum taxonomy for numeric chart compatibility."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import MeasureKind, ScenarioKind, UnitFamily


@dataclass(frozen=True, slots=True)
class MetricSemantics:
    metric_key: str
    metric_label: str
    measure_kind: str
    unit_family: str
    unit_scale: str
    currency: str
    scenario: str


_METRICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("parent_net_profit", re.compile(r"归母净利润|归属于母公司.*净利润")),
    ("net_profit", re.compile(r"净利润|净利")),
    ("revenue", re.compile(r"营业收入|营业总收入|营收|销售收入|收入")),
    ("operating_cash_flow", re.compile(r"经营.*现金流|经营活动.*现金")),
    ("gross_margin", re.compile(r"毛利率")),
    ("net_margin", re.compile(r"净利率")),
    ("market_share", re.compile(r"市占率|市场份额")),
    ("eps", re.compile(r"EPS|每股收益", re.IGNORECASE)),
    ("pe", re.compile(r"PE|市盈率", re.IGNORECASE)),
    ("pb", re.compile(r"PB|市净率", re.IGNORECASE)),
    ("price", re.compile(r"价格|单价")),
    ("sales_volume", re.compile(r"销量|出货量")),
    ("capacity", re.compile(r"产能|装机")),
    ("composition", re.compile(r"构成|占比|份额")),
)


def metric_keys(text: str) -> tuple[str, ...]:
    """Return distinct metric concepts explicitly named by text."""

    keys = [key for key, pattern in _METRICS if pattern.search(str(text or ""))]
    # The generic net-profit expression is a lexical subset of parent net
    # profit; one phrase must not be counted as two separate metrics.
    if "parent_net_profit" in keys and "net_profit" in keys:
        keys.remove("net_profit")
    return tuple(dict.fromkeys(keys))


def unit_semantics(unit: str) -> tuple[str, str, str]:
    value = str(unit or "").strip()
    if value in {"亿元", "万元", "百万元", "千万", "万美元", "亿美元", "美元", "元"}:
        scale = {
            "元": "1",
            "万元": "10000",
            "百万元": "1000000",
            "千万": "10000000",
            "亿元": "100000000",
            "美元": "1",
            "万美元": "10000",
            "亿美元": "100000000",
        }.get(value, "1")
        return UnitFamily.CURRENCY.value, scale, "USD" if "美元" in value else "CNY"
    if value in {"%", "个百分点"}:
        return UnitFamily.PERCENTAGE.value, "1", ""
    if value == "倍":
        return UnitFamily.MULTIPLE.value, "1", ""
    if value in {"个", "项", "颗"}:
        return UnitFamily.COUNT.value, "1", ""
    if value in {"GW", "MW"}:
        return UnitFamily.CAPACITY.value, "1000" if value == "GW" else "1", ""
    if value:
        return UnitFamily.VOLUME.value, "1", ""
    return UnitFamily.DIMENSIONLESS.value, "1", ""


def scenario_from_text(period: str | None, text: str) -> str:
    period_value = str(period or "").strip()
    context = str(text or "")
    if re.search(r"指引|guidance", context, re.IGNORECASE):
        return ScenarioKind.GUIDANCE.value
    if re.search(r"目标|target", context, re.IGNORECASE):
        return ScenarioKind.TARGET.value
    if period_value.upper().endswith("E") or re.search(r"预计|预测|预期|estimate|forecast", context, re.IGNORECASE):
        return ScenarioKind.ESTIMATE.value
    if period_value or re.search(r"实际|实现|actual", context, re.IGNORECASE):
        return ScenarioKind.ACTUAL.value
    return ScenarioKind.ACTUAL.value


def classify_metric(*, label: str | None, context: str, unit: str, period: str | None) -> MetricSemantics:
    label_text = str(label or "")
    context_text = str(context or "")
    text = " ".join(item for item in (label_text, context_text) if item)
    label_keys = metric_keys(label_text)
    context_keys = metric_keys(context_text)
    # A nearby label is stronger evidence than the surrounding paragraph or
    # table caption.  Context may fill a missing key only when it names exactly
    # one metric; selecting the first of several metrics silently mis-types data.
    metric_key = (
        label_keys[0]
        if label_keys
        else context_keys[0]
        if len(context_keys) == 1
        else "unknown"
    )
    family, scale, currency = unit_semantics(unit)
    if re.search(r"同比|环比|增速|增长率|CAGR", text, re.IGNORECASE):
        measure = MeasureKind.GROWTH_RATE.value
    elif re.search(r"占比|份额|构成", text):
        measure = MeasureKind.SHARE.value
        if metric_key == "unknown":
            metric_key = "composition"
    elif metric_key == "eps" or re.search(r"每股", text):
        measure = MeasureKind.PER_SHARE.value
    elif metric_key in {"pe", "pb"} or family == UnitFamily.MULTIPLE.value:
        measure = MeasureKind.MULTIPLE.value
    elif metric_key in {"gross_margin", "net_margin", "market_share"} or family == UnitFamily.PERCENTAGE.value:
        measure = MeasureKind.RATIO.value
    elif family == UnitFamily.COUNT.value:
        measure = MeasureKind.COUNT.value
    elif metric_key != "unknown" or family in {UnitFamily.CURRENCY.value, UnitFamily.CAPACITY.value, UnitFamily.VOLUME.value}:
        measure = MeasureKind.AMOUNT.value
    else:
        measure = MeasureKind.UNKNOWN.value
    return MetricSemantics(
        metric_key=metric_key,
        metric_label=str(label or "").strip(),
        measure_kind=measure,
        unit_family=family,
        unit_scale=scale,
        currency=currency,
        scenario=scenario_from_text(period, text),
    )
