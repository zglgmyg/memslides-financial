"""Validate generated Outline references against the source DocumentBundle."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from memslides.research_pipeline.document_intelligence.figures import build_figure_inventory
from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot
from memslides.research_pipeline.outline_generator.front_matter import detect_front_matter_summary
from memslides.research_pipeline.tools.validate_outline import Issue


_CITATION_RE = re.compile(r"\[\^[^\]]+\]")
_SPACE_RE = re.compile(r"\s+")
_FIRST_SENTENCE_RE = re.compile(r"^(.+?[。！？：])")
_LEADING_LIST_MARKER_RE = re.compile(
    r"^(?:(?:[➢►▶◆◇■□●○•·▪▫\-–—])|(?:\d+|[一二三四五六七八九十]+)[）).、])\s*"
)
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{1,4}(?:[.,]\d+)*)(?:%|倍|X|x|亿元|万元|百万元|TOPS|POPS)?"
)


def _normalized_text(value: object) -> str:
    return _SPACE_RE.sub("", _CITATION_RE.sub("", str(value or ""))).strip()


def _first_topic_sentence(value: object) -> str | None:
    text = _SPACE_RE.sub(" ", _CITATION_RE.sub("", str(value or ""))).strip()
    text = _LEADING_LIST_MARKER_RE.sub("", text).strip()
    if not text:
        return None
    match = _FIRST_SENTENCE_RE.match(text)
    sentence = (match.group(1) if match else text).strip()
    # A long analytical paragraph often starts directly with supporting detail.
    # Enforce verbatim preservation only for a concise, presentation-ready lead.
    normalized_sentence = _normalized_text(sentence)
    return sentence if 6 <= len(normalized_sentence) <= 120 else None


def normalize_topic_sentence_key_messages(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
) -> int:
    """Deterministically restore concise source lead sentences before validation."""
    changes = 0
    slides = outline.get("slides", [])
    if not isinstance(slides, list):
        return changes
    for slide in slides:
        if (
            not isinstance(slide, dict)
            or slide.get("page_role") != "content"
            or slide.get("slide_type") == "figure_page"
        ):
            continue
        refs = slide.get("evidence_refs", [])
        if not isinstance(refs, list):
            continue
        topic_sentence = None
        for ref in refs:
            if not isinstance(ref, Mapping) or ref.get("kind") != "block":
                continue
            block = snapshot.blocks_by_id.get(str(ref.get("id") or ""))
            if not isinstance(block, Mapping) or str(block.get("type")) not in {
                "paragraph",
                "blockquote",
            }:
                continue
            topic_sentence = _first_topic_sentence(block.get("text_raw"))
            if topic_sentence:
                break
        if (
            topic_sentence
            and _normalized_text(slide.get("key_message"))
            != _normalized_text(topic_sentence)
        ):
            slide["key_message"] = topic_sentence
            changes += 1
    return changes


def _number_key(value: str) -> str:
    match = re.search(r"\d{1,4}(?:[.,]\d+)*", value)
    if match is None:
        return ""
    raw = match.group(0).replace(",", "")
    try:
        return str(Decimal(raw).normalize())
    except InvalidOperation:
        return raw


def _number_keys_in_text(value: object) -> set[str]:
    text = str(value or "")
    keys = {_number_key(token) for token in _NUMBER_RE.findall(text)} - {""}
    normalized = _normalized_text(text)
    for start, end in re.findall(
        r"((?:19|20)\d{2})[-—至]((?:19|20)\d{2})", normalized
    ):
        if int(start) <= int(end) <= int(start) + 20:
            keys.update(str(year) for year in range(int(start), int(end) + 1))
    return keys


def canonicalize_outline_from_bundle(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
    allowed_evidence: set[tuple[str, str]] | None = None,
) -> dict[str, int]:
    """Resolve source-owned fields and omitted citations without another LLM call.

    The model chooses slide semantics and evidence. DocumentBundle remains the
    authority for labels, headings, and whether a numeric claim is grounded.
    """

    counts = {
        "labels": 0,
        "titles": 0,
        "evidence_refs": 0,
        "null_fields": 0,
        "figure_pages_removed": 0,
    }
    slides = outline.get("slides", [])
    if not isinstance(slides, list):
        return counts

    selectable_figures = {
        str(item["figure_id"])
        for item in build_figure_inventory(snapshot)
        if item.get("selectable") is True
    }
    retained_slides: list[Any] = []
    for slide in slides:
        if isinstance(slide, dict) and slide.get("section_ref") is None:
            slide.pop("section_ref", None)
            counts["null_fields"] += 1
        refs = slide.get("evidence_refs", []) if isinstance(slide, Mapping) else []
        figure_ids = {
            str(ref.get("id") or "")
            for ref in refs
            if isinstance(ref, Mapping) and ref.get("kind") == "figure"
        } if isinstance(refs, list) else set()
        if (
            isinstance(slide, Mapping)
            and slide.get("slide_type") == "figure_page"
            and figure_ids
            and not (figure_ids & selectable_figures)
        ):
            counts["figure_pages_removed"] += 1
            continue
        retained_slides.append(slide)
    slides[:] = retained_slides

    for slide in slides:
        if not isinstance(slide, dict):
            continue
        section_id = str(slide.get("section_ref") or "")
        section = snapshot.sections_by_id.get(section_id)
        if section is None:
            continue
        title_block = snapshot.blocks_by_id.get(
            str(section.get("title_block_id") or ""), {}
        )
        canonical_title = str(title_block.get("text_raw") or "").strip()
        if canonical_title and str(slide.get("section") or "").strip() != canonical_title:
            slide["section"] = canonical_title
            counts["labels"] += 1
        if (
            canonical_title
            and slide.get("page_role") == "content"
            and slide.get("slide_type") != "figure_page"
            and str(slide.get("title") or "").strip() != canonical_title
        ):
            slide["title"] = canonical_title
            counts["titles"] += 1

        if slide.get("page_role") != "content" or slide.get("slide_type") == "figure_page":
            continue
        refs = slide.get("evidence_refs")
        if not isinstance(refs, list):
            continue
        claim_keys = set().union(
            _number_keys_in_text(slide.get("title")),
            _number_keys_in_text(slide.get("key_message")),
            *(
                _number_keys_in_text(bullet)
                for bullet in slide.get("bullet_points", [])
            ),
        )
        cited_keys: set[str] = _number_keys_in_text(canonical_title)
        existing = {
            (str(ref.get("kind") or ""), str(ref.get("id") or ""))
            for ref in refs
            if isinstance(ref, Mapping)
        }
        for kind, identity in existing:
            if kind == "block" and identity in snapshot.blocks_by_id:
                cited_keys.update(
                    _number_keys_in_text(snapshot.blocks_by_id[identity].get("text_raw"))
                )
            elif kind == "table" and identity in snapshot.tables_by_id:
                cited_keys.update(
                    _number_keys_in_text(snapshot.tables_by_id[identity].get("structure_raw"))
                )
        missing = claim_keys - cited_keys
        if not missing:
            continue

        candidates: list[tuple[str, str, set[str]]] = []
        for kind, identity in snapshot.evidence_by_key:
            key = (str(kind), str(identity))
            if key in existing or (
                allowed_evidence is not None and key not in allowed_evidence
            ):
                continue
            evidence = snapshot.evidence(*key)
            if evidence is None or not _descendant_or_same(
                snapshot, evidence.section_id, section_id
            ):
                continue
            if kind == "block" and identity in snapshot.blocks_by_id:
                text = snapshot.blocks_by_id[identity].get("text_raw")
            elif kind == "table" and identity in snapshot.tables_by_id:
                text = snapshot.tables_by_id[identity].get("structure_raw")
            else:
                continue
            candidate_keys = _number_keys_in_text(text)
            if candidate_keys & missing:
                candidates.append((str(kind), str(identity), candidate_keys))

        while missing:
            useful = [item for item in candidates if item[2] & missing]
            if not useful:
                break
            kind, identity, candidate_keys = max(
                useful, key=lambda item: len(item[2] & missing)
            )
            refs.append({"kind": kind, "id": identity})
            counts["evidence_refs"] += 1
            missing -= candidate_keys
            candidates.remove((kind, identity, candidate_keys))
    return counts


def _grounded_number_issues(
    slide: Mapping[str, Any],
    *,
    evidence_text: str,
    base: str,
) -> list[Issue]:
    available = _number_keys_in_text(evidence_text)
    issues: list[Issue] = []
    fields: list[tuple[str, object]] = [
        ("title", slide.get("title")),
        ("key_message", slide.get("key_message")),
        *[
            (f"bullet_points[{index}]", value)
            for index, value in enumerate(slide.get("bullet_points", []))
        ],
    ]
    for field, value in fields:
        for token in _NUMBER_RE.findall(str(value or "")):
            normalized = _number_key(token)
            if normalized and normalized not in available:
                issues.append(
                    Issue(
                        "error",
                        "BUNDLE.UNGROUNDED_NUMBER",
                        f"{base}.{field}",
                        f"numeric claim {token!r} is absent from the cited evidence",
                    )
                )
    return issues


def _descendant_or_same(
    snapshot: DocumentIntelligenceSnapshot,
    candidate: str | None,
    ancestor: str,
) -> bool:
    return candidate == ancestor or (
        candidate is not None and ancestor in snapshot.section_paths.get(candidate, ())
    )


def _front_matter_summary_issues(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
) -> list[Issue]:
    summary = detect_front_matter_summary(snapshot)
    if summary is None:
        return []

    slides = [
        slide if isinstance(slide, Mapping) else {}
        for slide in outline.get("slides", [])
    ]
    summary_indices = [
        index
        for index, slide in enumerate(slides)
        if slide.get("page_role") == "content"
        and slide.get("slide_type") == "summary"
        and str(slide.get("section_ref") or "") == summary.section_id
    ]
    if not summary_indices:
        return [
            Issue(
                "error",
                "BUNDLE.FRONT_SUMMARY_MISSING",
                "$.slides",
                (
                    "pre-contents highlights require a content/summary slide "
                    f"for section {summary.section_id!r} immediately after the title"
                ),
            )
        ]

    issues: list[Issue] = []
    first_non_title = next(
        (
            index
            for index, slide in enumerate(slides)
            if slide.get("page_role") != "title"
        ),
        None,
    )
    if first_non_title not in summary_indices:
        issues.append(
            Issue(
                "error",
                "BUNDLE.FRONT_SUMMARY_ORDER",
                f"$.slides[{summary_indices[0]}]",
                "pre-contents highlights must be the first non-title slide",
            )
        )

    used_block_ids = {
        str(ref.get("id") or "")
        for index in summary_indices
        for ref in slides[index].get("evidence_refs", [])
        if isinstance(ref, Mapping) and ref.get("kind") == "block"
    }
    for block_id in summary.block_ids:
        if block_id not in used_block_ids:
            issues.append(
                Issue(
                    "error",
                    "BUNDLE.FRONT_SUMMARY_EVIDENCE_MISSING",
                    "$.slides",
                    (
                        f"pre-contents highlight {block_id!r} must be cited by "
                        "a content/summary slide"
                    ),
                )
            )
    return issues


def validate_outline_evidence(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
    allowed_evidence: set[tuple[str, str]] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    section_positions = {value: index for index, value in enumerate(snapshot.section_order)}
    figure_inventory = build_figure_inventory(snapshot)
    figure_positions = {
        str(item["figure_id"]): int(item["order"]) for item in figure_inventory
    }
    selectable_figures = {
        str(item["figure_id"])
        for item in figure_inventory
        if item.get("selectable") is True
    }
    previous_figure_position = 0
    previous_position = -1
    for slide_index, slide in enumerate(outline.get("slides", [])):
        if not isinstance(slide, Mapping):
            continue
        base = f"$.slides[{slide_index}]"
        section_ref = slide.get("section_ref")
        if section_ref is not None:
            section_ref = str(section_ref)
            if section_ref not in snapshot.sections_by_id:
                issues.append(Issue("error", "BUNDLE.UNKNOWN_SECTION", f"{base}.section_ref", f"unknown section_ref {section_ref!r}"))
            else:
                position = section_positions[section_ref]
                if position < previous_position:
                    issues.append(Issue("error", "BUNDLE.SECTION_ORDER", f"{base}.section_ref", "slide section order differs from DocumentBundle"))
                previous_position = max(previous_position, position)
                section = snapshot.sections_by_id[section_ref]
                title_block = snapshot.blocks_by_id.get(str(section.get("title_block_id") or ""), {})
                canonical_title = str(title_block.get("text_raw") or "").strip()
                declared_section = str(slide.get("section") or "").strip()
                if declared_section and canonical_title and declared_section != canonical_title:
                    issues.append(Issue("error", "BUNDLE.SECTION_TITLE_MISMATCH", f"{base}.section", "slide section label must match the DocumentBundle heading"))
                if (
                    slide.get("page_role") == "content"
                    and slide.get("slide_type") != "figure_page"
                    and canonical_title
                    and slide.get("title") is not None
                    and str(slide.get("title") or "").strip() != canonical_title
                ):
                    issues.append(
                        Issue(
                            "error",
                            "BUNDLE.SECTION_SLIDE_TITLE",
                            f"{base}.title",
                            "content slide title must exactly match the DocumentBundle heading",
                        )
                    )

        role = slide.get("page_role")
        refs = slide.get("evidence_refs", [])
        slide_type = slide.get("slide_type")
        raw_visual_candidates = slide.get("visual_candidates", [])
        visual_candidates = (
            raw_visual_candidates
            if isinstance(raw_visual_candidates, list)
            else []
        )
        maximum_visuals = 2 if role == "content" else 0
        if slide_type == "figure_page":
            maximum_visuals = 0
        if len(visual_candidates) > maximum_visuals:
            issues.append(
                Issue(
                    "error",
                    "LAYOUT.VISUAL_BUDGET_EXCEEDED",
                    f"{base}.visual_candidates",
                    (
                        f"slide has {len(visual_candidates)} visual candidates; "
                        f"maximum is {maximum_visuals}. Split the content across "
                        "slides or remove lower-priority visuals"
                    ),
                )
            )
        figure_refs = [
            str(ref.get("id") or "")
            for ref in refs
            if isinstance(ref, Mapping) and ref.get("kind") == "figure"
        ] if isinstance(refs, list) else []
        if slide_type == "figure_page":
            if len(refs) != 1 or len(figure_refs) != 1:
                issues.append(
                    Issue(
                        "error",
                        "FIGURE.PAGE_REQUIRES_ONE_FIGURE",
                        f"{base}.evidence_refs",
                        "figure_page must reference exactly one figure and no other evidence",
                    )
                )
            elif figure_refs[0] not in selectable_figures:
                issues.append(
                    Issue(
                        "error",
                        "FIGURE.ASSET_UNAVAILABLE",
                        f"{base}.evidence_refs[0]",
                        f"figure {figure_refs[0]!r} has no available original asset",
                    )
                )
            else:
                current_figure_position = figure_positions[figure_refs[0]]
                figure = snapshot.figures_by_id[figure_refs[0]]
                caption_block = snapshot.blocks_by_id.get(
                    str(figure.get("caption_block_id") or ""), {}
                )
                canonical_caption = str(
                    caption_block.get("text_raw") or ""
                ).strip()
                if (
                    canonical_caption
                    and str(slide.get("title") or "").strip()
                    != canonical_caption
                ):
                    issues.append(
                        Issue(
                            "error",
                            "FIGURE.TITLE_MISMATCH",
                            f"{base}.title",
                            "figure page title must exactly match its source caption",
                        )
                    )
                if current_figure_position <= previous_figure_position:
                    issues.append(
                        Issue(
                            "error",
                            "FIGURE.ORDER",
                            f"{base}.evidence_refs[0]",
                            "figure_page order must be strictly increasing in PDF order",
                        )
                    )
                previous_figure_position = max(
                    previous_figure_position, current_figure_position
                )
        elif figure_refs:
            issues.append(
                Issue(
                    "error",
                    "FIGURE.REQUIRES_FIGURE_PAGE",
                    f"{base}.evidence_refs",
                    "figure evidence must be migrated on a dedicated figure_page",
                )
            )
        if role == "content" and not refs:
            issues.append(Issue("error", "BUNDLE.CONTENT_WITHOUT_EVIDENCE", f"{base}.evidence_refs", "content slide must reference DocumentBundle evidence"))
        if role == "content" and section_ref is None:
            issues.append(Issue("error", "BUNDLE.SLIDE_WITHOUT_SECTION", f"{base}.section_ref", f"{role} slide must reference an existing section"))
        if role == "content" and not slide.get("source_refs"):
            issues.append(Issue("error", "BUNDLE.CONTENT_WITHOUT_SOURCE", f"{base}.source_refs", "content slide must preserve source_refs"))
        if not isinstance(refs, list):
            continue
        evidence_blocks: list[Mapping[str, Any]] = []
        evidence_text_parts: list[str] = []
        for ref_index, ref in enumerate(refs):
            path = f"{base}.evidence_refs[{ref_index}]"
            if not isinstance(ref, Mapping):
                continue
            kind = str(ref.get("kind") or "")
            identity = str(ref.get("id") or "")
            evidence = snapshot.evidence(kind, identity)
            if evidence is None:
                issues.append(Issue("error", "BUNDLE.UNKNOWN_EVIDENCE", path, f"unknown {kind} evidence {identity!r}"))
            elif allowed_evidence is not None and (kind, identity) not in allowed_evidence:
                issues.append(Issue("error", "BUNDLE.EVIDENCE_NOT_IN_MEMORY", path, f"evidence {identity!r} was not preserved by Context Compression"))
            elif section_ref in snapshot.sections_by_id and not _descendant_or_same(snapshot, evidence.section_id, str(section_ref)):
                issues.append(Issue("error", "BUNDLE.CROSS_SECTION_EVIDENCE", path, f"evidence {identity!r} is outside section {section_ref!r}"))
            if evidence is not None:
                if kind == "block" and identity in snapshot.blocks_by_id:
                    block = snapshot.blocks_by_id[identity]
                    evidence_blocks.append(block)
                    evidence_text_parts.append(str(block.get("text_raw") or ""))
                elif kind == "table" and identity in snapshot.tables_by_id:
                    table = snapshot.tables_by_id[identity]
                    evidence_text_parts.append(str(table.get("structure_raw") or ""))

        for candidate_index, candidate in enumerate(visual_candidates):
            if not isinstance(candidate, Mapping):
                continue
            candidate_refs = candidate.get("evidence_refs", [])
            if not isinstance(candidate_refs, list):
                continue
            if not candidate_refs:
                issues.append(
                    Issue(
                        "error",
                        "BUNDLE.VISUAL_WITHOUT_NATIVE_EVIDENCE",
                        f"{base}.visual_candidates[{candidate_index}].evidence_refs",
                        "visual candidate must cite native block/table evidence",
                    )
                )
                continue
            for ref_index, ref in enumerate(candidate_refs):
                if not isinstance(ref, Mapping):
                    continue
                path = (
                    f"{base}.visual_candidates[{candidate_index}]"
                    f".evidence_refs[{ref_index}]"
                )
                kind = str(ref.get("kind") or "")
                identity = str(ref.get("id") or "")
                evidence = snapshot.evidence(kind, identity)
                if evidence is None:
                    issues.append(
                        Issue(
                            "error",
                            "BUNDLE.UNKNOWN_VISUAL_EVIDENCE",
                            path,
                            f"unknown {kind} evidence {identity!r}",
                        )
                    )
                elif (
                    section_ref in snapshot.sections_by_id
                    and not _descendant_or_same(
                        snapshot, evidence.section_id, str(section_ref)
                    )
                ):
                    issues.append(
                        Issue(
                            "error",
                            "BUNDLE.CROSS_SECTION_VISUAL_EVIDENCE",
                            path,
                            f"visual evidence {identity!r} is outside section {section_ref!r}",
                        )
                    )

        if role == "content" and slide_type != "figure_page":
            first_paragraph = next(
                (
                    block
                    for block in evidence_blocks
                    if str(block.get("type")) in {"paragraph", "blockquote"}
                ),
                None,
            )
            topic_sentence = (
                _first_topic_sentence(first_paragraph.get("text_raw"))
                if first_paragraph is not None
                else None
            )
            key_message = _normalized_text(slide.get("key_message"))
            if (
                topic_sentence
                and slide.get("key_message") is not None
                and key_message != _normalized_text(topic_sentence)
            ):
                issues.append(
                    Issue(
                        "error",
                        "BUNDLE.TOPIC_SENTENCE_MISMATCH",
                        f"{base}.key_message",
                        "a concise first-sentence topic statement must be preserved verbatim",
                    )
                )
            evidence_text = " ".join(evidence_text_parts)
            if section_ref in snapshot.sections_by_id:
                section = snapshot.sections_by_id[str(section_ref)]
                title_block = snapshot.blocks_by_id.get(
                    str(section.get("title_block_id") or ""), {}
                )
                evidence_text += " " + str(title_block.get("text_raw") or "")
            issues.extend(
                _grounded_number_issues(
                    slide,
                    evidence_text=evidence_text,
                    base=base,
                )
            )
    issues.extend(_front_matter_summary_issues(outline, snapshot))
    return issues
