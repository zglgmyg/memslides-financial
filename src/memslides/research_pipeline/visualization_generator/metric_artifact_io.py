"""Schema-validated readers for persisted Phase 1 metric artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class MetricArtifactError(ValueError):
    """Raised when a persisted typed-metric artifact is unusable."""


def load_metric_artifact(
    artifact_path: Path,
    schema_path: Path,
    *,
    collection_key: str,
    count_key: str,
) -> dict[str, Any]:
    """Load one metric artifact and enforce schema plus count consistency."""

    try:
        value = json.loads(artifact_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MetricArtifactError(f"metric artifact input not found: {exc.filename}") from exc
    except json.JSONDecodeError as exc:
        raise MetricArtifactError(
            f"invalid JSON at {exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict) or not isinstance(schema, dict):
        raise MetricArtifactError("metric artifact and schema roots must be objects")

    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in first.absolute_path
        )
        raise MetricArtifactError(f"schema validation failed at {location}: {first.message}")

    collection = value.get(collection_key)
    if not isinstance(collection, list) or value.get(count_key) != len(collection):
        raise MetricArtifactError(
            f"{count_key} must equal the length of {collection_key}"
        )
    return value


def load_numeric_fact_ledger(path: Path, schema_path: Path) -> dict[str, Any]:
    return load_metric_artifact(
        path,
        schema_path,
        collection_key="facts",
        count_key="fact_count",
    )


def load_metric_group_catalog(path: Path, schema_path: Path) -> dict[str, Any]:
    return load_metric_artifact(
        path,
        schema_path,
        collection_key="groups",
        count_key="group_count",
    )
