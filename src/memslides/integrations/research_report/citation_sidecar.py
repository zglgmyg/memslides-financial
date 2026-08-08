"""Apply the citation sidecar to final slide HTML files."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .citation_appendix_html import build_citation_appendix_pages
from .citation_html import apply_citations_to_html
from .citation_matching import DEFAULT_BASE_URL, DEFAULT_MODEL, judge_claim_citations
from .citation_reference_normalization import normalize_reference_catalog
from .citation_resolution import build_pdf_source_numbers, resolve_page_citation_sources
from .html_claims import extract_html_claims
from .slide_citation_candidates import build_slide_citation_candidates


def run_citation_sidecar(
    html_directory: str | Path,
    slide_outline_path: str | Path,
    citation_units_path: str | Path,
    validation_report_path: str | Path,
    source_catalog_path: str | Path,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 120,
) -> int:
    """Add PDF-numbered marks and citation appendix pages to final slide HTML."""

    html_dir = Path(html_directory).resolve()
    slide_outline = json.loads(
        Path(slide_outline_path).resolve().read_text(encoding="utf-8")
    )
    citation_units = json.loads(
        Path(citation_units_path).resolve().read_text(encoding="utf-8")
    )
    validation_report = json.loads(
        Path(validation_report_path).resolve().read_text(encoding="utf-8")
    )
    source_catalog_file = Path(source_catalog_path).resolve()
    reference_catalog_file = source_catalog_file.with_name(
        "citation_reference_catalog.json"
    )
    if reference_catalog_file.exists():
        source_catalog = json.loads(reference_catalog_file.read_text(encoding="utf-8"))
    else:
        source_catalog = normalize_reference_catalog(
            json.loads(source_catalog_file.read_text(encoding="utf-8")),
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
        )
        reference_catalog_file.write_text(
            json.dumps(source_catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    candidates = build_slide_citation_candidates(
        slide_outline,
        citation_units,
        validation_report,
    )
    candidate_ids_by_slide = {
        item["slide_id"]: item["candidate_unit_ids"] for item in candidates
    }
    units_by_id = {unit["unit_id"]: unit for unit in citation_units}
    source_numbers = build_pdf_source_numbers(source_catalog)
    updated_pages: dict[Path, str] = {}

    for page_number, slide in enumerate(slide_outline.get("slides", []), start=1):
        slide_id = str(slide["slide_id"])
        html_path = html_dir / f"slide_{page_number:02d}.html"
        html_text = html_path.read_text(encoding="utf-8")
        html_claims = extract_html_claims(html_text, slide_id)
        candidate_units = [
            units_by_id[unit_id]
            for unit_id in candidate_ids_by_slide.get(slide_id, [])
        ]
        claim_mappings = judge_claim_citations(
            html_claims,
            candidate_units,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
        )
        resolved = resolve_page_citation_sources(
            claim_mappings,
            citation_units,
            source_catalog,
            source_numbers,
        )
        has_references = any(
            item["reference_numbers"] for item in resolved["claim_references"]
        )
        if (
            has_references
            or "reference-mark" in html_text
        ):
            updated_pages[html_path] = apply_citations_to_html(
                html_text,
                slide_id,
                resolved,
            )

    old_appendix_paths: list[Path] = []
    body_page_numbers: list[int] = []
    brand_html = ""
    for html_path in html_dir.glob("slide_*.html"):
        html_text = html_path.read_text(encoding="utf-8")
        if "data-citation-appendix-page" in html_text:
            old_appendix_paths.append(html_path)
            continue
        page_match = re.fullmatch(r"slide_(\d+)\.html", html_path.name)
        if page_match:
            body_page_numbers.append(int(page_match.group(1)))
        if not brand_html:
            brand = BeautifulSoup(html_text, "lxml").select_one(
                "#sjtu-financial-brand-mark"
            )
            if brand is not None:
                brand_html = str(brand)

    appendix_pages = build_citation_appendix_pages(
        source_catalog,
        max(body_page_numbers) + 1,
        brand_html=brand_html,
    )
    for filename, html_text in appendix_pages.items():
        updated_pages[html_dir / filename] = html_text

    for html_path in old_appendix_paths:
        html_path.unlink()
    for html_path, html_text in updated_pages.items():
        html_path.write_text(html_text, encoding="utf-8")
    return len(updated_pages)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add verified citation references to final slide HTML files."
    )
    parser.add_argument("--html-dir", required=True, type=Path)
    parser.add_argument("--outline", required=True, type=Path)
    parser.add_argument("--citation-units", required=True, type=Path)
    parser.add_argument("--validation-report", required=True, type=Path)
    parser.add_argument("--source-catalog", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    updated_count = run_citation_sidecar(
        args.html_dir,
        args.outline,
        args.citation_units,
        args.validation_report,
        args.source_catalog,
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        timeout=args.timeout,
    )
    print(f"Updated citation HTML pages: {updated_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
