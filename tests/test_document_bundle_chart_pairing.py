from __future__ import annotations

from pathlib import Path

import pytest

from memslides.research_pipeline.document_bundle.errors import DocumentBundleError
from memslides.research_pipeline.document_bundle.markdown import build_from_markdown


def _pdf_bundle(tmp_path: Path, count: int) -> tuple[Path, dict]:
    bundle = tmp_path / "pdf-bundle"
    figures = []
    for index in range(1, count + 1):
        relative = Path("assets") / "figures" / f"fig-{index:03d}.png"
        asset = bundle / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(f"figure-{index}".encode())
        figures.append(
            {
                "id": f"fig-{index:03d}",
                "page": index,
                "source_content_index": index,
                "asset_path": relative.as_posix(),
                "bbox": [0, 0, 100, 100],
            }
        )
    return bundle, {"figures": figures}


def test_chart_ids_bind_to_pdf_figures_in_document_order(tmp_path: Path) -> None:
    markdown = tmp_path / "report.md"
    markdown.write_text(
        "# Report\n\n"
        "![First caption](chart:chart_first)\n\n"
        "![Second caption](chart:chart_second)\n",
        encoding="utf-8",
    )
    pdf_bundle, pdf_document = _pdf_bundle(tmp_path, 2)

    document, validation = build_from_markdown(
        markdown,
        tmp_path / "paired",
        pdf_bundle_directory=pdf_bundle,
        pdf_document=pdf_document,
    )

    figures = document["figures"]
    assert validation["status"] == "passed"
    assert [figure["markdown_chart_id"] for figure in figures] == [
        "chart_first",
        "chart_second",
    ]
    assert [figure["pdf_figure_id"] for figure in figures] == [
        "fig-001",
        "fig-002",
    ]
    assert [figure["asset_path"] for figure in figures] == [
        "assets/figures/chart_first.png",
        "assets/figures/chart_second.png",
    ]
    assert (tmp_path / "paired" / figures[0]["asset_path"]).read_bytes() == b"figure-1"
    assert (tmp_path / "paired" / figures[1]["asset_path"]).read_bytes() == b"figure-2"


def test_chart_pairing_rejects_count_mismatch(tmp_path: Path) -> None:
    markdown = tmp_path / "report.md"
    markdown.write_text(
        "# Report\n\n![Only chart](chart:chart_only)\n",
        encoding="utf-8",
    )
    pdf_bundle, pdf_document = _pdf_bundle(tmp_path, 2)

    with pytest.raises(
        DocumentBundleError,
        match="Markdown chart count does not match PDF figure count: 1 != 2",
    ):
        build_from_markdown(
            markdown,
            tmp_path / "paired",
            pdf_bundle_directory=pdf_bundle,
            pdf_document=pdf_document,
        )
