"""Optional SJTU branding for financial HTML before its first export."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag


SJTU_LOGO_MARKER = "sjtu-financial-brand-mark"
SJTU_BACKGROUND_MARKER = "sjtu-title-closing-background"
FINANCIAL_TITLE_BAR_STYLE_MARKER = "financial-content-title-bar-style"

_LOGO = Path(__file__).parent / "assets" / "sjtu" / "sjtu-logo-white.png"
_BACKGROUND_ART = Path(__file__).parent / "assets" / "sjtu" / "sjtu-title-closing-background.png"
_STYLE_ATTR_RE = re.compile(
    r"(?P<prefix>\bstyle\s*=\s*)(?P<quote>['\"])(?P<css>.*?)(?P=quote)",
    re.I | re.S,
)
_BODY_OPEN_RE = re.compile(r"<body\b(?P<attrs>[^>]*)>", re.I | re.S)
_BACKGROUND_DECL_RE = re.compile(
    r"(?:^|;)\s*background(?:-color)?\s*:\s*[^;]+",
    re.I,
)


def _logo_markup() -> str:
    encoded = base64.b64encode(_LOGO.read_bytes()).decode("ascii")
    return (
        f'<div id="{SJTU_LOGO_MARKER}" data-sjtu-brand="official-seal" aria-hidden="true" '
        'style="position:absolute;right:16px;top:10px;height:48px;z-index:2147483647;'
        'pointer-events:none">'
        f'<img alt="" src="data:image/png;base64,{encoded}" '
        'style="display:block;height:48px;width:auto"></div>'
    )


def _add_content_title_bar_style(html: str) -> str:
    if f'data-financial-style="{FINANCIAL_TITLE_BAR_STYLE_MARKER}"' in html:
        return html
    style = (
        f'<style data-financial-style="{FINANCIAL_TITLE_BAR_STYLE_MARKER}">'
        '[data-financial-role="content-title-bar"]{position:fixed!important;top:0!important;'
        'left:0!important;right:0!important;width:100%!important;height:72px!important;'
        'margin:0!important;padding:0 60px!important;border-radius:0!important;'
        'box-sizing:border-box!important;display:flex!important;align-items:center!important;'
        'background:#1E3A5F!important;color:#FFFFFF!important;z-index:1000!important;}'
        '[data-financial-role="content-title-bar"]>h1{width:100%!important;margin:0!important;'
        'padding:0!important;color:#FFFFFF!important;}'
        '[data-financial-role="content-title-bar"] *{color:#FFFFFF!important;}'
        '</style>'
    )
    head_end = re.search(r"</head\s*>", html, flags=re.I)
    return (
        html[: head_end.start()] + style + "\n" + html[head_end.start() :]
        if head_end
        else style + "\n" + html
    )


def _normalize_content_title_bar(html: str, title: str) -> str:
    """Replace model-specific title shells with one deterministic title bar."""

    soup = BeautifulSoup(html, "lxml")
    body = soup.body
    if body is None:
        return html

    marker = body.select_one('[data-financial-role="content-title-bar"]')
    if marker is None:
        for selector in ('[data-element="title"]', "h1", "h2", ".title"):
            marker = body.select_one(selector)
            if marker is not None:
                break

    resolved_title = title.strip()
    if not resolved_title:
        raise RuntimeError("Financial content pages require a non-empty outline title.")

    shell: Tag | None = marker if isinstance(marker, Tag) else None
    if shell is not None:
        for parent in shell.parents:
            if parent is body or not isinstance(parent, Tag):
                break
            class_tokens = {str(item).casefold() for item in parent.get("class", [])}
            if parent.name == "header" or class_tokens.intersection(
                {"title-bar", "header-bar", "page-header", "slide-header"}
            ):
                shell = parent
                break

    title_bar = soup.new_tag("header")
    title_bar["data-financial-role"] = "content-title-bar"
    heading = soup.new_tag("h1")
    heading.string = resolved_title
    title_bar.append(heading)
    if shell is not None:
        shell.replace_with(title_bar)
    else:
        body.insert(0, title_bar)

    for duplicate in list(body.select('[data-financial-role="content-title-bar"]'))[1:]:
        duplicate.decompose()
    return str(soup)


def _add_background_art(html: str, encoded_background: str) -> tuple[str, bool]:
    body_match = _BODY_OPEN_RE.search(html)
    if not body_match or f'data-sjtu-background="{SJTU_BACKGROUND_MARKER}"' in html:
        return html, False

    body_tag = body_match.group(0)
    style_match = _STYLE_ATTR_RE.search(body_tag)
    if style_match:
        css = style_match.group("css").strip().rstrip(";")
        css = _BACKGROUND_DECL_RE.sub("", css).strip().strip(";")
        if not re.search(r"(?:^|;)\s*position\s*:", css, flags=re.I):
            css = f"{css};position:relative" if css else "position:relative"
        quote = style_match.group("quote")
        replacement = (
            style_match.group("prefix")
            + quote
            + (f"{css};" if css else "")
            + "background:transparent;isolation:isolate"
            + quote
        )
        branded_tag = (
            body_tag[: style_match.start()]
            + replacement
            + body_tag[style_match.end() :]
        )
    else:
        branded_tag = (
            body_tag[:-1]
            + ' style="position:relative;background:transparent;isolation:isolate">'
        )

    background_markup = (
        f'<img data-sjtu-background="{SJTU_BACKGROUND_MARKER}" alt="" aria-hidden="true" '
        'data-memslides-pptx-background="true" '
        f'src="data:image/png;base64,{encoded_background}" '
        'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;'
        'object-position:center;z-index:-1;display:block">'
    )
    return (
        html[: body_match.start()]
        + branded_tag
        + "\n"
        + background_markup
        + html[body_match.end() :],
        True,
    )


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
    for path, role, title in zip(slide_paths, page_roles, page_titles, strict=True):
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
            html = _normalize_content_title_bar(html, title)
            html = _add_content_title_bar_style(html)
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
    if not _BACKGROUND_ART.is_file():
        raise RuntimeError(f"SJTU background art is missing: {_BACKGROUND_ART}")

    logos_added = 0
    backgrounds_added = 0
    markup = _logo_markup()
    encoded_background = base64.b64encode(_BACKGROUND_ART.read_bytes()).decode("ascii")
    for path, role in zip(slide_paths, page_roles, strict=True):
        html = path.read_text(encoding="utf-8")
        if role in {"title", "closing"}:
            html, background_added = _add_background_art(html, encoded_background)
            backgrounds_added += int(background_added)
        if role == "content" and f'id="{SJTU_LOGO_MARKER}"' not in html:
            body_end = re.search(r"</body\s*>", html, re.I)
            html = (
                html[: body_end.start()] + markup + "\n" + html[body_end.start() :]
                if body_end
                else html + "\n" + markup
            )
            logos_added += 1
        path.write_text(html, encoding="utf-8")

    return {
        "slide_html_dir": str(html_dir),
        "slide_count": len(slide_paths),
        "content_slides": [
            index for index, role in enumerate(page_roles, start=1) if role == "content"
        ],
        "logos_added": logos_added,
        "backgrounds_added": backgrounds_added,
    }
