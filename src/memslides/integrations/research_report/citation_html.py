"""Write resolved citation references into final slide HTML."""

from __future__ import annotations

from typing import Any, Mapping

from bs4 import BeautifulSoup

from .html_claims import find_html_claim_elements


REFERENCE_COLOR = "#475569"
_SOURCE_SELECTOR = (
    "[data-citation-sources], .source-footer, .source-list, .sources, .references"
)


def _remove_source_container(soup: BeautifulSoup) -> None:
    existing = soup.select_one(_SOURCE_SELECTOR)
    if existing is None:
        existing = next(
            (
                element
                for element in reversed(soup.find_all(["div", "p", "footer"]))
                if element.get_text(" ", strip=True).startswith(("来源：", "来源:"))
            ),
            None,
        )
    if existing is not None:
        existing.decompose()


def apply_citations_to_html(
    html_text: str,
    page_id: str,
    resolved_citations: Mapping[str, Any],
) -> str:
    """Append PDF-numbered superscripts and remove the page source footer."""

    soup = BeautifulSoup(html_text, "lxml")
    for existing_mark in soup.select("sup.reference-mark"):
        existing_mark.decompose()
    _remove_source_container(soup)
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
