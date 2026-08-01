"""Deterministically identify research-report highlights before the contents page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot


_SPACE_RE = re.compile(r"\s+")
_SUMMARY_MARKER_RE = re.compile(
    r"^\s*(?:"
    r"[➢►▶◆◇•●▪■□☞✓✔]"
    r"|[（(]?[一二三四五六七八九十\d]+[）).、]"
    r")\s*"
)
_TOC_TITLES = {"目录", "contents", "tableofcontents"}
_SUMMARY_BLOCK_TYPES = {"paragraph", "blockquote"}
_SENTENCE_RE = re.compile(r".+?(?:[。！？]|$)")
_INVESTMENT_CUE_RE = re.compile(r"投资建议|评级|目标价|预计")
_LABEL_ONLY_RE = re.compile(r"^(?:投资建议|风险提示)[：。]$")
_SUMMARY_BODY_BUDGET = 480


def _compact_excerpt(value: str, max_chars: int) -> str:
    text = _SPACE_RE.sub(" ", value).strip()
    if len(text) <= max_chars:
        return text
    cutoff = max_chars - 1
    prefix = text[:cutoff]
    boundary = max(prefix.rfind(mark) for mark in ("；", "，", "、", "："))
    if boundary >= max(20, cutoff // 2):
        prefix = prefix[:boundary]
    return prefix.rstrip("；，、： ") + "…"


def summarize_front_matter_item(value: object, *, max_chars: int = 120) -> str:
    """Return a concise, source-grounded display sentence for a cover highlight."""
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    text = _SUMMARY_MARKER_RE.sub("", text, count=1).strip()
    sentences = [
        match.group(0).strip()
        for match in _SENTENCE_RE.finditer(text)
        if match.group(0).strip()
    ]
    if not sentences:
        return _compact_excerpt(text, max_chars)

    excerpt = sentences[0]
    if _LABEL_ONLY_RE.match(excerpt) and len(sentences) > 1:
        excerpt += sentences[1]
    if text.startswith("投资建议"):
        salient = [
            sentence
            for sentence in sentences[1:]
            if _INVESTMENT_CUE_RE.search(sentence)
        ]
        if salient:
            excerpt = salient[-1]
            if not excerpt.startswith("投资建议"):
                excerpt = "投资建议：" + excerpt
    return _compact_excerpt(excerpt, max_chars)


@dataclass(frozen=True, slots=True)
class FrontMatterSummary:
    """Application-owned evidence contract for a pre-contents highlights page."""

    section_id: str
    section_title: str
    toc_section_id: str
    block_ids: tuple[str, ...]
    item_texts: tuple[str, ...]

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "required": True,
            "placement": "immediately_after_title_before_other_non_title_slides",
            "page_role": "content",
            "slide_type": "summary",
            "section_ref": self.section_id,
            "required_title": self.section_title,
            "required_evidence_refs": [
                {"kind": "block", "id": block_id}
                for block_id in self.block_ids
            ],
            "items": [
                {
                    "block_id": block_id,
                    "text": summarize_front_matter_item(text),
                }
                for block_id, text in zip(self.block_ids, self.item_texts)
            ],
            "display_constraint": {
                "mode": "concise_source_excerpt",
                "max_total_body_chars": _SUMMARY_BODY_BUDGET,
            },
        }


def _normalized_heading(value: object) -> str:
    return _SPACE_RE.sub("", str(value or "")).strip("：:").casefold()


def _toc_boundary(
    snapshot: DocumentIntelligenceSnapshot,
) -> tuple[str, int] | None:
    """Return the first actual contents section and its title-block order."""

    for section_id in snapshot.section_order:
        section = snapshot.sections_by_id[section_id]
        title_block_id = str(section.get("title_block_id") or "")
        title_block = snapshot.blocks_by_id.get(title_block_id, {})
        title = _normalized_heading(title_block.get("text_raw"))
        if title not in _TOC_TITLES and not title.startswith("目录contents"):
            continue
        order = title_block.get("reading_order")
        if isinstance(order, int):
            return section_id, order
    return None


def detect_front_matter_summary(
    snapshot: DocumentIntelligenceSnapshot,
) -> FrontMatterSummary | None:
    """Find two or more marked highlight paragraphs before the contents page.

    Requiring repeated visible bullet markers in one section prevents dates,
    analyst metadata, ratings, headers, and footers from being mistaken for a
    report highlights page.
    """

    boundary = _toc_boundary(snapshot)
    if boundary is None:
        return None
    toc_section_id, toc_order = boundary

    runs: list[tuple[str, list[tuple[int, str, str]]]] = []
    current_section = ""
    current_run: list[tuple[int, str, str]] = []

    def flush() -> None:
        nonlocal current_section, current_run
        if len(current_run) >= 2:
            runs.append((current_section, current_run))
        current_section = ""
        current_run = []

    for block_id in snapshot.ordered_block_ids:
        block = snapshot.blocks_by_id[block_id]
        order = block.get("reading_order")
        if not isinstance(order, int) or order >= toc_order:
            break
        is_summary_type = str(block.get("type") or "") in _SUMMARY_BLOCK_TYPES
        text = str(block.get("text_raw") or "").strip()
        section_id = str(block.get("section_id") or "")
        if (
            not is_summary_type
            or not _SUMMARY_MARKER_RE.match(text)
            or not section_id
            or section_id not in snapshot.sections_by_id
        ):
            flush()
            continue
        if current_run and current_section != section_id:
            flush()
        current_section = section_id
        current_run.append((order, block_id, text))
    flush()

    if not runs:
        return None

    section_id, values = min(runs, key=lambda item: item[1][0][0])
    section = snapshot.sections_by_id[section_id]
    title_block = snapshot.blocks_by_id.get(
        str(section.get("title_block_id") or ""), {}
    )
    section_title = str(title_block.get("text_raw") or "").strip()
    return FrontMatterSummary(
        section_id=section_id,
        section_title=section_title,
        toc_section_id=toc_section_id,
        block_ids=tuple(block_id for _, block_id, _ in values),
        item_texts=tuple(text for _, _, text in values),
    )


def front_matter_summary_payload(
    snapshot: DocumentIntelligenceSnapshot,
) -> dict[str, Any]:
    summary = detect_front_matter_summary(snapshot)
    return summary.prompt_payload() if summary is not None else {"required": False}


def compact_front_matter_summary_slides(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
) -> int:
    """Compact required summary slides while preserving all evidence references."""
    summary = detect_front_matter_summary(snapshot)
    slides = outline.get("slides", [])
    if summary is None or not isinstance(slides, list):
        return 0

    source_text = dict(zip(summary.block_ids, summary.item_texts))
    changes = 0
    for slide in slides:
        if (
            not isinstance(slide, dict)
            or slide.get("page_role") != "content"
            or slide.get("slide_type") != "summary"
            or str(slide.get("section_ref") or "") != summary.section_id
        ):
            continue
        refs = slide.get("evidence_refs", [])
        if not isinstance(refs, list):
            continue
        block_ids = [
            str(ref.get("id") or "")
            for ref in refs
            if isinstance(ref, Mapping)
            and ref.get("kind") == "block"
            and str(ref.get("id") or "") in source_text
        ]
        if not block_ids:
            continue
        per_item_limit = max(60, _SUMMARY_BODY_BUDGET // len(block_ids))
        excerpts = [
            summarize_front_matter_item(
                source_text[block_id],
                max_chars=per_item_limit,
            )
            for block_id in block_ids
        ]
        key_message = excerpts[0]
        bullet_points = excerpts[1:]
        if (
            slide.get("key_message") != key_message
            or slide.get("bullet_points") != bullet_points
        ):
            slide["key_message"] = key_message
            slide["bullet_points"] = bullet_points
            changes += 1
    return changes
