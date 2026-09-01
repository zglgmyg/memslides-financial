"""Parse the citation source catalog from a PDF appendix with MinerU."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from memslides.research_pipeline.document_bundle.parser.mineru_client import MinerUClient


_FIELD_PREFIX_RE = re.compile(r"^[-*+]\s+")
_FILENAME_RE = re.compile(r"^文件名\s*[:：]\s*(.+)$", re.IGNORECASE)
_TYPE_RE = re.compile(r"^类型\s*[:：]")
_DESCRIPTION_RE = re.compile(r"^描述\s*[:：]\s*(.*)$")
_SOURCE_RE = re.compile(r"^来源\s*[:：]\s*(.*)$")
_DOMAIN_RE = re.compile(r"来源\s*[:：]\s*([^）)]+)")
_WEB_DESCRIPTION_RE = re.compile(
    r"^网页发布时间：(?P<date>\d{4}-\d{2}-\d{2})"
)


def _clean_line(line: str) -> str:
    value = line.strip().replace(r"\-", "-").replace(r"\_", "_")
    value = re.sub(r"^#{1,6}\s*", "", value)
    return _FIELD_PREFIX_RE.sub("", value).strip()


def _source_fields(value: str) -> tuple[str, str]:
    domains = _DOMAIN_RE.findall(value)
    domain = domains[-1].strip() if domains else ""
    source_type = re.split(r"\s*搜索结果|[（(]", value, maxsplit=1)[0].strip()
    return source_type, domain


def _display_date(value: str) -> str:
    year, month, day = value.split("-")
    return f"{year}年{int(month)}月{int(day)}日"


def _clean_title(value: str, limit: int | None = 42) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    value = value.replace(r"\$", "").replace("$", "")
    value = re.sub(r"[_*#|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -：:，,")
    value = value.translate(str.maketrans({"(": "（", ")": "）", ":": "："}))
    return value if limit is None or len(value) <= limit else value[:limit].rstrip() + "…"


def format_citation_source(source: Mapping[str, Any]) -> str:
    """Return a compact deterministic citation label from appendix metadata."""

    description = str(source.get("description", "")).strip()
    source_type = str(source.get("source_type", "")).strip()
    domain = str(source.get("source_domain", "")).strip()
    web_match = _WEB_DESCRIPTION_RE.match(description)

    if web_match:
        date = _display_date(web_match.group("date"))
        normalized_title = str(source.get("reference_title", "")).strip()
        if not normalized_title:
            raise ValueError("Normalized web reference title is required")
        title = _clean_title(normalized_title, limit=None)
        publisher = _clean_title(
            str(source.get("reference_publisher", "")), limit=None
        )
        number = _clean_title(
            str(source.get("reference_document_number", "")), limit=None
        )

        parts = []
        if publisher:
            parts.append(publisher)
        parts.append(f"《{title}》")
        if number:
            parts[-1] += f"（公告编号：{number}）"
        parts.append(date)
        if domain:
            parts.append(f"来源：{domain}")
        return "，".join(parts) + "。"

    pdf_match = re.search(r"([^/\\]+?)\.pdf", description, re.IGNORECASE)
    if pdf_match:
        title = pdf_match.group(1)
        dated_title = re.match(r"(?:\d{6}_)?(\d{4}-\d{2}-\d{2})_(.+)", title)
        if dated_title:
            return (
                f"《{_clean_title(dated_title.group(2))}》，"
                f"{_display_date(dated_title.group(1))}。"
            )
        return f"《{_clean_title(title)}》。"

    title = _clean_title(description.split("；", 1)[0])
    origin = domain or source_type
    return f"《{title}》" + (f"，来源：{origin}。" if origin else "。")


def parse_mineru_appendix(markdown: str) -> dict[str, dict[str, str]]:
    """Parse the first citation-material appendix from MinerU Markdown."""

    catalog: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    description_lines: list[str] = []
    reading_description = False
    in_appendix = False

    def finish_entry() -> None:
        nonlocal current, description_lines, reading_description
        if current is not None:
            current["description"] = " ".join(description_lines).strip()
            catalog[current["cite_id"]] = current
        current = None
        description_lines = []
        reading_description = False

    for raw_line in markdown.splitlines():
        line = _clean_line(raw_line)
        compact = re.sub(r"\s+", "", line)

        if "第一部分" in compact and "引用材料清单" in compact:
            in_appendix = True
            continue
        if (
            in_appendix
            and (current is not None or catalog)
            and "第二部分" in compact
            and "数据溯源附录" in compact
        ):
            finish_entry()
            break
        if not in_appendix or not line:
            continue

        filename_match = _FILENAME_RE.match(line)
        if filename_match:
            finish_entry()
            filename = filename_match.group(1).strip().strip("` ")
            current = {
                "cite_id": Path(filename).stem,
                "description": "",
                "source_type": "",
                "source_domain": "",
                "filename": filename,
            }
            continue
        if current is None:
            continue

        description_match = _DESCRIPTION_RE.match(line)
        if description_match:
            reading_description = True
            initial = description_match.group(1).strip()
            if initial:
                description_lines.append(initial)
            continue

        source_match = _SOURCE_RE.match(line)
        if source_match:
            current["source_type"], current["source_domain"] = _source_fields(
                source_match.group(1).strip()
            )
            reading_description = False
            continue

        if _TYPE_RE.match(line):
            reading_description = False
            continue
        if reading_description:
            description_lines.append(line)

    else:
        finish_entry()

    return catalog


def parse_pdf_citation_appendix(
    pdf_path: str | Path,
    output_directory: str | Path,
    *,
    data_id: str | None = None,
) -> Path:
    """Parse a PDF with MinerU and write its citation source catalog."""

    pdf = Path(pdf_path).resolve()
    output = Path(output_directory).resolve()
    raw_directory = output / "mineru_raw"

    with MinerUClient() as client:
        client.parse_to_raw(pdf, raw_directory, data_id or pdf.stem)

    return write_citation_source_catalog_from_markdown(
        raw_directory / "document.md", output
    )


def write_citation_source_catalog_from_markdown(
    markdown_path: str | Path,
    output_directory: str | Path,
) -> Path:
    """Write the catalog from an existing MinerU document without parsing again."""

    markdown = Path(markdown_path).resolve()
    output = Path(output_directory).resolve()
    catalog = parse_mineru_appendix(markdown.read_text(encoding="utf-8"))
    catalog_path = output / "citation_source_catalog.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return catalog_path
