from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from memslides.integrations.research_report import (
    ResearchReportAdapterError,
    adapt_research_report,
)


def _dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _canonical_sha256(payload: object) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _outline(tmp_path: Path, *, slides: list[dict] | None = None) -> Path:
    path = tmp_path / "slide_outline.json"
    _dump(
        path,
        {
            "schema_version": "1.0.0",
            "slides": slides
            or [
                {
                    "slide_id": "slide_001",
                    "title": "收入趋势",
                    "key_message": "收入稳步增长。",
                    "bullet_points": ["2024 年增速加快"],
                    "evidence_refs": [{"kind": "block", "id": "block-001"}],
                }
            ],
        },
    )
    return path


def _manifest(
    tmp_path: Path, outline: Path, *, bindings: list[dict], asset_root: str = "."
) -> Path:
    path = tmp_path / "visualizations" / "visualization_manifest.json"
    outline_payload = json.loads(outline.read_text(encoding="utf-8"))
    _dump(
        path,
        {
            "schema_version": "3.0.0",
            "outline_sha256": _canonical_sha256(outline_payload),
            "asset_root": asset_root,
            "bindings": bindings,
        },
    )
    return path


def _audit(tmp_path: Path, *, visualizations: list[dict], mismatch_count: int = 0) -> Path:
    path = tmp_path / "numeric_audit.json"
    _dump(
        path,
        {
            "schema_version": "1.0.0",
            "status": "passed",
            "summary": {
                "visualization_count": len(visualizations),
                "audited_value_count": sum(
                    item.get("audited_value_count", 0) for item in visualizations
                ),
                "mismatch_count": mismatch_count,
            },
            "visualizations": visualizations,
        },
    )
    return path


def _fake_renderer(kind: str, calls: list[dict]):
    def render(**kwargs):
        calls.append(kwargs)
        output_dir = Path(kwargs["workspace"]) / "generated_visuals"
        output_dir.mkdir(parents=True, exist_ok=True)
        primary = output_dir / f"{kwargs['output_stem']}.svg"
        primary.write_text("<svg></svg>", encoding="utf-8")
        meta = output_dir / f"{kwargs['output_stem']}.meta.json"
        _dump(meta, {"kind": kind, "primary_path": str(primary.resolve())})
        return {
            "kind": kind,
            "renderer": "test-renderer",
            "primary_path": str(primary.resolve()),
            "meta_path": str(meta.resolve()),
            "rendered_paths": {"svg": str(primary.resolve())},
        }

    return render


def test_adapts_audited_chart_and_table_to_memslides_inputs(tmp_path: Path) -> None:
    outline = _outline(
        tmp_path,
        slides=[
            {
                "slide_id": "slide_001",
                "title": "收入趋势",
                "key_message": "收入稳步增长。",
                "bullet_points": ["2024 年增速加快"],
                "evidence_refs": [{"kind": "block", "id": "block-001"}],
            },
            {
                "slide_id": "slide_002",
                "title": "分部收入",
                "key_message": "核心业务贡献最大。",
                "bullet_points": [],
                "evidence_refs": [],
            },
        ],
    )
    visual_dir = tmp_path / "visualizations"
    _dump(
        visual_dir / "chart.json",
        {
            "chart_type": "column",
            "title": "2023–2024 年收入",
            "unit": "亿元",
            "categories": ["2023", "2024"],
            "series": [
                {"name": "收入", "values": [10.0, 12.5]},
                {"name": "利润", "values": [1.2, 1.8]},
            ],
            "sources": [{"kind": "table", "id": "table-001"}],
            "note": "数据已经审计",
        },
    )
    _dump(
        visual_dir / "table.json",
        {
            "title": "分部收入",
            "columns": ["业务", "收入", "占比", "占比"],
            "rows": [
                ["核心业务", 8.0, "2023: 60%", "2024: 64%"],
                ["其他业务", 4.5, "2023: 40%", "2024: 36%"],
            ],
            "sources": [{"kind": "table", "id": "table-002"}],
        },
    )
    manifest = _manifest(
        tmp_path,
        outline,
        bindings=[
            {
                "slide_id": "slide_001",
                "visualization_id": "visual_001",
                "visual_type": "chart",
                "visualization_file": "chart.json",
                "sources": [{"kind": "table", "id": "table-001"}],
            },
            {
                "slide_id": "slide_002",
                "visualization_id": "visual_002",
                "visual_type": "table",
                "visualization_file": "table.json",
                "sources": [{"kind": "table", "id": "table-002"}],
            },
        ],
    )
    audit = _audit(
        tmp_path,
        visualizations=[
            {
                "slide_id": "slide_001",
                "visualization_id": "visual_001",
                "status": "passed",
                "audited_value_count": 4,
                "entries": [{"status": "matched"}],
            },
            {
                "slide_id": "slide_002",
                "visualization_id": "visual_002",
                "status": "passed",
                "audited_value_count": 6,
                "entries": [{"status": "matched"}],
            },
        ],
    )
    chart_calls: list[dict] = []
    table_calls: list[dict] = []

    result = adapt_research_report(
        outline_path=outline,
        visualization_manifest_path=manifest,
        numeric_audit_path=audit,
        output_dir=tmp_path / "memslides-workspace",
        chart_renderer=_fake_renderer("chart", chart_calls),
        table_renderer=_fake_renderer("table", table_calls),
    )

    assert result.asset_count == 2
    assert chart_calls[0]["chart_type"] == "grouped_bar"
    assert chart_calls[0]["rows"] == [
        {"category": "2023", "收入": 10.0, "利润": 1.2},
        {"category": "2024", "收入": 12.5, "利润": 1.8},
    ]
    assert table_calls[0]["rows"] == [
        {"业务": "核心业务", "收入": 8.0, "占比": "2023: 60%", "占比（2）": "2024: 64%"},
        {"业务": "其他业务", "收入": 4.5, "占比": "2023: 40%", "占比（2）": "2024: 36%"},
    ]
    assert table_calls[0]["columns"] == ["业务", "收入", "占比", "占比（2）"]

    manuscript = result.manuscript.read_text(encoding="utf-8")
    assert "# 收入趋势" in manuscript
    assert "![2023–2024 年收入](generated_visuals/slide_001__visual_001.svg)" in manuscript
    assert "Evidence: block:block-001, table:table-001" in manuscript

    asset_manifest = json.loads(result.asset_manifest.read_text(encoding="utf-8"))
    assert len(asset_manifest["assets"]) == 2
    assert asset_manifest["assets"][0]["verification"] == {
        "status": "passed",
        "slide_id": "slide_001",
        "visualization_id": "visual_001",
        "numeric_audit_status": "passed",
        "audited_value_count": 4,
        "sources": [{"kind": "table", "id": "table-001"}],
    }

    evidence = json.loads(result.evidence_manifest.read_text(encoding="utf-8"))
    assert evidence["status"] == "passed"
    assert evidence["summary"]["audited_visualization_count"] == 2


def test_rejects_numeric_audit_with_mismatches(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    manifest = _manifest(tmp_path, outline, bindings=[])
    audit = _audit(tmp_path, visualizations=[], mismatch_count=1)

    with pytest.raises(ResearchReportAdapterError, match="mismatch_count = 0"):
        adapt_research_report(
            outline_path=outline,
            visualization_manifest_path=manifest,
            numeric_audit_path=audit,
            output_dir=tmp_path / "workspace",
            chart_renderer=_fake_renderer("chart", []),
            table_renderer=_fake_renderer("table", []),
        )


def test_rejects_visualization_file_path_traversal(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    _dump(tmp_path / "outside.json", {"title": "outside"})
    manifest = _manifest(
        tmp_path,
        outline,
        bindings=[
            {
                "slide_id": "slide_001",
                "visualization_id": "visual_001",
                "visual_type": "chart",
                "visualization_file": "../outside.json",
            }
        ],
    )
    audit = _audit(tmp_path, visualizations=[])

    with pytest.raises(ResearchReportAdapterError, match="escapes its allowed directory"):
        adapt_research_report(
            outline_path=outline,
            visualization_manifest_path=manifest,
            numeric_audit_path=audit,
            output_dir=tmp_path / "workspace",
            chart_renderer=_fake_renderer("chart", []),
            table_renderer=_fake_renderer("table", []),
        )


def test_copies_source_image_without_numeric_audit(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    source = tmp_path / "document_bundle" / "images" / "figure.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"verified-image")
    visual_dir = tmp_path / "visualizations"
    _dump(
        visual_dir / "image.json",
        {
            "title": "业务结构",
            "asset_path": "images/figure.png",
            "sources": [{"kind": "figure", "id": "figure-001"}],
        },
    )
    manifest = _manifest(
        tmp_path,
        outline,
        asset_root="../document_bundle",
        bindings=[
            {
                "slide_id": "slide_001",
                "visualization_id": "visual_001",
                "visual_type": "image",
                "visualization_file": "image.json",
            }
        ],
    )
    audit = _audit(tmp_path, visualizations=[])

    result = adapt_research_report(
        outline_path=outline,
        visualization_manifest_path=manifest,
        numeric_audit_path=audit,
        output_dir=tmp_path / "workspace",
        chart_renderer=_fake_renderer("chart", []),
        table_renderer=_fake_renderer("table", []),
    )

    copied = result.workspace / "verified_assets" / "slide_001__visual_001.png"
    assert copied.read_bytes() == b"verified-image"
    asset_manifest = json.loads(result.asset_manifest.read_text(encoding="utf-8"))
    assert asset_manifest["assets"][0]["verification"]["numeric_audit_status"] == "not_applicable"
