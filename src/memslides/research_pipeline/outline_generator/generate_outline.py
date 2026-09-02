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

import argparse
import http.client
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreparedOutlineContext:
    """Transient runtime context prepared before Narrative and Outline converge."""

    bundle_directory: Path
    runtime_memories: tuple[dict[str, Any], ...]
    input_chars: int
    direct_planning_max_chars: int
    context_mode: str


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

    def __init__(self, issues: Sequence[Issue], *, content: str):
        errors = [issue for issue in issues if issue.severity == "error"]
        summary = "\n".join(
            f"- {issue.code} {issue.path}: {issue.message}" for issue in errors[:20]
        )
        super().__init__(f"Generated outline failed validation:\n{summary}")
        self.issues = list(issues)
        self.content = content
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
                    stage=f"context_compression:{chunk.id}:attempt_{attempt}",
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


def prepare_outline_context(
    snapshot: DocumentIntelligenceSnapshot,
    *,
    api_key: str,
    base_url: str,
    timeout: int,
    model: str,
    max_attempts: int,
    api_provider: str = "auto",
    chunk_chars: int = 30_000,
    direct_planning_max_chars: int = DEFAULT_DIRECT_PLANNING_MAX_CHARS,
    compression_max_tokens: int = 4_000,
    thinking: str = "enabled",
    reasoning_effort: str = "high",
) -> PreparedOutlineContext:
    """Prepare the unchanged runtime memories consumed by Slide Planning."""

    if direct_planning_max_chars < 0:
        raise OutlineGenerationError("direct_planning_max_chars cannot be negative")
    provider = resolve_api_provider(api_provider, base_url)
    chunks = generate_chunks(snapshot, chunk_chars)
    input_chars = context_input_chars(chunks)
    effective_limit = effective_direct_planning_max_chars(
        direct_planning_max_chars,
        provider,
    )
    use_direct_context = effective_limit > 0 and input_chars <= effective_limit
    memories = (
        [build_direct_context_memory(chunk) for chunk in chunks]
        if use_direct_context
        else generate_context_memories(
            chunks,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            model=model,
            max_tokens=compression_max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            api_provider=provider,
            max_attempts=max_attempts,
        )
    )
    return PreparedOutlineContext(
        bundle_directory=snapshot.bundle_directory.resolve(),
        runtime_memories=tuple(memories),
        input_chars=input_chars,
        direct_planning_max_chars=effective_limit,
        context_mode="direct" if use_direct_context else "compressed",
    )


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
        request["stream"] = True
        request["stream_options"] = {"include_usage": True}
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


def _stream_response_payload(
    response: Any, *, started_at: float, stage: str
) -> Dict[str, Any]:
    """Reassemble a Chat Completions SSE response and attach phase timings."""

    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    first_token_at: float | None = None
    first_reasoning_at: float | None = None
    first_content_at: float | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    response_metadata: dict[str, Any] = {}

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise DeepSeekAPIError(
                "DeepSeek API stream contains invalid JSON",
                retryable=True,
            ) from exc
        if not isinstance(chunk, dict):
            continue
        for key in ("id", "model", "created", "system_fingerprint"):
            if key in chunk:
                response_metadata[key] = chunk[key]
        chunk_usage = chunk.get("usage")
        if isinstance(chunk_usage, dict):
            usage = dict(chunk_usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, Mapping):
            continue
        if choice.get("finish_reason") is not None:
            finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            continue
        reasoning = delta.get("reasoning_content")
        content = delta.get("content")
        now = time.perf_counter()
        if isinstance(reasoning, str) and reasoning:
            if first_token_at is None:
                first_token_at = now
            if first_reasoning_at is None:
                first_reasoning_at = now
            reasoning_parts.append(reasoning)
        if isinstance(content, str) and content:
            if first_token_at is None:
                first_token_at = now
            if first_content_at is None:
                first_content_at = now
            content_parts.append(content)

    completed_at = time.perf_counter()
    reasoning_tokens = None
    completion_details = usage.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        reasoning_tokens = completion_details.get("reasoning_tokens")
    timing = {
        "stage": stage,
        "elapsed_seconds": round(completed_at - started_at, 3),
        "ttft_seconds": (
            round(first_token_at - started_at, 3)
            if first_token_at is not None
            else None
        ),
        "thinking_seconds": (
            round((first_content_at or completed_at) - first_reasoning_at, 3)
            if first_reasoning_at is not None
            else 0.0
        ),
        "content_seconds": (
            round(completed_at - first_content_at, 3)
            if first_content_at is not None
            else 0.0
        ),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": reasoning_tokens,
        "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
        "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
        "finish_reason": finish_reason,
    }
    print("API_TIMING " + json.dumps(timing, ensure_ascii=False, sort_keys=True))
    return {
        **response_metadata,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "reasoning_content": "".join(reasoning_parts),
                    "content": "".join(content_parts),
                },
            }
        ],
        "usage": usage,
        "_memslides_timing": timing,
    }


def call_deepseek(
    request_body: Mapping[str, Any],
    *,
    api_key: str,
    base_url: str,
    timeout: int,
    stage: str = "model",
) -> Dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    serialized_payload = json.dumps(request_body, ensure_ascii=False)
    body = serialized_payload.encode("utf-8")
    messages = request_body.get("messages", [])
    message_count = len(messages) if isinstance(messages, list) else 0
    print(
        "Calling model API: "
        f"stage={stage}, url={endpoint}, messages={message_count}, "
        f"payload_bytes={len(body)}, "
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
    started_at = time.perf_counter()
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            if request_body.get("stream") is True:
                payload = _stream_response_payload(
                    response,
                    started_at=started_at,
                    stage=stage,
                )
            else:
                payload = json.loads(response.read().decode("utf-8"))
                completed_at = time.perf_counter()
                usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
                timing = {
                    "stage": stage,
                    "elapsed_seconds": round(completed_at - started_at, 3),
                    "ttft_seconds": None,
                    "thinking_seconds": None,
                    "content_seconds": None,
                    "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, Mapping) else None,
                    "completion_tokens": usage.get("completion_tokens") if isinstance(usage, Mapping) else None,
                    "reasoning_tokens": None,
                    "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens") if isinstance(usage, Mapping) else None,
                    "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens") if isinstance(usage, Mapping) else None,
                    "finish_reason": None,
                }
                print("API_TIMING " + json.dumps(timing, ensure_ascii=False, sort_keys=True))
                if isinstance(payload, dict):
                    payload["_memslides_timing"] = timing
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


def build_truncation_continuation_request(
    original_messages: Sequence[Mapping[str, str]],
    partial_content: str,
    *,
    model: str,
    max_tokens: int,
) -> Dict[str, Any]:
    """Continue the exact JSON prefix without regenerating completed content."""

    request = build_request(
        [
            *[dict(message) for message in original_messages],
            {
                "role": "assistant",
                "content": partial_content,
                "prefix": True,
            },
        ],
        model=model,
        max_tokens=max_tokens,
        thinking="disabled",
        reasoning_effort="high",
        api_provider="deepseek",
    )
    # DeepSeek's beta prefix-completion endpoint rejects JSON mode. The
    # existing assistant prefix already constrains the combined result to the
    # JSON object started by the original response.
    request.pop("response_format", None)
    return request


def deepseek_beta_base_url(base_url: str) -> str:
    """Select the official endpoint required by Chat Prefix Completion."""

    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/beta") else normalized + "/beta"


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
            response = call_deepseek(
                request_body,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                stage=f"outline:attempt_{attempt}",
            )
            content = extract_response_content(response)
            outline = parse_outline_content(content)
            if postprocess_outline is not None:
                postprocess_outline(outline)
            issues = validate_outline_data(outline, schema) if validate_output else []
            if validate_output and additional_validator is not None:
                issues.extend(additional_validator(outline))
            errors = [issue for issue in issues if issue.severity == "error"]
            if errors:
                raise OutlineValidationError(issues, content=content)
            return outline, issues, attempt
        except DeepSeekAPIError as exc:
            last_error = exc
            if not exc.retryable or attempt >= max_attempts:
                raise
            attempt_messages = list(messages)
        except OutlineResponseError as exc:
            last_error = exc
            if not exc.retryable:
                raise
            if exc.truncated:
                if api_provider == "deepseek" and exc.content.strip():
                    try:
                        continuation = call_deepseek(
                            build_truncation_continuation_request(
                                messages,
                                exc.content,
                                model=model,
                                max_tokens=expanded_retry_token_budget(max_tokens),
                            ),
                            api_key=api_key,
                            base_url=deepseek_beta_base_url(base_url),
                            timeout=timeout,
                            stage=f"outline_continuation:after_attempt_{attempt}",
                        )
                        continued_content = extract_response_content(continuation)
                        merged_content = exc.content + continued_content
                        outline = parse_outline_content(merged_content)
                        if postprocess_outline is not None:
                            postprocess_outline(outline)
                        issues = (
                            validate_outline_data(outline, schema)
                            if validate_output
                            else []
                        )
                        if validate_output and additional_validator is not None:
                            issues.extend(additional_validator(outline))
                        errors = [
                            issue for issue in issues if issue.severity == "error"
                        ]
                        if errors:
                            raise OutlineValidationError(
                                issues,
                                content=merged_content,
                            )
                        print("Recovered truncated model output by prefix continuation")
                        return outline, issues, attempt
                    except (
                        DeepSeekAPIError,
                        OutlineResponseError,
                        OutlineValidationError,
                    ) as continuation_error:
                        print(
                            "Prefix continuation failed; falling back to complete "
                            f"regeneration: {continuation_error}"
                        )
                if attempt >= max_attempts:
                    raise
                attempt_messages = build_truncation_retry_messages(messages)
                attempt_thinking = "disabled"
                attempt_max_tokens = expanded_retry_token_budget(
                    attempt_max_tokens
                )
            else:
                if attempt >= max_attempts:
                    raise
                attempt_messages = build_correction_messages(
                    messages,
                    previous_content=exc.content,
                    errors=[str(exc)],
                )
        except OutlineValidationError as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise
            attempt_messages = build_correction_messages(
                messages,
                previous_content=exc.content,
                errors=[
                    f"{issue.code} {issue.path}: {issue.message}"
                    for issue in exc.issues
                    if issue.severity == "error"
                ],
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
        help=(
            "Maximum complete-generation attempts for retryable failures; a truncated "
            "DeepSeek response may add one prefix-continuation call (default: 2)"
        ),
    )
    parser.add_argument("--thinking", choices=["enabled", "disabled"], default="disabled")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "max"], default="high")
    parser.add_argument("--dry-run", action="store_true", help="Build request JSON without calling DeepSeek")
    parser.add_argument(
        "--request-output",
        type=Path,
        help="Dry-run-only path for request previews; runtime LLM memory is never persisted",
    )
    parser.add_argument("--skip-validation", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    prepared_context: PreparedOutlineContext | None = None,
) -> int:
    args = parse_args(argv)
    output = args.output or Path("output/outlines") / f"{args.input.stem}_outline.json"

    try:
        if args.max_attempts < 1:
            raise OutlineGenerationError("--max-attempts must be at least 1")
        if args.direct_planning_max_chars < 0:
            raise OutlineGenerationError("--direct-planning-max-chars cannot be negative")
        if args.request_output and not args.dry_run:
            raise OutlineGenerationError("--request-output is only allowed with --dry-run")
        if args.dry_run and prepared_context is not None:
            raise OutlineGenerationError("prepared context is not accepted in --dry-run mode")
        api_provider = resolve_api_provider(args.api_provider, args.base_url)
        snapshot = load_document_intelligence(args.input, args.bundle_schema)
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
        if prepared_context is None:
            prepared_context = prepare_outline_context(
                snapshot,
                api_key=api_key,
                base_url=args.base_url,
                timeout=args.timeout,
                model=args.model,
                max_attempts=args.max_attempts,
                api_provider=api_provider,
                chunk_chars=args.chunk_chars,
                direct_planning_max_chars=args.direct_planning_max_chars,
                compression_max_tokens=args.compression_max_tokens,
                thinking=args.thinking,
                reasoning_effort=args.reasoning_effort,
            )
        elif prepared_context.bundle_directory != snapshot.bundle_directory.resolve():
            raise OutlineGenerationError(
                "prepared context belongs to a different DocumentBundle"
            )
        runtime_memories = list(prepared_context.runtime_memories)
        input_chars = prepared_context.input_chars
        direct_planning_max_chars = prepared_context.direct_planning_max_chars
        context_mode = prepared_context.context_mode
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
