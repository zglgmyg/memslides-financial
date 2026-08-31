"""Generate an evidence-grounded speaker manuscript for a finalized slide outline."""

from __future__ import annotations

from memslides.utils.run_timing import log_validation_failure, timed_stage, timing_span

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from memslides.research_pipeline.document_intelligence import generate_chunks
from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot
from memslides.research_pipeline.outline_generator.generate_outline import (
    DeepSeekAPIError,
    OutlineResponseError,
    build_request,
    call_deepseek,
    extract_response_content,
    expanded_retry_token_budget,
    parse_outline_content,
    resolve_api_provider,
)
from memslides.research_pipeline.outline_generator.llm_understanding import (
    build_direct_context_memory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "speaker_manuscript.schema.json"
DEFAULT_PROMPT = PROJECT_ROOT / "prompts" / "speaker_manuscript_system_prompt.md"


class SpeakerManuscriptError(RuntimeError):
    """Raised when a slide-aligned manuscript cannot be generated or verified."""


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SpeakerManuscriptError(f"JSON root must be an object: {path}")
    return value


def _snapshot_evidence_refs(snapshot: DocumentIntelligenceSnapshot) -> set[tuple[str, str]]:
    memories = [
        build_direct_context_memory(chunk) for chunk in generate_chunks(snapshot, 30000)
    ]
    return {
        (str(ref.get("kind")), str(ref.get("id")))
        for memory in memories
        for ref in memory.get("evidence_refs", [])
        if isinstance(ref, Mapping) and ref.get("kind") and ref.get("id")
    }


def _outline_slides(outline: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [slide for slide in outline.get("slides", []) if isinstance(slide, Mapping)]


def _canonicalize_fixed_slide_fields(
    manuscript: dict[str, Any], outline: Mapping[str, Any]
) -> None:
    actual = manuscript.get("slides")
    expected = outline.get("slides")
    if (
        not isinstance(actual, list)
        or not isinstance(expected, list)
        or len(actual) != len(expected)
        or not all(isinstance(slide, dict) for slide in actual)
        or not all(isinstance(slide, Mapping) for slide in expected)
    ):
        return
    for script_slide, outline_slide in zip(actual, expected):
        script_slide["slide_id"] = str(outline_slide.get("slide_id") or "")
        script_slide["slide_title"] = str(outline_slide.get("title") or "")
    if actual:
        actual[-1]["transition_to_next"] = ""


def _refs(value: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(ref.get("kind")), str(ref.get("id")))
        for ref in value.get("evidence_refs", [])
        if isinstance(ref, Mapping) and ref.get("kind") and ref.get("id")
    }


@timed_stage('research.speaker_validation')
def validate_speaker_manuscript(
    manuscript: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    outline: Mapping[str, Any],
    allowed_evidence_refs: set[tuple[str, str]],
) -> list[str]:
    """Validate schema, slide alignment, ordering, titles, and evidence scope."""

    errors = sorted(
        Draft202012Validator(schema).iter_errors(manuscript),
        key=lambda error: list(error.absolute_path),
    )
    messages = [
        "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        ) + f": {error.message}"
        for error in errors[:30]
    ]
    expected = _outline_slides(outline)
    actual = manuscript.get("slides", [])
    if not isinstance(actual, list):
        return messages
    expected_ids = [str(slide.get("slide_id") or "") for slide in expected]
    actual_ids = [
        str(slide.get("slide_id") or "") if isinstance(slide, Mapping) else ""
        for slide in actual
    ]
    if actual_ids != expected_ids:
        messages.append("speaker slides must match outline slide_id values and order exactly")
        return messages

    for index, (outline_slide, script_slide) in enumerate(zip(expected, actual)):
        if not isinstance(script_slide, Mapping):
            continue
        expected_title = str(outline_slide.get("title") or "")
        if str(script_slide.get("slide_title") or "") != expected_title:
            messages.append(f"slides[{index}].slide_title must exactly match the outline")
        script_refs = _refs(script_slide)
        slide_refs = _refs(outline_slide)
        if not script_refs.issubset(slide_refs):
            messages.append(f"slides[{index}] cites evidence outside its outline slide")
        unavailable = script_refs - allowed_evidence_refs
        if unavailable:
            messages.append(f"slides[{index}] cites unavailable report evidence")
        if index == len(actual) - 1 and str(
            script_slide.get("transition_to_next") or ""
        ):
            messages.append("the final slide transition_to_next must be empty")
    return messages


def validate_speaker_manuscript_for_snapshot(
    manuscript: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot,
    outline: Mapping[str, Any],
    *,
    schema_path: Path = DEFAULT_SCHEMA,
) -> list[str]:
    """Validate a prepared manuscript against its report and finalized outline."""

    return validate_speaker_manuscript(
        manuscript,
        _load_json(schema_path),
        outline=outline,
        allowed_evidence_refs=_snapshot_evidence_refs(snapshot),
    )


def _visualization_payload(artifacts: Sequence[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for artifact in artifacts:
        output.append(
            {
                "slide_id": str(getattr(artifact, "slide_id", "")),
                "visualization_id": str(getattr(artifact, "visualization_id", "")),
                "data": dict(getattr(artifact, "data", {}) or {}),
            }
        )
    return output


def _messages(
    snapshot: DocumentIntelligenceSnapshot,
    outline: Mapping[str, Any],
    narrative_plan: Mapping[str, Any],
    artifacts: Sequence[Any],
    schema: Mapping[str, Any],
    prompt: str,
) -> list[dict[str, str]]:
    payload = {
        "task": "write_slide_aligned_speaker_manuscript",
        "document": dict(snapshot.metadata),
        "narrative_plan": dict(narrative_plan),
        "slide_outline": dict(outline),
        "verified_visualizations": _visualization_payload(artifacts),
        "output_schema": dict(schema),
        "constraints": {
            "one_script_per_slide": True,
            "preserve_slide_order": True,
            "preserve_slide_titles": True,
            "evidence_must_be_slide_scoped": True,
            "no_external_facts": True,
            "spoken_language": "zh-CN",
        },
    }
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _correction_messages(
    messages: Sequence[Mapping[str, str]], previous: str, errors: Sequence[str]
) -> list[dict[str, str]]:
    return [
        *[dict(message) for message in messages],
        {"role": "assistant", "content": previous[:80_000] or "{}"},
        {
            "role": "user",
            "content": (
                "The previous speaker manuscript JSON was invalid. Return only one "
                "corrected complete JSON object. Preserve the finalized slide order and "
                "titles, and do not add facts or evidence. Fix:\n- "
                + "\n- ".join(errors[:30])
            ),
        },
    ]


def _failed_slide_indices(errors: Sequence[str], slide_count: int) -> list[int]:
    indices: set[int] = set()
    for error in errors:
        match = re.search(r"(?:\$\.)?slides\[(\d+)\]", error)
        if match is None:
            return []
        index = int(match.group(1))
        if index >= slide_count:
            return []
        indices.add(index)
    return sorted(indices)


def _slide_correction_messages(
    messages: Sequence[Mapping[str, str]],
    manuscript: Mapping[str, Any],
    errors: Sequence[str],
    indices: Sequence[int],
) -> list[dict[str, str]]:
    slides = manuscript["slides"]
    payload = {
        "task": "repair_invalid_speaker_slides",
        "errors": list(errors),
        "invalid_slides": [slides[index] for index in indices],
        "required_slide_ids": [slides[index]["slide_id"] for index in indices],
        "response_format": {"slides": "complete corrected slide objects only"},
        "constraints": {
            "preserve_slide_ids": True,
            "do_not_add_facts_or_evidence": True,
        },
    }
    return [
        *[dict(message) for message in messages],
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _merge_slide_repairs(
    manuscript: Mapping[str, Any], repairs: Mapping[str, Any], indices: Sequence[int]
) -> dict[str, Any]:
    repaired_slides = repairs.get("slides")
    if not isinstance(repaired_slides, list) or len(repaired_slides) != len(indices):
        raise SpeakerManuscriptError("local repair returned an invalid slide count")
    expected_ids = {str(manuscript["slides"][index]["slide_id"]) for index in indices}
    replacements = {
        str(slide.get("slide_id")): slide
        for slide in repaired_slides
        if isinstance(slide, dict) and slide.get("slide_id")
    }
    if set(replacements) != expected_ids:
        raise SpeakerManuscriptError("local repair returned unexpected slide_id values")
    merged = dict(manuscript)
    merged["slides"] = list(manuscript["slides"])
    for index in indices:
        merged["slides"][index] = replacements[str(manuscript["slides"][index]["slide_id"])]
    return merged


def generate_speaker_manuscript(
    snapshot: DocumentIntelligenceSnapshot,
    outline: Mapping[str, Any],
    narrative_plan: Mapping[str, Any],
    artifacts: Sequence[Any],
    *,
    api_key: str,
    model: str,
    base_url: str,
    api_provider: str = "auto",
    max_tokens: int = 24000,
    max_attempts: int = 2,
    timeout: int = 300,
    thinking: str = "enabled",
    reasoning_effort: str = "high",
    schema_path: Path = DEFAULT_SCHEMA,
    prompt_path: Path = DEFAULT_PROMPT,
) -> dict[str, Any]:
    """Generate a manuscript after slide and visualization planning are finalized."""

    schema = _load_json(schema_path)
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    allowed_refs = _snapshot_evidence_refs(snapshot)
    original_messages = _messages(
        snapshot, outline, narrative_plan, artifacts, schema, prompt
    )
    attempt_messages = list(original_messages)
    attempt_max_tokens = max_tokens
    attempt_thinking = thinking
    provider = resolve_api_provider(api_provider, base_url)
    last_error = ""
    local_repair_attempted = False

    for attempt in range(1, max_attempts + 1):
        content = ""
        try:
            with timing_span(f"research.speaker.request attempt={attempt}"):
                response = call_deepseek(
                    build_request(
                        attempt_messages,
                        model=model,
                        max_tokens=attempt_max_tokens,
                        thinking=attempt_thinking,
                        reasoning_effort=reasoning_effort,
                        api_provider=provider,
                    ),
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout,
                )
            content = extract_response_content(response)
            manuscript = parse_outline_content(content)
            _canonicalize_fixed_slide_fields(manuscript, outline)
            errors = validate_speaker_manuscript(
                manuscript,
                schema,
                outline=outline,
                allowed_evidence_refs=allowed_refs,
            )
            if not errors:
                return manuscript
            log_validation_failure("speaker", attempt, errors)
            slides = manuscript.get("slides")
            indices = _failed_slide_indices(
                errors, len(slides) if isinstance(slides, list) else 0
            )
            if indices and not local_repair_attempted and attempt < max_attempts:
                local_repair_attempted = True
                try:
                    with timing_span(f"research.speaker.local_repair attempt={attempt}"):
                        repair_response = call_deepseek(
                            build_request(
                                _slide_correction_messages(
                                    original_messages, manuscript, errors, indices
                                ),
                                model=model,
                                max_tokens=attempt_max_tokens,
                                thinking=attempt_thinking,
                                reasoning_effort=reasoning_effort,
                                api_provider=provider,
                            ),
                            api_key=api_key,
                            base_url=base_url,
                            timeout=timeout,
                        )
                    repaired = _merge_slide_repairs(
                        manuscript,
                        parse_outline_content(extract_response_content(repair_response)),
                        indices,
                    )
                    _canonicalize_fixed_slide_fields(repaired, outline)
                    repair_errors = validate_speaker_manuscript(
                        repaired,
                        schema,
                        outline=outline,
                        allowed_evidence_refs=allowed_refs,
                    )
                    if not repair_errors:
                        return repaired
                    log_validation_failure("speaker_local_repair", attempt, repair_errors)
                except (DeepSeekAPIError, OutlineResponseError, SpeakerManuscriptError) as exc:
                    log_validation_failure(
                        "speaker_local_repair", attempt, [type(exc).__name__]
                    )
            last_error = "; ".join(errors)
            attempt_messages = _correction_messages(original_messages, content, errors)
        except (DeepSeekAPIError, OutlineResponseError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if not getattr(exc, "retryable", True):
                raise SpeakerManuscriptError(last_error) from exc
            attempt_messages = list(original_messages)
            if isinstance(exc, OutlineResponseError) and exc.truncated:
                attempt_max_tokens = expanded_retry_token_budget(
                    attempt_max_tokens
                )
                attempt_thinking = "disabled"
        if attempt >= max_attempts:
            break
    raise SpeakerManuscriptError(
        f"speaker manuscript generation failed after {max_attempts} attempt(s): {last_error}"
    )


def render_speaker_manuscript_markdown(manuscript: Mapping[str, Any]) -> str:
    """Render the verified slide-aligned JSON as a presenter handoff."""

    lines = [
        f"# {manuscript['metadata']['title']}",
        "",
        "## 开场",
        "",
        str(manuscript["opening"]),
    ]
    for page_number, slide in enumerate(manuscript.get("slides", []), start=1):
        lines.extend(
            [
                "",
                f"## 第{page_number}页｜{slide['slide_title']}",
                "",
                str(slide["script"]),
            ]
        )
        transition = str(slide.get("transition_to_next") or "").strip()
        if transition:
            lines.extend(["", f"转场：{transition}"])
    lines.extend(["", "## 结束语", "", str(manuscript["closing"]), ""])
    return "\n".join(lines)
