from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

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
        '<html><body data-page-role="title" data-slide-id="slide_01" style="background:#1E3A5F;color:#FFFFFF">Cover</body></html>',
        encoding="utf-8",
    )
    (slides / "slide_02.html").write_text(
        """<html><body data-page-role="content" data-slide-id="slide_02" style="background:#1E3A5F;color:#FFFFFF">
        <div data-financial-role="content-title-bar" style="position:absolute;left:8%;top:12%;background:#1E3A5F;color:#FFFFFF">
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
    titles = ["Cover", "Freely placed title", "End"]

    first = apply_sjtu_brand_to_html(slides, roles, titles)
    second = apply_sjtu_brand_to_html(slides, roles, titles)

    cover = (slides / "slide_01.html").read_text(encoding="utf-8")
    content = (slides / "slide_02.html").read_text(encoding="utf-8")
    closing = (slides / "slide_03.html").read_text(encoding="utf-8")
    assert 'data-sjtu-background="title"' in cover
    assert 'data-page-role="content"' in content
    assert 'position:absolute;left:8%;top:12%;background:#1E3A5F;color:#FFFFFF' in content
    assert "position:fixed!important" not in content
    assert "financial-content-title-bar-style" not in content
    assert "height:48px;width:auto" in content
    assert "border-radius:50%" not in content
    assert "#DBEAFE" in content
    assert "#CBD5E1" in content
    assert "#475569" in content
    assert 'data-sjtu-background="closing"' in closing
    assert "background-image:none!important" in cover
    assert "background-image:none!important" in closing
    assert f'id="{SJTU_BACKGROUND_MARKER}"' in cover
    assert f'id="{SJTU_BACKGROUND_MARKER}"' in closing
    assert 'data-memslides-pptx-background="true"' in cover
    assert 'data-memslides-pptx-background="true"' in closing
    assert f'id="{SJTU_LOGO_MARKER}"' not in cover
    assert content.count(f'id="{SJTU_LOGO_MARKER}"') == 1
    assert f'id="{SJTU_LOGO_MARKER}"' not in closing
    assert first["logos_added"] == 1
    assert second["logos_added"] == 0
    assert first["backgrounds_added"] == 2
    assert second["backgrounds_added"] == 0
    assert first["conflicting_backgrounds_removed"] == 0

    soup = BeautifulSoup(content, "lxml")
    title_bar = soup.select_one('[data-financial-role="content-title-bar"]')
    assert title_bar is not None
    assert title_bar.find(id=SJTU_LOGO_MARKER) is not None
    assert title_bar.find(id=SJTU_LOGO_MARKER).parent is title_bar
    assert "top:50%;transform:translateY(-50%)" in content

    _validate_slide_html_dir(
        slides,
        3,
        page_roles=roles,
    )


def test_branding_applies_template_without_removing_title_and_closing_content(tmp_path: Path) -> None:
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

    report = apply_sjtu_brand_to_html(
        slides,
        ["title", "content", "closing"],
        ["Gradient cover", "Title", "End"],
    )

    cover = (slides / "slide_01.html").read_text(encoding="utf-8")
    closing = (slides / "slide_03.html").read_text(encoding="utf-8")
    assert "Gradient cover" in cover
    assert "End" in closing
    assert 'data-sjtu-background="title"' in cover
    assert 'data-sjtu-background="closing"' in closing
    assert report["backgrounds_added"] == 2


def test_branding_removes_conflicting_full_slide_image_but_preserves_native_text(
    tmp_path: Path,
) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        """<html><body data-page-role="title">
        <img class="cover-background" src="data:image/png;base64,old"
             data-memslides-pptx-background="true"
             style="position:absolute;inset:0;width:1280px;height:720px">
        <main class="cover"><h1>Native report title</h1><p>Native subtitle</p></main>
        </body></html>""",
        encoding="utf-8",
    )

    report = apply_sjtu_brand_to_html(slides, ["title"], ["Native report title"])

    html = (slides / "slide_01.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    backgrounds = soup.select('img[data-memslides-pptx-background="true"]')
    assert len(backgrounds) == 1
    assert backgrounds[0].get("id") == SJTU_BACKGROUND_MARKER
    assert "Native report title" in html
    assert "Native subtitle" in html
    assert "base64,old" not in html
    assert report["conflicting_backgrounds_removed"] == 1

    _validate_slide_html_dir(slides, 1, page_roles=["title"])


def test_branding_keeps_cover_minimal_and_uses_white_text(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        """<html><head><style>
        .title { color:#0F172A } .subtitle,.footer { color:#475569 }
        </style></head><body><main class="content">
        <h1 class="title">Report title</h1><p class="subtitle">Subtitle</p>
        <div class="stat-row"><div class="stat-card">总营收 4091 亿元</div>
        <div class="stat-card">净利润 385 亿元</div></div>
        <p class="footer">Source</p></main></body></html>""",
        encoding="utf-8",
    )

    report = apply_sjtu_brand_to_html(slides, ["title"], ["Report title"])

    html = (slides / "slide_01.html").read_text(encoding="utf-8")
    assert "总营收" not in html
    assert "净利润" not in html
    assert 'body[data-sjtu-background="title"] *' in html
    assert "color: #FFFFFF !important" in html
    assert report["cover_metrics_removed"] == 1


def test_branding_synchronizes_visible_content_title(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        """<html><body><div class="title-bar"
        data-financial-role="content-title-bar"><h1>Repeated section title</h1></div>
        <main>Body</main></body></html>""",
        encoding="utf-8",
    )

    apply_sjtu_brand_to_html(
        slides,
        ["content"],
        ["国补政策拉动下，空调终端需求持续向好"],
    )

    html = (slides / "slide_01.html").read_text(encoding="utf-8")
    assert "Repeated section title" not in html
    assert "国补政策拉动下，空调终端需求持续向好" in html


def test_financial_contract_rejects_designer_owned_full_slide_cover_image(
    tmp_path: Path,
) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        """<html><body data-page-role="title" data-slide-id="slide_01">
        <img src="cover-with-title.png"
             style="position:absolute;inset:0;width:1280px;height:720px">
        <h1>Duplicate native title</h1></body></html>""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="full-slide image background"):
        _validate_slide_html_dir(slides, 1, page_roles=["title"])


def test_financial_postprocessing_sets_page_roles_and_updates_existing_title_bar(
    tmp_path: Path,
) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        '<html><body style="background:#F5BFC1;color:#2D2D3F">Cover</body></html>',
        encoding="utf-8",
    )
    (slides / "slide_02.html").write_text(
        '<html><body><div data-financial-role="content-title-bar" style="position:absolute;top:7px"><h1>Original title</h1></div>Body</body></html>',
        encoding="utf-8",
    )

    apply_page_roles_to_html(slides, ["title", "content"], ["Cover", "Title"])

    html = (slides / "slide_01.html").read_text(encoding="utf-8")
    content = (slides / "slide_02.html").read_text(encoding="utf-8")
    assert 'data-page-role="title"' in html
    assert 'data-page-role="content"' in content
    assert 'data-financial-role="content-title-bar"' in content
    assert "Original title" not in content
    assert "<h1>Title</h1>" in content
    assert "position:absolute;top:7px" in content
    assert "financial-content-title-bar-style" not in content


def test_branding_recovers_missing_marker_from_visual_titlebar(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        "<html><body>Cover</body></html>", encoding="utf-8"
    )
    (slides / "slide_02.html").write_text(
        """<html><body><div class="titlebar" style="position:absolute;top:0">
        <h1>Company research</h1></div><main>Body</main></body></html>""",
        encoding="utf-8",
    )

    report = apply_sjtu_brand_to_html(
        slides,
        ["title", "content"],
        ["Cover", "Company research"],
    )

    content = (slides / "slide_02.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(content, "lxml")
    title_bar = soup.select_one(".titlebar")
    assert title_bar is not None
    assert title_bar.get("data-financial-role") == "content-title-bar"
    assert title_bar.find(id=SJTU_LOGO_MARKER) is not None
    assert report["logos_added"] == 1


def test_branding_reports_the_invalid_content_slide_path(tmp_path: Path) -> None:
    slides = tmp_path / "outputs"
    slides.mkdir()
    (slides / "slide_01.html").write_text(
        "<html><body>Cover</body></html>",
        encoding="utf-8",
    )
    broken = slides / "slide_02.html"
    broken.write_text(
        "<html><body><p>CSS was emitted as visible text.</p></body></html>",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"slide_02\.html.*exactly one title bar"):
        apply_sjtu_brand_to_html(
            slides,
            ["title", "content"],
            ["Cover", "Broken content"],
        )
