"""Deterministic few-shot case loading and rule-based selection.

This module deliberately has no memory, embedding, vector search, or automatic
learning behavior. Cases are static project resources selected only from
observable Document Intelligence features.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from memslides.research_pipeline.document_intelligence.figures import build_figure_inventory
from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot


CASE_LIBRARY_VERSION = "3.0.0"
SELECTOR_VERSION = "rule-based-v1"
DEFAULT_MAX_SELECTED_CASES = 3

_NUMBERED_ITEM = re.compile(r"(?:^|[\s。；;])(?:\d{1,2}|[一二三四五六七八九十]+)[）)]")
_FINANCIAL_TERMS = re.compile(
    r"盈利预测|财务预测|营业收入|归母净利润|净利润|每股收益|EPS|PE|PB|预测年度",
    re.IGNORECASE,
)
_VALUATION_TERMS = re.compile(
    r"估值|目标价|市盈率|市净率|DCF|PE|PB|可比公司",
    re.IGNORECASE,
)
_RISK_TERMS = re.compile(r"风险提示|不及预期|竞争加剧|政策风险|经营风险")


class FewShotCaseError(ValueError):
    """Raised when the static case library is invalid."""


@dataclass(frozen=True, slots=True)
class CaseSelection:
    """Selected prompt cases plus an application-owned explainability trace."""

    prompt_payload: Mapping[str, Any]
    trace: Mapping[str, Any]


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FewShotCaseError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FewShotCaseError(
            f"{label} is not valid JSON: {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise FewShotCaseError(f"{label} root must be a JSON object: {path}")
    return value


def _validation_details(
    instance: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> list[str]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    details: list[str] = []
    for error in errors[:20]:
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        details.append(f"{location}: {error.message}")
    return details


def load_case_library(path: Path) -> dict[str, Any]:
    """Load a case directory, or normalize the legacy monolithic JSON file."""

    if path.is_file():
        legacy = _read_json(path, "Few-shot examples")
        examples = legacy.get("examples")
        if not isinstance(examples, list):
            raise FewShotCaseError(
                f"Legacy few-shot file must contain an examples array: {path}"
            )
        cases = []
        for index, example in enumerate(examples, 1):
            if not isinstance(example, Mapping):
                raise FewShotCaseError(
                    f"Legacy few-shot example #{index} must be an object: {path}"
                )
            cases.append(
                {
                    "schema_version": "legacy",
                    "id": f"legacy_{index:03d}",
                    "name": str(example.get("name") or f"Legacy case {index}"),
                    "category": "legacy",
                    "priority": 0,
                    "match": {"all": ["always"], "any": [], "none": []},
                    "guidance": {
                        "applicable_when": ["Legacy compatibility input"],
                        "recommended": [str(example.get("lesson") or "Follow the example")],
                        "avoid": [],
                    },
                    "example": dict(example),
                }
            )
        return {
            "schema_version": CASE_LIBRARY_VERSION,
            "source_mode": "legacy_file",
            "max_selected_cases": len(cases),
            "cases": cases,
        }

    if not path.is_dir():
        raise FewShotCaseError(f"Few-shot case path not found: {path}")

    schema_path = path / "case.schema.json"
    schema = _read_json(schema_path, "Few-shot case schema")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise FewShotCaseError(
            f"Few-shot case schema is invalid: {schema_path}: {exc.message}"
        ) from exc

    manifest = _read_json(path / "manifest.json", "Few-shot case manifest")
    if manifest.get("schema_version") != CASE_LIBRARY_VERSION:
        raise FewShotCaseError(
            "Few-shot manifest schema_version must be "
            f"{CASE_LIBRARY_VERSION}: {path / 'manifest.json'}"
        )
    max_selected = manifest.get(
        "max_selected_cases", DEFAULT_MAX_SELECTED_CASES
    )
    if not isinstance(max_selected, int) or max_selected < 1:
        raise FewShotCaseError("max_selected_cases must be a positive integer")

    case_root = path / "cases"
    case_paths = sorted(case_root.rglob("*.json")) if case_root.is_dir() else []
    if not case_paths:
        raise FewShotCaseError(f"No few-shot cases found under: {case_root}")

    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for case_path in case_paths:
        case = _read_json(case_path, "Few-shot case")
        details = _validation_details(case, schema)
        if details:
            raise FewShotCaseError(
                f"Few-shot case failed schema validation: {case_path}\n- "
                + "\n- ".join(details)
            )
        case_id = str(case["id"])
        if case_id in seen_ids:
            raise FewShotCaseError(f"Duplicate few-shot case id: {case_id}")
        seen_ids.add(case_id)
        cases.append(case)

    return {
        "schema_version": CASE_LIBRARY_VERSION,
        "source_mode": "case_directory",
        "max_selected_cases": max_selected,
        "cases": cases,
    }


def _document_text(snapshot: DocumentIntelligenceSnapshot) -> str:
    return "\n".join(
        str(snapshot.blocks_by_id[block_id].get("text_raw") or "")
        for block_id in snapshot.ordered_block_ids
    )


def detect_case_features(snapshot: DocumentIntelligenceSnapshot) -> tuple[str, ...]:
    """Return stable boolean feature names derived only from source structure."""

    text = _document_text(snapshot)
    paragraphs = [
        str(block.get("text_raw") or "")
        for block in snapshot.blocks_by_id.values()
        if block.get("type") == "paragraph"
    ]
    numbered_paragraphs = [
        paragraph for paragraph in paragraphs if _NUMBERED_ITEM.search(paragraph)
    ]
    figure_inventory = build_figure_inventory(snapshot)
    features = {"always"}
    if any(
        str(snapshot.blocks_by_id.get(str(section.get("title_block_id")), {}).get("text_raw") or "")
        for section in snapshot.sections_by_id.values()
    ):
        features.add("has_section_titles")
    if numbered_paragraphs:
        features.add("has_numbered_paragraph")
    if any(len(paragraph) <= 500 for paragraph in numbered_paragraphs):
        features.add("has_short_numbered_paragraph")
    if any(len(paragraph) > 500 for paragraph in paragraphs):
        features.add("has_long_paragraph")
    if snapshot.figures_by_id:
        features.add("has_figures")
    if any(item.get("selectable") is True for item in figure_inventory):
        features.add("has_selectable_figures")
    if snapshot.tables_by_id:
        features.add("has_tables")
    if any(len(table.get("fragments") or []) > 1 for table in snapshot.tables_by_id.values()):
        features.add("has_multi_fragment_table")
    if _FINANCIAL_TERMS.search(text):
        features.add("has_financial_forecast_terms")
    if _VALUATION_TERMS.search(text):
        features.add("has_valuation_terms")
    if _RISK_TERMS.search(text):
        features.add("has_risk_terms")
    return tuple(sorted(features))


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)


def _match_case(
    case: Mapping[str, Any],
    detected_features: set[str],
) -> tuple[bool, list[str], list[str]]:
    match = case.get("match")
    match = match if isinstance(match, Mapping) else {}
    required = _string_list(match.get("all"))
    alternatives = _string_list(match.get("any"))
    excluded = _string_list(match.get("none"))

    missing = [feature for feature in required if feature not in detected_features]
    forbidden = [feature for feature in excluded if feature in detected_features]
    any_matched = [
        feature for feature in alternatives if feature in detected_features
    ]
    matched = [
        feature for feature in (*required, *any_matched)
        if feature in detected_features
    ]
    is_match = not missing and not forbidden and (
        not alternatives or bool(any_matched)
    )
    reasons = []
    if missing:
        reasons.append("missing:" + ",".join(missing))
    if forbidden:
        reasons.append("excluded_by:" + ",".join(forbidden))
    if alternatives and not any_matched:
        reasons.append("no_any_feature_matched")
    return is_match, matched, reasons


def select_cases(
    snapshot: DocumentIntelligenceSnapshot,
    library: Mapping[str, Any],
) -> CaseSelection:
    """Select prompt cases by deterministic feature rules and priority."""

    detected = set(detect_case_features(snapshot))
    raw_cases = library.get("cases")
    if not isinstance(raw_cases, Sequence):
        raise FewShotCaseError("Few-shot library must contain a cases array")
    max_selected = library.get(
        "max_selected_cases", DEFAULT_MAX_SELECTED_CASES
    )
    if not isinstance(max_selected, int) or max_selected < 1:
        raise FewShotCaseError("max_selected_cases must be a positive integer")

    matched_cases: list[tuple[int, str, dict[str, Any], list[str]]] = []
    evaluated: list[dict[str, Any]] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise FewShotCaseError("Each few-shot case must be an object")
        case = dict(raw_case)
        case_id = str(case.get("id") or "")
        priority = int(case.get("priority") or 0)
        is_match, matched_features, reasons = _match_case(case, detected)
        evaluated.append(
            {
                "case_id": case_id,
                "matched": is_match,
                "matched_features": matched_features,
                "rejection_reasons": reasons,
            }
        )
        if is_match:
            matched_cases.append((priority, case_id, case, matched_features))

    matched_cases.sort(key=lambda item: (-item[0], item[1]))
    selected = matched_cases[:max_selected]
    selected_cases = [item[2] for item in selected]
    selected_trace = [
        {
            "case_id": item[1],
            "category": item[2].get("category"),
            "priority": item[0],
            "matched_features": item[3],
        }
        for item in selected
    ]
    trace = {
        "selector_version": SELECTOR_VERSION,
        "library_schema_version": library.get("schema_version"),
        "source_mode": library.get("source_mode", "in_memory"),
        "detected_features": sorted(detected),
        "max_selected_cases": max_selected,
        "selected_cases": selected_trace,
        "matching_cases_not_selected": [
            item[1] for item in matched_cases[max_selected:]
        ],
        "evaluated_cases": evaluated,
    }
    prompt_payload = {
        "schema_version": CASE_LIBRARY_VERSION,
        "selection_policy": (
            "Cases are lower-priority examples selected deterministically from "
            "document features; they never override source evidence or hard rules."
        ),
        "cases": selected_cases,
    }
    return CaseSelection(prompt_payload=prompt_payload, trace=trace)
