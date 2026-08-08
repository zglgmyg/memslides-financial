"""Publish verified pipeline artifacts as a portable research-run directory."""

from __future__ import annotations

import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from memslides.research_pipeline.visualization_generator.manifest import canonical_sha256, visual_type


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = PROJECT_ROOT / "schemas"


class ResearchRunExportError(ValueError):
    """Raised when verified artifacts cannot form a safe research-run package."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchRunExportError(f"cannot load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchRunExportError(f"{label} root must be an object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_schema(value: Mapping[str, Any], schema_name: str, label: str) -> None:
    schema = _read_json(SCHEMA_DIRECTORY / schema_name, f"{label} schema")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        path = ".".join(str(part) for part in errors[0].absolute_path) or "$"
        raise ResearchRunExportError(
            f"{label} schema validation failed at {path}: {errors[0].message}"
        )


def _safe_asset(source_root: Path, asset_path: object) -> Path:
    relative = Path(str(asset_path or ""))
    if not str(asset_path or "") or relative.is_absolute() or ".." in relative.parts:
        raise ResearchRunExportError(f"unsafe image asset_path: {asset_path!r}")
    source_root = source_root.resolve()
    resolved = (source_root / relative).resolve()
    try:
        resolved.relative_to(source_root)
    except ValueError as exc:
        raise ResearchRunExportError(
            f"image asset escapes DocumentBundle: {asset_path!r}"
        ) from exc
    if not resolved.is_file():
        raise ResearchRunExportError(f"image asset does not exist: {resolved}")
    return resolved


def _validate_outline_semantics(outline: Mapping[str, Any]) -> set[str]:
    _validate_schema(outline, "slide_outline.schema.json", "Slide Outline")
    slide_ids = [
        str(slide.get("slide_id"))
        for slide in outline.get("slides", [])
        if isinstance(slide, Mapping)
    ]
    if len(slide_ids) != len(set(slide_ids)):
        raise ResearchRunExportError("Slide Outline contains duplicate slide_id")
    return set(slide_ids)


def _validate_visualization_semantics(
    visualization_id: str,
    artifact_type: str,
    data: Mapping[str, Any],
) -> None:
    _validate_schema(data, "visualization.schema.json", f"Visualization {visualization_id}")
    if not data.get("sources"):
        raise ResearchRunExportError(
            f"Visualization {visualization_id} has no native evidence sources"
        )
    if artifact_type == "chart":
        if data.get("chart_type") == "combo":
            raise ResearchRunExportError(
                f"Visualization {visualization_id} uses unsupported combo chart"
            )
        categories = data.get("categories", [])
        series = data.get("series", [])
        names = [str(item.get("name") or "").strip() for item in series]
        if not categories or not series:
            raise ResearchRunExportError(
                f"Visualization {visualization_id} has empty chart data"
            )
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ResearchRunExportError(
                f"Visualization {visualization_id} has empty or duplicate series names"
            )
        if any(len(item.get("values", [])) != len(categories) for item in series):
            raise ResearchRunExportError(
                f"Visualization {visualization_id} series length does not match categories"
            )
        if data.get("chart_type") == "pie" and len(series) != 1:
            raise ResearchRunExportError(
                f"Visualization {visualization_id} pie chart requires one series"
            )
    elif artifact_type == "table":
        columns = data.get("columns", [])
        rows = data.get("rows", [])
        if not columns or any(not str(column).strip() for column in columns):
            raise ResearchRunExportError(
                f"Visualization {visualization_id} has invalid table columns"
            )
        if not rows or any(
            not isinstance(row, list) or len(row) != len(columns) for row in rows
        ):
            raise ResearchRunExportError(
                f"Visualization {visualization_id} has invalid table rows"
            )


def _validate_audit(
    numeric_audit: Mapping[str, Any],
    exported: Sequence[tuple[str, str]],
) -> None:
    _validate_schema(numeric_audit, "numeric_audit.schema.json", "Numeric Audit")
    records = {
        str(record.get("visualization_id")): record
        for record in numeric_audit.get("visualizations", [])
        if isinstance(record, Mapping)
    }
    if numeric_audit.get("summary", {}).get("visualization_count") != len(exported):
        raise ResearchRunExportError(
            "Numeric Audit visualization_count does not match the Manifest"
        )
    for visualization_id, artifact_type in exported:
        record = records.get(visualization_id)
        if record is None:
            raise ResearchRunExportError(
                f"Numeric Audit has no record for {visualization_id}"
            )
        if artifact_type in {"chart", "table"} and record.get("status") != "passed":
            raise ResearchRunExportError(
                f"Numeric Audit did not pass for {visualization_id}"
            )
        if any(
            entry.get("status") != "matched"
            for entry in record.get("entries", [])
            if isinstance(entry, Mapping)
        ):
            raise ResearchRunExportError(
                f"Numeric Audit contains a mismatch for {visualization_id}"
            )


def export_research_run(
    *,
    output_directory: Path,
    outline: Mapping[str, Any],
    numeric_audit: Mapping[str, Any],
    artifacts: Sequence[Any],
    document_bundle_directory: Path,
    document_source_sha256: str,
    overwrite: bool = False,
    speaker_manuscript: Mapping[str, Any] | None = None,
    speaker_manuscript_markdown: str | None = None,
) -> Path:
    """Atomically publish only the files required by the financial adapter."""

    output_directory = output_directory.resolve()
    if output_directory.exists() and not overwrite:
        raise ResearchRunExportError(
            f"output directory already exists: {output_directory}; use overwrite=True"
        )
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.staging-",
            dir=output_directory.parent,
        )
    )
    try:
        slide_ids = _validate_outline_semantics(outline)
        visualizations_directory = staging / "visualizations"
        image_directory = visualizations_directory / "images"
        visualizations_directory.mkdir(parents=True)

        counters = {"chart": 0, "table": 0, "image": 0}
        seen_visualization_ids: set[str] = set()
        bindings: list[dict[str, Any]] = []
        exported: list[tuple[str, str]] = []

        for artifact in artifacts:
            visualization_id = str(artifact.visualization_id)
            slide_id = str(artifact.slide_id)
            if visualization_id in seen_visualization_ids:
                raise ResearchRunExportError(
                    f"duplicate visualization_id: {visualization_id}"
                )
            if slide_id not in slide_ids:
                raise ResearchRunExportError(
                    f"Visualization {visualization_id} references unknown slide {slide_id}"
                )
            seen_visualization_ids.add(visualization_id)

            data = deepcopy(dict(artifact.data))
            artifact_type = visual_type(data)
            if artifact_type not in counters:
                raise ResearchRunExportError(
                    f"cannot identify Visualization type for {visualization_id}"
                )
            counters[artifact_type] += 1
            filename = f"{artifact_type}_{counters[artifact_type]:03d}.json"

            if artifact_type == "image":
                source_asset = _safe_asset(
                    document_bundle_directory,
                    data.get("asset_path"),
                )
                suffix = source_asset.suffix.lower() or ".bin"
                image_name = f"figure_{counters['image']:03d}{suffix}"
                image_directory.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_asset, image_directory / image_name)
                data["asset_path"] = f"images/{image_name}"

            _validate_visualization_semantics(visualization_id, artifact_type, data)
            _write_json(visualizations_directory / filename, data)
            bindings.append(
                {
                    "slide_id": slide_id,
                    "visualization_id": visualization_id,
                    "visual_type": artifact_type,
                    "sources": [dict(source) for source in artifact.sources],
                    "visualization_file": filename,
                }
            )
            exported.append((visualization_id, artifact_type))

        _validate_audit(numeric_audit, exported)
        manifest = {
            "schema_version": "3.0.0",
            "outline_sha256": canonical_sha256(outline),
            "document_source_sha256": document_source_sha256,
            "asset_root": ".",
            "bindings": bindings,
        }
        _validate_schema(
            manifest,
            "visualization_manifest.schema.json",
            "Visualization Manifest",
        )
        _write_json(staging / "slide_outline.json", outline)
        _write_json(staging / "numeric_audit.json", numeric_audit)
        if speaker_manuscript is not None:
            _validate_schema(
                speaker_manuscript,
                "speaker_manuscript.schema.json",
                "Speaker Manuscript",
            )
            _write_json(staging / "speaker_manuscript.json", speaker_manuscript)
            (staging / "speaker_manuscript.md").write_text(
                speaker_manuscript_markdown or "",
                encoding="utf-8",
            )
        _write_json(visualizations_directory / "visualization_manifest.json", manifest)

        if output_directory.exists():
            if not output_directory.is_dir():
                raise ResearchRunExportError(
                    f"output target is not a directory: {output_directory}"
                )
            shutil.rmtree(output_directory)
        staging.replace(output_directory)
        return output_directory
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
