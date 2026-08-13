"""Generate a compact narrative plan without defining slide boundaries."""

from __future__ import annotations

import json
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
    parse_outline_content,
    resolve_api_provider,
)
from memslides.research_pipeline.outline_generator.llm_understanding import (
    build_direct_context_memory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "narrative_plan.schema.json"
DEFAULT_PROMPT = PROJECT_ROOT / "prompts" / "narrative_plan_system_prompt.md"


class NarrativePlanError(RuntimeError):
    """Raised when a narrative plan cannot be generated or verified."""


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise NarrativePlanError(f"JSON root must be an object: {path}")
    return value


def _section_catalog(snapshot: DocumentIntelligenceSnapshot) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for ordinal, section_id in enumerate(snapshot.section_order):
        section = snapshot.sections_by_id[section_id]
        title_block = snapshot.blocks_by_id.get(str(section.get("title_block_id") or ""), {})
        output.append(
            {
                "ordinal": ordinal,
                "section_id": section_id,
                "parent_id": section.get("parent_id"),
                "level": section.get("level"),
                "title": title_block.get("text_raw", ""),
            }
        )
    return output


def _allowed_evidence_refs(
    runtime_memories: Sequence[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    return {
        (str(ref.get("kind")), str(ref.get("id")))
        for memory in runtime_memories
        for ref in memory.get("evidence_refs", [])
        if isinstance(ref, Mapping) and ref.get("kind") and ref.get("id")
    }


def validate_narrative_plan(
    plan: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    allowed_evidence_refs: set[tuple[str, str]],
    allowed_section_refs: set[str],
) -> list[str]:
    """Validate the schema and every application-owned source reference."""

    errors = sorted(
        Draft202012Validator(schema).iter_errors(plan),
        key=lambda error: list(error.absolute_path),
    )
    messages = [
        "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        ) + f": {error.message}"
        for error in errors[:30]
    ]
    for section_index, section in enumerate(plan.get("sections", [])):
        if not isinstance(section, Mapping):
            continue
        for section_ref in section.get("source_section_refs", []):
            if str(section_ref) not in allowed_section_refs:
                messages.append(
                    f"sections[{section_index}] uses unknown source section {section_ref}"
                )
        for claim_index, claim in enumerate(section.get("key_claims", [])):
            if not isinstance(claim, Mapping):
                continue
            for ref in claim.get("evidence_refs", []):
                if not isinstance(ref, Mapping):
                    continue
                key = (str(ref.get("kind")), str(ref.get("id")))
                if key not in allowed_evidence_refs:
                    messages.append(
                        f"sections[{section_index}].key_claims[{claim_index}] "
                        f"uses unavailable evidence ref {key[0]}/{key[1]}"
                    )
    return messages


def _messages(
    snapshot: DocumentIntelligenceSnapshot,
    runtime_memories: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Any],
    prompt: str,
) -> list[dict[str, str]]:
    payload = {
        "task": "write_evidence_grounded_narrative_plan",
        "document": dict(snapshot.metadata),
        "section_catalog": _section_catalog(snapshot),
        "runtime_context_memories": [dict(memory) for memory in runtime_memories],
        "output_schema": dict(schema),
        "constraints": {
            "advisory_only": True,
            "no_slide_boundaries": True,
            "no_speaker_script": True,
            "preserve_source_section_order": True,
            "preserve_evidence_ids": True,
            "no_external_facts": True,
            "language": "zh-CN",
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
                "The previous narrative plan JSON was invalid. Return only one corrected "
                "complete JSON object. Do not add facts, evidence IDs, slide boundaries, "
                "or speaker-script content. Fix:\n- " + "\n- ".join(errors[:30])
            ),
        },
    ]


def generate_narrative_plan(
    snapshot: DocumentIntelligenceSnapshot,
    *,
    api_key: str,
    model: str,
    base_url: str,
    api_provider: str = "auto",
    max_tokens: int = 8000,
    max_attempts: int = 2,
    timeout: int = 300,
    thinking: str = "enabled",
    reasoning_effort: str = "high",
    schema_path: Path = DEFAULT_SCHEMA,
    prompt_path: Path = DEFAULT_PROMPT,
) -> dict[str, Any]:
    """Generate and validate an advisory, evidence-grounded narrative plan."""

    schema = _load_json(schema_path)
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    runtime_memories = [
        build_direct_context_memory(chunk) for chunk in generate_chunks(snapshot, 30000)
    ]
    allowed_refs = _allowed_evidence_refs(runtime_memories)
    original_messages = _messages(snapshot, runtime_memories, schema, prompt)
    attempt_messages = list(original_messages)
    provider = resolve_api_provider(api_provider, base_url)
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        content = ""
        try:
            response = call_deepseek(
                build_request(
                    attempt_messages,
                    model=model,
                    max_tokens=max_tokens,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                    api_provider=provider,
                ),
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            content = extract_response_content(response)
            plan = parse_outline_content(content)
            errors = validate_narrative_plan(
                plan,
                schema,
                allowed_evidence_refs=allowed_refs,
                allowed_section_refs=set(snapshot.section_order),
            )
            if not errors:
                return plan
            last_error = "; ".join(errors)
            attempt_messages = _correction_messages(original_messages, content, errors)
        except (DeepSeekAPIError, OutlineResponseError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if not getattr(exc, "retryable", True):
                raise NarrativePlanError(last_error) from exc
            attempt_messages = list(original_messages)
        if attempt >= max_attempts:
            break
    raise NarrativePlanError(
        f"narrative plan generation failed after {max_attempts} attempt(s): {last_error}"
    )
