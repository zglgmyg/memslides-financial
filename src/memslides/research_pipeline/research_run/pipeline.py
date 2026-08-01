"""Slim orchestration for report-to-research-run generation."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from memslides.research_pipeline.document_bundle.bundle import parse_pdf
from memslides.research_pipeline.document_bundle.config import MinerUConfig
from memslides.research_pipeline.document_bundle.markdown import build_from_markdown
from memslides.research_pipeline.document_bundle.parser.mineru_client import MinerUClient
from memslides.research_pipeline.document_intelligence import load_document_intelligence
from memslides.research_pipeline.outline_generator.generate_outline import main as generate_outline_main
from memslides.research_pipeline.visualization_generator.audit import audit_visualization_artifacts
from memslides.research_pipeline.visualization_generator.generate_visualizations import generate_visualizations
from memslides.research_pipeline.visualization_generator.numeric_facts import build_numeric_fact_ledger

from .exporter import PROJECT_ROOT, export_research_run


class ResearchRunPipelineError(RuntimeError):
    """Raised when one pipeline stage cannot produce a verified result."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchRunPipelineError(f"cannot load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchRunPipelineError(f"{label} root must be an object")
    return value


def _validate_schema(value: Mapping[str, Any], schema_name: str, label: str) -> None:
    schema = _load_json(PROJECT_ROOT / "schemas" / schema_name, f"{label} schema")
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ResearchRunPipelineError(
            f"{label} schema validation failed: {errors[0].message}"
        )


def _materialize_bundle(input_path: Path, working_directory: Path) -> Path:
    if input_path.is_dir() and (input_path / "document.json").is_file():
        return input_path.resolve()
    if input_path.name == "document.json" and input_path.is_file():
        return input_path.parent.resolve()
    if not input_path.is_file():
        raise ResearchRunPipelineError(f"input does not exist: {input_path}")

    if input_path.suffix.casefold() == ".pdf":
        parse_root = working_directory / "pdf_parse"
        with MinerUClient(MinerUConfig()) as client:
            bundle_directory, _, validation = parse_pdf(
                input_path.resolve(),
                parse_root,
                input_path.stem,
                client,
            )
    else:
        bundle_directory = working_directory / "document_bundle"
        _, validation = build_from_markdown(
            input_path.resolve(),
            bundle_directory,
            input_path.stem,
            source_format="auto",
        )
    if validation.get("status") == "failed":
        raise ResearchRunPipelineError("DocumentBundle validation failed")
    return bundle_directory


def _materialize_outline(
    *,
    bundle_directory: Path,
    working_directory: Path,
    outline_input: Path | None,
    model: str | None,
    base_url: str | None,
    api_provider: str | None,
    max_tokens: int | None,
    max_attempts: int | None,
    timeout: int | None,
) -> dict[str, Any]:
    outline_path = working_directory / "slide_outline.json"
    if outline_input is not None:
        outline = _load_json(outline_input.resolve(), "Slide Outline")
        _validate_schema(outline, "slide_outline.schema.json", "Slide Outline")
        return outline

    forwarded = [str(bundle_directory), "-o", str(outline_path)]
    for option, value in (
        ("--model", model),
        ("--base-url", base_url),
        ("--api-provider", api_provider),
        ("--max-tokens", max_tokens),
        ("--max-attempts", max_attempts),
        ("--timeout", timeout),
    ):
        if value is not None:
            forwarded.extend([option, str(value)])
    exit_code = generate_outline_main(forwarded)
    if exit_code != 0 or not outline_path.is_file():
        raise ResearchRunPipelineError(
            f"Outline generation exited with status {exit_code}"
        )
    return _load_json(outline_path, "Slide Outline")


def _blocking_generation_issues(
    outline: Mapping[str, Any], issues: Sequence[Any]
) -> tuple[list[Any], list[Any]]:
    explicit_ids = {
        str(candidate.get("candidate_id"))
        for slide in outline.get("slides", [])
        if isinstance(slide, Mapping)
        for candidate in slide.get("visual_candidates", [])
        if isinstance(candidate, Mapping) and candidate.get("candidate_id")
    }
    safe_codes = (
        "reject.mixed_metric",
        "reject.mixed_measure_kind",
        "reject.mixed_unit_family",
        "reject.mixed_unit_scale",
        "reject.mixed_currency",
        "reject.mixed_entity",
        "reject.mixed_scope",
        "reject.mixed_scenario",
        "reject.invalid_forecast_boundary",
        "reject.incomplete_metric_typing",
        "reject.invalid_category_count",
    )
    warnings = [
        issue
        for issue in issues
        if issue.visualization_id not in explicit_ids
        or issue.reason == "no_traceable_source_data"
        or any(code in issue.reason for code in safe_codes)
    ]
    warning_ids = {id(issue) for issue in warnings}
    return [issue for issue in issues if id(issue) not in warning_ids], warnings


def run_research_pipeline(
    input_path: Path,
    output_directory: Path,
    *,
    outline_input: Path | None = None,
    candidate_mode: str = "active",
    overwrite: bool = False,
    model: str | None = None,
    base_url: str | None = None,
    api_provider: str | None = None,
    max_tokens: int | None = None,
    max_attempts: int | None = None,
    timeout: int | None = None,
) -> tuple[Path, tuple[str, ...]]:
    """Generate a portable research-run directory without rendering a PPT."""

    if candidate_mode not in {"active", "shadow", "disabled"}:
        raise ResearchRunPipelineError(f"invalid candidate_mode: {candidate_mode}")
    temporary_root = Path(tempfile.mkdtemp(prefix="research-run-work-"))
    try:
        bundle_directory = _materialize_bundle(input_path, temporary_root)
        snapshot = load_document_intelligence(
            bundle_directory,
            PROJECT_ROOT / "schemas" / "document_bundle.schema.json",
        )
        outline = _materialize_outline(
            bundle_directory=bundle_directory,
            working_directory=temporary_root,
            outline_input=outline_input,
            model=model,
            base_url=base_url,
            api_provider=api_provider,
            max_tokens=max_tokens,
            max_attempts=max_attempts,
            timeout=timeout,
        )
        artifacts, issues = generate_visualizations(
            outline,
            snapshot,
            candidate_mode=candidate_mode,
        )
        blocking, warnings = _blocking_generation_issues(outline, issues)
        if blocking:
            raise ResearchRunPipelineError(blocking[0].format())
        ledger = build_numeric_fact_ledger(snapshot)
        numeric_audit = audit_visualization_artifacts(artifacts, ledger)
        source_sha256 = str(snapshot.metadata.get("source_sha256") or "0" * 64)
        result = export_research_run(
            output_directory=output_directory,
            outline=outline,
            numeric_audit=numeric_audit,
            artifacts=artifacts,
            document_bundle_directory=bundle_directory,
            document_source_sha256=source_sha256,
            overwrite=overwrite,
        )
        return result, tuple(issue.format() for issue in warnings)
    except ResearchRunPipelineError:
        raise
    except Exception as exc:
        raise ResearchRunPipelineError(str(exc)) from exc
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
