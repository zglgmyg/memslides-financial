from __future__ import annotations

import json
from pathlib import Path

from memslides.integrations.research_report.input_preparation import (
    prepare_financial_input,
)
from memslides.integrations.research_report.inputs import (
    FinancialReportMode,
    resolve_financial_report_inputs,
)


class _FakeMinerUClient:
    def __init__(self, *_: object, **__: object) -> None:
        pass

    def __enter__(self) -> "_FakeMinerUClient":
        return self

    def __exit__(self, *_: object) -> None:
        pass


def _fake_pdf_parse(
    pdf: Path,
    output_root: Path,
    data_id: str,
    parser: object,
) -> tuple[Path, dict[str, object], dict[str, str]]:
    del data_id, parser
    bundle = output_root / pdf.stem / "document_bundle"
    (bundle / "raw").mkdir(parents=True)
    (bundle / "raw" / "document.md").write_text(
        "# 正文\n# 参考资料\n## 1. source.pdf\n描述：说明\n来源：机构 | 官网\n",
        encoding="utf-8",
    )
    (bundle / "document.json").write_text("{}", encoding="utf-8")
    (bundle / "validation.json").write_text(
        '{"status":"passed"}', encoding="utf-8"
    )
    return bundle, {"figures": [{"id": "fig-pdf-001"}]}, {"status": "passed"}


def test_input_contract_keeps_human_and_agent_sources_distinct(tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    markdown = tmp_path / "report.md"
    pdf.write_bytes(b"%PDF")
    markdown.write_text("# Agent report", encoding="utf-8")

    human = resolve_financial_report_inputs(pdf, None, None)
    agent = resolve_financial_report_inputs(markdown, None, None)

    assert human.mode is FinancialReportMode.HUMAN
    assert human.research_source == pdf.resolve()
    assert human.citations_required is False
    assert agent.mode is FinancialReportMode.AGENT
    assert agent.research_source == markdown.resolve()
    assert agent.pdf == pdf.resolve()
    assert agent.citations_required is True


def test_human_preparation_uses_pdf_bundle_directly(
    tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "human.pdf"
    pdf.write_bytes(b"%PDF")
    inputs = resolve_financial_report_inputs(pdf, None, None)
    monkeypatch.setattr(
        "memslides.integrations.research_report.input_preparation.MinerUClient",
        _FakeMinerUClient,
    )
    monkeypatch.setattr(
        "memslides.integrations.research_report.input_preparation.parse_pdf",
        _fake_pdf_parse,
    )

    prepared = prepare_financial_input(inputs, tmp_path / "prepared")

    assert prepared.bundle_directory.name == "document_bundle"
    assert prepared.agent_pdf_markdown is None


def test_agent_preparation_reuses_pdf_bundle_for_markdown_figures(
    tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "agent.pdf"
    markdown = tmp_path / "agent.md"
    pdf.write_bytes(b"%PDF")
    markdown.write_text("# Agent report\n![chart](chart:chart_001)", encoding="utf-8")
    inputs = resolve_financial_report_inputs(markdown, None, None)
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "memslides.integrations.research_report.input_preparation.MinerUClient",
        _FakeMinerUClient,
    )
    monkeypatch.setattr(
        "memslides.integrations.research_report.input_preparation.parse_pdf",
        _fake_pdf_parse,
    )

    def fake_markdown_build(
        source: Path,
        bundle: Path,
        data_id: str,
        **kwargs: object,
    ) -> tuple[dict[str, object], dict[str, str]]:
        calls.append({"source": source, "data_id": data_id, **kwargs})
        bundle.mkdir(parents=True)
        (bundle / "document.json").write_text("{}", encoding="utf-8")
        (bundle / "validation.json").write_text(
            json.dumps({"status": "passed"}), encoding="utf-8"
        )
        return {}, {"status": "passed"}

    monkeypatch.setattr(
        "memslides.integrations.research_report.input_preparation.build_from_markdown",
        fake_markdown_build,
    )

    prepared = prepare_financial_input(inputs, tmp_path / "prepared")

    assert len(calls) == 1
    assert calls[0]["source"] == markdown.resolve()
    assert calls[0]["source_format"] == "auto"
    assert calls[0]["pdf_bundle_directory"] == (
        tmp_path / "prepared" / "pdf_parse" / "agent" / "document_bundle"
    )
    assert calls[0]["pdf_document"] == {"figures": [{"id": "fig-pdf-001"}]}
    assert prepared.agent_pdf_markdown == (
        tmp_path
        / "prepared"
        / "pdf_parse"
        / "agent"
        / "document_bundle"
        / "raw"
        / "document.md"
    )
