from memslides.integrations.research_report.citation_appendix_html import (
    build_citation_appendix_pages,
)


def test_appendix_title_bar_matches_financial_content_title_bar() -> None:
    pages = build_citation_appendix_pages(
        {"source_001": {"citation_text": "Source one"}},
        start_page_number=14,
    )

    html = pages["slide_14.html"]
    assert ".appendix-title-bar" in html
    assert "background: #1E3A5F" in html
    assert "#A62038" not in html
