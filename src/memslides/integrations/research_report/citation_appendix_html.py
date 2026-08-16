"""Generate PDF-ordered citation appendix slide HTML files."""

from __future__ import annotations

from html import escape
from typing import Any, Mapping

SOURCES_PER_PAGE = 10


def build_citation_appendix_pages(
    source_catalog: Mapping[str, Mapping[str, Any]],
    start_page_number: int,
    *,
    brand_html: str = "",
) -> dict[str, str]:
    """Return appendix HTML pages containing all sources in PDF order."""

    numbered_sources = list(enumerate(source_catalog.values(), start=1))
    pages: dict[str, str] = {}
    for page_offset, start in enumerate(
        range(0, len(numbered_sources), SOURCES_PER_PAGE)
    ):
        rows = []
        for number, source in numbered_sources[start : start + SOURCES_PER_PAGE]:
            citation_text = str(source["citation_text"])
            rows.append(
                '<p class="source-row">'
                f'<span class="source-number">[{number}]</span>'
                f'<span class="source-text">{escape(citation_text)}</span>'
                "</p>"
            )

        page_number = start_page_number + page_offset
        pages[f"slide_{page_number:02d}.html"] = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<style>
* {{ box-sizing: border-box; }}
html, body {{ width: 1280px; height: 720px; margin: 0; overflow: hidden; }}
body {{ position: relative; background: #F8FAFC; color: #0F172A;
  font-family: Arial, 'Microsoft YaHei', sans-serif; }}
.appendix-title-bar {{ height: 96px; padding: 0 80px; display: flex;
  align-items: center; background: #1E3A5F; }}
.appendix-title {{ margin: 0; color: #FFFFFF; font-size: 32px;
  line-height: 1; font-weight: 700; }}
.appendix-content {{ position: absolute; top: 124px; left: 80px; right: 80px;
  bottom: 34px; display: flex; flex-direction: column; gap: 6px; }}
.source-row {{ display: flex; align-items: flex-start; color: #475569;
  margin: 0; font-size: 16px; line-height: 1.45; }}
.source-number {{ flex: 0 0 54px; font-weight: 700; color: #475569; }}
.source-text {{ flex: 1; }}
</style>
</head>
<body data-page-role="content" data-citation-appendix-page="true">
<div class="appendix-title-bar"><h1 class="appendix-title">附录</h1></div>
<div class="appendix-content">{''.join(rows)}</div>
{brand_html}
</body>
</html>
"""
    return pages
