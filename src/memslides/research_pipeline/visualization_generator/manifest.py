"""Validated Visualization Manifest loading for compiler consumers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_SCHEMA = PROJECT_ROOT / "schemas/visualization_manifest.schema.json"
DEFAULT_VISUALIZATION_SCHEMA = PROJECT_ROOT / "schemas/visualization.schema.json"


class VisualizationManifestError(ValueError):
    """Raised when a manifest or one of its artifacts is invalid."""


@dataclass(frozen=True, slots=True)
class LoadedVisualizationManifest:
    path: Path
    data: Mapping[str, Any]
    asset_root: Path
    visualizations_by_id: Mapping[str, Mapping[str, Any]]
    bindings_by_slide: Mapping[str, tuple[Mapping[str, Any], ...]]


def canonical_sha256(value: Mapping[str, Any]) -> str:
    import hashlib

    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def visual_type(value: Mapping[str, Any]) -> str | None:
    if value.get("type") == "image":
        return "image"
    if "chart_type" in value:
        return "chart"
    if "columns" in value:
        return "table"
    return None


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise VisualizationManifestError(f"cannot load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VisualizationManifestError(f"{label} root must be an object")
    return value


def _validate(value: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise VisualizationManifestError(
            f"{label} schema validation failed: {errors[0].message}"
        )


def _normalized_v2(
    manifest: Mapping[str, Any], manifest_path: Path
) -> dict[str, Any]:
    """Read-only compatibility for manifests emitted before this contract."""

    bindings = []
    for raw in manifest.get("bindings", []):
        if not isinstance(raw, Mapping):
            continue
        artifact_path = manifest_path.parent / str(raw.get("visualization_file", ""))
        artifact = _load_json(artifact_path, "Visualization")
        bindings.append(
            {
                "slide_id": raw.get("slide_id"),
                "visualization_id": raw.get("visualization_id"),
                "visual_type": visual_type(artifact),
                "sources": list(raw.get("sources", [])),
                "visualization_file": raw.get("visualization_file"),
            }
        )
    return {
        "schema_version": "3.0.0",
        "outline_sha256": "0" * 64,
        "document_source_sha256": "0" * 64,
        "asset_root": str(manifest.get("asset_root") or manifest_path.parent),
        "bindings": bindings,
    }


def load_visualization_manifest(
    path: Path,
    *,
    manifest_schema_path: Path = DEFAULT_MANIFEST_SCHEMA,
    visualization_schema_path: Path = DEFAULT_VISUALIZATION_SCHEMA,
    allow_legacy_v2: bool = True,
) -> LoadedVisualizationManifest:
    manifest_path = path.resolve()
    manifest = _load_json(manifest_path, "Visualization Manifest")
    if manifest.get("schema_version") == "2.0" and allow_legacy_v2:
        manifest = _normalized_v2(manifest, manifest_path)
    manifest_schema = _load_json(manifest_schema_path, "Visualization Manifest schema")
    visualization_schema = _load_json(
        visualization_schema_path, "Visualization schema"
    )
    _validate(manifest, manifest_schema, "Visualization Manifest")

    asset_root = Path(str(manifest["asset_root"]))
    if not asset_root.is_absolute():
        asset_root = (manifest_path.parent / asset_root).resolve()
    else:
        asset_root = asset_root.resolve()
    if not asset_root.is_dir():
        raise VisualizationManifestError(
            f"Visualization Manifest asset_root does not exist: {asset_root}"
        )

    by_id: dict[str, Mapping[str, Any]] = {}
    by_slide: dict[str, list[Mapping[str, Any]]] = {}
    for binding in manifest["bindings"]:
        visualization_id = str(binding["visualization_id"])
        if visualization_id in by_id:
            raise VisualizationManifestError(
                f"duplicate visualization_id: {visualization_id}"
            )
        relative = Path(str(binding["visualization_file"]))
        artifact_path = (manifest_path.parent / relative).resolve()
        try:
            artifact_path.relative_to(manifest_path.parent)
        except ValueError as exc:
            raise VisualizationManifestError(
                f"visualization_file escapes manifest directory: {relative}"
            ) from exc
        artifact = _load_json(artifact_path, "Visualization")
        _validate(artifact, visualization_schema, "Visualization")
        actual_type = visual_type(artifact)
        if actual_type != binding["visual_type"]:
            raise VisualizationManifestError(
                f"visualization {visualization_id!r} declares "
                f"{binding['visual_type']!r} but contains {actual_type!r}"
            )
        record = {
            "visualization_id": visualization_id,
            "slide_id": str(binding["slide_id"]),
            "visual_type": actual_type,
            "visualization_file": str(relative.as_posix()),
            "data": artifact,
        }
        by_id[visualization_id] = record
        by_slide.setdefault(record["slide_id"], []).append(record)
    return LoadedVisualizationManifest(
        manifest_path,
        manifest,
        asset_root,
        by_id,
        {key: tuple(values) for key, values in by_slide.items()},
    )
