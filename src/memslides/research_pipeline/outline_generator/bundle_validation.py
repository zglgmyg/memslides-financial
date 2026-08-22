"""Validate generated Outline references against the source DocumentBundle."""

from __future__ import annotations

import re
from collections import defaultdict
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


def _concise_takeaway_title(value: object, *, maximum_chars: int = 42) -> str:
    """Create a short, source-faithful display title from a key message."""

    text = _SPACE_RE.sub(" ", _CITATION_RE.sub("", str(value or ""))).strip()
    text = _LEADING_LIST_MARKER_RE.sub("", text).strip()
    if not text:
        return ""
    sentence = re.split(r"[。！？；;]", text, maxsplit=1)[0].strip(" ，,")
    if len(_normalized_text(sentence)) <= maximum_chars:
        return sentence

    clauses = [
        clause.strip()
        for clause in re.split(r"[，,]", sentence)
        if clause.strip()
    ]
    if len(clauses) > 1 and len(_normalized_text(clauses[0])) <= 8:
        clauses = clauses[1:]
    selected: list[str] = []
    for clause in clauses:
        candidate = "，".join([*selected, clause])
        if selected and len(_normalized_text(candidate)) > maximum_chars:
            break
        selected.append(clause)
        if len(_normalized_text(candidate)) >= maximum_chars:
            break
    result = "，".join(selected).strip()
    if result and len(_normalized_text(result)) <= maximum_chars:
        return result
    compact = sentence.strip()
    return compact[: maximum_chars - 1].rstrip(" ，,") + "…"


def normalize_repeated_content_titles(outline: Mapping[str, Any]) -> int:
    """Keep section labels verbatim while making repeated slide titles distinct.

    The first content slide in a source section may retain the source heading.
    Later slides that repeat that title use their grounded key message as the
    audience-facing takeaway title. Summary and figure pages keep their
    application-owned titles.
    """

    slides = outline.get("slides", [])
    if not isinstance(slides, list):
        return 0
    seen_by_section: dict[str, set[str]] = defaultdict(set)
    changes = 0
    for slide in slides:
        if (
            not isinstance(slide, dict)
            or slide.get("page_role") != "content"
            or slide.get("slide_type") in {"summary", "figure_page"}
        ):
            continue
        section_ref = str(slide.get("section_ref") or "")
        title_key = _normalized_text(slide.get("title"))
        seen = seen_by_section[section_ref]
        if title_key and title_key not in seen:
            seen.add(title_key)
            continue
        replacement = _concise_takeaway_title(slide.get("key_message"))
        replacement_key = _normalized_text(replacement)
        if replacement_key and replacement_key not in seen:
            slide["title"] = replacement
            seen.add(replacement_key)
            changes += 1
        elif title_key:
            seen.add(title_key)
    return changes


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
        "figure_titles": 0,
        "figure_sections": 0,
        "figure_order": 0,
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

    figure_inventory = build_figure_inventory(snapshot)
    figures_by_id = {
        str(item["figure_id"]): item for item in figure_inventory
    }
    figure_slots: list[int] = []
    figure_slides: list[dict[str, Any]] = []
    figure_id_by_object: dict[int, str] = {}
    for index, slide in enumerate(slides):
        if isinstance(slide, dict) and slide.get("slide_type") == "figure_page":
            refs = slide.get("evidence_refs", [])
            figure_ids = [
                str(ref.get("id") or "")
                for ref in refs
                if isinstance(ref, Mapping) and ref.get("kind") == "figure"
            ] if isinstance(refs, list) else []
            if len(figure_ids) == 1 and figure_ids[0] in figures_by_id:
                figure_slots.append(index)
                figure_slides.append(slide)
                figure_id_by_object[id(slide)] = figure_ids[0]
    ordered_figure_slides = sorted(
        figure_slides,
        key=lambda slide: int(figures_by_id[figure_id_by_object[id(slide)]]["order"]),
    )
    counts["figure_order"] = sum(
        before is not after
        for before, after in zip(figure_slides, ordered_figure_slides)
    )
    for index, slide in zip(figure_slots, ordered_figure_slides):
        slides[index] = slide

    for slide in slides:
        if not isinstance(slide, dict):
            continue
        if slide.get("slide_type") == "figure_page":
            refs = slide.get("evidence_refs", [])
            figure_ids = [
                str(ref.get("id") or "")
                for ref in refs
                if isinstance(ref, Mapping) and ref.get("kind") == "figure"
            ] if isinstance(refs, list) else []
            item = figures_by_id.get(figure_ids[0]) if len(figure_ids) == 1 else None
            if item is not None:
                caption = str(item.get("caption") or "").strip()
                if caption and str(slide.get("title") or "").strip() != caption:
                    slide["title"] = caption
                    counts["figure_titles"] += 1
                section_id = item.get("section_id")
                if section_id and str(slide.get("section_ref") or "") != str(section_id):
                    slide["section_ref"] = str(section_id)
                    counts["figure_sections"] += 1
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


def _semantic_terms(value: object) -> set[str]:
    """Return lightweight Chinese/Latin terms for deterministic figure matching."""

    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "").casefold())
    terms = set(re.findall(r"[a-z]+|\d+(?:\.\d+)?", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    terms.update(
        chinese[index : index + 2]
        for index in range(max(0, len(chinese) - 1))
    )
    return terms


def _figure_host_score(
    slide: Mapping[str, Any], figure: Mapping[str, Any]
) -> tuple[int, int]:
    slide_text = " ".join(
        [
            str(slide.get("title") or ""),
            str(slide.get("key_message") or ""),
            *(str(value) for value in slide.get("bullet_points", [])),
        ]
    )
    figure_text = " ".join(
        [
            str(figure.get("caption") or ""),
            *(
                str(item.get("text") or "")
                for item in figure.get("nearby_blocks", [])
                if isinstance(item, Mapping)
            ),
        ]
    )
    slide_block_ids = {
        str(ref.get("id") or "")
        for ref in slide.get("evidence_refs", [])
        if isinstance(ref, Mapping) and ref.get("kind") == "block"
    }
    nearby_block_ids = {
        str(item.get("block_id") or "")
        for item in figure.get("nearby_blocks", [])
        if isinstance(item, Mapping)
    }
    direct = 1 if slide_block_ids & nearby_block_ids else 0
    overlap = len(_semantic_terms(slide_text) & _semantic_terms(figure_text))
    return direct, overlap


def compact_figure_pages_into_content_slides(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
    *,
    maximum_figures_per_slide: int = 2,
    maximum_standalone_ratio: float = 0.4,
) -> dict[str, int]:
    """Compress model-selected PDF figure pages without another model call.

    Original figures are assigned only to non-figure content slides in the same
    source section. A slide receives at most two images. Any remaining
    standalone pages are capped relative to the narrative content page count;
    overflow figures are omitted instead of expanding the deck unboundedly.
    """

    counts = {
        "embedded_figures": 0,
        "paired_slides": 0,
        "standalone_retained": 0,
        "figure_pages_omitted": 0,
    }
    slides = outline.get("slides", [])
    if not isinstance(slides, list) or maximum_figures_per_slide < 1:
        return counts

    inventory = build_figure_inventory(snapshot)
    figures_by_id = {
        str(item.get("figure_id") or ""): item
        for item in inventory
        if item.get("selectable") is True
    }
    order_by_id = {
        identity: int(item.get("order") or 0)
        for identity, item in figures_by_id.items()
    }
    figure_page_records: list[tuple[dict[str, Any], str]] = []
    hosts_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    used_candidate_ids = {
        str(candidate.get("candidate_id") or "")
        for slide in slides
        if isinstance(slide, Mapping)
        for candidate in slide.get("visual_candidates", [])
        if isinstance(candidate, Mapping) and candidate.get("candidate_id")
    }

    for slide in slides:
        if not isinstance(slide, dict) or slide.get("page_role") != "content":
            continue
        refs = slide.get("evidence_refs", [])
        figure_ids = [
            str(ref.get("id") or "")
            for ref in refs
            if isinstance(ref, Mapping) and ref.get("kind") == "figure"
        ] if isinstance(refs, list) else []
        if slide.get("slide_type") == "figure_page":
            if len(figure_ids) == 1 and figure_ids[0] in figures_by_id:
                figure_page_records.append((slide, figure_ids[0]))
            continue
        non_image_candidates = [
            candidate
            for candidate in slide.get("visual_candidates", [])
            if isinstance(candidate, Mapping) and candidate.get("type") != "image"
        ]
        figure_capacity = min(
            maximum_figures_per_slide,
            max(0, 2 - len(non_image_candidates)),
        )
        if len(figure_ids) < figure_capacity:
            hosts_by_section[str(slide.get("section_ref") or "")].append(slide)

    if not figure_page_records:
        return counts

    assigned_by_host: dict[int, list[str]] = defaultdict(list)
    host_by_object_id: dict[int, dict[str, Any]] = {}
    unassigned: list[tuple[dict[str, Any], str]] = []
    for figure_slide, figure_id in sorted(
        figure_page_records, key=lambda item: order_by_id.get(item[1], 0)
    ):
        section_id = str(figure_slide.get("section_ref") or "")
        available_hosts: list[dict[str, Any]] = []
        for host in hosts_by_section.get(section_id, []):
            existing_count = sum(
                1
                for ref in host.get("evidence_refs", [])
                if isinstance(ref, Mapping) and ref.get("kind") == "figure"
            )
            non_image_count = sum(
                1
                for candidate in host.get("visual_candidates", [])
                if isinstance(candidate, Mapping) and candidate.get("type") != "image"
            )
            figure_capacity = min(
                maximum_figures_per_slide,
                max(0, 2 - non_image_count),
            )
            if existing_count + len(assigned_by_host[id(host)]) < figure_capacity:
                available_hosts.append(host)
        if not available_hosts:
            unassigned.append((figure_slide, figure_id))
            continue
        host = max(
            available_hosts,
            key=lambda item: (
                _figure_host_score(item, figures_by_id[figure_id]),
                -hosts_by_section[section_id].index(item),
            ),
        )
        assigned_by_host[id(host)].append(figure_id)
        host_by_object_id[id(host)] = host

    for host_id, assigned_ids in assigned_by_host.items():
        host = host_by_object_id[host_id]
        refs = host.setdefault("evidence_refs", [])
        existing_figure_ids = [
            str(ref.get("id") or "")
            for ref in refs
            if isinstance(ref, Mapping) and ref.get("kind") == "figure"
        ]
        all_figure_ids = sorted(
            dict.fromkeys([*existing_figure_ids, *assigned_ids]),
            key=lambda identity: order_by_id.get(identity, 0),
        )[:maximum_figures_per_slide]
        refs[:] = [
            ref
            for ref in refs
            if not (isinstance(ref, Mapping) and ref.get("kind") == "figure")
        ]
        refs.extend({"kind": "figure", "id": identity} for identity in all_figure_ids)

        candidates = host.setdefault("visual_candidates", [])
        existing_image_candidate = next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, dict) and candidate.get("type") == "image"
            ),
            None,
        )
        candidates[:] = [
            candidate
            for candidate in candidates
            if not (isinstance(candidate, Mapping) and candidate.get("type") == "image")
        ]
        candidate_id = str(
            (existing_image_candidate or {}).get("candidate_id") or ""
        )
        if not candidate_id:
            base_id = "visual_image_" + re.sub(
                r"[^A-Za-z0-9_.-]+", "_", str(host.get("slide_id") or "slide")
            )
            candidate_id = base_id
            suffix = 2
            while candidate_id in used_candidate_ids:
                candidate_id = f"{base_id}_{suffix}"
                suffix += 1
        used_candidate_ids.add(candidate_id)
        captions = [
            str(figures_by_id[identity].get("caption") or identity)
            for identity in all_figure_ids
        ]
        candidates.append(
            {
                "candidate_id": candidate_id,
                "type": "image",
                "display_mode": "paired" if len(all_figure_ids) == 2 else "embedded",
                "description": " / ".join(captions),
                "source_refs": list(host.get("source_refs", [])),
                "evidence_refs": [
                    {"kind": "figure", "id": identity}
                    for identity in all_figure_ids
                ],
            }
        )
        counts["embedded_figures"] += len(assigned_ids)
        if len(all_figure_ids) == 2:
            counts["paired_slides"] += 1

    narrative_content_count = sum(
        1
        for slide in slides
        if isinstance(slide, Mapping)
        and slide.get("page_role") == "content"
        and slide.get("slide_type") != "figure_page"
    )
    standalone_budget = int(narrative_content_count * maximum_standalone_ratio)
    if narrative_content_count == 0:
        standalone_budget = 1
    retained_unassigned: set[int] = set()
    seen_sections: set[str] = set()
    ranked_unassigned = sorted(
        unassigned,
        key=lambda item: (
            -len(figures_by_id[item[1]].get("nearby_blocks", [])),
            -len(str(figures_by_id[item[1]].get("caption") or "")),
            order_by_id.get(item[1], 0),
        ),
    )
    retained_per_section: dict[str, int] = defaultdict(int)
    for figure_slide, _ in ranked_unassigned:
        if len(retained_unassigned) >= standalone_budget:
            break
        section_id = str(figure_slide.get("section_ref") or "")
        if section_id in seen_sections:
            continue
        retained_unassigned.add(id(figure_slide))
        seen_sections.add(section_id)
        retained_per_section[section_id] += 1
    # A long source section can contain several genuinely dense figures. Fill
    # any remaining global budget conservatively, while keeping a per-section
    # ceiling so one figure-heavy chapter cannot dominate the deck again.
    for figure_slide, _ in ranked_unassigned:
        if len(retained_unassigned) >= standalone_budget:
            break
        if id(figure_slide) in retained_unassigned:
            continue
        section_id = str(figure_slide.get("section_ref") or "")
        if retained_per_section[section_id] >= 3:
            continue
        retained_unassigned.add(id(figure_slide))
        retained_per_section[section_id] += 1

    assigned_slide_ids = {
        id(figure_slide)
        for figure_slide, figure_id in figure_page_records
        if any(figure_id in values for values in assigned_by_host.values())
    }
    retained_slides: list[Any] = []
    for slide in slides:
        if id(slide) in assigned_slide_ids:
            continue
        if isinstance(slide, Mapping) and slide.get("slide_type") == "figure_page":
            if id(slide) not in retained_unassigned:
                counts["figure_pages_omitted"] += 1
                continue
            counts["standalone_retained"] += 1
        retained_slides.append(slide)
    slides[:] = retained_slides
    return counts


def canonicalize_slide_section_order(
    outline: Mapping[str, Any], snapshot: DocumentIntelligenceSnapshot
) -> int:
    """Restore DocumentBundle section order without another model request.

    Slide semantics and evidence stay attached to their slide. Only list order
    and the positional slide IDs are source-owned here.
    """
    slides = outline.get("slides", [])
    if not isinstance(slides, list):
        return 0
    section_positions = {
        section_id: index for index, section_id in enumerate(snapshot.section_order)
    }
    original = list(slides)

    def order_key(item: tuple[int, Any]) -> tuple[int, int]:
        index, slide = item
        if not isinstance(slide, Mapping):
            return (len(section_positions) + 1, index)
        role = slide.get("page_role")
        if role == "title":
            return (-1, index)
        if role == "closing":
            return (len(section_positions) + 2, index)
        return (
            section_positions.get(str(slide.get("section_ref") or ""), len(section_positions)),
            index,
        )

    reordered = [slide for _, slide in sorted(enumerate(original), key=order_key)]
    changes = sum(left is not right for left, right in zip(original, reordered))
    slides[:] = reordered
    for page_number, slide in enumerate(slides, start=1):
        if isinstance(slide, dict):
            slide["slide_id"] = f"slide_{page_number:03d}"
    return changes


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


def normalize_section_evidence_and_visual_budget(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
) -> dict[str, int]:
    """Remove deterministic outline residues that do not require another LLM turn.

    Content slides may cite only evidence from their declared source section and
    may consume at most two visual slots. Visual candidates are ordered by the
    model, so the earliest candidates that fit are retained. When an image
    candidate is removed, its figure reference is removed from the slide too so
    figure coverage remains internally consistent.
    """

    counts = {
        "cross_section_evidence_removed": 0,
        "cross_section_visuals_removed": 0,
        "over_budget_visuals_removed": 0,
        "orphan_figure_refs_removed": 0,
    }
    slides = outline.get("slides", [])
    if not isinstance(slides, list):
        return counts

    for slide in slides:
        if not isinstance(slide, dict):
            continue
        role = str(slide.get("page_role") or "")
        slide_type = str(slide.get("slide_type") or "")
        section_ref = str(slide.get("section_ref") or "")
        section_known = section_ref in snapshot.sections_by_id

        refs = slide.get("evidence_refs", [])
        if isinstance(refs, list) and section_known:
            retained_refs: list[Any] = []
            for ref in refs:
                if not isinstance(ref, Mapping):
                    retained_refs.append(ref)
                    continue
                evidence = snapshot.evidence(
                    str(ref.get("kind") or ""), str(ref.get("id") or "")
                )
                if evidence is not None and not _descendant_or_same(
                    snapshot, evidence.section_id, section_ref
                ):
                    counts["cross_section_evidence_removed"] += 1
                    continue
                retained_refs.append(ref)
            refs[:] = retained_refs

        candidates = slide.get("visual_candidates", [])
        if not isinstance(candidates, list):
            continue

        scoped_candidates: list[Any] = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or not section_known:
                scoped_candidates.append(candidate)
                continue
            candidate_refs = candidate.get("evidence_refs", [])
            candidate_ref_items = (
                candidate_refs if isinstance(candidate_refs, list) else []
            )
            crosses_section = any(
                evidence is not None
                and not _descendant_or_same(
                    snapshot, evidence.section_id, section_ref
                )
                for ref in candidate_ref_items
                if isinstance(ref, Mapping)
                for evidence in [
                    snapshot.evidence(
                        str(ref.get("kind") or ""), str(ref.get("id") or "")
                    )
                ]
            )
            if crosses_section:
                counts["cross_section_visuals_removed"] += 1
                continue
            scoped_candidates.append(candidate)

        maximum_units = 2 if role == "content" and slide_type != "figure_page" else 0
        retained_candidates: list[Any] = []
        used_units = 0
        for candidate in scoped_candidates:
            units = (
                2
                if isinstance(candidate, Mapping)
                and candidate.get("type") == "image"
                and candidate.get("display_mode") == "paired"
                else 1
            )
            if used_units + units > maximum_units:
                counts["over_budget_visuals_removed"] += 1
                continue
            retained_candidates.append(candidate)
            used_units += units
        candidates[:] = retained_candidates

        if slide_type == "figure_page" or not isinstance(refs, list):
            continue
        retained_figure_ids = {
            str(ref.get("id") or "")
            for candidate in retained_candidates
            if isinstance(candidate, Mapping) and candidate.get("type") == "image"
            for ref in candidate.get("evidence_refs", [])
            if isinstance(ref, Mapping) and ref.get("kind") == "figure"
        }
        filtered_refs = [
            ref
            for ref in refs
            if not (
                isinstance(ref, Mapping)
                and ref.get("kind") == "figure"
                and str(ref.get("id") or "") not in retained_figure_ids
            )
        ]
        counts["orphan_figure_refs_removed"] += len(refs) - len(filtered_refs)
        refs[:] = filtered_refs

    return counts


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
        visual_units = sum(
            2
            if isinstance(candidate, Mapping)
            and candidate.get("type") == "image"
            and candidate.get("display_mode") == "paired"
            else 1
            for candidate in visual_candidates
        )
        if visual_units > maximum_visuals:
            issues.append(
                Issue(
                    "error",
                    "LAYOUT.VISUAL_BUDGET_EXCEEDED",
                    f"{base}.visual_candidates",
                    (
                        f"slide requires {visual_units} visual slots; "
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
                canonical_caption = str(
                    next(
                        (
                            item.get("caption")
                            for item in figure_inventory
                            if str(item.get("figure_id")) == figure_refs[0]
                        ),
                        "",
                    ) or ""
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
            if len(figure_refs) > 2:
                issues.append(
                    Issue(
                        "error",
                        "FIGURE.CONTENT_BUDGET_EXCEEDED",
                        f"{base}.evidence_refs",
                        "a content slide may contain at most two original figures",
                    )
                )
            for figure_id in figure_refs:
                if figure_id not in selectable_figures:
                    issues.append(
                        Issue(
                            "error",
                            "FIGURE.ASSET_UNAVAILABLE",
                            f"{base}.evidence_refs",
                            f"figure {figure_id!r} has no available original asset",
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
                        "visual candidate must cite native block/table/figure evidence",
                    )
                )
                continue
            candidate_type = str(candidate.get("type") or "")
            candidate_figure_ids = [
                str(ref.get("id") or "")
                for ref in candidate_refs
                if isinstance(ref, Mapping) and ref.get("kind") == "figure"
            ]
            if candidate_type == "image":
                display_mode = str(candidate.get("display_mode") or "")
                expected_count = 2 if display_mode == "paired" else 1
                if (
                    len(candidate_refs) != expected_count
                    or len(candidate_figure_ids) != expected_count
                ):
                    issues.append(
                        Issue(
                            "error",
                            "FIGURE.INVALID_IMAGE_CANDIDATE",
                            f"{base}.visual_candidates[{candidate_index}].evidence_refs",
                            (
                                "paired image candidates require exactly two figures; "
                                "embedded/standalone candidates require exactly one"
                            ),
                        )
                    )
                elif any(identity not in figure_refs for identity in candidate_figure_ids):
                    issues.append(
                        Issue(
                            "error",
                            "FIGURE.IMAGE_EVIDENCE_NOT_ON_SLIDE",
                            f"{base}.visual_candidates[{candidate_index}].evidence_refs",
                            "image candidate figure evidence must also appear on the slide",
                        )
                    )
                positions = [
                    figure_positions.get(identity, 0)
                    for identity in candidate_figure_ids
                ]
                if positions != sorted(positions):
                    issues.append(
                        Issue(
                            "error",
                            "FIGURE.IMAGE_ORDER",
                            f"{base}.visual_candidates[{candidate_index}].evidence_refs",
                            "paired image evidence must follow PDF figure order",
                        )
                    )
            elif candidate_figure_ids:
                issues.append(
                    Issue(
                        "error",
                        "FIGURE.WRONG_VISUAL_TYPE",
                        f"{base}.visual_candidates[{candidate_index}].evidence_refs",
                        "original figure evidence requires a type=image visual candidate",
                    )
                )
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

        if slide_type != "figure_page":
            candidate_figure_ids = {
                str(ref.get("id") or "")
                for candidate in visual_candidates
                if isinstance(candidate, Mapping) and candidate.get("type") == "image"
                for ref in candidate.get("evidence_refs", [])
                if isinstance(ref, Mapping) and ref.get("kind") == "figure"
            }
            if set(figure_refs) != candidate_figure_ids:
                issues.append(
                    Issue(
                        "error",
                        "FIGURE.CONTENT_IMAGE_COVERAGE",
                        f"{base}.visual_candidates",
                        (
                            "every figure on a content slide must be covered exactly "
                            "by an image visual candidate"
                        ),
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
