"""Persist and apply source notes for figures selected from a direct human PDF."""

from __future__ import annotations

import html
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from memslides.utils.webview import PlaywrightConverter, convert_html_to_pptx


_SOURCE_RE = re.compile(r"^\s*(?:资料|数据)来源\s*[:：]\s*(?P<value>.+)$")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_MARKER = "data-human-pdf-figure-source"


def _plain_text(value: object) -> str:
    return _SPACE_RE.sub(" ", _TAG_RE.sub("", str(value or ""))).strip()


def _source_text(value: object) -> str | None:
    match = _SOURCE_RE.match(_plain_text(value))
    if match is None:
        return None
    payload = match.group("value").strip()
    return f"资料来源：{payload}" if payload else None


def build_figure_source_manifest(snapshot: Any) -> dict[str, Any]:
    """Extract only explicit MinerU figure source notes from a PDF snapshot."""

    figures: dict[str, dict[str, Any]] = {}
    for figure_id, figure in snapshot.figures_by_id.items():
        source_texts: list[str] = []
        source_block_ids: list[str] = []
        for raw_block_id in figure.get("footnote_block_ids", []) or []:
            block_id = str(raw_block_id)
            block = snapshot.blocks_by_id.get(block_id, {})
            source = _source_text(block.get("text_raw"))
            if source and source not in source_texts:
                source_texts.append(source)
                source_block_ids.append(block_id)
        if not source_texts:
            continue
        caption_ids = list(figure.get("caption_block_ids", []) or [])
        caption = " ".join(
            text
            for block_id in caption_ids
            if (
                text := _plain_text(
                    snapshot.blocks_by_id.get(str(block_id), {}).get("text_raw")
                )
            )
        )
        figures[str(figure_id)] = {
            "figure_id": str(figure_id),
            "page": figure.get("page"),
            "bbox": figure.get("bbox"),
            "caption": caption,
            "source_text": "；".join(source_texts),
            "source_block_ids": source_block_ids,
            "source_content_index": figure.get("source_content_index"),
        }
    return {
        "schema_version": "1.0.0",
        "mode": "human_pdf_figure_sources",
        "figures": figures,
        "summary": {"source_figure_count": len(figures)},
    }


def write_figure_source_manifest(snapshot: Any, output_path: Path) -> Path:
    payload = build_figure_source_manifest(snapshot)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return output_path


def _image_name(raw_source: str) -> str:
    if raw_source.startswith("data:"):
        return ""
    parsed = urlparse(raw_source)
    return Path(unquote(parsed.path.replace("\\", "/"))).name


def _image_index(html_text: str, filename: str) -> int | None:
    soup = BeautifulSoup(html_text, "lxml")
    for index, image in enumerate(soup.find_all("img")):
        if image.get("data-memslides-pptx-background"):
            continue
        if _image_name(str(image.get("src") or "")) == filename:
            return index
    return None


def build_application_plan(
    *,
    outline: Mapping[str, Any],
    asset_manifest: Mapping[str, Any],
    figure_sources: Mapping[str, Any],
    html_directory: Path,
) -> dict[str, Any]:
    slide_pages = {
        str(slide.get("slide_id") or ""): page
        for page, slide in enumerate(outline.get("slides", []), start=1)
        if isinstance(slide, Mapping)
    }
    sources = figure_sources.get("figures", {})
    sources = sources if isinstance(sources, Mapping) else {}
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for asset in asset_manifest.get("assets", []) or []:
        if not isinstance(asset, Mapping):
            continue
        if not (
            asset.get("generated_by_tool") is False
            and str(asset.get("renderer") or "") == "verified-source-copy"
        ):
            continue
        verification = asset.get("verification", {})
        verification = verification if isinstance(verification, Mapping) else {}
        figure_ids = [
            str(source.get("id"))
            for source in verification.get("sources", []) or []
            if isinstance(source, Mapping)
            and source.get("kind") == "figure"
            and source.get("id")
        ]
        filename = str(asset.get("filename") or Path(str(asset.get("path") or "")).name)
        if not figure_ids or figure_ids[0] not in sources:
            skipped.append({"asset": filename, "reason": "explicit_source_note_not_found"})
            continue
        slide_id = str(verification.get("slide_id") or "")
        page_number = slide_pages.get(slide_id)
        html_path = html_directory / f"slide_{page_number:02d}.html" if page_number else None
        if html_path is None or not html_path.is_file():
            skipped.append({"asset": filename, "reason": "slide_html_not_found"})
            continue
        image_index = _image_index(html_path.read_text(encoding="utf-8"), filename)
        if image_index is None:
            skipped.append({"asset": filename, "reason": "image_not_used_in_final_html"})
            continue
        source = sources[figure_ids[0]]
        if not isinstance(source, Mapping) or not source.get("source_text"):
            skipped.append({"asset": filename, "reason": "invalid_source_record"})
            continue
        planned.append(
            {
                "slide_id": slide_id,
                "page_number": page_number,
                "visualization_id": str(verification.get("visualization_id") or ""),
                "figure_id": figure_ids[0],
                "asset_filename": filename,
                "image_index": image_index,
                "source_text": str(source.get("source_text") or ""),
            }
        )
    return {"items": planned, "skipped": skipped}


async def _measure(
    converter: PlaywrightConverter, html_path: Path, image_index: int
) -> dict[str, Any] | None:
    page = converter.page
    if page is None:
        raise RuntimeError("Playwright page is unavailable")
    await page.set_viewport_size({"width": 1280, "height": 720})
    await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
    return await page.evaluate(
        """(imageIndex) => {
          const images = Array.from(document.querySelectorAll('img'));
          const target = images[imageIndex];
          if (!target) return null;
          const body = document.body.getBoundingClientRect();
          const rect = target.getBoundingClientRect();
          const blockers = [];
          for (const element of document.querySelectorAll(
            'img,table,svg,canvas,h1,h2,h3,h4,h5,h6,p,li,[data-human-pdf-figure-source]'
          )) {
            if (element === target || element.contains(target) || target.contains(element)) continue;
            const style = getComputedStyle(element);
            if (style.display === 'none' || style.visibility === 'hidden' ||
                Number(style.opacity || 1) === 0) continue;
            const other = element.getBoundingClientRect();
            if (other.width <= 0 || other.height <= 0) continue;
            blockers.push({
              left: other.left - body.left, top: other.top - body.top,
              right: other.right - body.left, bottom: other.bottom - body.top
            });
          }
          return {
            body: {width: body.width, height: body.height},
            image: {
              left: rect.left - body.left, top: rect.top - body.top,
              right: rect.right - body.left, bottom: rect.bottom - body.top,
              width: rect.width, height: rect.height
            }, blockers
          };
        }""",
        image_index,
    )


def _intersects(first: Mapping[str, float], second: Mapping[str, float]) -> bool:
    return not (
        first["right"] <= second["left"]
        or first["left"] >= second["right"]
        or first["bottom"] <= second["top"]
        or first["top"] >= second["bottom"]
    )


def _safe_box(
    measurement: Mapping[str, Any], source_text: str
) -> tuple[dict[str, float] | None, str | None]:
    image = measurement.get("image", {})
    body = measurement.get("body", {})
    width = float(image.get("width", 0) or 0)
    if width < 80:
        return None, "image_too_narrow"
    lines = max(1, math.ceil(len(source_text) * 8.2 / width))
    if lines > 3:
        return None, "source_too_long_for_image_width"
    height = float(lines * 12)
    box = {
        "left": float(image.get("left", 0) or 0),
        "top": float(image.get("bottom", 0) or 0) + 3,
        "right": float(image.get("right", 0) or 0),
        "bottom": float(image.get("bottom", 0) or 0) + 3 + height,
        "width": width,
        "height": height,
    }
    if box["left"] < 0 or box["right"] > float(body.get("width", 0) or 0):
        return None, "source_box_outside_slide"
    if box["bottom"] > float(body.get("height", 0) or 0) - 4:
        return None, "no_space_below_image"
    for blocker in measurement.get("blockers", []) or []:
        if isinstance(blocker, Mapping) and _intersects(box, blocker):
            return None, "source_box_would_overlap_content"
    return box, None


def _inject(
    html_text: str, *, source_key: str, source_text: str, box: Mapping[str, float]
) -> str:
    if _MARKER in html_text and f'data-source-key="{source_key}"' in html_text:
        return html_text
    closing = html_text.lower().rfind("</body>")
    if closing < 0:
        raise RuntimeError("Slide HTML has no closing body tag")
    key = html.escape(source_key, quote=True)
    markup = (
        f'<!-- HUMAN_PDF_SOURCE_START:{key} -->'
        f'<div {_MARKER}="true" data-source-key="{key}" '
        'style="position:absolute;z-index:70;box-sizing:border-box;overflow:hidden;'
        f'left:{box["left"]:.2f}px;top:{box["top"]:.2f}px;'
        f'width:{box["width"]:.2f}px;height:{box["height"]:.2f}px;'
        "font-family:'Microsoft YaHei',Arial,sans-serif;font-size:10px;"
        'font-weight:400;line-height:12px;color:#64748b;background:transparent;'
        'text-align:left;white-space:normal;pointer-events:none;">'
        f'{html.escape(source_text)}</div>'
        f'<!-- HUMAN_PDF_SOURCE_END:{key} -->'
    )
    return html_text[:closing] + markup + html_text[closing:]


async def apply_human_pdf_figure_citations(
    *, research_directory: Path, deck_directory: Path
) -> dict[str, Any]:
    """Apply direct-PDF figure sources to copied final HTML and a separate PPTX."""

    source_html = deck_directory / "outputs"
    target_html = deck_directory / "outputs-human-cited"
    cited_pptx = deck_directory / "manuscript-human-cited.pptx"
    figure_source_path = research_directory / "figure_source_manifest.json"
    outline_path = research_directory / "slide_outline.json"
    asset_manifest_path = deck_directory / "asset_manifest.json"
    for path in (source_html, figure_source_path, outline_path, asset_manifest_path):
        if not path.exists():
            raise RuntimeError(f"Missing human PDF citation input: {path}")
    if target_html.exists():
        shutil.rmtree(target_html)
    shutil.copytree(source_html, target_html)
    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))
    plan = build_application_plan(
        outline=load(outline_path),
        asset_manifest=load(asset_manifest_path),
        figure_sources=load(figure_source_path),
        html_directory=target_html,
    )
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(plan["skipped"])
    items = plan["items"]
    if items:
        async with PlaywrightConverter() as converter:
            for item in items:
                html_path = target_html / f'slide_{int(item["page_number"]):02d}.html'
                measurement = await _measure(
                    converter, html_path, int(item["image_index"])
                )
                if measurement is None:
                    skipped.append({**item, "reason": "image_geometry_not_found"})
                    continue
                box, reason = _safe_box(measurement, str(item["source_text"]))
                if box is None:
                    skipped.append({**item, "reason": reason or "unsafe_placement"})
                    continue
                current = html_path.read_text(encoding="utf-8")
                html_path.write_text(
                    _inject(
                        current,
                        source_key=f'{item["slide_id"]}__{item["visualization_id"]}',
                        source_text=str(item["source_text"]),
                        box=box,
                    ),
                    encoding="utf-8",
                )
                applied.append({**item, "box": box})
    if applied:
        await convert_html_to_pptx(
            target_html,
            cited_pptx,
            "16:9",
            speaker_notes_path=deck_directory / "speaker_manuscript.json",
        )
    return {
        "schema_version": "1.0.0",
        "mode": "human_pdf_figure_sources",
        "applied": applied,
        "skipped": skipped,
        "summary": {
            "planned_count": len(items),
            "applied_count": len(applied),
            "skipped_count": len(skipped),
        },
        "outputs": {
            "original_html": str(source_html),
            "cited_html": str(target_html),
            "cited_pptx": str(cited_pptx) if applied else "",
        },
    }
