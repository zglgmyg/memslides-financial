"""Semantic visualization planning over Slide Outline evidence.

This layer decides what a visual should communicate.  It never extracts or
emits chart values, table cells, or asset paths.  LLM-produced Outline
``visual_candidates`` are treated as suggestions and every native evidence
reference is checked against Document Intelligence before generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot

from .candidate_detection import locate_visual_candidates


CANDIDATE_MODES = frozenset({"active", "shadow", "disabled"})


class VisualizationPlanningError(ValueError):
    """Raised when a semantic plan references nonexistent bundle evidence."""


@dataclass(frozen=True, slots=True)
class VisualizationPlan:
    slide_id: str
    visualization_id: str
    visual_type: str
    purpose: str
    chart_intent: str | None
    data_requirement: Mapping[str, str]
    evidence_refs: tuple[tuple[str, str], ...]
    source_refs: tuple[str, ...]


def _refs(values: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    result: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        kind = str(value.get("kind") or "")
        identity = str(value.get("id") or "")
        if kind and identity:
            result.append((kind, identity))
    return tuple(dict.fromkeys(result))


def _source_refs(slide: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[str, ...]:
    values = candidate.get("source_refs") or slide.get("source_refs") or []
    return tuple(dict.fromkeys(str(value) for value in values if isinstance(value, str)))


def _intent(candidate: Mapping[str, Any]) -> str | None:
    value = str(candidate.get("chart_intent") or "").strip().lower()
    if value in {"trend", "comparison", "composition", "relationship"}:
        return value
    description = str(
        candidate.get("purpose") or candidate.get("description") or ""
    ).casefold()
    if any(token in description for token in ("占比", "构成", "份额", "composition")):
        return "composition"
    if any(token in description for token in ("趋势", "变化", "增长", "trend", "cagr")):
        return "trend"
    if any(token in description for token in ("对比", "比较", "排名", "comparison")):
        return "comparison"
    return None


def _require_valid_evidence(
    snapshot: DocumentIntelligenceSnapshot,
    refs: Sequence[tuple[str, str]],
) -> None:
    for kind, identity in refs:
        if kind not in {"block", "table", "figure"}:
            raise VisualizationPlanningError(f"Unsupported evidence kind: {kind}")
        if snapshot.evidence(kind, identity) is None:
            raise VisualizationPlanningError(
                f"Visualization plan references unknown {kind}: {identity}"
            )


def plan_visualizations(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
    *,
    candidate_mode: str = "active",
) -> list[VisualizationPlan]:
    """Merge validated Outline suggestions with proactively located candidates."""

    if candidate_mode not in CANDIDATE_MODES:
        raise VisualizationPlanningError(
            "candidate_mode must be one of: active, shadow, disabled"
        )

    plans: list[VisualizationPlan] = []
    auto_index = 1
    for slide in outline.get("slides", []):
        if not isinstance(slide, Mapping):
            continue
        slide_id = str(slide.get("slide_id") or "")
        slide_refs = _refs(slide.get("evidence_refs"))
        _require_valid_evidence(snapshot, slide_refs)
        candidates = [
            value
            for value in slide.get("visual_candidates", [])
            if isinstance(value, Mapping)
        ]
        planned_keys: set[
            tuple[str, tuple[tuple[str, str], ...], str | None]
        ] = set()
        outline_evidence: set[tuple[str, str]] = set()
        slide_plan_count = 0
        for candidate in candidates:
            forbidden = {"values", "categories", "series", "columns", "rows", "asset_path"} & set(candidate)
            if forbidden:
                raise VisualizationPlanningError(
                    "Visualization planning suggestion contains deterministic data fields: "
                    + ", ".join(sorted(forbidden))
                )
            visual_type = str(candidate.get("type") or "")
            if visual_type not in {"chart", "table", "image"}:
                raise VisualizationPlanningError(
                    f"Unsupported visual candidate type: {visual_type or '<empty>'}"
                )
            candidate_refs = _refs(candidate.get("evidence_refs"))
            if not candidate_refs:
                candidate_refs = slide_refs
            _require_valid_evidence(snapshot, candidate_refs)
            chart_intent = None if visual_type == "image" else _intent(candidate)
            purpose = str(candidate.get("purpose") or candidate.get("description") or slide.get("key_message") or slide.get("title") or "").strip()
            requirement = candidate.get("data_requirement")
            requirement_items = requirement.items() if isinstance(requirement, Mapping) else ()
            plan_ref_groups = (candidate_refs,)
            if visual_type == "image":
                if not candidate.get("evidence_refs"):
                    raise VisualizationPlanningError(
                        "Image visual candidates require explicit figure evidence"
                    )
                if not 1 <= len(candidate_refs) <= 2 or any(
                    kind != "figure" for kind, _ in candidate_refs
                ):
                    raise VisualizationPlanningError(
                        "Image visual candidates require one or two figure references"
                    )
                plan_ref_groups = tuple((ref,) for ref in candidate_refs)
            base_visualization_id = str(
                candidate.get("candidate_id") or f"visual_auto_{auto_index:03d}"
            )
            for image_index, plan_refs in enumerate(plan_ref_groups, start=1):
                key = (visual_type, tuple(sorted(plan_refs)), chart_intent)
                if key in planned_keys:
                    continue
                visualization_id = base_visualization_id
                if len(plan_ref_groups) > 1:
                    visualization_id += f"_{image_index:02d}"
                plans.append(
                    VisualizationPlan(
                        slide_id=slide_id,
                        visualization_id=visualization_id,
                        visual_type=visual_type,
                        purpose=purpose,
                        chart_intent=chart_intent,
                        data_requirement={
                            str(key): str(value)
                            for key, value in requirement_items
                            if isinstance(key, str)
                        },
                        evidence_refs=plan_refs,
                        source_refs=_source_refs(slide, candidate),
                    )
                )
                planned_keys.add(key)
                auto_index += 1
                slide_plan_count += 1
            outline_evidence.update(candidate_refs)
            for kind, identity in candidate_refs:
                if kind == "block":
                    outline_evidence.update(
                        ("table", table_id)
                        for table_id in snapshot.block_table_ids.get(identity, ())
                    )

        # Dedicated figure pages remain a compatibility path for the small
        # subset of dense source figures that genuinely need a full slide.
        if slide.get("slide_type") == "figure_page":
            figure_refs = tuple(ref for ref in slide_refs if ref[0] == "figure")
            if candidates or len(figure_refs) != 1 or len(slide_refs) != 1:
                raise VisualizationPlanningError(
                    "figure_page requires exactly one figure evidence ref and no visual candidates"
                )
            plans.append(
                VisualizationPlan(
                    slide_id=slide_id,
                    visualization_id=f"visual_auto_{auto_index:03d}",
                    visual_type="image",
                    purpose=str(slide.get("key_message") or slide.get("title") or "原始研报图片"),
                    chart_intent=None,
                    data_requirement={},
                    evidence_refs=figure_refs,
                    source_refs=tuple(
                        str(value)
                        for value in slide.get("source_refs", [])
                        if isinstance(value, str)
                    ),
                )
            )
            auto_index += 1
            continue

        # Explicit Outline suggestions win for evidence they already cover.
        # The active locator contributes only new, slide-scoped evidence and
        # therefore cannot silently reinterpret the same source as another
        # visual type.
        located_candidates = (
            locate_visual_candidates(slide, snapshot)
            if candidate_mode != "disabled"
            else []
        )
        if candidate_mode != "active":
            continue
        for located in located_candidates:
            if slide_plan_count >= 2:
                break
            key = (
                located.visual_type,
                tuple(sorted(located.evidence_refs)),
                located.chart_intent,
            )
            if key in planned_keys or outline_evidence.intersection(located.evidence_refs):
                continue
            _require_valid_evidence(snapshot, located.evidence_refs)
            purpose = str(
                slide.get("key_message")
                or slide.get("title")
                or "Evidence-backed visualization"
            ).strip()
            plans.append(
                VisualizationPlan(
                    slide_id=slide_id,
                    visualization_id=located.candidate_id,
                    visual_type=located.visual_type,
                    purpose=purpose,
                    chart_intent=located.chart_intent,
                    data_requirement={},
                    evidence_refs=located.evidence_refs,
                    source_refs=_source_refs(slide, {}),
                )
            )
            planned_keys.add(key)
            slide_plan_count += 1
    return plans


def build_candidate_report(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
    *,
    candidate_mode: str,
) -> dict[str, Any]:
    """Return an auditable report without changing the generated deck in shadow mode."""

    if candidate_mode not in CANDIDATE_MODES:
        raise VisualizationPlanningError(
            "candidate_mode must be one of: active, shadow, disabled"
        )
    entries: list[dict[str, Any]] = []
    if candidate_mode != "disabled":
        for slide in outline.get("slides", []):
            if not isinstance(slide, Mapping):
                continue
            for candidate in locate_visual_candidates(slide, snapshot):
                entries.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "slide_id": candidate.slide_id,
                        "visual_type": candidate.visual_type,
                        "chart_intent": candidate.chart_intent,
                        "evidence_refs": [
                            {"kind": kind, "id": identity}
                            for kind, identity in candidate.evidence_refs
                        ],
                        "trigger_ids": list(candidate.trigger_ids),
                        "score": candidate.score,
                        "excerpt": candidate.excerpt,
                        "selected": candidate_mode == "active",
                        "decision_reason": (
                            "candidate_locator_active"
                            if candidate_mode == "active"
                            else "candidate_locator_shadow_only"
                        ),
                    }
                )
    return {
        "schema_version": "1.0.0",
        "mode": candidate_mode,
        "candidate_count": len(entries),
        "selected_count": sum(bool(item["selected"]) for item in entries),
        "candidates": entries,
    }
