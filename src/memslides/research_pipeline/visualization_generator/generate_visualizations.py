"""CLI/facade for native DocumentBundle visualization planning and generation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from memslides.research_pipeline.document_intelligence import build_snapshot, load_document_intelligence
from memslides.research_pipeline.document_intelligence.models import DocumentIntelligenceSnapshot
from .generator import GenerationIssue, VisualizationArtifact, generate_from_plans
from .extraction import LLMExtractionAdapter
from .manifest import canonical_sha256, visual_type
from .planning import VisualizationPlanningError, plan_visualizations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "visualization.schema.json"
DEFAULT_BUNDLE_SCHEMA = PROJECT_ROOT / "schemas" / "document_bundle.schema.json"
DEFAULT_LAYOUT_MAP = PROJECT_ROOT / "templates" / "template_layout_map.json"


@dataclass(frozen=True)
class VisualizationCoverageWarning:
    slide_id: str
    required_visual: str
    layout_id: str
    reason: str

    def format(self) -> str:
        return (
            "[visualization-warning]\n"
            f"slide_id={self.slide_id}\n"
            f"required_visual={self.required_visual}\n"
            f"layout_id={self.layout_id}\n"
            f"reason={self.reason}"
        )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def generate_visualizations(
    outline: Mapping[str, Any],
    snapshot: DocumentIntelligenceSnapshot | Mapping[str, Any],
    *,
    schema: Mapping[str, Any] | None = None,
    llm_adapter: LLMExtractionAdapter | None = None,
    candidate_mode: str = "active",
) -> tuple[list[VisualizationArtifact], list[GenerationIssue]]:
    """Plan semantically, then extract only verified DocumentBundle content."""

    if isinstance(snapshot, Mapping):
        snapshot = build_snapshot(snapshot, Path.cwd())
    schema = schema or _load_json(DEFAULT_SCHEMA, "Visualization schema")
    plans = plan_visualizations(outline, snapshot, candidate_mode=candidate_mode)
    return generate_from_plans(
        plans,
        snapshot,
        schema,
        llm_adapter=llm_adapter,
    )


def bindings_from_artifacts(
    artifacts: Iterable[VisualizationArtifact],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        result.setdefault(artifact.slide_id, []).append(artifact.data)
    return result


def _visual_type(data: Mapping[str, Any]) -> str | None:
    if data.get("type") == "image":
        return "image"
    if "chart_type" in data:
        return "chart"
    if "columns" in data:
        return "table"
    return None


def preflight_visualizations(
    outline: Mapping[str, Any],
    layout_map: Mapping[str, Any],
    bindings: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[VisualizationCoverageWarning]:
    """Report missing visual artifacts or layouts that cannot render them."""

    # Layout coverage belongs to the optional PPT integration.  Keeping this
    # import local lets the standalone research-run pipeline generate verified
    # data artifacts without installing the PowerPoint engine.
    from ppt_engine.layout_resolver import resolve_outline

    resolutions = {
        item.slide_id: item
        for item in resolve_outline(
            outline,
            visualizations_by_slide=bindings,
            layout_map=layout_map,
            debug=False,
        )
    }
    warnings: list[VisualizationCoverageWarning] = []
    for slide in outline.get("slides", []):
        if not isinstance(slide, Mapping):
            continue
        slide_id = str(slide.get("slide_id", ""))
        resolution = resolutions.get(slide_id)
        if resolution is None:
            continue
        fields = layout_map["layouts"][resolution.layout_id].get("fields", {})
        supported: set[str] = set()
        for spec in fields.values():
            if not isinstance(spec, Mapping):
                continue
            if spec.get("type") == "chart_slot":
                supported.add("chart")
            elif spec.get("type") == "table":
                supported.add("table")
            elif spec.get("type") == "image_slot":
                supported.add("image")
        requested = {
            str(candidate.get("type"))
            for candidate in slide.get("visual_candidates", [])
            if isinstance(candidate, Mapping)
            and candidate.get("type") in {"chart", "table", "image"}
        }
        if slide.get("slide_type") == "figure_page":
            requested.add("image")
        available = {
            visual_type
            for item in bindings.get(slide_id, [])
            if (visual_type := _visual_type(item)) is not None
        }
        for visual_type in sorted((requested & supported) - available):
            warnings.append(
                VisualizationCoverageWarning(
                    slide_id,
                    visual_type,
                    resolution.layout_id,
                    f"no_{visual_type}_visualization_data",
                )
            )
        for visual_type in sorted(available - supported):
            warnings.append(
                VisualizationCoverageWarning(
                    slide_id,
                    visual_type,
                    resolution.layout_id,
                    f"{visual_type}_not_supported_by_layout",
                )
            )
    return warnings


def warn_for_render_args(argv: Sequence[str]) -> None:
    """Emit non-blocking coverage warnings before the Renderer CLI is entered."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("outline", type=Path)
    parser.add_argument("-o", "--output")
    parser.add_argument("--template")
    parser.add_argument("--layout-map", type=Path, default=DEFAULT_LAYOUT_MAP)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--visualization", action="append", default=[])
    try:
        from ppt_engine.layout_resolver import load_layout_map

        args, _ = parser.parse_known_args(argv)
        outline = _load_json(args.outline, "outline")
        layout_map = load_layout_map(args.layout_map)
        bindings: dict[str, list[dict[str, Any]]] = {}
        for item in args.visualization:
            if "=" not in item:
                continue
            slide_id, path = item.split("=", 1)
            bindings.setdefault(slide_id, []).append(_load_json(Path(path), "visualization"))
        for warning in preflight_visualizations(outline, layout_map, bindings):
            print(warning.format(), file=sys.stderr)
    except (OSError, ValueError):
        return


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-") or "visualization"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate evidence-backed Visualization JSON from Slide Outline and DocumentBundle"
    )
    parser.add_argument("outline", type=Path, help="Slide Outline JSON")
    parser.add_argument("document", type=Path, help="DocumentBundle directory or document.json")
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--layout-map", type=Path, default=DEFAULT_LAYOUT_MAP)
    parser.add_argument("--bundle-schema", type=Path, default=DEFAULT_BUNDLE_SCHEMA)
    parser.add_argument("--visualization-schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--candidate-mode",
        choices=["shadow", "active", "disabled"],
        default="shadow",
        help="Candidate Locator policy; shadow records candidates without adding slides",
    )
    args = parser.parse_args(argv)
    try:
        from ppt_engine.layout_resolver import load_layout_map

        outline = _load_json(args.outline, "outline")
        snapshot = load_document_intelligence(args.document, args.bundle_schema)
        schema = _load_json(args.visualization_schema, "Visualization schema")
        layout_map = load_layout_map(args.layout_map)
        artifacts, issues = generate_visualizations(
            outline,
            snapshot,
            schema=schema,
            candidate_mode=args.candidate_mode,
        )
        bindings = bindings_from_artifacts(artifacts)
        coverage = preflight_visualizations(outline, layout_map, bindings)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "schema_version": "3.0.0",
            "outline_sha256": canonical_sha256(outline),
            "document_source_sha256": str(
                snapshot.metadata.get("source_sha256") or "0" * 64
            ),
            "asset_root": str(snapshot.bundle_directory.resolve()),
            "bindings": [],
        }
        for artifact in artifacts:
            filename = f"{_safe_name(artifact.slide_id)}__{_safe_name(artifact.visualization_id)}.json"
            output = args.output_dir / filename
            output.write_text(
                json.dumps(artifact.data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest["bindings"].append(
                {
                    "slide_id": artifact.slide_id,
                    "visualization_id": artifact.visualization_id,
                    "visual_type": visual_type(artifact.data),
                    "sources": list(artifact.sources),
                    "visualization_file": filename,
                }
            )
            print(f"Created: {output}")
            print(f"Render argument: --visualization {artifact.slide_id}={output}")
        manifest_path = args.output_dir / "visualization_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Created manifest: {manifest_path}")
        print(f"Render argument: --asset-root {snapshot.bundle_directory.resolve()}")
        for issue in issues:
            print(issue.format(), file=sys.stderr)
        for warning in coverage:
            print(warning.format(), file=sys.stderr)
        return 0
    except (OSError, ValueError, VisualizationPlanningError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
