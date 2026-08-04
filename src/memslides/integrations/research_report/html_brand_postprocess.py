"""Optional SJTU branding for financial HTML before its first export."""

from __future__ import annotations

import base64
import colorsys
import re
from collections import Counter
from pathlib import Path
from typing import Any


SJTU_RED = "A62038"
SJTU_LIGHT_GRAY = "BFBFBF"
SJTU_CHAMPAGNE = "E0CFBD"
SJTU_LOGO_MARKER = "sjtu-financial-brand-mark"
SJTU_BACKGROUND_MARKER = "sjtu-solid-red-background"
FINANCIAL_CONTENT_BACKGROUND = "F8FAFC"

_LOGO = Path(__file__).parent / "assets" / "sjtu" / "sjtu-logo-white.png"
_BACKGROUND_ART = (
    Path(__file__).parent / "assets" / "sjtu" / "sjtu-solid-red-background.png"
)
_HEX_RE = re.compile(r"#(?P<hex>[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
_RGB_RE = re.compile(
    r"(?P<kind>rgba?)\(\s*(?P<r>\d{1,3})\s*,\s*(?P<g>\d{1,3})\s*,\s*"
    r"(?P<b>\d{1,3})(?:\s*,\s*(?P<a>(?:\d*\.)?\d+))?\s*\)",
    re.I,
)
_DECLARATION_RE = re.compile(
    r"(?P<property>background(?:-color)?|border(?:-[a-z-]+)?|outline(?:-color)?|"
    r"box-shadow|color|fill|stroke)\s*:\s*(?P<value>[^;{}]+)",
    re.I,
)
_STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.I | re.S)
_STYLE_ATTR_RE = re.compile(
    r"(?P<prefix>\bstyle\s*=\s*)(?P<quote>['\"])(?P<css>.*?)(?P=quote)",
    re.I | re.S,
)
_BODY_OPEN_RE = re.compile(r"<body\b(?P<attrs>[^>]*)>", re.I | re.S)
_BACKGROUND_DECL_RE = re.compile(
    r"(?:^|;)\s*background(?:-color)?\s*:\s*[^;]+",
    re.I,
)
_CSS_RULE_RE = re.compile(r"(?P<selectors>[^{}]+)\{(?P<css>[^{}]*)\}", re.S)
_CANVAS_BACKGROUND_RE = re.compile(
    r"(?P<property>background(?:-color|-image)?)\s*:\s*(?P<value>[^;{}]+)",
    re.I,
)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.upper()
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _target(rgb: tuple[int, int, int], property_name: str) -> str | None:
    normalized = tuple(channel / 255 for channel in rgb)
    hue, saturation, _value = colorsys.rgb_to_hsv(*normalized)
    lightness = colorsys.rgb_to_hls(*normalized)[1]
    if not 185 <= hue * 360 <= 260 or max(rgb) - min(rgb) < 8:
        return None
    prop = property_name.lower()
    if prop == "color":
        return SJTU_RED if saturation >= 0.45 and lightness >= 0.12 else None
    if prop.startswith(("border", "outline")) or prop == "stroke":
        return SJTU_LIGHT_GRAY if saturation < 0.35 or lightness >= 0.78 else SJTU_RED
    return SJTU_CHAMPAGNE if lightness >= 0.82 else SJTU_RED


def _replace_css(css: str, counts: Counter[str]) -> str:
    def declaration(match: re.Match[str]) -> str:
        prop = match.group("property")
        value = match.group("value")

        def hex_color(color_match: re.Match[str]) -> str:
            source = color_match.group("hex").upper()
            if len(source) == 3:
                source = "".join(character * 2 for character in source)
            target = _target(_rgb(source), prop)
            if not target or target == source:
                return color_match.group(0)
            counts[f"#{source}->#{target}"] += 1
            return f"#{target}"

        def rgb_color(color_match: re.Match[str]) -> str:
            source_rgb = tuple(
                max(0, min(255, int(color_match.group(channel))))
                for channel in ("r", "g", "b")
            )
            target = _target(source_rgb, prop)  # type: ignore[arg-type]
            if not target:
                return color_match.group(0)
            source = "".join(f"{channel:02X}" for channel in source_rgb)
            target_rgb = _rgb(target)
            counts[f"#{source}->#{target}"] += 1
            if color_match.group("kind").lower() == "rgba":
                return f"rgba({target_rgb[0]},{target_rgb[1]},{target_rgb[2]},{color_match.group('a') or '1'})"
            return f"rgb({target_rgb[0]},{target_rgb[1]},{target_rgb[2]})"

        value = _HEX_RE.sub(hex_color, value)
        value = _RGB_RE.sub(rgb_color, value)
        return f"{prop}: {value}"

    return _DECLARATION_RE.sub(declaration, css)


def _replace_html_colors(html: str, counts: Counter[str]) -> str:
    html = _STYLE_BLOCK_RE.sub(
        lambda match: match.group(1) + _replace_css(match.group(2), counts) + match.group(3),
        html,
    )

    def style_attr(match: re.Match[str]) -> str:
        quote = match.group("quote")
        return match.group("prefix") + quote + _replace_css(match.group("css"), counts) + quote

    return _STYLE_ATTR_RE.sub(style_attr, html)


def _normalize_content_canvas(html: str) -> tuple[str, bool]:
    """Give content slides a light inline canvas that overrides broad CSS rules."""

    body_match = _BODY_OPEN_RE.search(html)
    if not body_match:
        return html, False

    body_tag = body_match.group(0)
    style_match = _STYLE_ATTR_RE.search(body_tag)
    if style_match:
        original_css = style_match.group("css")
        background_declarations = list(_BACKGROUND_DECL_RE.finditer(original_css))
        existing_background = (
            background_declarations[0].group(0).split(":", 1)[1].strip()
            if len(background_declarations) == 1
            else ""
        )
        if (
            len(background_declarations) == 1
            and re.fullmatch(
                rf"#{FINANCIAL_CONTENT_BACKGROUND}(?:\s*!important)?",
                existing_background,
                flags=re.I,
            )
        ):
            return html, False
        css = _BACKGROUND_DECL_RE.sub("", original_css)
        css = css.strip().strip(";")
        normalized_css = (
            f"{css};background:#{FINANCIAL_CONTENT_BACKGROUND}"
            if css
            else f"background:#{FINANCIAL_CONTENT_BACKGROUND}"
        )
        quote = style_match.group("quote")
        replacement = style_match.group("prefix") + quote + normalized_css + quote
        normalized_tag = (
            body_tag[: style_match.start()]
            + replacement
            + body_tag[style_match.end() :]
        )
    else:
        normalized_tag = (
            body_tag[:-1] + f' style="background:#{FINANCIAL_CONTENT_BACKGROUND}">'
        )

    return (
        html[: body_match.start()] + normalized_tag + html[body_match.end() :],
        normalized_tag != body_tag,
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


def _selector_targets_body(selector: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:html\s+)?body(?:\[[^\]]+\])?(?:(?:\.|#)[a-z0-9_-]+)*",
            selector.strip(),
            flags=re.I,
        )
    )


def _body_style_fragments(html: str) -> list[str]:
    fragments: list[str] = []
    for style_block in _STYLE_BLOCK_RE.finditer(html):
        for rule in _CSS_RULE_RE.finditer(style_block.group(2)):
            if any(
                _selector_targets_body(selector)
                for selector in rule.group("selectors").split(",")
            ):
                fragments.append(rule.group("css"))

    body_match = _BODY_OPEN_RE.search(html)
    if body_match and (style_match := _STYLE_ATTR_RE.search(body_match.group(0))):
        fragments.append(style_match.group("css"))
    return fragments


def _has_solid_red_canvas(html: str) -> bool:
    color = ""
    has_image = False
    for css in _body_style_fragments(html):
        for declaration in _CANVAS_BACKGROUND_RE.finditer(css):
            prop = declaration.group("property").lower()
            value = declaration.group("value").strip()
            lowered = value.lower()
            if prop == "background":
                has_image = "url(" in lowered or "gradient(" in lowered
                color = value
            elif prop == "background-color":
                color = value
            elif prop == "background-image":
                has_image = lowered not in {"none", "initial", "unset"}

    red_values = {
        f"#{SJTU_RED}".lower(),
        "rgb(166,32,56)",
        "rgba(166,32,56,1)",
    }
    return not has_image and color.replace(" ", "").lower() in red_values


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
        f'src="data:image/png;base64,{encoded_background}" '
        'style="position:absolute;inset:0;width:100%;height:100%;z-index:-1;display:block">'
    )
    return (
        html[: body_match.start()]
        + branded_tag
        + "\n"
        + background_markup
        + html[body_match.end() :],
        True,
    )


def apply_sjtu_brand_to_html(
    slide_html_dir: str | Path,
    page_roles: list[str],
) -> dict[str, Any]:
    """Modify financial slide HTML in place; safe to call more than once."""

    html_dir = Path(slide_html_dir).resolve()
    slide_paths = sorted(html_dir.glob("slide_*.html"))
    expected = [f"slide_{index:02d}.html" for index in range(1, len(slide_paths) + 1)]
    if not slide_paths or [path.name for path in slide_paths] != expected:
        raise RuntimeError("Financial slide HTML must be contiguous from slide_01.html.")
    if len(page_roles) != len(slide_paths):
        raise RuntimeError("Financial page-role count does not match slide HTML count.")
    if not _LOGO.is_file():
        raise RuntimeError(f"SJTU logo is missing: {_LOGO}")
    if not _BACKGROUND_ART.is_file():
        raise RuntimeError(f"SJTU background art is missing: {_BACKGROUND_ART}")

    counts: Counter[str] = Counter()
    logos_added = 0
    backgrounds_added = 0
    content_canvases_normalized = 0
    markup = _logo_markup()
    encoded_background = base64.b64encode(_BACKGROUND_ART.read_bytes()).decode("ascii")
    for path, role in zip(slide_paths, page_roles, strict=True):
        html = path.read_text(encoding="utf-8")
        if role == "content":
            html, normalized = _normalize_content_canvas(html)
            content_canvases_normalized += int(normalized)
        html = _replace_html_colors(html, counts)
        if role in {"title", "closing"} and _has_solid_red_canvas(html):
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
        "color_replacements": dict(sorted(counts.items())),
        "logos_added": logos_added,
        "backgrounds_added": backgrounds_added,
        "content_canvases_normalized": content_canvases_normalized,
    }
