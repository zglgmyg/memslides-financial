"""Explicit derived reading order based on MinerU content-list order."""

from __future__ import annotations

from typing import Any


def assign_reading_order(blocks: list[dict[str, Any]]) -> list[str]:
    if not all("source_content_index" in block for block in blocks):
        ordered = list(blocks)
    else:
        groups: dict[int, list[dict[str, Any]]] = {}
        for block in blocks:
            groups.setdefault(block["source_content_index"], []).append(block)
        ordered = []
        for source_index in sorted(groups):
            group = groups[source_index]
            captions = [block for block in group if "caption" in block.get("source_field", "")]
            footnotes = [block for block in group if "footnote" in block.get("source_field", "")]
            primary = [block for block in group if block not in captions and block not in footnotes]
            ordered.extend([*captions, *primary, *footnotes])

        # MinerU can place a vector figure's source note before its final visual
        # column. Move only that verified note behind all figure body blocks.
        vector_captions = [
            block
            for block in ordered
            if block.get("type") == "image_caption" and block.get("source_type") == "text"
        ]
        for caption in vector_captions:
            notes = [
                block
                for block in ordered
                if block.get("page") == caption.get("page")
                and block.get("type") == "image_footnote"
                and block.get("source_type") == "text"
                and block.get("bbox") is not None
                and caption.get("bbox") is not None
                and block["bbox"][1] > caption["bbox"][3]
            ]
            if not notes:
                continue
            note = min(notes, key=lambda block: block["bbox"][1])
            body = [
                block
                for block in ordered
                if block.get("page") == caption.get("page")
                and block.get("type") == "figure_text"
                and block.get("bbox") is not None
                and block["bbox"][1] >= caption["bbox"][3]
                and block["bbox"][3] <= note["bbox"][1]
            ]
            if body:
                ordered.remove(note)
                last_body_position = max(ordered.index(block) for block in body)
                ordered.insert(last_body_position + 1, note)

    order: list[str] = []
    for index, block in enumerate(ordered):
        block["reading_order"] = index
        order.append(block["id"])
    return order
