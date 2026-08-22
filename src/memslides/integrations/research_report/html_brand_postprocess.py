"""Optional SJTU branding for financial HTML before its first export."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag


SJTU_LOGO_MARKER = "sjtu-financial-brand-mark"
SJTU_BACKGROUND_MARKER = "sjtu-financial-title-closing-background"
SJTU_BACKGROUND_STYLE_MARKER = "sjtu-financial-title-closing-background-style"

_LOGO = Path(__file__).parent / "assets" / "sjtu" / "sjtu-logo-white.png"
_TITLE_CLOSING_BACKGROUND = (
    Path(__file__).parent / "assets" / "sjtu" / "sjtu-title-closing-background.png"
)
_TITLE_METRIC_CLASS_RE = re.compile(
    r"(?:^|[-_])(?:stat|stats|metric|metrics|kpi)(?:$|[-_])", re.I
)


def _looks_like_full_bleed_image(image: Tag) -> bool:
    """Return whether an image is acting as a slide-sized background layer."""

    if image.get("id") == SJTU_BACKGROUND_MARKER:
        return False
    if str(image.get("data-memslides-pptx-background") or "").lower() == "true":
        return True
    style = re.sub(r"\s+", "", str(image.get("style") or "").lower())
    tokens = " ".join(
        [
            str(image.get("id") or ""),
            *(
                image.get("class", [])
                if isinstance(image.get("class", []), list)
                else str(image.get("class") or "").split()
            ),
        ]
    ).lower()
    named_background = any(
        marker in tokens for marker in ("background", "backdrop", "fullbleed", "full-bleed")
    )
    positioned = "position:absolute" in style or "position:fixed" in style
    full_width = "width:100%" in style or "width:1280px" in style
    full_height = "height:100%" in style or "height:720px" in style
    inset = "inset:0" in style
    return named_background and (inset or (positioned and full_width and full_height))


def _apply_title_closing_background(
    html: str, role: str
) -> tuple[str, bool, int, int]:
    """Apply one export-safe SJTU background to title/closing pages."""

    soup = BeautifulSoup(html, "lxml")
    body = soup.body
    if body is None:
        raise RuntimeError(f"Financial {role} page is missing a body element.")
    changed = False
    removed = 0
    cover_metrics_removed = 0
    for image in list(body.find_all("img")):
        if _looks_like_full_bleed_image(image):
            image.decompose()
            removed += 1
            changed = True

    if role == "title":
        for element in list(body.find_all(True)):
            if element.parent is None:
                continue
            classes = element.get("class", [])
            if isinstance(classes, str):
                classes = classes.split()
            if any(_TITLE_METRIC_CLASS_RE.search(str(item)) for item in classes):
                element.decompose()
                cover_metrics_removed += 1
                changed = True

    # Older runs stored the template only as a body background-image. The PPTX
    # exporter does not map CSS body images to slide backgrounds, so explicitly
    # disable that path and migrate to a marked <img> below.
    inline_style = str(body.get("style", "") or "").strip().rstrip(";")
    background_override = (
        "background-image:none!important;background-color:#1E3A5F!important"
    )
    if background_override not in inline_style.replace(" ", ""):
        body["style"] = (
            f"{inline_style};{background_override}"
            if inline_style
            else background_override
        )
        changed = True

    template_style = f"""
body[data-sjtu-background="title"] > .cover,
body[data-sjtu-background="title"] > .slide,
body[data-sjtu-background="title"] > .content,
body[data-sjtu-background="title"] > main,
body[data-sjtu-background="title"] > section,
body[data-sjtu-background="closing"] > .closing,
body[data-sjtu-background="closing"] > .slide,
body[data-sjtu-background="closing"] > main,
body[data-sjtu-background="closing"] > section {{
  background-color: transparent !important;
  background-image: none !important;
}}
body[data-sjtu-background="title"],
body[data-sjtu-background="title"] * {{
  color: #FFFFFF !important;
  -webkit-text-fill-color: #FFFFFF !important;
}}
#{SJTU_BACKGROUND_MARKER} {{
  position: absolute !important;
  inset: 0 !important;
  width: 1280px !important;
  height: 720px !important;
  object-fit: cover !important;
  pointer-events: none !important;
}}
""".strip()
    style_tag = soup.find("style", id=SJTU_BACKGROUND_STYLE_MARKER)
    if style_tag is None:
        style_tag = soup.new_tag("style", id=SJTU_BACKGROUND_STYLE_MARKER)
        style_tag.string = template_style
        if soup.head is not None:
            soup.head.append(style_tag)
        elif soup.html is not None:
            soup.html.insert(0, style_tag)
        else:
            soup.insert(0, style_tag)
        changed = True
    elif style_tag.get_text() != template_style:
        style_tag.string = template_style
        changed = True

    background = body.find("img", id=SJTU_BACKGROUND_MARKER)
    if background is None:
        encoded = base64.b64encode(_TITLE_CLOSING_BACKGROUND.read_bytes()).decode("ascii")
        background = soup.new_tag("img", id=SJTU_BACKGROUND_MARKER)
        background["alt"] = ""
        background["aria-hidden"] = "true"
        background["data-sjtu-template-background"] = role
        background["data-memslides-pptx-background"] = "true"
        background["src"] = f"data:image/png;base64,{encoded}"
        background["style"] = (
            "position:absolute;inset:0;width:1280px;height:720px;"
            "object-fit:cover;pointer-events:none"
        )
        body.insert(0, background)
        changed = True
    elif background.get("data-memslides-pptx-background") != "true":
        background["data-memslides-pptx-background"] = "true"
        changed = True

    if body.get("data-sjtu-background") != role:
        body["data-sjtu-background"] = role
        changed = True
    return str(soup), changed, removed, cover_metrics_removed


def _logo_markup() -> str:
    encoded = base64.b64encode(_LOGO.read_bytes()).decode("ascii")
    return (
        f'<div id="{SJTU_LOGO_MARKER}" data-sjtu-brand="official-seal" aria-hidden="true" '
        'style="position:absolute;right:16px;top:50%;transform:translateY(-50%);'
        'height:48px;z-index:2147483647;'
        'pointer-events:none">'
        f'<img alt="" src="data:image/png;base64,{encoded}" '
        'style="display:block;height:48px;width:auto"></div>'
    )


def _add_logo_to_title_bar(html: str, markup: str) -> tuple[str, bool]:
    """Attach the logo to the designer-owned title bar without restyling it."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.body
    if body is None:
        raise RuntimeError("Financial content page is missing a body element.")
    title_bars = body.select('[data-financial-role="content-title-bar"]')
    if len(title_bars) != 1:
        raise RuntimeError(
            "Financial content page must contain exactly one title bar marker."
        )
    if body.find(id=SJTU_LOGO_MARKER) is not None:
        return html, False

    title_bar = title_bars[0]
    inline_style = str(title_bar.get("style", "")).strip()
    if not re.search(r"(?:^|;)\s*position\s*:", inline_style, flags=re.I):
        title_bar["style"] = (
            f"{inline_style.rstrip(';')};position:relative"
            if inline_style
            else "position:relative"
        )
    logo = BeautifulSoup(markup, "html.parser").find(id=SJTU_LOGO_MARKER)
    if not isinstance(logo, Tag):
        raise RuntimeError("Failed to construct the SJTU logo element.")
    title_bar.append(logo)
    return str(soup), True


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def _ensure_content_title_bar_marker(html: str, page_title: str) -> str:
    """Recover the protocol marker from an unambiguous visual title bar.

    DeckDesigner occasionally emits the required title bar styling and class but
    omits only ``data-financial-role``.  That is a deterministic HTML repair and
    should not consume another model repair round.
    """

    soup = BeautifulSoup(html, "lxml")
    body = soup.body
    if body is None:
        return html

    marked = body.select('[data-financial-role="content-title-bar"]')
    if len(marked) == 1:
        return html
    if len(marked) > 1:
        return html

    title_bar_class_names = {
        "titlebar",
        "contenttitlebar",
        "slidetitlebar",
        "pagetitlebar",
    }
    candidates: list[Tag] = []
    for element in body.find_all(True):
        classes = element.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        normalized_classes = {
            re.sub(r"[-_]", "", str(class_name)).lower()
            for class_name in classes
        }
        if normalized_classes & title_bar_class_names:
            candidates.append(element)

    if len(candidates) > 1 and page_title.strip():
        expected_title = _normalized_text(page_title)
        exact_matches = [
            candidate
            for candidate in candidates
            if _normalized_text(candidate.get_text(" ", strip=True)) == expected_title
        ]
        if len(exact_matches) == 1:
            candidates = exact_matches

    if len(candidates) == 1:
        candidates[0]["data-financial-role"] = "content-title-bar"
        return str(soup)
    return html


def _synchronize_content_title(html: str, page_title: str) -> str:
    """Make the visible title bar agree with the finalized outline title."""

    if not page_title.strip():
        return html
    soup = BeautifulSoup(html, "lxml")
    body = soup.body
    if body is None:
        return html
    title_bars = body.select('[data-financial-role="content-title-bar"]')
    if len(title_bars) != 1:
        return html
    title_bar = title_bars[0]
    heading = title_bar.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading is None and str(title_bar.name).lower() in {
        "h1", "h2", "h3", "h4", "h5", "h6"
    }:
        heading = title_bar
    if heading is None or _normalized_text(heading.get_text(" ", strip=True)) == _normalized_text(page_title):
        return html
    heading.clear()
    heading.append(page_title.strip())
    return str(soup)


def apply_page_roles_to_html(
    slide_html_dir: str | Path,
    page_roles: list[str],
    page_titles: list[str],
) -> list[Path]:
    html_dir = Path(slide_html_dir).resolve()
    slide_paths = sorted(html_dir.glob("slide_*.html"))
    expected = [f"slide_{index:02d}.html" for index in range(1, len(slide_paths) + 1)]
    if not slide_paths or [path.name for path in slide_paths] != expected:
        raise RuntimeError("Financial slide HTML must be contiguous from slide_01.html.")
    if len(page_roles) != len(slide_paths):
        raise RuntimeError(
            "Deck generation is incomplete before financial postprocessing: "
            f"expected {len(page_roles)} slide HTML files, found {len(slide_paths)}."
        )
    if len(page_titles) != len(slide_paths):
        raise RuntimeError(
            "Financial title map does not match the generated slide count."
        )
    for path, role, page_title in zip(
        slide_paths, page_roles, page_titles, strict=True
    ):
        html = path.read_text(encoding="utf-8")
        body_match = re.search(r"<body\b[^>]*>", html, flags=re.I | re.S)
        if body_match:
            body_tag = body_match.group(0)
            if re.search(r"\bdata-page-role\s*=", body_tag, flags=re.I):
                branded_body_tag = re.sub(
                    r"\bdata-page-role\s*=\s*(['\"])[^'\"]*\1",
                    f'data-page-role="{role}"',
                    body_tag,
                    count=1,
                    flags=re.I,
                )
            else:
                branded_body_tag = body_tag[:-1] + f' data-page-role="{role}">'
            slide_id = path.stem
            if re.search(r"\bdata-slide-id\s*=", branded_body_tag, flags=re.I):
                branded_body_tag = re.sub(
                    r"\bdata-slide-id\s*=\s*(['\"])[^'\"]*\1",
                    f'data-slide-id="{slide_id}"',
                    branded_body_tag,
                    count=1,
                    flags=re.I,
                )
            else:
                branded_body_tag = (
                    branded_body_tag[:-1] + f' data-slide-id="{slide_id}">'
                )
            html = html[: body_match.start()] + branded_body_tag + html[body_match.end() :]
        if role == "content":
            html = _ensure_content_title_bar_marker(html, page_title)
            html = _synchronize_content_title(html, page_title)
        path.write_text(html, encoding="utf-8")
    return slide_paths


def apply_sjtu_brand_to_html(
    slide_html_dir: str | Path,
    page_roles: list[str],
    page_titles: list[str],
) -> dict[str, Any]:
    """Modify financial slide HTML in place; safe to call more than once."""

    html_dir = Path(slide_html_dir).resolve()
    slide_paths = apply_page_roles_to_html(html_dir, page_roles, page_titles)
    missing_assets = [
        path for path in (_LOGO, _TITLE_CLOSING_BACKGROUND) if not path.is_file()
    ]
    if missing_assets:
        raise RuntimeError(
            "SJTU template asset is missing: " + ", ".join(map(str, missing_assets))
        )

    logos_added = 0
    backgrounds_added = 0
    conflicting_backgrounds_removed = 0
    cover_metrics_removed = 0
    markup = _logo_markup()
    for path, role in zip(slide_paths, page_roles, strict=True):
        html = path.read_text(encoding="utf-8")
        if role == "content":
            try:
                html, logo_added = _add_logo_to_title_bar(html, markup)
            except RuntimeError as exc:
                raise RuntimeError(f"{path}: {exc}") from exc
            logos_added += int(logo_added)
        elif role in {"title", "closing"}:
            try:
                (
                    html,
                    background_added,
                    backgrounds_removed,
                    metrics_removed,
                ) = _apply_title_closing_background(html, role)
            except RuntimeError as exc:
                raise RuntimeError(f"{path}: {exc}") from exc
            backgrounds_added += int(background_added)
            conflicting_backgrounds_removed += backgrounds_removed
            cover_metrics_removed += metrics_removed
        path.write_text(html, encoding="utf-8")

    return {
        "slide_html_dir": str(html_dir),
        "slide_count": len(slide_paths),
        "template_mode": "built_in_sjtu_visual_template",
        "title_closing_background": str(_TITLE_CLOSING_BACKGROUND),
        "content_slides": [
            index for index, role in enumerate(page_roles, start=1) if role == "content"
        ],
        "logos_added": logos_added,
        "backgrounds_added": backgrounds_added,
        "conflicting_backgrounds_removed": conflicting_backgrounds_removed,
        "cover_metrics_removed": cover_metrics_removed,
    }
