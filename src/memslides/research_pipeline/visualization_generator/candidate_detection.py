"""Deterministic, slide-scoped visualization candidate discovery.

The locator is intentionally data-free: it identifies promising block/table
evidence and explains why it was selected, but it never emits chart values or
table cells.  Production discovery is bounded to slide evidence and a small
same-section expansion.  Corpus-wide scanning is exposed only for evaluation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from lxml import html

from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot

from .contracts import CandidateTriggerCode, VisualCandidate


MAX_SECTION_BLOCKS = 12
MAX_SECTION_TABLES = 4
_MIN_SCORE = 0.55
_PERIOD_RE = re.compile(
    r"(?:(?:19|20)\d{2}(?:年|年末|[AE])?|(?:Q[1-4]|[一二三四]季度))",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"(?<![\d.])"
    r"([+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(万颗|百万元|亿元|万元|元|%|倍|颗|GW|MW)?",
    re.IGNORECASE,
)
_METRIC_RE = re.compile(
    r"同比|环比|增长率|增速|CAGR|毛利率|净利率|市占率|占比|份额|"
    r"营收|收入|利润|现金流|市场规模|销量|产量",
    re.IGNORECASE,
)
_ADMIN_RE = re.compile(
    r"页码|第\s*\d+\s*页|股票代码|证券代码|报告编号|发布日期|报告日期",
    re.IGNORECASE,
)
_MISSING_VALUES = frozenset({"", "-", "--", "—", "N/A", "n/a", "NA", "null"})
_MARKDOWN_RE = re.compile(r"[*_`~]+")


def _clean(value: object) -> str:
    text = _MARKDOWN_RE.sub("", str(value or ""))
    return " ".join(text.replace("\u3000", " ").split())


def _overlaps(span: tuple[int, int], spans: Sequence[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in spans)


def _observations(text: str) -> list[tuple[float, str, int, int]]:
    period_spans = [match.span() for match in _PERIOD_RE.finditer(text)]
    has_metric = bool(_METRIC_RE.search(text))
    result: list[tuple[float, str, int, int]] = []
    for match in _NUMBER_RE.finditer(text):
        if _overlaps(match.span(1), period_spans):
            continue
        raw = match.group(1).replace(",", "")
        unit = match.group(2) or ""
        if not unit and not has_metric:
            continue
        try:
            number = float(raw)
        except ValueError:
            continue
        result.append((number, unit.casefold(), match.start(1), match.end(1)))
    return result


def _candidate_id(
    slide_id: str | None,
    visual_type: str,
    chart_intent: str | None,
    evidence_refs: Sequence[tuple[str, str]],
) -> str:
    payload = json.dumps(
        {
            "slide_id": slide_id,
            "visual_type": visual_type,
            "chart_intent": chart_intent,
            "evidence_refs": list(evidence_refs),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"cand_{digest}"


def _make_candidate(
    *,
    slide_id: str | None,
    visual_type: str,
    chart_intent: str | None,
    evidence_ref: tuple[str, str],
    triggers: Iterable[CandidateTriggerCode],
    score: float,
    excerpt: str,
) -> VisualCandidate | None:
    trigger_ids = tuple(dict.fromkeys(code.value for code in triggers))
    bounded_score = min(1.0, round(score, 4))
    if bounded_score < _MIN_SCORE:
        return None
    refs = (evidence_ref,)
    return VisualCandidate(
        candidate_id=_candidate_id(slide_id, visual_type, chart_intent, refs),
        slide_id=slide_id,
        visual_type=visual_type,
        chart_intent=chart_intent,
        evidence_refs=refs,
        trigger_ids=trigger_ids,
        score=bounded_score,
        excerpt=excerpt[:400],
    )


def _block_candidate(
    block: Mapping[str, Any],
    *,
    slide_id: str | None,
) -> VisualCandidate | None:
    if str(block.get("type") or "") not in {"paragraph", "blockquote", "list_item"}:
        return None
    identity = str(block.get("id") or "")
    text = _clean(block.get("text_raw"))
    if not identity or not text:
        return None

    observations = _observations(text)
    if len(observations) < 2:
        return None
    explicit_units = {unit for _, unit, _, _ in observations if unit}
    percentages = [number for number, unit, _, _ in observations if unit == "%"]
    composition = (
        3 <= len(percentages) <= 6
        and len(percentages) == len(observations)
        and all(value >= 0 for value in percentages)
        and 95.0 <= sum(percentages) <= 105.0
    )
    if len(explicit_units) > 1 and not composition:
        return None
    if _ADMIN_RE.search(text) and not _METRIC_RE.search(text) and not explicit_units:
        return None

    periods = tuple(dict.fromkeys(match.group(0) for match in _PERIOD_RE.finditer(text)))
    has_labels = bool(re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", text))
    if not has_labels:
        return None

    triggers: list[CandidateTriggerCode] = [
        CandidateTriggerCode.COMPARABLE_NUMBERS,
        CandidateTriggerCode.LABELS_COLOCATED,
    ]
    score = 0.25 + 0.15
    intent: str
    if composition:
        intent = "composition"
        triggers.append(CandidateTriggerCode.COMPOSITION)
        score += 0.30
    elif len(periods) >= 4:
        intent = "trend"
        triggers.append(CandidateTriggerCode.TIME_SERIES)
        score += 0.30
    else:
        intent = "comparison"
        triggers.append(CandidateTriggerCode.CATEGORY_COMPARISON)
        score += 0.25
    if _METRIC_RE.search(text):
        triggers.append(CandidateTriggerCode.METRIC_KEYWORD)
        score += 0.10

    return _make_candidate(
        slide_id=slide_id,
        visual_type="chart",
        chart_intent=intent,
        evidence_ref=("block", identity),
        triggers=triggers,
        score=score,
        excerpt=text,
    )


def _table_grid(table: Mapping[str, Any]) -> tuple[list[str], list[list[str]]]:
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


def _parse_cell_number(value: str) -> tuple[float, str] | None:
    text = _clean(value)
    if text in _MISSING_VALUES:
        return None
    match = _NUMBER_RE.fullmatch(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "")), (match.group(2) or "").casefold()
    except ValueError:
        return None


def _table_candidate(
    table: Mapping[str, Any],
    *,
    slide_id: str | None,
) -> VisualCandidate | None:
    identity = str(table.get("id") or "")
    columns, rows = _table_grid(table)
    if not identity or len(columns) < 2 or not rows:
        return None

    max_width = max([len(columns), *(len(row) for row in rows)])
    numeric_columns: dict[int, list[tuple[float, str]]] = {}
    for column_index in range(1, max_width):
        values = [
            parsed
            for row in rows
            if column_index < len(row)
            and (parsed := _parse_cell_number(row[column_index])) is not None
        ]
        if values:
            numeric_columns[column_index] = values
    labels = [row[0] for row in rows if row and row[0] and _parse_cell_number(row[0]) is None]
    if not numeric_columns or not labels:
        return None

    all_values = [item for values in numeric_columns.values() for item in values]
    if len(all_values) < 2:
        return None
    units_by_column = [
        {unit for _, unit in values if unit}
        for values in numeric_columns.values()
    ]
    all_explicit_units = set().union(*units_by_column)
    mixed_units = (
        any(len(units) > 1 for units in units_by_column)
        or len(all_explicit_units) > 1
    )
    period_columns = [
        index
        for index in numeric_columns
        if index < len(columns) and _PERIOD_RE.search(columns[index])
    ]
    composition_column = None
    for index in numeric_columns:
        values = [
            parsed
            for row in rows
            if row
            and not re.search(r"合计|总计|total", str(row[0]), re.IGNORECASE)
            and index < len(row)
            and (parsed := _parse_cell_number(row[index])) is not None
        ]
        if (
            3 <= len(values) <= 6
            and (
                all(unit == "%" for _, unit in values)
                or (
                    all(not unit for _, unit in values)
                    and index < len(columns)
                    and bool(re.search(r"%|占比|份额|构成", columns[index]))
                )
            )
            and all(number >= 0 for number, _ in values)
            and 95.0 <= sum(number for number, _ in values) <= 105.0
        ):
            composition_column = index
            break

    triggers: list[CandidateTriggerCode] = [
        CandidateTriggerCode.COMPLETE_TABLE,
        CandidateTriggerCode.COMPARABLE_NUMBERS,
        CandidateTriggerCode.LABELS_COLOCATED,
    ]
    score = 0.25 + 0.25 + 0.15
    if composition_column is not None:
        visual_type = "chart"
        intent = "composition"
        triggers.append(CandidateTriggerCode.COMPOSITION)
        score += 0.25
    elif len(period_columns) >= 4 and not mixed_units:
        visual_type = "chart"
        intent = "trend"
        triggers.append(CandidateTriggerCode.TIME_SERIES)
        score += 0.25
    elif (
        len(labels) >= 2
        and len(numeric_columns) <= 3
        and len(rows) <= 8
        and not mixed_units
    ):
        visual_type = "chart"
        intent = "comparison"
        triggers.append(CandidateTriggerCode.CATEGORY_COMPARISON)
        score += 0.20
    else:
        visual_type = "table"
        intent = None

    text = " | ".join(
        [*columns, *(str(cell) for row in rows[:8] for cell in row)]
    )
    if _METRIC_RE.search(text):
        triggers.append(CandidateTriggerCode.METRIC_KEYWORD)
        score += 0.10
    return _make_candidate(
        slide_id=slide_id,
        visual_type=visual_type,
        chart_intent=intent,
        evidence_ref=("table", identity),
        triggers=triggers,
        score=score,
        excerpt=text,
    )


def _detect_ref(
    ref: tuple[str, str],
    snapshot: DocumentIntelligenceSnapshot,
    *,
    slide_id: str | None,
) -> VisualCandidate | None:
    kind, identity = ref
    if kind == "block":
        block = snapshot.blocks_by_id.get(identity)
        return _block_candidate(block, slide_id=slide_id) if block is not None else None
    if kind == "table":
        table = snapshot.tables_by_id.get(identity)
        return _table_candidate(table, slide_id=slide_id) if table is not None else None
    return None


def _refs(values: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    result: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        kind = str(value.get("kind") or "")
        identity = str(value.get("id") or "")
        if kind in {"block", "table"} and identity:
            result.append((kind, identity))
    return tuple(dict.fromkeys(result))


def _direct_refs(
    slide: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for ref in _refs(slide.get("evidence_refs")):
        if snapshot.evidence(*ref) is None:
            continue
        result.append(ref)
        if ref[0] == "block":
            result.extend(
                ("table", table_id)
                for table_id in snapshot.block_table_ids.get(ref[1], ())
                if table_id in snapshot.tables_by_id
            )
    return tuple(dict.fromkeys(result))


def _same_section_refs(
    slide: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
    direct_refs: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    section_ids: list[str] = []
    explicit_section = str(slide.get("section_ref") or "")
    if explicit_section in snapshot.sections_by_id:
        section_ids.append(explicit_section)
    for ref in direct_refs:
        evidence = snapshot.evidence(*ref)
        if evidence is not None and evidence.section_id:
            section_ids.append(evidence.section_id)
    section_ids = list(dict.fromkeys(section_ids))
    if not section_ids:
        return ()

    direct_block_ids = {identity for kind, identity in direct_refs if kind == "block"}
    blocks: list[str] = []
    for section_id in section_ids:
        section = snapshot.sections_by_id[section_id]
        ordered = [
            str(identity)
            for identity in section.get("content_block_ids", [])
            if str(identity) in snapshot.blocks_by_id
        ]
        if direct_block_ids:
            anchor_indices = [
                index for index, identity in enumerate(ordered) if identity in direct_block_ids
            ]
            if anchor_indices:
                center = anchor_indices[0]
                start = max(0, center - MAX_SECTION_BLOCKS // 2)
                ordered = ordered[start : start + MAX_SECTION_BLOCKS]
        blocks.extend(ordered[:MAX_SECTION_BLOCKS])
    blocks = list(dict.fromkeys(blocks))[:MAX_SECTION_BLOCKS]

    tables: list[str] = []
    for block_id in blocks:
        tables.extend(snapshot.block_table_ids.get(block_id, ()))
    for section_id in section_ids:
        tables.extend(
            identity
            for identity, table in snapshot.tables_by_id.items()
            if str(table.get("section_id") or "") == section_id
        )
    tables = list(dict.fromkeys(tables))[:MAX_SECTION_TABLES]
    refs = [
        *(("block", identity) for identity in blocks),
        *(("table", identity) for identity in tables),
    ]
    direct_set = set(direct_refs)
    return tuple(ref for ref in refs if ref not in direct_set)


def _deduplicate(candidates: Iterable[VisualCandidate]) -> list[VisualCandidate]:
    result: dict[
        tuple[tuple[tuple[str, str], ...], str, str | None],
        VisualCandidate,
    ] = {}
    for candidate in candidates:
        key = (
            tuple(sorted(candidate.evidence_refs)),
            candidate.visual_type,
            candidate.chart_intent,
        )
        previous = result.get(key)
        if previous is None or candidate.score > previous.score:
            result[key] = candidate
    return sorted(
        result.values(),
        key=lambda item: (-item.score, item.evidence_refs, item.candidate_id),
    )


def locate_visual_candidates(
    slide: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
) -> list[VisualCandidate]:
    """Locate candidates within slide evidence and bounded same-section context."""

    if slide.get("slide_type") == "figure_page":
        return []
    if slide.get("page_role") not in {None, "content"}:
        return []
    slide_id = str(slide.get("slide_id") or "") or None
    direct_refs = _direct_refs(slide, snapshot)
    direct = _deduplicate(
        candidate
        for ref in direct_refs
        if (candidate := _detect_ref(ref, snapshot, slide_id=slide_id)) is not None
    )
    if direct:
        return direct
    expanded_refs = _same_section_refs(slide, snapshot, direct_refs)
    return _deduplicate(
        candidate
        for ref in expanded_refs
        if (candidate := _detect_ref(ref, snapshot, slide_id=slide_id)) is not None
    )


def locate_corpus_candidates(
    snapshot: DocumentIntelligenceSnapshot,
) -> list[VisualCandidate]:
    """Scan every evidence unit independently for offline evaluation only."""

    refs = [
        *(("block", identity) for identity in snapshot.ordered_block_ids),
        *(("table", identity) for identity in snapshot.tables_by_id),
    ]
    return _deduplicate(
        candidate
        for ref in refs
        if (candidate := _detect_ref(ref, snapshot, slide_id=None)) is not None
    )
