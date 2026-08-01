from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from memslides.integrations.research_report.adapter import adapt_research_report
from memslides.research_pipeline.research_run.exporter import (
    ResearchRunExportError,
    export_research_run,
)


@dataclass
class Artifact:
    slide_id: str
    visualization_id: str
    sources: tuple[dict[str, str], ...]
    data: dict[str, Any]


def outline() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "company": "示例公司",
            "company_name": "示例公司",
            "stock_code": "000001",
            "industry": "测试",
            "report_title": "测试报告",
            "report_date": "2026-01-01",
            "source_file": "report.md",
        },
        "sources": [
            {"source_id": "src_report", "type": "broker_report", "title": "测试报告"}
        ],
        "slides": [
            {
                "slide_id": "slide_001",
                "page_role": "content",
                "slide_type": "financial_forecast",
                "title": "收入趋势",
                "key_message": "收入持续增长",
                "bullet_points": ["收入增长"],
                "source_refs": ["src_report"],
                "evidence_refs": [{"kind": "table", "id": "table-001"}],
                "visual_candidates": [],
            }
        ],
    }


def audit(*visualization_ids: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "status": "passed",
        "summary": {
            "visualization_count": len(visualization_ids),
            "audited_value_count": 4,
            "mismatch_count": 0,
        },
        "visualizations": [
            {
                "slide_id": "slide_001",
                "visualization_id": visualization_id,
                "status": "passed",
                "audited_value_count": 0 if visualization_id == "visual_image" else 2,
                "entries": [],
            }
            for visualization_id in visualization_ids
        ],
    }


def artifacts() -> list[Artifact]:
    source = ({"kind": "table", "id": "table-001"},)
    return [
        Artifact(
            "slide_001",
            "visual_chart",
            source,
            {
                "chart_type": "column",
                "title": "收入",
                "unit": "亿元",
                "categories": ["2024", "2025"],
                "series": [{"name": "收入", "values": [10, 12]}],
                "source_refs": ["src_report"],
                "sources": list(source),
            },
        ),
        Artifact(
            "slide_001",
            "visual_table",
            source,
            {
                "title": "收入",
                "columns": ["年度", "收入"],
                "rows": [["2024", 10], ["2025", 12]],
                "source_refs": ["src_report"],
                "sources": list(source),
            },
        ),
    ]


def test_exporter_publishes_financial_adapter_layout(tmp_path: Path) -> None:
    output = tmp_path / "research-run"
    result = export_research_run(
        output_directory=output,
        outline=outline(),
        numeric_audit=audit("visual_chart", "visual_table"),
        artifacts=artifacts(),
        document_bundle_directory=tmp_path,
        document_source_sha256="a" * 64,
    )

    assert result == output.resolve()
    assert (output / "slide_outline.json").is_file()
    assert (output / "numeric_audit.json").is_file()
    assert (output / "visualizations/chart_001.json").is_file()
    assert (output / "visualizations/table_001.json").is_file()
    manifest = json.loads(
        (output / "visualizations/visualization_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["asset_root"] == "."
    assert [item["visualization_file"] for item in manifest["bindings"]] == [
        "chart_001.json",
        "table_001.json",
    ]


def test_exporter_copies_images_into_portable_asset_root(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    source_image = bundle / "assets/figures/source.png"
    source_image.parent.mkdir(parents=True)
    source_image.write_bytes(b"png")
    image_artifact = Artifact(
        "slide_001",
        "visual_image",
        ({"kind": "figure", "id": "figure-001"},),
        {
            "type": "image",
            "title": "业务布局",
            "source": {"kind": "figure", "id": "figure-001"},
            "asset_path": "assets/figures/source.png",
            "source_refs": ["src_report"],
            "sources": [{"kind": "figure", "id": "figure-001"}],
        },
    )

    output = tmp_path / "research-run"
    export_research_run(
        output_directory=output,
        outline=outline(),
        numeric_audit=audit("visual_image"),
        artifacts=[image_artifact],
        document_bundle_directory=bundle,
        document_source_sha256="b" * 64,
    )

    assert (output / "visualizations/images/figure_001.png").read_bytes() == b"png"
    image_data = json.loads(
        (output / "visualizations/image_001.json").read_text(encoding="utf-8")
    )
    assert image_data["asset_path"] == "images/figure_001.png"


def test_exporter_rejects_duplicate_series_names(tmp_path: Path) -> None:
    bad_artifacts = artifacts()
    bad_artifacts[0].data["series"].append(
        {"name": "收入", "values": [11, 13]}
    )
    with pytest.raises(ResearchRunExportError, match="duplicate series"):
        export_research_run(
            output_directory=tmp_path / "research-run",
            outline=outline(),
            numeric_audit=audit("visual_chart", "visual_table"),
            artifacts=bad_artifacts,
            document_bundle_directory=tmp_path,
            document_source_sha256="c" * 64,
        )


def test_exported_run_is_consumed_by_financial_adapter(tmp_path: Path) -> None:
    research_run = export_research_run(
        output_directory=tmp_path / "research-run",
        outline=outline(),
        numeric_audit=audit("visual_chart", "visual_table"),
        artifacts=artifacts(),
        document_bundle_directory=tmp_path,
        document_source_sha256="d" * 64,
    )

    def renderer(**kwargs: Any) -> dict[str, Any]:
        output = Path(kwargs["workspace"]) / "generated_visuals" / (
            f"{kwargs['output_stem']}.svg"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("<svg></svg>", encoding="utf-8")
        return {
            "renderer": "test-renderer",
            "primary_path": str(output.resolve()),
            "rendered_paths": {"svg": str(output.resolve())},
            "meta_path": "",
        }

    result = adapt_research_report(
        outline_path=research_run / "slide_outline.json",
        visualization_manifest_path=(
            research_run / "visualizations" / "visualization_manifest.json"
        ),
        numeric_audit_path=research_run / "numeric_audit.json",
        output_dir=tmp_path / "memslides-workspace",
        chart_renderer=renderer,
        table_renderer=renderer,
    )

    assert result.asset_count == 2
    assert result.manuscript.is_file()
    assert result.evidence_manifest.is_file()
