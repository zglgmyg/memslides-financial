#!/usr/bin/env python3
"""Generate a research-report PPT semantic outline from DocumentBundle.

The runtime pipeline is deterministic Document Intelligence, transient LLM
Context Compression, then LLM Slide Planning.  Only the final Slide Outline is
persisted.

Examples (run from the project root):
    python -m outline_generator.generate_outline \
      output/002544/document_bundle \
      -o outputs/outlines/002544_2025-10-28_outline.json

    python -m outline_generator.generate_outline INPUT.json --dry-run \
      --request-output output/outline_request_preview.json

Environment:
    DEEPSEEK_API_KEY   Required unless --dry-run is used.
    DEEPSEEK_BASE_URL  Optional; defaults to https://api.deepseek.com.
"""

from __future__ import annotations

from memslides.utils.run_timing import log_validation_failure, timed_stage, timing_span

import argparse
import http.client
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


MAX_RETRY_OUTPUT_TOKENS = 32_000


def expanded_retry_token_budget(current: int) -> int:
    """Increase output capacity after a confirmed length truncation."""

    return min(
        MAX_RETRY_OUTPUT_TOKENS,
        max(current + 4_000, int(current * 1.5)),
    )

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from memslides.research_pipeline.tools.validate_outline import Issue, validate_outline as validate_outline_data
from memslides.research_pipeline.document_intelligence import generate_chunks, load_document_intelligence
from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot, IntelligenceChunk
from memslides.research_pipeline.document_intelligence.loader import DocumentIntelligenceError
from memslides.research_pipeline.outline_generator.bundle_validation import (
    canonicalize_outline_from_bundle,
    canonicalize_slide_section_order,
    compact_figure_pages_into_content_slides,
    normalize_repeated_content_titles,
    normalize_section_evidence_and_visual_budget,
    normalize_topic_sentence_key_messages,
    validate_outline_evidence,
)
from memslides.research_pipeline.outline_generator.few_shot import (
    FewShotCaseError,
    load_case_library,
    select_cases,
)
from memslides.research_pipeline.outline_generator.front_matter import compact_front_matter_summary_slides
from memslides.research_pipeline.outline_generator.llm_understanding import (
    ContextMemoryError,
    build_direct_context_memory,
    build_context_compression_correction_messages,
    build_context_compression_messages,
    build_slide_planning_messages,
    parse_context_memory,
    preview_context_memory,
)


DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_PROVIDERS = ("auto", "deepseek", "siliconflow")
DEFAULT_DIRECT_PLANNING_MAX_CHARS = 300_000
SILICONFLOW_DIRECT_PLANNING_MAX_CHARS = 60_000


class OutlineGenerationError(RuntimeError):
    """Raised when prompt creation, API invocation, or validation fails."""


class DeepSeekAPIError(OutlineGenerationError):
    """Raised for an HTTP/network API failure with retry metadata."""

    def __init__(self, message: str, *, retryable: bool, status_code: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def fill_blank_key_messages(outline: Mapping[str, Any]) -> int:
    """Repair blank key messages from existing, schema-bound slide content."""

    changes = 0
    slides = outline.get("slides", [])
    if not isinstance(slides, list):
        return changes
    for slide in slides:
        if not isinstance(slide, dict) or str(
            slide.get("key_message") or ""
        ).strip():
            continue
        bullets = slide.get("bullet_points", [])
        fallback = next(
            (
                str(value).strip()
                for value in bullets
                if isinstance(value, str) and value.strip()
            ),
            "",
        )
        if not fallback:
            fallback = str(slide.get("title") or "").strip()
        if fallback:
            slide["key_message"] = fallback
            changes += 1
    return changes


def ensure_terminal_closing_slide(
    outline: Mapping[str, Any], narrative_plan: Mapping[str, Any]
) -> int:
    """Guarantee exactly one final closing page before downstream generation."""

    slides = outline.get("slides", [])
    if not isinstance(slides, list):
        return 0
    closing_slides = [
        slide
        for slide in slides
        if isinstance(slide, dict) and slide.get("page_role") == "closing"
    ]
    if len(closing_slides) == 1 and slides and slides[-1] is closing_slides[0]:
        return 0

    if closing_slides:
        closing = closing_slides[-1]
        slides[:] = [
            slide
            for slide in slides
            if not (isinstance(slide, dict) and slide.get("page_role") == "closing")
        ]
        slides.append(closing)
        return max(1, len(closing_slides))

    used_ids = {
        str(slide.get("slide_id"))
        for slide in slides
        if isinstance(slide, Mapping) and slide.get("slide_id")
    }
    next_number = len(slides) + 1
    slide_id = f"slide_{next_number:03d}"
    while slide_id in used_ids:
        next_number += 1
        slide_id = f"slide_{next_number:03d}"
    closing_message = str(narrative_plan.get("closing_message") or "感谢聆听").strip()
    slides.append(
        {
            "slide_id": slide_id,
            "page_role": "closing",
            "slide_type": "summary",
            "title": "结束语",
            "key_message": closing_message,
            "bullet_points": [],
            "source_refs": [],
            "evidence_refs": [],
            "visual_candidates": [],
        }
    )
    return 1


class OutlineResponseError(OutlineGenerationError):
    """Raised when the model response cannot be used as an outline."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        content: str = "",
        truncated: bool = False,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.content = content
        self.truncated = truncated


class OutlineValidationError(OutlineGenerationError):
    """Raised when a generated outline violates the canonical contract."""

    def __init__(
        self, issues: Sequence[Issue], *, content: str, outline: Mapping[str, Any]
    ):
        errors = [issue for issue in issues if issue.severity == "error"]
        summary = "\n".join(
            f"- {issue.code} {issue.path}: {issue.message}" for issue in errors[:20]
        )
        super().__init__(f"Generated outline failed validation:\n{summary}")
        self.issues = list(issues)
        self.content = content
        self.outline = outline
        self.retryable = True


def load_json(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OutlineGenerationError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise OutlineGenerationError(
            f"{label} is not valid JSON: {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise OutlineGenerationError(f"{label} root must be a JSON object: {path}")
    return value


def load_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise OutlineGenerationError(f"{label} not found: {path}") from exc


def validate_json_instance(
    instance: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    label: str,
) -> None:
    """Fail early when an input JSON object violates its canonical schema."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise OutlineGenerationError(f"{label} schema is invalid: {exc.message}") from exc

    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    details = []
    for error in errors[:20]:
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        details.append(f"- {path}: {error.message}")
    raise OutlineGenerationError(
        f"{label} failed schema validation ({len(errors)} error(s)):\n"
        + "\n".join(details)
    )


def build_messages(
    snapshot: DocumentIntelligenceSnapshot,
    runtime_memories: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Any],
    few_shot: Mapping[str, Any],
    system_prompt: str,
    narrative_plan: Mapping[str, Any],
) -> List[Dict[str, str]]:
    selected_few_shot: Mapping[str, Any]
    if "cases" in few_shot and "selection_policy" not in few_shot:
        selected_few_shot = select_cases(snapshot, few_shot).prompt_payload
    else:
        # Selected case payloads and the legacy monolithic file remain accepted
        # for callers that construct prompts directly.
        selected_few_shot = few_shot
    return build_slide_planning_messages(
        snapshot,
        runtime_memories,
        schema,
        selected_few_shot,
        system_prompt,
        narrative_plan,
    )


def context_input_chars(chunks: Sequence[IntelligenceChunk]) -> int:
    """Measure deterministic source context before choosing the LLM pipeline."""

    return sum(
        len(json.dumps(dict(chunk.payload), ensure_ascii=False, separators=(",", ":")))
        for chunk in chunks
    )


def effective_direct_planning_max_chars(configured: int, api_provider: str) -> int:
    """Apply a conservative direct-context cap for SiliconFlow requests."""
    if api_provider == "siliconflow" and configured > 0:
        return min(configured, SILICONFLOW_DIRECT_PLANNING_MAX_CHARS)
    return configured


@timed_stage('research.context_compression')
def generate_context_memories(
    chunks: Sequence[IntelligenceChunk],
    *,
    api_key: str,
    base_url: str,
    timeout: int,
    model: str,
    max_tokens: int,
    thinking: str,
    reasoning_effort: str,
    max_attempts: int,
    api_provider: str = "deepseek",
) -> list[dict[str, Any]]:
    """Generate transient per-chunk memory; results are never written to disk."""

    memories: list[dict[str, Any]] = []
    for chunk in chunks:
        original_messages = build_context_compression_messages(chunk)
        messages = list(original_messages)
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            content = ""
            try:
                with timing_span(f"research.compression.request chunk={chunk.id} attempt={attempt}"):
                    response = call_deepseek(
                        build_request(
                            messages,
                            model=model,
                            max_tokens=max_tokens,
                            thinking=thinking,
                            reasoning_effort=reasoning_effort,
                            api_provider=api_provider,
                        ),
                        api_key=api_key,
                        base_url=base_url,
                        timeout=timeout,
                    )
                content = extract_response_content(response)
                memories.append(parse_context_memory(content, chunk))
                break
            except ContextMemoryError as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise OutlineGenerationError(
                        f"Context Compression failed for {chunk.id}: {exc}"
                    ) from exc
                messages = build_context_compression_correction_messages(
                    original_messages,
                    previous_content=content,
                    error=str(exc),
                )
            except (DeepSeekAPIError, OutlineResponseError) as exc:
                last_error = exc
                retryable = getattr(exc, "retryable", True)
                if not retryable or attempt >= max_attempts:
                    raise OutlineGenerationError(
                        f"Context Compression failed for {chunk.id}: {exc}"
                    ) from exc
                messages = list(original_messages)
        else:
            raise OutlineGenerationError(
                f"Context Compression failed for {chunk.id}: {last_error}"
            )
    return memories


def build_request(
    messages: List[Dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    thinking: str,
    reasoning_effort: str,
    api_provider: str = "deepseek",
) -> Dict[str, Any]:
    request: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if api_provider == "siliconflow":
        # Keep SiliconFlow's OpenAI-compatible request deliberately minimal.
        # Model-specific extension fields have caused HTTP 400 parse failures
        # even when the selected model nominally advertises those capabilities.
        return request
    if api_provider == "deepseek":
        request["response_format"] = {"type": "json_object"}
        request["stream"] = False
        request["thinking"] = {"type": thinking}
        if thinking == "enabled":
            request["reasoning_effort"] = reasoning_effort
        return request
    raise OutlineGenerationError(f"Unsupported API provider: {api_provider}")


def resolve_api_provider(requested: str, base_url: str) -> str:
    """Resolve provider-specific request parameters without changing prompts."""
    if requested != "auto":
        return requested
    if "siliconflow.cn" in base_url.lower():
        return "siliconflow"
    return "deepseek"


def call_deepseek(request_body: Mapping[str, Any], *, api_key: str, base_url: str, timeout: int) -> Dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    serialized_payload = json.dumps(request_body, ensure_ascii=False)
    body = serialized_payload.encode("utf-8")
    messages = request_body.get("messages", [])
    message_count = len(messages) if isinstance(messages, list) else 0
    print(
        "Calling model API: "
        f"url={endpoint}, messages={message_count}, payload_bytes={len(body)}, "
        f"max_tokens={request_body.get('max_tokens')}, "
        f"thinking={request_body.get('extra_body', {}).get('thinking', request_body.get('thinking'))}"
    )
    http_request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DeepSeekAPIError(
            f"DeepSeek API returned HTTP {exc.code}: {detail[:1000]}",
            retryable=exc.code == 429 or exc.code >= 500,
            status_code=exc.code,
        ) from exc
    except TimeoutError as exc:
        raise DeepSeekAPIError(
            f"DeepSeek API read timed out after {timeout} seconds",
            retryable=True,
        ) from exc
    except urllib.error.URLError as exc:
        raise DeepSeekAPIError(
            f"Cannot connect to DeepSeek API: {exc.reason}",
            retryable=True,
        ) from exc
    except (http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionResetError) as exc:
        raise DeepSeekAPIError(
            f"DeepSeek API response connection was interrupted: {exc}",
            retryable=True,
        ) from exc
    except json.JSONDecodeError as exc:
        raise DeepSeekAPIError(
            "DeepSeek API response is not valid JSON",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict):
        raise DeepSeekAPIError(
            "DeepSeek API response root is not an object",
            retryable=True,
        )
    return payload


def extract_response_content(api_response: Mapping[str, Any]) -> str:
    try:
        choice = api_response["choices"][0]
        finish_reason = choice.get("finish_reason")
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OutlineResponseError(
            "DeepSeek response does not contain choices[0].message.content",
            retryable=True,
        ) from exc
    if finish_reason == "length":
        raise OutlineResponseError(
            "DeepSeek output was truncated at --max-tokens",
            retryable=True,
            content=content if isinstance(content, str) else "",
            truncated=True,
        )
    if not isinstance(content, str) or not content.strip():
        raise OutlineResponseError(
            "DeepSeek returned empty content; retry or adjust the prompt",
            retryable=True,
        )
    return content


def parse_outline_content(content: str) -> Dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OutlineResponseError(
            f"Model content is not valid JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}",
            retryable=True,
            content=content,
        ) from exc
    if not isinstance(value, dict):
        raise OutlineResponseError(
            "Generated outline root must be a JSON object",
            retryable=True,
            content=content,
        )
    return value


def extract_outline(api_response: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract and parse the final JSON object from a DeepSeek response."""
    return parse_outline_content(extract_response_content(api_response))


def build_correction_messages(
    original_messages: Sequence[Mapping[str, str]],
    *,
    previous_content: str,
    errors: Sequence[str],
) -> List[Dict[str, str]]:
    """Build one corrective turn without changing the schema or source input."""
    error_text = "\n".join(f"- {item}" for item in errors[:20])
    return [
        *[dict(message) for message in original_messages],
        {
            "role": "assistant",
            "content": previous_content[:50_000] or "{}",
        },
        {
            "role": "user",
            "content": (
                "上一个输出无效。请只返回修正后的完整 JSON 对象，不要解释，"
                "不得改变或补造输入事实。需要修复的问题：\n"
                + error_text
            ),
        },
    ]


def build_truncation_retry_messages(
    original_messages: Sequence[Mapping[str, str]],
) -> List[Dict[str, str]]:
    """Retry from the same complete context without replaying partial output."""

    return [
        *[dict(message) for message in original_messages],
        {
            "role": "user",
            "content": (
                "The previous response reached the output-token limit. "
                "Using the same complete source context above, return one complete, "
                "valid, compact JSON object only. Preserve every required slide field "
                "and evidence reference, but avoid repetition and unnecessary wording. "
                "Do not summarize, omit source coverage, or explain the response."
            ),
        },
    ]


_GLOBAL_OUTLINE_ISSUES = {
    "BUNDLE.FRONT_SUMMARY_ORDER",
    "BUNDLE.SECTION_ORDER",
    "FIGURE.ORDER",
    "SLIDE.DUPLICATE_ID",
}


def _failed_outline_slide_indices(
    issues: Sequence[Issue], outline: Mapping[str, Any]
) -> list[int]:
    slides = outline.get("slides")
    if not isinstance(slides, list):
        return []
    indices: set[int] = set()
    for issue in issues:
        if issue.severity != "error":
            continue
        match = re.match(r"^\$\.slides\[(\d+)\](?:\.|$)", issue.path)
        if issue.code in _GLOBAL_OUTLINE_ISSUES or match is None:
            return []
        index = int(match.group(1))
        if index >= len(slides):
            return []
        indices.add(index)
    selected = [slides[index] for index in sorted(indices)]
    slide_ids = [slide.get("slide_id") for slide in selected if isinstance(slide, Mapping)]
    if len(slide_ids) != len(selected) or any(
        not isinstance(value, str) or not value for value in slide_ids
    ):
        return []
    if len(set(slide_ids)) != len(slide_ids):
        return []
    return sorted(indices)


def _outline_slide_correction_messages(
    messages: Sequence[Mapping[str, str]],
    outline: Mapping[str, Any],
    errors: Sequence[str],
    indices: Sequence[int],
) -> list[dict[str, str]]:
    slides = outline["slides"]
    payload = {
        "task": "repair_invalid_outline_slides",
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


def _merge_outline_slide_repairs(
    outline: Mapping[str, Any], repairs: Mapping[str, Any], indices: Sequence[int]
) -> dict[str, Any]:
    repaired_slides = repairs.get("slides")
    if not isinstance(repaired_slides, list) or len(repaired_slides) != len(indices):
        raise OutlineGenerationError("local repair returned an invalid slide count")
    expected_ids = {str(outline["slides"][index]["slide_id"]) for index in indices}
    replacements = {
        str(slide.get("slide_id")): slide
        for slide in repaired_slides
        if isinstance(slide, dict) and slide.get("slide_id")
    }
    if set(replacements) != expected_ids:
        raise OutlineGenerationError("local repair returned unexpected slide_id values")
    merged = dict(outline)
    merged["slides"] = list(outline["slides"])
    for index in indices:
        merged["slides"][index] = replacements[str(outline["slides"][index]["slide_id"])]
    return merged


@timed_stage('research.outline_generation')
def generate_with_retries(
    messages: List[Dict[str, str]],
    schema: Mapping[str, Any],
    *,
    api_key: str,
    base_url: str,
    timeout: int,
    model: str,
    max_tokens: int,
    thinking: str,
    reasoning_effort: str,
    max_attempts: int,
    api_provider: str = "deepseek",
    validate_output: bool = True,
    postprocess_outline: Callable[[Dict[str, Any]], None] | None = None,
    additional_validator: Callable[[Mapping[str, Any]], Sequence[Issue]] | None = None,
) -> Tuple[Dict[str, Any], List[Issue], int]:
    """Call DeepSeek and retry only failures that can be corrected safely."""
    attempt_messages = list(messages)
    attempt_thinking = thinking
    attempt_max_tokens = max_tokens
    last_error: Optional[OutlineGenerationError] = None
    local_repair_attempted = False

    for attempt in range(1, max_attempts + 1):
        request_body = build_request(
            attempt_messages,
            model=model,
            max_tokens=attempt_max_tokens,
            thinking=attempt_thinking,
            reasoning_effort=reasoning_effort,
            api_provider=api_provider,
        )
        content = ""
        try:
            with timing_span(f"research.outline.request attempt={attempt}"):
                response = call_deepseek(
                    request_body,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout,
                )
            content = extract_response_content(response)
            outline = parse_outline_content(content)
            with timing_span("research.outline_validation"):
                if postprocess_outline is not None:
                    postprocess_outline(outline)
                issues = validate_outline_data(outline, schema) if validate_output else []
                if validate_output and additional_validator is not None:
                    issues.extend(additional_validator(outline))
                errors = [issue for issue in issues if issue.severity == "error"]
                if errors:
                    raise OutlineValidationError(issues, content=content, outline=outline)
            return outline, issues, attempt
        except DeepSeekAPIError as exc:
            last_error = exc
            if not exc.retryable or attempt >= max_attempts:
                raise
            attempt_messages = list(messages)
        except OutlineResponseError as exc:
            last_error = exc
            if not exc.retryable or attempt >= max_attempts:
                raise
            if exc.truncated:
                attempt_messages = build_truncation_retry_messages(messages)
                attempt_thinking = "disabled"
                attempt_max_tokens = expanded_retry_token_budget(
                    attempt_max_tokens
                )
            else:
                attempt_messages = build_correction_messages(
                    messages,
                    previous_content=exc.content,
                    errors=[str(exc)],
                )
        except OutlineValidationError as exc:
            last_error = exc
            validation_errors = [
                f"{issue.code} {issue.path}: {issue.message}"
                for issue in exc.issues
                if issue.severity == "error"
            ]
            log_validation_failure("outline", attempt, validation_errors)
            if attempt >= max_attempts:
                raise
            indices = _failed_outline_slide_indices(exc.issues, exc.outline)
            if indices and not local_repair_attempted:
                local_repair_attempted = True
                try:
                    with timing_span(f"research.outline.local_repair attempt={attempt}"):
                        repair_response = call_deepseek(
                            build_request(
                                _outline_slide_correction_messages(
                                    messages, exc.outline, validation_errors, indices
                                ),
                                model=model,
                                max_tokens=attempt_max_tokens,
                                thinking=attempt_thinking,
                                reasoning_effort=reasoning_effort,
                                api_provider=api_provider,
                            ),
                            api_key=api_key,
                            base_url=base_url,
                            timeout=timeout,
                        )
                    repaired = _merge_outline_slide_repairs(
                        exc.outline,
                        parse_outline_content(extract_response_content(repair_response)),
                        indices,
                    )
                    with timing_span("research.outline_validation"):
                        if postprocess_outline is not None:
                            postprocess_outline(repaired)
                        repair_issues = (
                            validate_outline_data(repaired, schema) if validate_output else []
                        )
                        if validate_output and additional_validator is not None:
                            repair_issues.extend(additional_validator(repaired))
                    repair_errors = [
                        issue for issue in repair_issues if issue.severity == "error"
                    ]
                    if not repair_errors:
                        return repaired, repair_issues, attempt + 1
                    log_validation_failure(
                        "outline_local_repair",
                        attempt,
                        [
                            f"{issue.code} {issue.path}: {issue.message}"
                            for issue in repair_errors
                        ],
                    )
                except OutlineGenerationError as repair_error:
                    log_validation_failure(
                        "outline_local_repair", attempt, [type(repair_error).__name__]
                    )
            attempt_messages = build_correction_messages(
                messages,
                previous_content=exc.content,
                errors=validation_errors,
            )

    raise last_error or OutlineGenerationError("Outline generation failed")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a PPT outline JSON with DeepSeek V4")
    parser.add_argument("input", type=Path, help="DocumentBundle directory or document.json")
    parser.add_argument("-o", "--output", type=Path, help="Final outline JSON path")
    parser.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "schemas" / "slide_outline.schema.json",
    )
    parser.add_argument(
        "--bundle-schema",
        type=Path,
        default=PROJECT_ROOT / "schemas" / "document_bundle.schema.json",
        help="Schema used to validate DocumentBundle document.json",
    )
    parser.add_argument(
        "--system-prompt",
        type=Path,
        default=PROJECT_ROOT / "prompts" / "outline_system_prompt.md",
    )
    parser.add_argument(
        "--narrative-plan",
        type=Path,
        required=True,
        help="Verified advisory narrative_plan.json for slide planning",
    )
    parser.add_argument(
        "--few-shot",
        type=Path,
        default=PROJECT_ROOT / "prompts" / "outline_cases",
        help=(
            "Static few-shot case directory. The legacy monolithic JSON file is "
            "also accepted for compatibility."
        ),
    )
    parser.add_argument(
        "--case-trace-output",
        type=Path,
        help=(
            "Optional JSON path for the deterministic selected-case trace. "
            "Dry-run request previews always include this trace."
        ),
    )
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--api-provider",
        choices=API_PROVIDERS,
        default=os.getenv("DEEPSEEK_API_PROVIDER", "auto"),
        help="Provider request dialect; auto detects SiliconFlow from its base URL",
    )
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--chunk-chars", type=int, default=30000)
    parser.add_argument(
        "--direct-planning-max-chars",
        type=int,
        default=DEFAULT_DIRECT_PLANNING_MAX_CHARS,
        help=(
            "Skip LLM context compression when serialized chunk input is at or below "
            "this size; use 0 to always compress (default: 300000)"
        ),
    )
    parser.add_argument("--compression-max-tokens", type=int, default=4000)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="Maximum API attempts for retryable response/API failures (default: 2)",
    )
    parser.add_argument("--thinking", choices=["enabled", "disabled"], default="enabled")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "max"], default="high")
    parser.add_argument("--dry-run", action="store_true", help="Build request JSON without calling DeepSeek")
    parser.add_argument(
        "--request-output",
        type=Path,
        help="Dry-run-only path for request previews; runtime LLM memory is never persisted",
    )
    parser.add_argument("--skip-validation", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    output = args.output or Path("output/outlines") / f"{args.input.stem}_outline.json"

    try:
        if args.max_attempts < 1:
            raise OutlineGenerationError("--max-attempts must be at least 1")
        if args.direct_planning_max_chars < 0:
            raise OutlineGenerationError("--direct-planning-max-chars cannot be negative")
        if args.request_output and not args.dry_run:
            raise OutlineGenerationError("--request-output is only allowed with --dry-run")
        api_provider = resolve_api_provider(args.api_provider, args.base_url)
        snapshot = load_document_intelligence(args.input, args.bundle_schema)
        chunks = generate_chunks(snapshot, args.chunk_chars)
        input_chars = context_input_chars(chunks)
        direct_planning_max_chars = effective_direct_planning_max_chars(
            args.direct_planning_max_chars,
            api_provider,
        )
        use_direct_context = (
            direct_planning_max_chars > 0
            and input_chars <= direct_planning_max_chars
        )
        context_mode = "direct" if use_direct_context else "compressed"
        schema = load_json(args.schema, "Outline schema")
        narrative_schema = load_json(
            PROJECT_ROOT / "schemas" / "narrative_plan.schema.json",
            "Narrative plan schema",
        )
        narrative_plan = load_json(args.narrative_plan, "Narrative plan")
        validate_json_instance(
            narrative_plan,
            narrative_schema,
            label="Narrative plan",
        )
        case_library = load_case_library(args.few_shot)
        case_selection = select_cases(snapshot, case_library)
        few_shot = case_selection.prompt_payload
        case_trace = {
            **dict(case_selection.trace),
            "input": str(args.input),
            "case_library": str(args.few_shot),
        }
        system_prompt = load_text(args.system_prompt, "System prompt")
        selected_case_ids = [
            str(item.get("case_id"))
            for item in case_trace.get("selected_cases", [])
            if isinstance(item, Mapping)
        ]
        print(
            "Selected cases: "
            + (", ".join(selected_case_ids) if selected_case_ids else "(none)")
        )
        if args.case_trace_output:
            args.case_trace_output.parent.mkdir(parents=True, exist_ok=True)
            args.case_trace_output.write_text(
                json.dumps(case_trace, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"Created selected-case trace: {args.case_trace_output}")
        if args.dry_run:
            preview_memories = [
                build_direct_context_memory(chunk)
                if use_direct_context
                else preview_context_memory(chunk)
                for chunk in chunks
            ]
            messages = build_messages(
                snapshot,
                preview_memories,
                schema,
                few_shot,
                system_prompt,
                narrative_plan,
            )
            preview = {
                "pipeline": (
                    "direct_slide_planning"
                    if use_direct_context
                    else "context_compression_then_slide_planning"
                ),
                "context_mode": context_mode,
                "context_input_chars": input_chars,
                "direct_planning_max_chars": direct_planning_max_chars,
                "selected_case_trace": case_trace,
                "compression_requests": (
                    []
                    if use_direct_context
                    else [
                        build_request(
                            build_context_compression_messages(chunk),
                            model=args.model,
                            max_tokens=args.compression_max_tokens,
                            thinking=args.thinking,
                            reasoning_effort=args.reasoning_effort,
                            api_provider=api_provider,
                        )
                        for chunk in chunks
                    ]
                ),
                "slide_planning_request": build_request(
                    messages,
                    model=args.model,
                    max_tokens=args.max_tokens,
                    thinking=args.thinking,
                    reasoning_effort=args.reasoning_effort,
                    api_provider=api_provider,
                ),
            }
            if args.request_output:
                args.request_output.parent.mkdir(parents=True, exist_ok=True)
                args.request_output.write_text(
                    json.dumps(preview, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(f"Created request preview: {args.request_output}")
            print(
                f"Dry run OK: provider={api_provider}, model={args.model}, "
                f"context_mode={context_mode}, context_chars={input_chars}, "
                f"chunks={len(chunks)}, stages={1 if use_direct_context else 2}"
            )
            return 0

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise OutlineGenerationError("DEEPSEEK_API_KEY is not set")
        runtime_memories = (
            [build_direct_context_memory(chunk) for chunk in chunks]
            if use_direct_context
            else generate_context_memories(
                chunks,
                api_key=api_key,
                base_url=args.base_url,
                timeout=args.timeout,
                model=args.model,
                max_tokens=args.compression_max_tokens,
                thinking=args.thinking,
                reasoning_effort=args.reasoning_effort,
                api_provider=api_provider,
                max_attempts=args.max_attempts,
            )
        )
        allowed_runtime_evidence = {
            (str(ref.get("kind")), str(ref.get("id")))
            for memory in runtime_memories
            for ref in memory.get("evidence_refs", [])
            if isinstance(ref, Mapping)
        }
        messages = build_messages(
            snapshot,
            runtime_memories,
            schema,
            few_shot,
            system_prompt,
            narrative_plan,
        )

        def postprocess_generated_outline(value: Dict[str, Any]) -> None:
            compact_front_matter_summary_slides(value, snapshot)
            normalize_topic_sentence_key_messages(value, snapshot)
            fill_blank_key_messages(value)
            canonicalize_outline_from_bundle(
                value,
                snapshot,
                allowed_runtime_evidence,
            )
            compact_figure_pages_into_content_slides(value, snapshot)
            normalize_section_evidence_and_visual_budget(value, snapshot)
            normalize_repeated_content_titles(value)
            canonicalize_slide_section_order(value, snapshot)
            ensure_terminal_closing_slide(value, narrative_plan)

        outline, issues, attempts_used = generate_with_retries(
            messages,
            schema,
            api_key=api_key,
            base_url=args.base_url,
            timeout=args.timeout,
            model=args.model,
            max_tokens=args.max_tokens,
            thinking=args.thinking,
            reasoning_effort=args.reasoning_effort,
            api_provider=api_provider,
            max_attempts=args.max_attempts,
            validate_output=not args.skip_validation,
            postprocess_outline=postprocess_generated_outline,
            additional_validator=lambda value: validate_outline_evidence(
                value,
                snapshot,
                allowed_runtime_evidence,
            ),
        )

        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="outline_",
            dir=output.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(outline, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

        try:
            temporary_path.replace(output)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        errors = [issue for issue in issues if issue.severity == "error"]
        warnings = [issue for issue in issues if issue.severity == "warning"]
        if not args.skip_validation:
            print(f"VALID: {len(errors)} error(s), {len(warnings)} warning(s)")
        print(f"Created outline: {output}")
        print(f"Model: {args.model}")
        print(f"API provider: {api_provider}")
        print(f"Context mode: {context_mode} ({input_chars} chars)")
        print(f"Attempts: {attempts_used}/{args.max_attempts}")
        print(f"Slides: {len(outline.get('slides', []))}")
        return 0
    except (
        OutlineGenerationError,
        DocumentIntelligenceError,
        FewShotCaseError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
