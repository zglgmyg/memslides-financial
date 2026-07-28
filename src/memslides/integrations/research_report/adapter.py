"""Adapt audited research-report artifacts into MemSlides inputs.

The adapter sits before MemSlides' LLM-driven design stages. It verifies the
research pipeline's cross-file contracts, renders only audited structured
visuals, and emits a normal MemSlides manuscript plus asset manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


JsonObject = dict[str, Any]
Renderer = Callable[..., JsonObject]


class ResearchReportAdapterError(ValueError):
    """Raised when upstream artifacts do not satisfy the integration contract."""


@dataclass(frozen=True)
class AdaptationResult:
    """Paths written by :func:`adapt_research_report`."""

    workspace: Path
    manuscript: Path
    asset_manifest: Path
    evidence_manifest: Path
    asset_count: int


def _read_json(path: Path, label: str) -> JsonObject:
    if not path.is_file():
        raise ResearchReportAdapterError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchReportAdapterError(f"Unable to read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResearchReportAdapterError(f"{label} must contain a JSON object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: JsonObject) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ResearchReportAdapterError(f"{label} must be a list.")
    return value


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ResearchReportAdapterError(f"{label} must be a non-empty string.")
    return text


def _safe_child(base: Path, relative_value: Any, label: str) -> Path:
    relative = Path(_required_text(relative_value, label))
    if relative.is_absolute():
        raise ResearchReportAdapterError(f"{label} must be relative: {relative}")
    resolved_base = base.resolve()
    resolved = (resolved_base / relative).resolve()
    if not resolved.is_relative_to(resolved_base):
        raise ResearchReportAdapterError(f"{label} escapes its allowed directory: {relative}")
    return resolved


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return text[:72] or "visual"


def _source_key(source: Any) -> tuple[str, str] | None:
    if not isinstance(source, dict):
        return None
    kind = str(source.get("kind", "") or "").strip()
    source_id = str(source.get("id", "") or "").strip()
    return (kind, source_id) if kind and source_id else None


def _merged_sources(binding: JsonObject, visual: JsonObject) -> list[JsonObject]:
    output: list[JsonObject] = []
    seen: set[tuple[str, str]] = set()
    for raw in [*(binding.get("sources") or []), *(visual.get("sources") or [])]:
        key = _source_key(raw)
        if key is None or key in seen:
            continue
        seen.add(key)
        output.append({"kind": key[0], "id": key[1]})
    return output


def _audit_index(audit: JsonObject) -> dict[tuple[str, str], JsonObject]:
    if str(audit.get("status", "")).lower() != "passed":
        raise ResearchReportAdapterError("numeric_audit.json status must be 'passed'.")
    summary = audit.get("summary")
    if not isinstance(summary, dict):
        raise ResearchReportAdapterError("numeric_audit.json summary must be an object.")
    if int(summary.get("mismatch_count", -1)) != 0:
        raise ResearchReportAdapterError("numeric_audit.json must report mismatch_count = 0.")

    indexed: dict[tuple[str, str], JsonObject] = {}
    for position, raw in enumerate(_require_list(audit.get("visualizations"), "numeric_audit.visualizations")):
        if not isinstance(raw, dict):
            raise ResearchReportAdapterError(f"numeric_audit.visualizations[{position}] must be an object.")
        key = (
            _required_text(raw.get("slide_id"), f"numeric_audit.visualizations[{position}].slide_id"),
            _required_text(raw.get("visualization_id"), f"numeric_audit.visualizations[{position}].visualization_id"),
        )
        if key in indexed:
            raise ResearchReportAdapterError(f"Duplicate numeric audit entry: {key[0]}/{key[1]}")
        indexed[key] = raw
    return indexed


def _verify_visual_audit(
    *, slide_id: str, visualization_id: str, audit_records: dict[tuple[str, str], JsonObject]
) -> JsonObject:
    record = audit_records.get((slide_id, visualization_id))
    if record is None:
        raise ResearchReportAdapterError(f"Missing numeric audit for {slide_id}/{visualization_id}.")
    if str(record.get("status", "")).lower() != "passed":
        raise ResearchReportAdapterError(f"Numeric audit did not pass for {slide_id}/{visualization_id}.")
    bad_entries = [
        entry
        for entry in (record.get("entries") or [])
        if not isinstance(entry, dict) or str(entry.get("status", "")).lower() != "matched"
    ]
    if bad_entries:
        raise ResearchReportAdapterError(
            f"Numeric audit contains unmatched values for {slide_id}/{visualization_id}."
        )
    return record


def _chart_arguments(visual: JsonObject, output_stem: str, workspace: Path) -> JsonObject:
    source_type = _required_text(visual.get("chart_type"), "chart_type").lower()
    categories = _require_list(visual.get("categories"), "chart.categories")
    series = _require_list(visual.get("series"), "chart.series")
    if not categories or not series:
        raise ResearchReportAdapterError("A chart needs at least one category and one series.")

    names: list[str] = []
    values_by_series: list[list[Any]] = []
    for position, raw in enumerate(series):
        if not isinstance(raw, dict):
            raise ResearchReportAdapterError(f"chart.series[{position}] must be an object.")
        name = _required_text(raw.get("name"), f"chart.series[{position}].name")
        values = _require_list(raw.get("values"), f"chart.series[{position}].values")
        if len(values) != len(categories):
            raise ResearchReportAdapterError(
                f"Series '{name}' has {len(values)} values for {len(categories)} categories."
            )
        if name in names:
            raise ResearchReportAdapterError(f"Duplicate chart series name: {name}")
        names.append(name)
        values_by_series.append(values)

    mapping = {
        "line": "line",
        "column": "grouped_bar" if len(names) > 1 else "bar",
        "bar": "bar",
        "area": "area",
        "pie": "pie",
        "scatter": "scatter",
    }
    if source_type not in mapping:
        raise ResearchReportAdapterError(
            f"Unsupported audited chart_type '{source_type}'. Supported types are line, column, "
            "bar, area, pie, and scatter; combo charts need an explicit rendering policy."
        )
    if source_type == "pie" and len(names) != 1:
        raise ResearchReportAdapterError("Pie charts must contain exactly one series.")

    rows = []
    for category_index, category in enumerate(categories):
        row: JsonObject = {"category": category}
        for series_index, name in enumerate(names):
            row[name] = values_by_series[series_index][category_index]
        rows.append(row)

    return {
        "chart_type": mapping[source_type],
        "rows": rows,
        "x_field": "category",
        "y_fields": names,
        "title": str(visual.get("title", "") or ""),
        "y_label": str(visual.get("unit", "") or "").strip(),
        "note": str(visual.get("note", "") or "").strip(),
        "output_format": "svg",
        "output_stem": output_stem,
        "workspace": workspace,
    }


def _table_arguments(visual: JsonObject, output_stem: str, workspace: Path) -> JsonObject:
    source_columns = [str(item) for item in _require_list(visual.get("columns"), "table.columns")]
    if not source_columns or any(not item.strip() for item in source_columns):
        raise ResearchReportAdapterError("table.columns must contain non-empty names.")
    counts: dict[str, int] = {}
    columns: list[str] = []
    for name in source_columns:
        counts[name] = counts.get(name, 0) + 1
        columns.append(name if counts[name] == 1 else f"{name}（{counts[name]}）")

    shaped_rows: list[JsonObject] = []
    for position, raw in enumerate(_require_list(visual.get("rows"), "table.rows")):
        if isinstance(raw, dict):
            shaped_rows.append({column: raw.get(column, "") for column in columns})
            continue
        if not isinstance(raw, list) or len(raw) != len(source_columns):
            raise ResearchReportAdapterError(
                f"table.rows[{position}] must contain exactly {len(source_columns)} cells."
            )
        shaped_rows.append(dict(zip(columns, raw)))
    if not shaped_rows:
        raise ResearchReportAdapterError("table.rows must not be empty.")

    return {
        "rows": shaped_rows,
        "columns": columns,
        "caption": str(visual.get("title", "") or ""),
        "footnote": str(visual.get("note", "") or ""),
        "style": "three_line",
        "output_mode": "svg",
        "output_stem": output_stem,
        "workspace": workspace,
    }


def _copy_image(
    visual: JsonObject, *, asset_root: Path, workspace: Path, output_stem: str
) -> JsonObject:
    source = _safe_child(asset_root, visual.get("asset_path"), "image.asset_path")
    if not source.is_file():
        raise ResearchReportAdapterError(f"Image asset does not exist: {source}")
    target_dir = workspace / "verified_assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{output_stem}{source.suffix.lower()}"
    shutil.copy2(source, target)
    return {
        "kind": "figure",
        "renderer": "verified-source-copy",
        "primary_path": str(target.resolve()),
        "rendered_paths": {"source": str(target.resolve())},
        "meta_path": "",
        "warnings": [],
    }


def _asset_entry(
    *,
    result: JsonObject,
    title: str,
    kind: str,
    slide_id: str,
    visualization_id: str,
    sources: list[JsonObject],
    audit_record: JsonObject | None,
    workspace: Path,
) -> JsonObject:
    primary = Path(_required_text(result.get("primary_path"), "renderer.primary_path")).resolve()
    if not primary.is_file():
        raise ResearchReportAdapterError(f"Renderer did not create its primary asset: {primary}")
    resolved_workspace = workspace.resolve()
    if not primary.is_relative_to(resolved_workspace):
        raise ResearchReportAdapterError(f"Renderer output escaped the workspace: {primary}")
    return {
        "path": str(primary),
        "filename": primary.name,
        "caption": title,
        "kind": kind,
        "category": kind,
        "exists": True,
        "within_workspace": True,
        "generated_by_tool": result.get("renderer") != "verified-source-copy",
        "renderer": str(result.get("renderer", "") or ""),
        "meta_path": str(result.get("meta_path", "") or ""),
        "rendered_paths": result.get("rendered_paths") or {},
        "verification": {
            "status": "passed",
            "slide_id": slide_id,
            "visualization_id": visualization_id,
            "numeric_audit_status": "passed" if audit_record is not None else "not_applicable",
            "audited_value_count": int((audit_record or {}).get("audited_value_count", 0) or 0),
            "sources": sources,
        },
    }


def _evidence_text(slide: JsonObject, bindings: list[JsonObject]) -> str:
    items: list[str] = []
    for raw in slide.get("evidence_refs") or []:
        key = _source_key(raw)
        if key:
            items.append(f"{key[0]}:{key[1]}")
    for binding in bindings:
        for source in binding.get("sources") or []:
            key = _source_key(source)
            if key:
                items.append(f"{key[0]}:{key[1]}")
    return ", ".join(dict.fromkeys(items))


def _manuscript(
    slides: list[JsonObject],
    assets_by_slide: dict[str, list[JsonObject]],
    bindings_by_slide: dict[str, list[JsonObject]],
    workspace: Path,
) -> str:
    pages: list[str] = []
    for slide in slides:
        slide_id = str(slide["slide_id"])
        lines = [f"# {slide['title']}"]
        key_message = str(slide.get("key_message", "") or "").strip()
        if key_message:
            lines.extend(["", key_message])
        bullets = slide.get("bullet_points") or []
        if bullets:
            lines.append("")
            lines.extend(f"- {str(item).strip()}" for item in bullets if str(item).strip())
        for asset in assets_by_slide.get(slide_id, []):
            relative = Path(asset["path"]).relative_to(workspace.resolve()).as_posix()
            alt = re.sub(r"[\[\]\n\r]", " ", str(asset.get("caption", "") or "visual"))
            lines.extend(["", f"![{alt}]({relative})"])
        evidence = _evidence_text(slide, bindings_by_slide.get(slide_id, []))
        if evidence:
            lines.extend(["", f"Evidence: {evidence}"])
        lines.extend(["", f"<!-- research-report slide_id={slide_id} -->"])
        pages.append("\n".join(lines).rstrip())
    return "\n\n---\n\n".join(pages) + "\n"


def adapt_research_report(
    *,
    outline_path: str | Path,
    visualization_manifest_path: str | Path,
    numeric_audit_path: str | Path,
    output_dir: str | Path,
    chart_renderer: Renderer | None = None,
    table_renderer: Renderer | None = None,
) -> AdaptationResult:
    """Convert audited upstream artifacts into a MemSlides-ready workspace."""

    outline_file = Path(outline_path).resolve()
    manifest_file = Path(visualization_manifest_path).resolve()
    audit_file = Path(numeric_audit_path).resolve()
    workspace = Path(output_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    outline = _read_json(outline_file, "slide_outline.json")
    manifest = _read_json(manifest_file, "visualization_manifest.json")
    audit = _read_json(audit_file, "numeric_audit.json")

    expected_outline_hash = str(manifest.get("outline_sha256", "") or "").strip().lower()
    actual_outline_hash = _canonical_sha256(outline)
    if expected_outline_hash and expected_outline_hash != actual_outline_hash:
        raise ResearchReportAdapterError(
            "visualization_manifest.json was produced for a different slide_outline.json."
        )

    slides: list[JsonObject] = []
    slides_by_id: dict[str, JsonObject] = {}
    for position, raw in enumerate(_require_list(outline.get("slides"), "outline.slides")):
        if not isinstance(raw, dict):
            raise ResearchReportAdapterError(f"outline.slides[{position}] must be an object.")
        slide_id = _required_text(raw.get("slide_id"), f"outline.slides[{position}].slide_id")
        title = _required_text(raw.get("title"), f"outline.slides[{position}].title")
        if slide_id in slides_by_id:
            raise ResearchReportAdapterError(f"Duplicate slide_id: {slide_id}")
        slide = dict(raw)
        slide["slide_id"] = slide_id
        slide["title"] = title
        slides.append(slide)
        slides_by_id[slide_id] = slide

    audit_records = _audit_index(audit)
    bindings_by_slide: dict[str, list[JsonObject]] = {}
    seen_visualizations: set[str] = set()
    loaded_bindings: list[tuple[JsonObject, JsonObject]] = []
    manifest_dir = manifest_file.parent
    asset_root_path = Path(str(manifest.get("asset_root", ".") or "."))
    asset_root = (
        asset_root_path.resolve()
        if asset_root_path.is_absolute()
        else (manifest_dir / asset_root_path).resolve()
    )

    for position, raw in enumerate(_require_list(manifest.get("bindings"), "manifest.bindings")):
        if not isinstance(raw, dict):
            raise ResearchReportAdapterError(f"manifest.bindings[{position}] must be an object.")
        binding = dict(raw)
        slide_id = _required_text(binding.get("slide_id"), f"manifest.bindings[{position}].slide_id")
        visual_id = _required_text(binding.get("visualization_id"), f"manifest.bindings[{position}].visualization_id")
        if slide_id not in slides_by_id:
            raise ResearchReportAdapterError(f"Binding references unknown slide_id: {slide_id}")
        if visual_id in seen_visualizations:
            raise ResearchReportAdapterError(f"Duplicate visualization_id: {visual_id}")
        seen_visualizations.add(visual_id)
        visual_file = _safe_child(
            manifest_dir,
            binding.get("visualization_file"),
            f"manifest.bindings[{position}].visualization_file",
        )
        visual = _read_json(visual_file, f"visualization {visual_id}")
        binding["slide_id"] = slide_id
        binding["visualization_id"] = visual_id
        bindings_by_slide.setdefault(slide_id, []).append(binding)
        loaded_bindings.append((binding, visual))

    if chart_renderer is None or table_renderer is None:
        from memslides.tools.structured_visuals import render_chart_asset_impl, render_table_asset_impl

        chart_renderer = chart_renderer or render_chart_asset_impl
        table_renderer = table_renderer or render_table_asset_impl

    asset_entries: list[JsonObject] = []
    assets_by_slide: dict[str, list[JsonObject]] = {}
    evidence_visuals: list[JsonObject] = []
    for binding, visual in loaded_bindings:
        slide_id = str(binding["slide_id"])
        visual_id = str(binding["visualization_id"])
        visual_type = _required_text(binding.get("visual_type"), f"{visual_id}.visual_type").lower()
        output_stem = f"{_slug(slide_id)}__{_slug(visual_id)}"
        sources = _merged_sources(binding, visual)

        if visual_type == "chart":
            audit_record = _verify_visual_audit(
                slide_id=slide_id, visualization_id=visual_id, audit_records=audit_records
            )
            result = chart_renderer(**_chart_arguments(visual, output_stem, workspace))
            kind = "chart"
        elif visual_type == "table":
            audit_record = _verify_visual_audit(
                slide_id=slide_id, visualization_id=visual_id, audit_records=audit_records
            )
            result = table_renderer(**_table_arguments(visual, output_stem, workspace))
            kind = "table"
        elif visual_type in {"image", "figure"}:
            audit_record = None
            result = _copy_image(visual, asset_root=asset_root, workspace=workspace, output_stem=output_stem)
            kind = "figure"
        else:
            raise ResearchReportAdapterError(
                f"Unsupported visual_type '{visual_type}' for {slide_id}/{visual_id}."
            )

        title = str(visual.get("title", "") or slides_by_id[slide_id]["title"])
        entry = _asset_entry(
            result=result,
            title=title,
            kind=kind,
            slide_id=slide_id,
            visualization_id=visual_id,
            sources=sources,
            audit_record=audit_record,
            workspace=workspace,
        )
        asset_entries.append(entry)
        assets_by_slide.setdefault(slide_id, []).append(entry)
        evidence_visuals.append(
            {
                "slide_id": slide_id,
                "visualization_id": visual_id,
                "visual_type": visual_type,
                "asset_path": entry["path"],
                "numeric_audit_status": entry["verification"]["numeric_audit_status"],
                "audited_value_count": entry["verification"]["audited_value_count"],
                "sources": sources,
            }
        )

    manuscript_path = workspace / "manuscript.md"
    manuscript_path.write_text(
        _manuscript(slides, assets_by_slide, bindings_by_slide, workspace), encoding="utf-8"
    )
    asset_manifest_path = workspace / "asset_manifest.json"
    _write_json(
        asset_manifest_path,
        {"manuscript": str(manuscript_path), "workspace": str(workspace), "assets": asset_entries},
    )
    evidence_manifest_path = workspace / "financial_evidence_manifest.json"
    _write_json(
        evidence_manifest_path,
        {
            "schema_version": "1.0.0",
            "status": "passed",
            "inputs": {
                "slide_outline": str(outline_file),
                "slide_outline_sha256": actual_outline_hash,
                "slide_outline_file_sha256": _sha256(outline_file),
                "visualization_manifest": str(manifest_file),
                "visualization_manifest_sha256": _sha256(manifest_file),
                "numeric_audit": str(audit_file),
                "numeric_audit_sha256": _sha256(audit_file),
            },
            "summary": {
                "slide_count": len(slides),
                "visualization_count": len(evidence_visuals),
                "audited_visualization_count": sum(
                    item["numeric_audit_status"] == "passed" for item in evidence_visuals
                ),
                "asset_count": len(asset_entries),
            },
            "visualizations": evidence_visuals,
        },
    )
    return AdaptationResult(
        workspace=workspace,
        manuscript=manuscript_path,
        asset_manifest=asset_manifest_path,
        evidence_manifest=evidence_manifest_path,
        asset_count=len(asset_entries),
    )
