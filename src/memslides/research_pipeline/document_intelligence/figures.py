"""Build a deterministic, prompt-ready inventory of original report figures."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .models import DocumentIntelligenceSnapshot


_BOILERPLATE_MARKERS = (
    "本公司具备证券投资咨询业务资格",
    "证券研究报告",
    "MINSHENG SECURITIES",
    "执业证书",
    "分析师",
    "邮箱：",
    "资料来源：",
    "当前价格：",
    "民生证券",
)


def _useful_nearby_text(text: str) -> bool:
    if len(text) < 12 or re.fullmatch(r"[\d\s./-]+", text):
        return False
    if text.startswith("<table") or any(
        marker.casefold() in text.casefold() for marker in _BOILERPLATE_MARKERS
    ):
        return False
    return True


def _block_text(snapshot: DocumentIntelligenceSnapshot, block_id: object) -> str:
    block = snapshot.blocks_by_id.get(str(block_id), {})
    return str(block.get("text_raw") or "").strip()


def _caption(snapshot: DocumentIntelligenceSnapshot, figure: Mapping[str, Any]) -> str:
    block_ids = list(figure.get("caption_block_ids", []))
    if not block_ids and figure.get("caption_block_id"):
        block_ids = [figure["caption_block_id"]]
    return " ".join(
        value for block_id in block_ids if (value := _block_text(snapshot, block_id))
    )


def _section_title(
    snapshot: DocumentIntelligenceSnapshot, section_id: str | None
) -> str:
    section = snapshot.sections_by_id.get(section_id or "", {})
    return _block_text(snapshot, section.get("title_block_id"))


def _asset_available(
    snapshot: DocumentIntelligenceSnapshot, asset_path: object
) -> bool:
    if not isinstance(asset_path, str) or not asset_path:
        return False
    relative = Path(asset_path)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    bundle = snapshot.bundle_directory.resolve()
    resolved = (bundle / relative).resolve()
    try:
        resolved.relative_to(bundle)
    except ValueError:
        return False
    return resolved.is_file()


def _source_key(
    figure: Mapping[str, Any], original_index: int
) -> tuple[int, float, float, float, int]:
    source_index = figure.get("source_content_index")
    if isinstance(source_index, int):
        return (0, float(source_index), 0.0, 0.0, original_index)
    page = figure.get("page")
    bbox = figure.get("bbox")
    y = float(bbox[1]) if isinstance(bbox, list) and len(bbox) == 4 else 0.0
    x = float(bbox[0]) if isinstance(bbox, list) and len(bbox) == 4 else 0.0
    return (
        1,
        float(page) if isinstance(page, int) else float("inf"),
        y,
        x,
        original_index,
    )


def _nearby_blocks(
    snapshot: DocumentIntelligenceSnapshot,
    figure_id: str,
    figure: Mapping[str, Any],
    *,
    limit: int = 3,
) -> list[dict[str, str]]:
    positions = {
        block_id: index for index, block_id in enumerate(snapshot.ordered_block_ids)
    }
    related_ids = {
        block_id
        for block_id, figure_ids in snapshot.block_figure_ids.items()
        if figure_id in figure_ids
    }
    caption_ids = {
        str(value)
        for item in snapshot.figures_by_id.values()
        for value in (
            *(item.get("caption_block_ids") or []),
            *(item.get("footnote_block_ids") or []),
        )
    }
    anchor_positions = [
        positions[block_id] for block_id in related_ids if block_id in positions
    ]
    anchor = (
        min(anchor_positions)
        if anchor_positions
        else len(snapshot.ordered_block_ids)
    )
    section_id = (
        str(figure["section_id"]) if figure.get("section_id") is not None else None
    )
    page = figure.get("page")
    candidates: list[tuple[int, int, str, str]] = []
    for block_id in snapshot.ordered_block_ids:
        if block_id in caption_ids:
            continue
        block = snapshot.blocks_by_id[block_id]
        text = str(block.get("text_raw") or "").strip()
        if (
            not _useful_nearby_text(text)
            or block.get("type") in {"figure", "figure_text", "table"}
            or re.match(r"^[^\n]{1,30}\(\d{6}\)/", text)
        ):
            continue
        block_section = (
            str(block["section_id"]) if block.get("section_id") is not None else None
        )
        if section_id is not None and block_section != section_id:
            continue
        block_page = block.get("page")
        if isinstance(page, int) and isinstance(block_page, int) and abs(page - block_page) > 1:
            continue
        position = positions[block_id]
        candidates.append((abs(position - anchor), position, block_id, text))
    chosen = sorted(candidates)[:limit]
    chosen.sort(key=lambda item: item[1])
    return [{"block_id": block_id, "text": text} for _, _, block_id, text in chosen]


def build_figure_inventory(
    snapshot: DocumentIntelligenceSnapshot,
) -> list[dict[str, Any]]:
    """Return original figures in stable PDF order with resolved semantic context."""

    ordered = sorted(
        enumerate(snapshot.figures_by_id.values()),
        key=lambda item: _source_key(item[1], item[0]),
    )
    inventory: list[dict[str, Any]] = []
    for order, (_, figure) in enumerate(ordered, start=1):
        figure_id = str(figure.get("id") or "")
        section_id = (
            str(figure["section_id"])
            if figure.get("section_id") is not None
            else None
        )
        nearby = _nearby_blocks(snapshot, figure_id, figure)
        caption = _caption(snapshot, figure)
        available = _asset_available(snapshot, figure.get("asset_path"))
        inventory.append(
            {
                "figure_id": figure_id,
                "caption": caption,
                "page": figure.get("page"),
                "section_id": section_id,
                "section_title": _section_title(snapshot, section_id),
                "nearby_blocks": nearby,
                "asset_path": figure.get("asset_path"),
                "available": available,
                "selectable": available and bool(caption),
                "order": order,
            }
        )
    return inventory
