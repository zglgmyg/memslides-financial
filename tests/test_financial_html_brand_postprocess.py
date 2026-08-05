from __future__ import annotations

from pathlib import Path

from memslides.integrations.research_report.generate import _validate_slide_html_dir
from memslides.integrations.research_report.html_brand_postprocess import (
    SJTU_BACKGROUND_MARKER,
    SJTU_LOGO_MARKER,
    apply_page_roles_to_html,
    apply_sjtu_brand_to_html,
)


def test_optional_html_branding_runs_before_export_and_is_idempotent(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        '<html><body data-page-role="title" style="background:#1E3A5F;color:#FFFFFF">Cover</body></html>',
        encoding="utf-8",
    )
    (slides / "slide_02.html").write_text(
        """<html><body data-page-role="content" style="background:#1E3A5F;color:#FFFFFF">
        <div data-financial-role="content-title-bar" style="position:absolute;left:8%;top:12%">
          Freely placed title
        </div><div style="background:#DBEAFE;border:1px solid #CBD5E1;color:#475569">Body</div>
        </body></html>""",
        encoding="utf-8",
    )
    (slides / "slide_03.html").write_text(
        '<html><body data-page-role="closing" style="background:rgb(30,58,95);color:#FFFFFF">End</body></html>',
        encoding="utf-8",
    )
    roles = ["title", "content", "closing"]

    first = apply_sjtu_brand_to_html(slides, roles)
    second = apply_sjtu_brand_to_html(slides, roles)

    cover = (slides / "slide_01.html").read_text(encoding="utf-8")
    content = (slides / "slide_02.html").read_text(encoding="utf-8")
    closing = (slides / "slide_03.html").read_text(encoding="utf-8")
    assert "background:transparent" in cover
    assert 'data-page-role="content"' in content
    assert 'background:#1E3A5F!important' in content
    assert 'color:#FFFFFF!important' in content
    assert 'position:fixed!important;top:0!important' in content
    assert 'left:0!important;right:0!important;width:100%!important' in content
    assert 'height:72px!important' in content
    assert "height:48px;width:auto" in content
    assert "border-radius:50%" not in content
    assert "#DBEAFE" in content
    assert "#CBD5E1" in content
    assert "#475569" in content
    assert "background:transparent" in closing
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' in cover
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' in closing
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' not in content
    assert '<img data-sjtu-background="sjtu-title-closing-background"' in cover
    assert "width:100%;height:100%;object-fit:cover" in cover
    assert "background:transparent" in cover
    assert "isolation:isolate" in cover
    assert '<img data-sjtu-background="sjtu-title-closing-background"' in closing
    assert f'id="{SJTU_LOGO_MARKER}"' not in cover
    assert content.count(f'id="{SJTU_LOGO_MARKER}"') == 1
    assert f'id="{SJTU_LOGO_MARKER}"' not in closing
    assert first["logos_added"] == 1
    assert second["logos_added"] == 0
    assert first["backgrounds_added"] == 2
    assert second["backgrounds_added"] == 0

    _validate_slide_html_dir(
        slides,
        3,
        page_roles=roles,
    )


def test_background_art_applies_to_title_and_closing_only(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        """<html><style>body { background:linear-gradient(90deg,#1E3A5F,#2563EB); }</style>
        <body data-page-role="title">Gradient cover</body></html>""",
        encoding="utf-8",
    )
    (slides / "slide_02.html").write_text(
        '<html><body data-page-role="content" style="background:#1E3A5F"><h1 data-financial-role="content-title-bar">Title</h1>Body</body></html>',
        encoding="utf-8",
    )
    (slides / "slide_03.html").write_text(
        '<html><body data-page-role="closing" style="background:#FFFFFF">End</body></html>',
        encoding="utf-8",
    )

    report = apply_sjtu_brand_to_html(slides, ["title", "content", "closing"])

    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' in (
        slides / "slide_01.html"
    ).read_text(encoding="utf-8")
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' not in (
        slides / "slide_02.html"
    ).read_text(encoding="utf-8")
    assert f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' in (
        slides / "slide_03.html"
    ).read_text(encoding="utf-8")
    assert report["backgrounds_added"] == 2


def test_financial_postprocessing_sets_page_roles_when_designer_omits_them(
    tmp_path: Path,
) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        '<html><body style="background:#F5BFC1;color:#2D2D3F">Cover</body></html>',
        encoding="utf-8",
    )
    (slides / "slide_02.html").write_text(
        '<html><body><div data-element="title"><h1>Title</h1></div>Body</body></html>',
        encoding="utf-8",
    )

    apply_page_roles_to_html(slides, ["title", "content"])

    html = (slides / "slide_01.html").read_text(encoding="utf-8")
    content = (slides / "slide_02.html").read_text(encoding="utf-8")
    assert 'data-page-role="title"' in html
    assert 'data-page-role="content"' in content
    assert 'data-financial-role="content-title-bar"' in content
    assert 'background:#1E3A5F!important' in content
