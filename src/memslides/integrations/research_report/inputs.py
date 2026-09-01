"""Explicit input contract for the two financial-report workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class FinancialReportMode(str, Enum):
    HUMAN = "human"
    AGENT = "agent"


@dataclass(frozen=True)
class FinancialReportInputs:
    """Resolved inputs without conflating the Human and Agent PDF meanings."""

    mode: FinancialReportMode
    report: Path
    pdf: Path
    parsed_json: Path
    markdown: Path | None = None

    @property
    def research_source(self) -> Path:
        return self.markdown if self.markdown is not None else self.pdf

    @property
    def citations_required(self) -> bool:
        return self.mode is FinancialReportMode.AGENT

    def manifest_paths(self) -> dict[str, Path]:
        paths = {"pdf": self.pdf, "parsed_json": self.parsed_json}
        if self.markdown is not None:
            paths["markdown"] = self.markdown
        return paths


def resolve_financial_report_inputs(
    report_path: str | Path,
    pdf_path: str | Path | None,
    parsed_json_path: str | Path | None,
) -> FinancialReportInputs:
    report = Path(report_path).expanduser().resolve()
    if report.suffix.casefold() not in {".md", ".markdown", ".pdf"} or not report.is_file():
        raise ValueError(f"A Markdown or PDF report is required: {report}")

    is_agent = report.suffix.casefold() in {".md", ".markdown"}
    pdf = (
        Path(pdf_path).expanduser().resolve()
        if pdf_path
        else report.with_suffix(".pdf") if is_agent else report
    )
    parsed = (
        Path(parsed_json_path).expanduser().resolve()
        if parsed_json_path
        else report.with_name(report.stem + "_parsed.json")
    )
    if not pdf.is_file():
        raise ValueError("Mandatory citation PDF is missing: " + str(pdf))
    return FinancialReportInputs(
        mode=FinancialReportMode.AGENT if is_agent else FinancialReportMode.HUMAN,
        report=report,
        pdf=pdf,
        parsed_json=parsed,
        markdown=report if is_agent else None,
    )
