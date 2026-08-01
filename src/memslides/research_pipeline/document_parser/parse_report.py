#!/usr/bin/env python3
"""Parse a Markdown or plain-text financial report into structured blocks.

The output conforms to ``parsed_document_schema.json`` and is designed as the
deterministic input layer for the later LLM outline-generation step.

Examples:
    python parse_report.py report.md -o report_parsed.json
    python parse_report.py report.txt --format plain_text

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


PARSER_NAME = "parse_report"
PARSER_VERSION = "1.0.0"

ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)\s*$")
SETEXT_HEADING_RE = re.compile(r"^\s{0,3}(?P<marker>=+|-+)\s*$")
LIST_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-+*]|\d+[.)])[ \t]+(?P<text>.+?)\s*$"
)
CITATION_RE = re.compile(r"\[\^cite_id:([^\]]+)\]")
IMAGE_ONLY_RE = re.compile(
    r'^\s*!\[(?P<alt>[^\]]*)\]\((?P<destination>.+?)\)\s*$'
)
FENCE_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
BLOCKQUOTE_RE = re.compile(r"^\s*>[ \t]?(?P<text>.*)$")
HORIZONTAL_RULE_RE = re.compile(
    r"^\s{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})\s*$"
)
TABLE_SEPARATOR_CELL_RE = re.compile(r"^\s*:?-{3,}:?\s*$")
PLAIN_CHAPTER_RE = re.compile(
    r"^(第[一二三四五六七八九十百零〇]+[章节篇部分])(?:[：:、.\s]+)?(.+)$"
)
PLAIN_CHINESE_NUMBER_RE = re.compile(
    r"^([一二三四五六七八九十百]+)[、.]\s*(.+)$"
)
PLAIN_DECIMAL_RE = re.compile(r"^(\d+(?:\.\d+){0,5})[、.\s]+(.+)$")
INLINE_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\([^)]+\)")
INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
HTML_TAG_RE = re.compile(r"<[^>]+>")


class ParseError(RuntimeError):
    """Raised when a source file cannot be parsed safely."""


def unique_in_order(values: Sequence[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract_citations(text: str) -> List[str]:
    return unique_in_order([match.strip() for match in CITATION_RE.findall(text) if match.strip()])


def clean_inline_text(text: str) -> str:
    """Remove common Markdown wrappers without changing the underlying words."""
    value = CITATION_RE.sub("", text)
    value = INLINE_IMAGE_RE.sub(lambda match: match.group(1), value)
    value = INLINE_LINK_RE.sub(lambda match: match.group(1), value)
    value = HTML_TAG_RE.sub("", value)
    value = re.sub(r"(?<!\\)(\*\*|__)(.+?)\1", r"\2", value)
    value = re.sub(r"(?<!\\)(\*|_)(.+?)\1", r"\2", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = value.replace("\\|", "|")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def normalized_indent(indent: str) -> int:
    spaces = len(indent.expandtabs(4))
    return spaces // 2


def split_table_row(line: str) -> List[str]:
    """Split a Markdown table row while respecting escaped pipe characters."""
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]

    cells: List[str] = []
    current: List[str] = []
    escaped = False
    in_code = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "`":
            in_code = not in_code
            current.append(char)
            continue
        if char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def is_table_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = split_table_row(line)
    return bool(cells) and all(TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells)


def table_alignment(cell: str) -> str:
    value = cell.strip()
    left = value.startswith(":")
    right = value.endswith(":")
    if left and right:
        return "center"
    if left:
        return "left"
    if right:
        return "right"
    return "none"


def parse_image_destination(value: str) -> Tuple[str, Optional[str]]:
    """Parse ``path \"title\"`` while allowing spaces in an unquoted path."""
    candidate = value.strip()
    match = re.match(r'^(.*?)(?:\s+["\'](.*)["\'])$', candidate)
    if match:
        return match.group(1).strip(), match.group(2)
    if candidate.startswith("<") and candidate.endswith(">"):
        candidate = candidate[1:-1].strip()
    return candidate, None


def safe_document_id(stem: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_.-")
    return value or "document"


def read_text_with_encoding(path: Path) -> Tuple[bytes, str, str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ParseError(f"Input file does not exist: {path}") from exc
    except OSError as exc:
        raise ParseError(f"Cannot read input file {path}: {exc}") from exc

    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw, raw.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass

    attempts = ("utf-8", "gb18030")
    for encoding in attempts:
        try:
            return raw, raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ParseError(f"Cannot decode {path}; tried {', '.join(attempts)}")


class ReportParser:
    def __init__(
        self,
        text: str,
        *,
        source_file: str,
        source_format: str,
        encoding: str,
        source_hash_sha256: str,
        document_id: str,
        plain_text_heading_detection: bool = True,
        preserve_code_blocks: bool = True,
        preserve_images: bool = True,
        merge_adjacent_paragraph_lines: bool = True,
    ) -> None:
        if source_format not in {"markdown", "plain_text"}:
            raise ParseError(f"Unsupported source format: {source_format}")
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = self.text.splitlines()
        self.source_file = source_file
        self.source_format = source_format
        self.encoding = encoding
        self.source_hash_sha256 = source_hash_sha256
        self.document_id = document_id
        self.plain_text_heading_detection = plain_text_heading_detection
        self.preserve_code_blocks = preserve_code_blocks
        self.preserve_images = preserve_images
        self.merge_adjacent_paragraph_lines = merge_adjacent_paragraph_lines
        self.blocks: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        self.heading_stack: List[Dict[str, Any]] = []
        self._next_block_number = 1

    def parse(self) -> Dict[str, Any]:
        index = 0
        while index < len(self.lines):
            line = self.lines[index]
            if not line.strip():
                index += 1
                continue

            fence_match = FENCE_RE.match(line)
            if fence_match:
                index = self._parse_code(index, fence_match)
                continue

            heading = self._detect_heading(index)
            if heading is not None:
                level, heading_text, style, end_index = heading
                self._append_heading(index, end_index, level, heading_text, style)
                index = end_index + 1
                continue

            if self._starts_table(index):
                index = self._parse_table(index)
                continue

            if HORIZONTAL_RULE_RE.fullmatch(line):
                self._append_block("horizontal_rule", index, index, {})
                index += 1
                continue

            if LIST_ITEM_RE.match(line):
                index = self._parse_list(index)
                continue

            if BLOCKQUOTE_RE.match(line):
                index = self._parse_blockquote(index)
                continue

            image_match = IMAGE_ONLY_RE.fullmatch(line)
            if image_match and self.preserve_images:
                destination, title = parse_image_destination(image_match.group("destination"))
                self._append_block(
                    "image",
                    index,
                    index,
                    {
                        "alt_text": clean_inline_text(image_match.group("alt")),
                        "url": destination,
                        "title": title,
                    },
                )
                index += 1
                continue

            index = self._parse_paragraph(index)

        result = self._build_result()
        self._validate_invariants(result)
        return result

    def _new_block_id(self) -> str:
        block_id = f"block_{self._next_block_number:04d}"
        self._next_block_number += 1
        return block_id

    def _current_section_path(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self.heading_stack]

    def _current_parent_heading_id(self) -> Optional[str]:
        if not self.heading_stack:
            return None
        return str(self.heading_stack[-1]["heading_block_id"])

    def _append_block(
        self,
        block_type: str,
        start_index: int,
        end_index: int,
        fields: Mapping[str, Any],
        *,
        section_path: Optional[List[Dict[str, Any]]] = None,
        parent_heading_id: Optional[str] = None,
        block_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw_text = "\n".join(self.lines[start_index : end_index + 1])
        block: Dict[str, Any] = {
            "block_id": block_id or self._new_block_id(),
            "type": block_type,
            "line_start": start_index + 1,
            "line_end": end_index + 1,
            "raw_text": raw_text,
            "section_path": section_path if section_path is not None else self._current_section_path(),
            "parent_heading_id": (
                parent_heading_id
                if parent_heading_id is not None
                else self._current_parent_heading_id()
            ),
            "citations": extract_citations(raw_text),
        }
        block.update(fields)
        self.blocks.append(block)
        return block

    def _detect_heading(self, index: int) -> Optional[Tuple[int, str, str, int]]:
        line = self.lines[index]
        match = ATX_HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            text = re.sub(r"\s+#+\s*$", "", match.group(2)).strip()
            if text:
                return level, clean_inline_text(text), "markdown", index

        if self.source_format == "markdown" and index + 1 < len(self.lines):
            underline = SETEXT_HEADING_RE.fullmatch(self.lines[index + 1])
            if underline and line.strip() and not self._line_starts_nonparagraph_block(index):
                marker = underline.group("marker")
                return (1 if marker.startswith("=") else 2), clean_inline_text(line), "markdown", index + 1

        if not self.plain_text_heading_detection or self.source_format != "plain_text":
            return None
        plain = self._detect_plain_text_heading(index)
        if plain is None:
            return None
        level, text, style = plain
        return level, text, style, index

    def _detect_plain_text_heading(self, index: int) -> Optional[Tuple[int, str, str]]:
        line = self.lines[index].strip()
        if not line or len(line) > 160 or CITATION_RE.search(line):
            return None
        if line.endswith(("。", "！", "？", "；", ";")):
            return None

        previous_blank = index == 0 or not self.lines[index - 1].strip()
        if not previous_blank:
            return None

        match = PLAIN_CHAPTER_RE.match(line)
        if match:
            return 1, clean_inline_text(line), "plain_text"
        match = PLAIN_CHINESE_NUMBER_RE.match(line)
        if match:
            return 2, clean_inline_text(match.group(2)), "plain_text"
        match = PLAIN_DECIMAL_RE.match(line)
        if match:
            segments = match.group(1).count(".") + 1
            return min(segments + 1, 6), clean_inline_text(match.group(2)), "plain_text"
        return None

    def _append_heading(self, start_index: int, end_index: int, level: int, text: str, style: str) -> None:
        parent_stack = [item for item in self.heading_stack if int(item["level"]) < level]
        parent_id = parent_stack[-1]["heading_block_id"] if parent_stack else None
        block_id = self._new_block_id()
        current_ref = {"level": level, "title": text, "heading_block_id": block_id}
        section_path = [dict(item) for item in parent_stack] + [dict(current_ref)]

        if self.heading_stack and level > int(self.heading_stack[-1]["level"]) + 1:
            self._warning(
                "HEADING_LEVEL_JUMP",
                f"Heading level jumps from {self.heading_stack[-1]['level']} to {level}",
                start_index,
                end_index,
            )

        self._append_block(
            "heading",
            start_index,
            end_index,
            {"text": text, "level": level, "style": style},
            section_path=section_path,
            parent_heading_id=str(parent_id) if parent_id else None,
            block_id=block_id,
        )
        self.heading_stack = section_path

    def _parse_code(self, start: int, opening_match: re.Match[str]) -> int:
        opening = opening_match.group("fence")
        marker = opening[0]
        info = opening_match.group("info").strip()
        end = start + 1
        closed = False
        closing_re = re.compile(rf"^\s*{re.escape(marker)}{{{len(opening)},}}\s*$")
        while end < len(self.lines):
            if closing_re.match(self.lines[end]):
                closed = True
                break
            end += 1
        last = end if closed else len(self.lines) - 1
        if not closed:
            self._warning("UNCLOSED_CODE_FENCE", "Code fence is not closed", start, last)

        if self.preserve_code_blocks:
            body_end = end if closed else len(self.lines)
            body = "\n".join(self.lines[start + 1 : body_end])
            self._append_block(
                "code",
                start,
                last,
                {
                    "language": info.split()[0] if info else None,
                    "text": body,
                    "fence": "```" if marker == "`" else "~~~",
                },
            )
        return last + 1

    def _starts_table(self, index: int) -> bool:
        if index + 1 >= len(self.lines):
            return False
        return "|" in self.lines[index] and is_table_separator(self.lines[index + 1])

    def _parse_table(self, start: int) -> int:
        columns = [clean_inline_text(cell) for cell in split_table_row(self.lines[start])]
        separator_cells = split_table_row(self.lines[start + 1])
        alignments = [table_alignment(cell) for cell in separator_cells]
        if len(alignments) != len(columns):
            self._warning(
                "TABLE_ALIGNMENT_COUNT",
                f"Alignment row has {len(alignments)} cells but header has {len(columns)}",
                start + 1,
                start + 1,
            )
            alignments = (alignments + ["none"] * len(columns))[: len(columns)]

        rows: List[List[str]] = []
        index = start + 2
        while index < len(self.lines):
            line = self.lines[index]
            if not line.strip() or "|" not in line:
                break
            if self._looks_like_block_boundary(index) and not is_table_separator(line):
                break
            cells = [cell.strip() for cell in split_table_row(line)]
            if len(cells) != len(columns):
                self._warning(
                    "TABLE_ROW_WIDTH",
                    f"Table row has {len(cells)} cells but header has {len(columns)}; row was normalized",
                    index,
                    index,
                )
                cells = (cells + [""] * len(columns))[: len(columns)]
            rows.append(cells)
            index += 1

        end = max(start + 1, index - 1)
        caption = self._infer_table_caption(start)
        self._append_block(
            "table",
            start,
            end,
            {
                "caption": caption,
                "columns": columns,
                "alignments": alignments,
                "rows": rows,
                "note": None,
            },
        )
        return index

    def _infer_table_caption(self, table_start: int) -> Optional[str]:
        if not self.blocks:
            return None
        previous = self.blocks[-1]
        if previous.get("type") != "paragraph":
            return None
        if table_start + 1 - int(previous.get("line_end", 0)) > 2:
            return None
        text = str(previous.get("text", "")).strip()
        if re.match(r"^(表|图表|Table\b)", text, flags=re.IGNORECASE):
            return text
        return None

    def _parse_list(self, start: int) -> int:
        first = LIST_ITEM_RE.match(self.lines[start])
        if first is None:
            return start + 1
        ordered = first.group("marker")[0].isdigit()
        start_number = int(re.match(r"\d+", first.group("marker")).group()) if ordered else None
        items: List[Dict[str, Any]] = []
        raw_end = start - 1
        index = start
        last_indent_width = 0

        while index < len(self.lines):
            line = self.lines[index]
            match = LIST_ITEM_RE.match(line)
            if match:
                item_ordered = match.group("marker")[0].isdigit()
                if item_ordered != ordered and normalized_indent(match.group("indent")) == 0:
                    break
                text = match.group("text").strip()
                indent_width = len(match.group("indent").expandtabs(4))
                items.append(
                    {
                        "text": clean_inline_text(text),
                        "level": normalized_indent(match.group("indent")),
                        "marker": match.group("marker"),
                        "citations": extract_citations(text),
                        "line_start": index + 1,
                        "line_end": index + 1,
                    }
                )
                last_indent_width = indent_width
                raw_end = index
                index += 1
                continue

            if not line.strip():
                break
            leading = len(line) - len(line.lstrip(" \t"))
            if items and leading > last_indent_width and not self._looks_like_block_boundary(index):
                continuation = line.strip()
                items[-1]["text"] = f"{items[-1]['text']} {clean_inline_text(continuation)}".strip()
                items[-1]["citations"] = unique_in_order(
                    list(items[-1]["citations"]) + extract_citations(continuation)
                )
                items[-1]["line_end"] = index + 1
                raw_end = index
                index += 1
                continue
            break

        self._append_block(
            "list",
            start,
            max(start, raw_end),
            {
                "ordered": ordered,
                "start_number": start_number,
                "tight": True,
                "items": items,
            },
        )
        return index

    def _parse_blockquote(self, start: int) -> int:
        lines: List[str] = []
        index = start
        while index < len(self.lines):
            match = BLOCKQUOTE_RE.match(self.lines[index])
            if match is None:
                break
            lines.append(match.group("text"))
            index += 1
        text = "\n".join(clean_inline_text(line) for line in lines).strip()
        self._append_block("blockquote", start, index - 1, {"text": text})
        return index

    def _parse_paragraph(self, start: int) -> int:
        end = start
        while end + 1 < len(self.lines):
            next_index = end + 1
            if not self.lines[next_index].strip() or self._looks_like_block_boundary(next_index):
                break
            if not self.merge_adjacent_paragraph_lines:
                break
            end = next_index
        raw_lines = self.lines[start : end + 1]
        text = " ".join(clean_inline_text(line.strip()) for line in raw_lines).strip()
        if not text:
            text = " ".join(line.strip() for line in raw_lines).strip()
        self._append_block("paragraph", start, end, {"text": text})
        return end + 1

    def _looks_like_block_boundary(self, index: int) -> bool:
        line = self.lines[index]
        if not line.strip():
            return True
        if FENCE_RE.match(line) or ATX_HEADING_RE.match(line):
            return True
        if self.source_format == "markdown" and index + 1 < len(self.lines):
            if SETEXT_HEADING_RE.fullmatch(self.lines[index + 1]) and not self._line_starts_nonparagraph_block(index):
                return True
        if self.source_format == "plain_text" and self.plain_text_heading_detection:
            if self._detect_plain_text_heading(index) is not None:
                return True
        if self._starts_table(index):
            return True
        if HORIZONTAL_RULE_RE.fullmatch(line):
            return True
        if LIST_ITEM_RE.match(line) or BLOCKQUOTE_RE.match(line):
            return True
        if self.preserve_images and IMAGE_ONLY_RE.fullmatch(line):
            return True
        return False

    def _line_starts_nonparagraph_block(self, index: int) -> bool:
        line = self.lines[index]
        return bool(
            FENCE_RE.match(line)
            or ATX_HEADING_RE.match(line)
            or LIST_ITEM_RE.match(line)
            or BLOCKQUOTE_RE.match(line)
            or IMAGE_ONLY_RE.fullmatch(line)
            or self._starts_table(index)
        )

    def _warning(self, code: str, message: str, start_index: int, end_index: int) -> None:
        self.warnings.append(
            {
                "code": code,
                "message": message,
                "severity": "warning",
                "line_start": start_index + 1 if start_index >= 0 else None,
                "line_end": end_index + 1 if end_index >= 0 else None,
            }
        )

    def _build_result(self) -> Dict[str, Any]:
        counts = Counter(str(block["type"]) for block in self.blocks)
        title = next(
            (
                str(block["text"])
                for block in self.blocks
                if block.get("type") == "heading" and block.get("level") == 1
            ),
            None,
        )
        if title is None:
            first_text = next((block.get("text") for block in self.blocks if block.get("text")), None)
            title = str(first_text)[:300] if first_text else None

        return {
            "schema_version": "1.0",
            "parser": {
                "name": PARSER_NAME,
                "version": PARSER_VERSION,
                "parsed_at": datetime.now(timezone.utc).isoformat(),
                "options": {
                    "plain_text_heading_detection": self.plain_text_heading_detection,
                    "preserve_code_blocks": self.preserve_code_blocks,
                    "preserve_images": self.preserve_images,
                    "merge_adjacent_paragraph_lines": self.merge_adjacent_paragraph_lines,
                },
            },
            "document": {
                "document_id": self.document_id,
                "title": title,
                "source_file": self.source_file,
                "source_format": self.source_format,
                "encoding": self.encoding,
                "line_count": len(self.lines),
                "character_count": len(self.text),
                "source_hash_sha256": self.source_hash_sha256,
                "language": "zh-CN",
            },
            "blocks": self.blocks,
            "statistics": {
                "block_count": len(self.blocks),
                "heading_count": counts["heading"],
                "paragraph_count": counts["paragraph"],
                "list_count": counts["list"],
                "table_count": counts["table"],
                "image_count": counts["image"],
                "blockquote_count": counts["blockquote"],
                "code_count": counts["code"],
                "horizontal_rule_count": counts["horizontal_rule"],
            },
            "warnings": self.warnings,
        }

    def _validate_invariants(self, result: Mapping[str, Any]) -> None:
        blocks = result.get("blocks", [])
        if not isinstance(blocks, list):
            raise ParseError("Internal error: blocks is not an array")
        block_ids = [block.get("block_id") for block in blocks if isinstance(block, Mapping)]
        if len(block_ids) != len(set(block_ids)):
            raise ParseError("Internal error: duplicate block IDs")
        heading_ids = {
            block.get("block_id")
            for block in blocks
            if isinstance(block, Mapping) and block.get("type") == "heading"
        }
        for block in blocks:
            if not isinstance(block, Mapping):
                raise ParseError("Internal error: block is not an object")
            start = block.get("line_start")
            end = block.get("line_end")
            if not isinstance(start, int) or not isinstance(end, int) or not (1 <= start <= end <= len(self.lines)):
                raise ParseError(f"Internal error: invalid line range in {block.get('block_id')}")
            parent = block.get("parent_heading_id")
            if parent is not None and parent not in heading_ids:
                raise ParseError(f"Internal error: unknown parent heading {parent}")
            if block.get("type") == "table":
                columns = block.get("columns", [])
                alignments = block.get("alignments", [])
                rows = block.get("rows", [])
                if len(columns) != len(alignments):
                    raise ParseError(f"Internal error: table alignment mismatch in {block.get('block_id')}")
                if any(len(row) != len(columns) for row in rows):
                    raise ParseError(f"Internal error: table row mismatch in {block.get('block_id')}")

        statistics = result.get("statistics", {})
        if isinstance(statistics, Mapping) and statistics.get("block_count") != len(blocks):
            raise ParseError("Internal error: block statistics mismatch")


def determine_source_format(path: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".mdown", ".mkd"}:
        return "markdown"
    if suffix in {".txt", ".text"}:
        return "plain_text"
    raise ParseError(
        f"Cannot infer source format from extension {suffix!r}; use --format markdown or --format plain_text"
    )


def parse_file(
    path: Path,
    *,
    source_format: str = "auto",
    document_id: Optional[str] = None,
    plain_text_heading_detection: bool = True,
    preserve_code_blocks: bool = True,
    preserve_images: bool = True,
    merge_adjacent_paragraph_lines: bool = True,
) -> Dict[str, Any]:
    raw, text, encoding = read_text_with_encoding(path)
    resolved_format = determine_source_format(path, source_format)
    parser = ReportParser(
        text,
        source_file=path.name,
        source_format=resolved_format,
        encoding=encoding,
        source_hash_sha256=hashlib.sha256(raw).hexdigest(),
        document_id=document_id or safe_document_id(path.stem),
        plain_text_heading_detection=plain_text_heading_detection,
        preserve_code_blocks=preserve_code_blocks,
        preserve_images=preserve_images,
        merge_adjacent_paragraph_lines=merge_adjacent_paragraph_lines,
    )
    return parser.parse()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse a Markdown/plain-text financial report into parsed_document JSON"
    )
    parser.add_argument("input", type=Path, help="Input .md/.markdown/.txt file")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON path")
    parser.add_argument(
        "--format",
        choices=["auto", "markdown", "plain_text"],
        default="auto",
        help="Source format (default: infer from extension)",
    )
    parser.add_argument("--document-id", help="Override generated document ID")
    parser.add_argument(
        "--no-plain-headings",
        action="store_true",
        help="Disable numbered-heading detection for plain text",
    )
    parser.add_argument(
        "--drop-code-blocks",
        action="store_true",
        help="Skip fenced code blocks instead of preserving them",
    )
    parser.add_argument(
        "--drop-images",
        action="store_true",
        help="Treat standalone Markdown images as paragraph text",
    )
    parser.add_argument(
        "--no-merge-paragraph-lines",
        action="store_true",
        help="Keep each non-empty plain line as a separate paragraph",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation (default: 2)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    output = args.output or args.input.with_name(f"{args.input.stem}_parsed.json")
    try:
        result = parse_file(
            args.input,
            source_format=args.format,
            document_id=args.document_id,
            plain_text_heading_detection=not args.no_plain_headings,
            preserve_code_blocks=not args.drop_code_blocks,
            preserve_images=not args.drop_images,
            merge_adjacent_paragraph_lines=not args.no_merge_paragraph_lines,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=args.indent) + "\n",
            encoding="utf-8",
        )
    except (ParseError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    stats = result["statistics"]
    print(f"Created: {output}")
    print(
        "Blocks: {block_count} (headings={heading_count}, paragraphs={paragraph_count}, "
        "lists={list_count}, tables={table_count}, images={image_count})".format(**stats)
    )
    print(f"Warnings: {len(result['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
