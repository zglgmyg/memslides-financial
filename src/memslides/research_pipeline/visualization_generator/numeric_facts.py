"""Deterministic numeric fact extraction from canonical DocumentBundle evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from lxml import html

from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot

from .contracts import NumericFact
from .metric_semantics import classify_metric


UNITS = (
    "万亿元",
    "千万元",
    "百万元",
    "亿美元",
    "万颗",
    "个百分点",
    "亿元",
    "万元",
    "美元",
    "GW",
    "MW",
    "元",
    "%",
    "倍",
    "颗",
)
_UNIT_CANONICAL = {unit.casefold(): unit for unit in UNITS}
_UNITS_PATTERN = "|".join(re.escape(unit) for unit in UNITS)
_NUMBER_RE = re.compile(
    rf"(?<![\d.])"
    rf"(?P<number>[+\-]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?)"
    rf"\s*(?P<unit>{_UNITS_PATTERN})?",
    re.IGNORECASE,
)
_FULL_NUMBER_RE = re.compile(
    rf"^\s*(?P<number>[+\-]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?)"
    rf"\s*(?P<unit>{_UNITS_PATTERN})?\s*$",
    re.IGNORECASE,
)
_PERIOD_RE = re.compile(
    r"(?:(?:19|20)\d{2}(?:年末|年|[AE])?|(?:Q[1-4]|[一二三四]季度))",
    re.IGNORECASE,
)
_YEAR_RANGE_RE = re.compile(r"((?:19|20)\d{2})\s*[-–—至]\s*((?:19|20)\d{2})年?")
_METRIC_RE = re.compile(
    r"同比|环比|增长率|增速|CAGR|毛利率|净利率|市占率|占比|份额|"
    r"营收|收入|利润|现金流|市场规模|销量|产量",
    re.IGNORECASE,
)
_LABEL_SUFFIX_RE = re.compile(
    r"(?:同比|环比|增长率|增速|CAGR|毛利率|净利率|市占率|占比|份额|构成)$",
    re.IGNORECASE,
)
_MISSING_VALUES = frozenset({"", "-", "--", "—", "N/A", "n/a", "NA", "null"})
_CITATION_RE = re.compile(r"\[\^cite_?id:[^\]]+\]", re.IGNORECASE)
_MARKDOWN_RE = re.compile(r"[*_`~]+")


def _clean(value: object) -> str:
    text = _CITATION_RE.sub("", str(value or ""))
    text = _MARKDOWN_RE.sub("", text)
    return " ".join(text.replace("\u3000", " ").split())


def _canonical_unit(value: str | None) -> str:
    return _UNIT_CANONICAL.get(str(value or "").casefold(), "")


def _decimal(value: str) -> Decimal | None:
    try:
        result = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _period_label(value: str) -> str:
    text = value.strip()
    if text.endswith("年末"):
        return text[:-2]
    if text.endswith("年"):
        return text[:-1]
    return text.upper() if re.fullmatch(r"Q[1-4]", text, re.IGNORECASE) else text


def period_labels(text: str) -> tuple[str, ...]:
    """Return ordered, normalized periods, expanding simple year ranges."""

    values: list[tuple[int, tuple[str, ...]]] = []
    covered: list[tuple[int, int]] = []
    for match in _YEAR_RANGE_RE.finditer(text):
        start_year = int(match.group(1))
        end_year = int(match.group(2))
        if start_year <= end_year and end_year - start_year <= 10:
            values.append((match.start(), tuple(str(year) for year in range(start_year, end_year + 1))))
            covered.append(match.span())
    for match in _PERIOD_RE.finditer(text):
        if any(match.start() < end and start < match.end() for start, end in covered):
            continue
        values.append((match.start(), (_period_label(match.group(0)),)))
    flattened = [
        period
        for _, group in sorted(values, key=lambda item: item[0])
        for period in group
    ]
    return tuple(dict.fromkeys(flattened))


def _label_before(text: str, start: int) -> str | None:
    boundary = max(text.rfind(mark, 0, start) for mark in ("，", ",", "。", "；", ";", "：", ":"))
    fragment = text[boundary + 1 : start]
    fragment = _YEAR_RANGE_RE.sub("", fragment)
    fragment = _PERIOD_RE.sub("", fragment)
    fragment = re.sub(r"^(?:预计|其中|分别|约|达到|为|是|达)+", "", fragment)
    fragment = re.sub(r"(?:分别为|约为|达到|为|是|达)\s*$", "", fragment)
    fragment = fragment.strip(" ：:、，,/")
    if not fragment:
        return None
    words = re.findall(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9+\-]{0,24}", fragment)
    if not words:
        return None
    label = words[-1]
    label = _LABEL_SUFFIX_RE.sub("", label).strip()
    return label or words[-1]


def _fact_id(
    source_kind: str,
    source_id: str,
    *,
    start: int | None = None,
    end: int | None = None,
    row_index: int | None = None,
    column_index: int | None = None,
) -> str:
    locator = (
        f"span:{start}:{end}"
        if start is not None and end is not None
        else f"cell:{row_index}:{column_index}"
    )
    digest = hashlib.sha256(
        f"{source_kind}:{source_id}:{locator}".encode("utf-8")
    ).hexdigest()[:20]
    return f"fact_{digest}"


def block_numeric_facts(
    block: Mapping[str, Any],
    *,
    entity_id: str = "document",
    entity_name: str = "",
    context: str = "",
) -> tuple[NumericFact, ...]:
    """Extract traceable facts from one paragraph-like block."""

    if str(block.get("type") or "") not in {"paragraph", "blockquote", "list_item"}:
        return ()
    source_id = str(block.get("id") or "")
    text = str(block.get("text_raw") or "")
    if not source_id or not text:
        return ()
    period_spans = [match.span() for match in _PERIOD_RE.finditer(text)]
    matches = [
        match
        for match in _NUMBER_RE.finditer(text)
        if not any(
            match.start("number") < end and start < match.end("number")
            for start, end in period_spans
        )
    ]
    if not matches:
        return ()
    has_metric = bool(_METRIC_RE.search(text))
    explicit_units = {
        _canonical_unit(match.group("unit"))
        for match in matches
        if match.group("unit")
    }
    shared_unit = next(iter(explicit_units)) if len(explicit_units) == 1 else ""
    periods = period_labels(text)
    provisional: list[dict[str, Any]] = []
    last_label: str | None = None
    for match in matches:
        unit = _canonical_unit(match.group("unit")) or shared_unit
        if not unit and not has_metric:
            continue
        normalized = _decimal(match.group("number"))
        if normalized is None:
            continue
        label = _label_before(text, match.start("number")) or last_label
        if label:
            last_label = label
        preceding = [
            period
            for period_match in _PERIOD_RE.finditer(text, 0, match.start("number"))
            if match.start("number") - period_match.end() <= 40
            for period in (_period_label(period_match.group(0)),)
        ]
        provisional.append(
            {
                "match": match,
                "normalized": normalized,
                "unit": unit,
                "label": label,
                "period": preceding[-1] if preceding else None,
            }
        )
    if len(periods) == len(provisional):
        for item, period in zip(provisional, periods):
            item["period"] = period

    facts: list[NumericFact] = []
    for item in provisional:
        match = item["match"]
        start, end = match.span("number")
        semantics = classify_metric(
            label=item["label"],
            context=f"{context} {text}",
            unit=item["unit"],
            period=item["period"],
        )
        facts.append(
            NumericFact(
                fact_id=_fact_id("block", source_id, start=start, end=end),
                source_kind="block",
                source_id=source_id,
                raw_value=match.group("number"),
                normalized_value=item["normalized"],
                unit=item["unit"],
                label=item["label"],
                period=item["period"],
                start=start,
                end=end,
                entity_id=entity_id,
                entity_name=entity_name,
                metric_key=semantics.metric_key,
                metric_label=semantics.metric_label,
                measure_kind=semantics.measure_kind,
                unit_family=semantics.unit_family,
                unit_scale=semantics.unit_scale,
                currency=semantics.currency,
                scenario=semantics.scenario,
            )
        )
    return tuple(facts)


def table_grid(table: Mapping[str, Any]) -> tuple[list[str], list[list[str]]]:
    """Return a normalized grid for a complete native table."""

    if table.get("status") != "complete":
        return [], []
    structure = table.get("structure_raw")
    if not isinstance(structure, Mapping):
        return [], []
    if structure.get("format") == "grid":
        columns = [_clean(value) for value in structure.get("columns", [])]
        rows = [
            [_clean(cell) for cell in row]
            for row in structure.get("rows", [])
            if isinstance(row, list)
        ]
        return columns, rows
    if structure.get("format") != "html":
        return [], []
    try:
        root = html.fromstring(str(structure.get("content") or ""))
    except (TypeError, ValueError):
        return [], []
    values = [
        [_clean(cell.text_content()) for cell in row.xpath("./th|./td")]
        for row in root.xpath(".//tr")
    ]
    values = [row for row in values if row]
    return (values[0], values[1:]) if values else ([], [])


def parse_table_number(value: object) -> tuple[str, Decimal, str] | None:
    text = _clean(value)
    if text in _MISSING_VALUES:
        return None
    match = _FULL_NUMBER_RE.fullmatch(text)
    if not match:
        return None
    normalized = _decimal(match.group("number"))
    if normalized is None:
        return None
    return match.group("number"), normalized, _canonical_unit(match.group("unit"))


def _header_unit(header: str) -> str:
    return next((unit for unit in UNITS if unit.casefold() in header.casefold()), "")


def table_numeric_facts(
    table: Mapping[str, Any],
    *,
    entity_id: str = "document",
    entity_name: str = "",
    context: str = "",
) -> tuple[NumericFact, ...]:
    """Extract facts from numeric cells in one complete table."""

    source_id = str(table.get("id") or "")
    columns, rows = table_grid(table)
    if not source_id or not columns or not rows:
        return ()
    facts: list[NumericFact] = []
    first_header = columns[0] if columns else ""
    table_unit = _header_unit(first_header)
    entity_rows = bool(re.search(r"公司|企业|可比|证券", first_header))
    for row_index, row in enumerate(rows):
        label = row[0] if row and row[0] else None
        for column_index, cell in enumerate(row):
            if column_index == 0:
                continue
            parsed = parse_table_number(cell)
            if parsed is None:
                continue
            raw_value, normalized, explicit_unit = parsed
            header = columns[column_index] if column_index < len(columns) else ""
            header_periods = period_labels(header)
            # Compound labels such as ``2024前三季度`` are still one reporting
            # period.  Treating them as a non-period category changes the scope
            # halfway through an otherwise coherent time series.
            period = (
                header_periods[0]
                if len(header_periods) == 1
                else re.sub(r"[（(][^）)]*[）)]", "", str(header)).strip()
                if header_periods
                else None
            )
            semantic_label = label if period else header
            resolved_unit = explicit_unit or _header_unit(header) or table_unit
            semantics = classify_metric(
                label=semantic_label,
                context=header,
                unit=resolved_unit,
                period=period,
            )
            if semantics.metric_key == "unknown" and context:
                fallback = classify_metric(
                    label=None,
                    context=context,
                    unit=resolved_unit,
                    period=period,
                )
                semantics = replace(
                    semantics,
                    metric_key=fallback.metric_key,
                    metric_label=fallback.metric_label,
                    measure_kind=(
                        semantics.measure_kind
                        if semantics.measure_kind != "unknown"
                        else fallback.measure_kind
                    ),
                )
            row_entity_id = entity_id
            row_entity_name = entity_name
            row_entity_type = "company"
            scope = "consolidated"
            scope_label = ""
            if entity_rows and label:
                row_entity_id = f"table:{source_id}:entity:{row_index}"
                row_entity_name = str(label)
                row_entity_type = "peer"
            elif period and label and semantics.metric_key == "unknown":
                scope = "segment"
                scope_label = str(label)
            elif not period and label:
                scope = "category"
                scope_label = str(label)
            facts.append(
                NumericFact(
                    fact_id=_fact_id(
                        "table",
                        source_id,
                        row_index=row_index,
                        column_index=column_index,
                    ),
                    source_kind="table",
                    source_id=source_id,
                    raw_value=raw_value,
                    normalized_value=normalized,
                    unit=resolved_unit,
                    label=label,
                    period=period,
                    start=None,
                    end=None,
                    row_index=row_index,
                    column_index=column_index,
                    entity_id=row_entity_id,
                    entity_name=row_entity_name,
                    entity_type=row_entity_type,
                    metric_key=semantics.metric_key,
                    metric_label=semantics.metric_label,
                    measure_kind=semantics.measure_kind,
                    unit_family=semantics.unit_family,
                    unit_scale=semantics.unit_scale,
                    currency=semantics.currency,
                    scope=scope,
                    scope_label=scope_label,
                    scenario=semantics.scenario,
                )
            )
    return tuple(facts)


@dataclass(frozen=True, slots=True)
class NumericFactLedger:
    """Read-only indexes over deterministically extracted numeric facts."""

    facts: tuple[NumericFact, ...]
    by_id: Mapping[str, NumericFact]
    by_source: Mapping[tuple[str, str], tuple[NumericFact, ...]]
    by_table_cell: Mapping[tuple[str, int, int], NumericFact]

    def get(self, fact_id: str) -> NumericFact | None:
        return self.by_id.get(fact_id)

    def for_source(self, kind: str, source_id: str) -> tuple[NumericFact, ...]:
        return self.by_source.get((kind, source_id), ())

    def table_cell(
        self, table_id: str, row_index: int, column_index: int
    ) -> NumericFact | None:
        return self.by_table_cell.get((table_id, row_index, column_index))


def _ledger(facts: Iterable[NumericFact]) -> NumericFactLedger:
    ordered = tuple(facts)
    by_id: dict[str, NumericFact] = {}
    by_source: dict[tuple[str, str], list[NumericFact]] = {}
    by_cell: dict[tuple[str, int, int], NumericFact] = {}
    for fact in ordered:
        if fact.fact_id in by_id:
            raise ValueError(f"duplicate numeric fact id: {fact.fact_id}")
        by_id[fact.fact_id] = fact
        by_source.setdefault((fact.source_kind, fact.source_id), []).append(fact)
        if (
            fact.source_kind == "table"
            and fact.row_index is not None
            and fact.column_index is not None
        ):
            by_cell[(fact.source_id, fact.row_index, fact.column_index)] = fact
    return NumericFactLedger(
        facts=ordered,
        by_id=MappingProxyType(by_id),
        by_source=MappingProxyType(
            {key: tuple(values) for key, values in by_source.items()}
        ),
        by_table_cell=MappingProxyType(by_cell),
    )


def build_numeric_fact_ledger(
    snapshot: DocumentIntelligenceSnapshot,
    evidence_refs: Sequence[tuple[str, str]] | None = None,
) -> NumericFactLedger:
    """Build a deterministic ledger for all or selected native evidence."""

    if evidence_refs is None:
        block_ids = list(snapshot.ordered_block_ids)
        table_ids = list(snapshot.tables_by_id)
    else:
        block_ids = [
            identity
            for kind, identity in evidence_refs
            if kind == "block" and identity in snapshot.blocks_by_id
        ]
        table_ids = [
            identity
            for kind, identity in evidence_refs
            if kind == "table" and identity in snapshot.tables_by_id
        ]
    facts: list[NumericFact] = []
    document_id = str(snapshot.metadata.get("id") or "document")
    document_name = str(snapshot.metadata.get("title") or "")
    for block_id in dict.fromkeys(block_ids):
        block = snapshot.blocks_by_id[block_id]
        section = snapshot.sections_by_id.get(str(block.get("section_id") or ""), {})
        title_block = snapshot.blocks_by_id.get(str(section.get("title_block_id") or ""), {})
        facts.extend(
            block_numeric_facts(
                block,
                entity_id=document_id,
                entity_name=document_name,
                context=str(title_block.get("text_raw") or ""),
            )
        )
    for table_id in dict.fromkeys(table_ids):
        table = snapshot.tables_by_id[table_id]
        context_ids = [
            *table.get("caption_block_ids", []),
            str(table.get("source_block_id") or ""),
        ]
        context = " ".join(
            str(snapshot.blocks_by_id[identity].get("text_raw") or "")
            for identity in context_ids
            if identity in snapshot.blocks_by_id
        )
        facts.extend(
            table_numeric_facts(
                table,
                entity_id=document_id,
                entity_name=document_name,
                context=context,
            )
        )
    return _ledger(facts)
