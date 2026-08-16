"""Optional SJTU branding for financial HTML before its first export."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag


SJTU_LOGO_MARKER = "sjtu-financial-brand-mark"

_LOGO = Path(__file__).parent / "assets" / "sjtu" / "sjtu-logo-white.png"
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
    for path, role, _title in zip(slide_paths, page_roles, page_titles, strict=True):
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
    if not _LOGO.is_file():
        raise RuntimeError(f"SJTU logo is missing: {_LOGO}")

    logos_added = 0
    markup = _logo_markup()
    for path, role in zip(slide_paths, page_roles, strict=True):
        html = path.read_text(encoding="utf-8")
        if role == "content":
            try:
                html, logo_added = _add_logo_to_title_bar(html, markup)
            except RuntimeError as exc:
                raise RuntimeError(f"{path}: {exc}") from exc
            logos_added += int(logo_added)
        path.write_text(html, encoding="utf-8")

    return {
        "slide_html_dir": str(html_dir),
        "slide_count": len(slide_paths),
        "content_slides": [
            index for index, role in enumerate(page_roles, start=1) if role == "content"
        ],
        "logos_added": logos_added,
        "backgrounds_added": 0,
    }
