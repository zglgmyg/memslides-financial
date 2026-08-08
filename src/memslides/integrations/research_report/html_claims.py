"""Extract presentation claims from final slide HTML."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag


_CLAIM_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th")


def _is_hidden(element: Tag) -> bool:
    for current in (element, *element.parents):
        if not isinstance(current, Tag):
            continue
        style = str(current.get("style", "")).replace(" ", "").lower()
        if (
            current.has_attr("hidden")
            or str(current.get("aria-hidden", "")).lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        ):
            return True
    return False


def find_html_claim_elements(soup: BeautifulSoup) -> list[Tag]:
    """Return visible claim elements in deterministic DOM order."""

    return [
        element
        for element in soup.find_all(_CLAIM_TAGS)
        if not element.find(_CLAIM_TAGS) and not _is_hidden(element)
    ]


def extract_html_claims(html_text: str, page_id: str) -> list[dict[str, str]]:
    """Return visible claim text from one final slide HTML document."""

    soup = BeautifulSoup(html_text, "lxml")
    for mark in soup.select("sup.reference-mark"):
        mark.decompose()
    claims: list[dict[str, str]] = []
    for element in find_html_claim_elements(soup):
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        claims.append(
            {
                "html_claim_id": f"{page_id}_claim_{len(claims) + 1:03d}",
                "text": text,
            }
        )
    return claims
