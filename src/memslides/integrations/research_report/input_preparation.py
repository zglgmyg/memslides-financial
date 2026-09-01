"""Prepare the canonical DocumentBundle once for either report mode."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from memslides.research_pipeline.document_bundle.bundle import parse_pdf
from memslides.research_pipeline.document_bundle.config import MinerUConfig
from memslides.research_pipeline.document_bundle.markdown import build_from_markdown
from memslides.research_pipeline.document_bundle.parser.mineru_client import MinerUClient

from .inputs import FinancialReportInputs, FinancialReportMode


@dataclass(frozen=True)
class PreparedFinancialInput:
    bundle_directory: Path
    agent_pdf_markdown: Path | None = None


def prepared_financial_input_paths(
    inputs: FinancialReportInputs,
    output_directory: Path,
) -> PreparedFinancialInput:
    pdf_bundle = output_directory / "pdf_parse" / inputs.pdf.stem / "document_bundle"
    if inputs.mode is FinancialReportMode.HUMAN:
        return PreparedFinancialInput(bundle_directory=pdf_bundle)
    return PreparedFinancialInput(
        bundle_directory=output_directory / "document_bundle",
        agent_pdf_markdown=pdf_bundle / "raw" / "document.md",
    )


def prepare_financial_input(
    inputs: FinancialReportInputs,
    output_directory: Path,
) -> PreparedFinancialInput:
    """Build the same canonical bundle as the baseline, retaining reusable PDF raw data."""

    output_directory.mkdir(parents=True, exist_ok=True)
    parse_root = output_directory / "pdf_parse"
    with MinerUClient(MinerUConfig()) as client:
        pdf_bundle, pdf_document, pdf_validation = parse_pdf(
            inputs.pdf,
            parse_root,
            inputs.report.stem,
            client,
        )
    if pdf_validation.get("status") == "failed":
        raise RuntimeError("PDF DocumentBundle validation failed")

    if inputs.mode is FinancialReportMode.HUMAN:
        return prepared_financial_input_paths(inputs, output_directory)

    if inputs.markdown is None:
        raise RuntimeError("Agent report preparation requires Markdown")
    bundle_directory = output_directory / "document_bundle"
    _, validation = build_from_markdown(
        inputs.markdown,
        bundle_directory,
        inputs.report.stem,
        source_format="auto",
        pdf_bundle_directory=pdf_bundle,
        pdf_document=pdf_document,
    )
    if validation.get("status") == "failed":
        raise RuntimeError("DocumentBundle validation failed")
    return prepared_financial_input_paths(inputs, output_directory)
