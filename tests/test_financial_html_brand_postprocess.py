from __future__ import annotations

from pathlib import Path

from memslides.integrations.research_report.generate import _validate_slide_html_dir
from memslides.integrations.research_report.html_brand_postprocess import (
    SJTU_BACKGROUND_MARKER,
    SJTU_LOGO_MARKER,
    apply_sjtu_brand_to_html,
)


def test_optional_html_branding_runs_before_export_and_is_idempotent(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        '<html><body data-page-role="title" data-slide-id="slide_01" style="background:#1E3A5F;color:#FFFFFF">Cover</body></html>',
        encoding="utf-8",
    )
    (slides / "slide_02.html").write_text(
        """<html><body data-page-role="content" data-slide-id="slide_02" style="background:#1E3A5F;color:#FFFFFF">
        <div style="position:absolute;left:8%;top:12%;background:#1E3A5F;color:#FFFFFF">
          Freely placed title
        </div><div style="background:#DBEAFE;border:1px solid #CBD5E1;color:#475569">Body</div>
        </body></html>""",
        encoding="utf-8",
    )
    (slides / "slide_03.html").write_text(
        '<html><body data-page-role="closing" data-slide-id="slide_03" style="background:rgb(30,58,95);color:#FFFFFF">End</body></html>',
        encoding="utf-8",
    )
    roles = ["title", "content", "closing"]

    first = apply_sjtu_brand_to_html(slides, roles)
    second = apply_sjtu_brand_to_html(slides, roles)

    cover = (slides / "slide_01.html").read_text(encoding="utf-8")
    content = (slides / "slide_02.html").read_text(encoding="utf-8")
    closing = (slides / "slide_03.html").read_text(encoding="utf-8")
    assert "background: transparent" in cover
    assert 'data-page-role="content"' in content
    assert "background: #F8FAFC" in content
    assert 'data-financial-role="content-title-bar"' not in content
    assert "background: #A62038" in content
    assert "height:48px;width:auto" in content
    assert "border-radius:50%" not in content
    assert "object-fit" not in content
    assert "#E0CFBD" in content
    assert "#BFBFBF" in content
    assert "#475569" in content
    assert "background: transparent" in closing
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' in cover
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' in closing
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' not in content
    assert '<img data-sjtu-background="sjtu-solid-red-background"' in cover
    assert "width:100%;height:100%;z-index:-1" in cover
    assert "background: transparent" in cover
    assert "isolation:isolate" in cover
    assert '<img data-sjtu-background="sjtu-solid-red-background"' in closing
    assert f'id="{SJTU_LOGO_MARKER}"' not in cover
    assert content.count(f'id="{SJTU_LOGO_MARKER}"') == 1
    assert f'id="{SJTU_LOGO_MARKER}"' not in closing
    assert first["logos_added"] == 1
    assert second["logos_added"] == 0
    assert first["backgrounds_added"] == 2
    assert second["backgrounds_added"] == 0
    assert first["content_canvases_normalized"] == 1
    assert second["content_canvases_normalized"] == 0

    _validate_slide_html_dir(
        slides,
        3,
        page_roles=roles,
        sjtu_branding=True,
    )


def test_background_art_skips_non_solid_red_and_content_pages(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        """<html><style>body { background:linear-gradient(90deg,#1E3A5F,#2563EB); }</style>
        <body data-page-role="title">Gradient cover</body></html>""",
        encoding="utf-8",
    )
    (slides / "slide_02.html").write_text(
        '<html><body data-page-role="content" style="background:#1E3A5F">Body</body></html>',
        encoding="utf-8",
    )
    (slides / "slide_03.html").write_text(
        '<html><body data-page-role="closing" style="background:#FFFFFF">End</body></html>',
        encoding="utf-8",
    )

    report = apply_sjtu_brand_to_html(slides, ["title", "content", "closing"])

    for path in sorted(slides.glob("slide_*.html")):
        assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' not in path.read_text(
            encoding="utf-8"
        )
    assert report["backgrounds_added"] == 0
