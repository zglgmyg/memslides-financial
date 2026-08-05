"""Write resolved citation references into final slide HTML."""

from __future__ import annotations

from typing import Any, Mapping

from bs4 import BeautifulSoup

from .html_claims import find_html_claim_elements


REFERENCE_COLOR = "#475569"


def apply_citations_to_html(
    html_text: str,
    page_id: str,
    resolved_citations: Mapping[str, Any],
) -> str:
    """Append PDF-numbered superscripts to resolved claims."""

    soup = BeautifulSoup(html_text, "lxml")
    for existing_mark in soup.select("sup.reference-mark"):
        existing_mark.decompose()
    elements_by_id = {
        f"{page_id}_claim_{index:03d}": element
        for index, element in enumerate(find_html_claim_elements(soup), start=1)
    }

    for claim_reference in resolved_citations.get("claim_references", []):
        numbers = claim_reference.get("reference_numbers", [])
        if not numbers:
            continue
        mark = soup.new_tag("sup")
        mark["class"] = "reference-mark"
        mark["style"] = (
            f"color:{REFERENCE_COLOR}; font-size:0.6em; "
            "vertical-align:super; line-height:0; margin-left:2px;"
        )
        mark.string = "[" + ",".join(str(number) for number in numbers) + "]"
        elements_by_id[str(claim_reference["html_claim_id"])].append(mark)

    return str(soup)
