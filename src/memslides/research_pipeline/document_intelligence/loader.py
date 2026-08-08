"""Load and validate a DocumentBundle without interpreting its semantics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .index import build_snapshot
from .models import DocumentIntelligenceSnapshot


class DocumentIntelligenceError(ValueError):
    pass


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DocumentIntelligenceError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DocumentIntelligenceError(
            f"{label} is not valid JSON: {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise DocumentIntelligenceError(f"{label} root must be an object: {path}")
    return value


def load_document_intelligence(
    input_path: Path,
    schema_path: Path,
) -> DocumentIntelligenceSnapshot:
    document_path = input_path / "document.json" if input_path.is_dir() else input_path
    bundle_directory = document_path.parent
    document = _load_object(document_path, "DocumentBundle document.json")
    schema = _load_object(schema_path, "DocumentBundle schema")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        details = []
        for error in errors[:20]:
            location = "$" + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}"
                for part in error.absolute_path
            )
            details.append(f"- {location}: {error.message}")
        raise DocumentIntelligenceError(
            f"DocumentBundle failed schema validation ({len(errors)} error(s)):\n"
            + "\n".join(details)
        )
    return build_snapshot(document, bundle_directory)
