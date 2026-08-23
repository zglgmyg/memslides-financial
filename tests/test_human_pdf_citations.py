from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from memslides.integrations.research_report.human_pdf_citations import (
    _inject,
    _safe_box,
    build_application_plan,
    build_figure_source_manifest,
)


def test_manifest_uses_only_explicit_mineru_source_footnotes() -> None:
    snapshot = SimpleNamespace(
        figures_by_id={
            "fig-001": {
                "page": 5,
                "bbox": [10, 20, 300, 200],
                "caption_block_ids": ["caption-1"],
                "footnote_block_ids": ["source-1", "note-1"],
                "source_content_index": 57,
            },
            "fig-002": {
                "page": 6,
                "caption_block_ids": [],
                "footnote_block_ids": ["note-2"],
            },
        },
        blocks_by_id={
            "caption-1": {"text_raw": "图1：公司发展历程"},
            "source-1": {"text_raw": "数据来源：公司公告、Wind"},
            "note-1": {"text_raw": "注：数据截至报告期末"},
            "note-2": {"text_raw": "这不是资料来源说明"},
        },
    )

    manifest = build_figure_source_manifest(snapshot)

    assert list(manifest["figures"]) == ["fig-001"]
    assert manifest["figures"]["fig-001"]["source_text"] == (
        "资料来源：公司公告、Wind"
    )
    assert manifest["figures"]["fig-001"]["caption"] == "图1：公司发展历程"


def test_plan_maps_figure_id_through_verified_asset_to_final_html(
    tmp_path: Path,
) -> None:
    html_dir = tmp_path / "outputs"
    html_dir.mkdir()
    (html_dir / "slide_02.html").write_text(
        '<html><body><img src="../verified_assets/'
        'slide_002__visual_001.png"></body></html>',
        encoding="utf-8",
    )
    outline = {
        "slides": [
            {"slide_id": "slide_001"},
            {"slide_id": "slide_002"},
        ]
    }
    assets = {
        "assets": [
            {
                "filename": "slide_002__visual_001.png",
                "renderer": "verified-source-copy",
                "generated_by_tool": False,
                "verification": {
                    "slide_id": "slide_002",
                    "visualization_id": "visual_001",
                    "sources": [{"kind": "figure", "id": "fig-002"}],
                },
            },
            {
                "filename": "generated.png",
                "renderer": "vega-lite+vl-convert",
                "generated_by_tool": True,
            },
        ]
    }
    figure_sources = {
        "figures": {
            "fig-002": {"source_text": "资料来源：Wind、公司公告"}
        }
    }

    plan = build_application_plan(
        outline=outline,
        asset_manifest=assets,
        figure_sources=figure_sources,
        html_directory=html_dir,
    )

    assert len(plan["items"]) == 1
    assert plan["items"][0]["figure_id"] == "fig-002"
    assert plan["items"][0]["image_index"] == 0


def test_injection_preserves_existing_html_bytes() -> None:
    original = (
        '<!doctype html><html><head><style>.x{display:grid}</style></head>'
        '<body><main class="x"><img src="figure.png"></main></body></html>'
    )

    updated = _inject(
        original,
        source_key="slide_002__visual_001",
        source_text="资料来源：Wind & 公司公告",
        box={"left": 100, "top": 500, "width": 400, "height": 10},
    )

    marker = updated.index("<!-- HUMAN_PDF_SOURCE_START")
    closing = updated.index("</body>")
    assert updated[:marker] == original[: original.index("</body>")]
    assert updated[closing:] == original[original.index("</body>") :]
    assert "资料来源：Wind &amp; 公司公告" in updated


def test_safe_box_rejects_overlap_without_moving_original_content() -> None:
    box, reason = _safe_box(
        {
            "body": {"width": 1280, "height": 720},
            "image": {
                "left": 100,
                "top": 100,
                "right": 600,
                "bottom": 500,
                "width": 500,
                "height": 400,
            },
            "blockers": [
                {"left": 90, "top": 502, "right": 620, "bottom": 540}
            ],
        },
        "资料来源：Wind、公司公告",
    )

    assert box is None
    assert reason == "source_box_would_overlap_content"
