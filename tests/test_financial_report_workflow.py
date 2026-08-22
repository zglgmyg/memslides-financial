from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import pytest

from memslides.integrations.research_report.workflow import (
    FinancialReportWorkflowError,
    _effective_generation_limits,
    _final_compliance,
    _is_retryable_deck_error,
    _migrate_legacy_run_without_models,
    _resolve_inputs,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_inputs_discovers_mandatory_companions(tmp_path: Path) -> None:
    markdown = tmp_path / "report.md"
    pdf = tmp_path / "report.pdf"
    parsed = tmp_path / "report_parsed.json"
    markdown.write_text("# Report", encoding="utf-8")
    pdf.write_bytes(b"%PDF")
    parsed.write_text("{}", encoding="utf-8")

    resolved = _resolve_inputs(markdown, None, None)

    assert resolved == {
        "markdown": markdown.resolve(),
        "pdf": pdf.resolve(),
        "parsed_json": parsed.resolve(),
    }


def test_resolve_inputs_fails_closed_without_citation_inputs(tmp_path: Path) -> None:
    markdown = tmp_path / "report.md"
    markdown.write_text("# Report", encoding="utf-8")

    with pytest.raises(FinancialReportWorkflowError, match="Mandatory citation PDF"):
        _resolve_inputs(markdown, None, None)


def test_resolve_inputs_allows_automatic_markdown_parsing(tmp_path: Path) -> None:
    markdown = tmp_path / "report.md"
    pdf = tmp_path / "report.pdf"
    markdown.write_text("# Report", encoding="utf-8")
    pdf.write_bytes(b"%PDF")

    resolved = _resolve_inputs(markdown, None, None)

    assert resolved["parsed_json"] == (tmp_path / "report_parsed.json").resolve()
    assert not resolved["parsed_json"].exists()


def test_resolve_inputs_accepts_direct_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF")

    resolved = _resolve_inputs(pdf, None, None)

    assert resolved == {
        "pdf": pdf.resolve(),
        "parsed_json": (tmp_path / "report_parsed.json").resolve(),
    }


def test_direct_pdf_receives_robust_generation_budget(tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF")

    limits = _effective_generation_limits(
        pdf,
        max_tokens=None,
        max_attempts=None,
        speaker_max_tokens=None,
        speaker_max_attempts=None,
    )

    assert limits["line_count"] is None
    assert limits["max_tokens"] == 16000
    assert limits["max_attempts"] == 4


def test_incomplete_deck_errors_are_retryable() -> None:
    assert _is_retryable_deck_error(RuntimeError("missing slide_12.html")) is True
    assert _is_retryable_deck_error(RuntimeError("invalid numeric audit")) is False


def test_long_reports_receive_larger_automatic_budgets(tmp_path: Path) -> None:
    markdown = tmp_path / "long.md"
    markdown.write_text("\n".join(["line"] * 301), encoding="utf-8")

    limits = _effective_generation_limits(
        markdown,
        max_tokens=None,
        max_attempts=None,
        speaker_max_tokens=None,
        speaker_max_attempts=None,
    )

    assert limits["max_tokens"] == 16000
    assert limits["max_attempts"] == 4
    assert limits["speaker_max_tokens"] == 32000
    assert limits["speaker_max_attempts"] == 3


def test_final_compliance_requires_all_mandatory_features(tmp_path: Path) -> None:
    research = tmp_path / "research"
    citations = tmp_path / "citations"
    deck = tmp_path / "deck"
    html = deck / "outputs"
    html.mkdir(parents=True)
    _write_json(
        research / "slide_outline.json",
        {
            "slides": [
                {"slide_id": "slide_001", "page_role": "title"},
                {"slide_id": "slide_002", "page_role": "closing"},
            ]
        },
    )
    _write_json(
        research / "speaker_manuscript.json",
        {
            "slides": [
                {"slide_id": "slide_001", "script": "Open"},
                {"slide_id": "slide_002", "script": "Close"},
            ]
        },
    )
    _write_json(citations / "citation_units.json", [{"unit_id": "unit-1"}])
    _write_json(citations / "citation_source_catalog.json", {"source-1": {}})
    _write_json(
        citations / "citation_validation_report.json",
        {"verified": ["source-1"], "source_missing": [], "unused_sources": []},
    )
    _write_json(deck / "sjtu_html_brand_report.json", {"slide_count": 2})
    _write_json(
        deck / "financial_generation_receipt.json",
        {"outputs": {"slide_html_dir": str(html), "citations_applied": True}},
    )
    (html / "slide_01.html").write_text(
        '<body data-sjtu-background="title">'
        '<div id="sjtu-financial-brand-mark"><sup class="reference-mark">1</sup></div>'
        '</body>',
        encoding="utf-8",
    )
    (html / "slide_02.html").write_text(
        '<body data-sjtu-background="closing">End</body>', encoding="utf-8"
    )
    (html / "slide_03.html").write_text(
        '<body data-citation-appendix-page="true">References</body>', encoding="utf-8"
    )
    pptx = deck / "result.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/notesSlides/notesSlide1.xml", "<notes/>")
        archive.writestr("ppt/notesSlides/notesSlide2.xml", "<notes/>")

    report = _final_compliance(
        research_dir=research, citation_dir=citations, deck_dir=deck, pptx_path=pptx
    )

    assert report["status"] == "passed"
    assert report["sjtu_branding"] is True


def test_final_compliance_rejects_missing_speaker_notes(tmp_path: Path) -> None:
    research = tmp_path / "research"
    citations = tmp_path / "citations"
    deck = tmp_path / "deck"
    html = deck / "outputs"
    html.mkdir(parents=True)
    _write_json(
        research / "slide_outline.json",
        {
            "slides": [
                {"slide_id": "slide_001", "page_role": "title"},
                {"slide_id": "slide_002", "page_role": "closing"},
            ]
        },
    )
    _write_json(
        research / "speaker_manuscript.json",
        {"slides": [{"slide_id": "slide_001"}, {"slide_id": "slide_002"}]},
    )
    _write_json(citations / "citation_units.json", [{}])
    _write_json(citations / "citation_source_catalog.json", {"source-1": {}})
    _write_json(citations / "citation_validation_report.json", {"verified": ["source-1"], "source_missing": []})
    _write_json(deck / "sjtu_html_brand_report.json", {"slide_count": 2})
    _write_json(deck / "financial_generation_receipt.json", {"outputs": {"slide_html_dir": str(html), "citations_applied": True}})
    (html / "slide_01.html").write_text(
        'reference-mark sjtu-financial-brand-mark data-sjtu-background="title"',
        encoding="utf-8",
    )
    (html / "slide_02.html").write_text(
        'data-sjtu-background="closing"', encoding="utf-8"
    )
    (html / "slide_03.html").write_text('data-citation-appendix-page', encoding="utf-8")
    pptx = deck / "result.pptx"
    with zipfile.ZipFile(pptx, "w"):
        pass

    with pytest.raises(FinancialReportWorkflowError, match="speaker notes"):
        _final_compliance(
            research_dir=research, citation_dir=citations, deck_dir=deck, pptx_path=pptx
        )


def test_final_compliance_reports_but_allows_unresolved_optional_citations(
    tmp_path: Path,
) -> None:
    research = tmp_path / "research"
    citations = tmp_path / "citations"
    deck = tmp_path / "deck"
    html = deck / "outputs"
    html.mkdir(parents=True)
    _write_json(
        research / "slide_outline.json",
        {
            "slides": [
                {"slide_id": "slide_001", "page_role": "title"},
                {"slide_id": "slide_002", "page_role": "closing"},
            ]
        },
    )
    _write_json(
        research / "speaker_manuscript.json",
        {"slides": [{"slide_id": "slide_001"}, {"slide_id": "slide_002"}]},
    )
    _write_json(citations / "citation_units.json", [{}])
    _write_json(citations / "citation_source_catalog.json", {"source-1": {}})
    _write_json(
        citations / "citation_validation_report.json",
        {"verified": ["source-1"], "source_missing": ["missing-1"]},
    )
    _write_json(deck / "sjtu_html_brand_report.json", {"slide_count": 2})
    _write_json(deck / "financial_generation_receipt.json", {"outputs": {"slide_html_dir": str(html), "citations_applied": True}})
    (html / "slide_01.html").write_text(
        'reference-mark sjtu-financial-brand-mark data-sjtu-background="title"',
        encoding="utf-8",
    )
    (html / "slide_02.html").write_text(
        'data-sjtu-background="closing"', encoding="utf-8"
    )
    (html / "slide_03.html").write_text('data-citation-appendix-page', encoding="utf-8")
    pptx = deck / "result.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/notesSlides/notesSlide1.xml", "<notes/>")
        archive.writestr("ppt/notesSlides/notesSlide2.xml", "<notes/>")

    report = _final_compliance(
        research_dir=research, citation_dir=citations, deck_dir=deck, pptx_path=pptx
    )

    assert report["status"] == "passed"
    assert report["excluded_missing_citation_ids"] == ["missing-1"]


def test_direct_pdf_compliance_does_not_require_citation_artifacts(
    tmp_path: Path,
) -> None:
    research, citations, deck = tmp_path / "research", tmp_path / "citations", tmp_path / "deck"
    html = deck / "outputs"
    html.mkdir(parents=True)
    _write_json(research / "slide_outline.json", {"slides": [
        {"slide_id": "slide_001", "page_role": "title"},
        {"slide_id": "slide_002", "page_role": "content"},
        {"slide_id": "slide_003", "page_role": "closing"},
    ]})
    _write_json(research / "speaker_manuscript.json", {"slides": [
        {"slide_id": "slide_001"}, {"slide_id": "slide_002"}, {"slide_id": "slide_003"},
    ]})
    _write_json(deck / "sjtu_html_brand_report.json", {"slide_count": 3})
    _write_json(deck / "financial_generation_receipt.json", {
        "outputs": {"slide_html_dir": str(html), "citations_applied": False}
    })
    (html / "slide_01.html").write_text('data-sjtu-background="title"', encoding="utf-8")
    (html / "slide_02.html").write_text('id="sjtu-financial-brand-mark"', encoding="utf-8")
    (html / "slide_03.html").write_text('data-sjtu-background="closing"', encoding="utf-8")
    pptx = deck / "result.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        for page in range(1, 4):
            archive.writestr(f"ppt/notesSlides/notesSlide{page}.xml", "<notes/>")

    report = _final_compliance(
        research_dir=research,
        citation_dir=citations,
        deck_dir=deck,
        pptx_path=pptx,
        citations_required=False,
    )

    assert report["status"] == "passed"
    assert report["citations_required"] is False
    assert report["verified_citation_ids"] == 0
    assert report["citation_appendix_pages"] == 0


def test_legacy_run_migration_inserts_closing_before_appendix_without_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research, deck, html = tmp_path / "research", tmp_path / "deck", tmp_path / "deck" / "outputs"
    html.mkdir(parents=True)
    outline = {
        "slides": [
            {"slide_id": "slide_001", "page_role": "title", "title": "Report"},
            {"slide_id": "slide_002", "page_role": "content", "title": "Thesis"},
        ]
    }
    manuscript = {
        "slides": [
            {"slide_id": "slide_001", "slide_title": "Report", "script": "Open", "transition_to_next": "Next"},
            {"slide_id": "slide_002", "slide_title": "Thesis", "script": "Body", "transition_to_next": ""},
        ]
    }
    _write_json(research / "slide_outline.json", outline)
    _write_json(research / "speaker_manuscript.json", manuscript)
    (html / "slide_01.html").write_text("<html><body>Title</body></html>", encoding="utf-8")
    (html / "slide_02.html").write_text(
        '<html><body><div data-financial-role="content-title-bar">Thesis</div></body></html>', encoding="utf-8"
    )
    (html / "slide_03.html").write_text(
        '<html><body data-citation-appendix-page="true">References</body></html>', encoding="utf-8"
    )
    pptx = deck / "manuscript.pptx"
    pptx.write_bytes(b"old")
    _write_json(deck / "financial_generation_receipt.json", {
        "outputs": {"slide_html_dir": str(html), "pptx": str(pptx), "citations_applied": True}
    })
    exported: list[Path] = []

    async def fake_export(html_inputs: Path, output: Path, *_: object, **__: object) -> Path:
        exported.append(Path(html_inputs))
        Path(output).write_bytes(b"new")
        return Path(output)

    monkeypatch.setattr("memslides.utils.webview.convert_html_to_pptx", fake_export)
    manifest: dict[str, object] = {"stages": {}}
    manifest_path = tmp_path / "run_manifest.json"

    migrated = asyncio.run(_migrate_legacy_run_without_models(
        research_dir=research, deck_dir=deck, manifest_path=manifest_path, manifest=manifest
    ))

    assert migrated is True
    assert exported == [html]
    assert sorted(path.name for path in html.glob("slide_*.html")) == [
        "slide_01.html", "slide_02.html", "slide_03.html", "slide_04.html"
    ]
    assert 'data-page-role="closing"' in (html / "slide_03.html").read_text(encoding="utf-8")
    assert "data-citation-appendix-page" in (html / "slide_04.html").read_text(encoding="utf-8")
    assert _read_json_for_test(research / "slide_outline.json")["slides"][-1]["page_role"] == "closing"
    assert len(_read_json_for_test(research / "speaker_manuscript.json")["slides"]) == 3
    assert _read_json_for_test(deck / "sjtu_html_brand_report.json")["slide_count"] == 3
    assert _read_json_for_test(deck / "financial_generation_receipt.json")["legacy_migration"]["mode"] == "local_only_no_models"


def _read_json_for_test(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))
