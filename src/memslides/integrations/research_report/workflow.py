"""One-command, fail-closed financial report to PowerPoint workflow."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memslides.research_pipeline.research_run.pipeline import run_research_pipeline
from memslides.research_pipeline.document_parser.parse_report import parse_file

from .citation_appendix import parse_pdf_citation_appendix
from .citation_units import write_citation_units
from .citation_validation import write_citation_validation_report
from .generate import generate_financial_deck


class FinancialReportWorkflowError(RuntimeError):
    """Raised when a required stage or final compliance check fails."""


@dataclass(frozen=True)
class FinancialReportWorkflowResult:
    output_dir: Path
    pptx_path: Path
    receipt_path: Path
    manifest_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "output_dir": str(self.output_dir),
            "pptx": str(self.pptx_path),
            "receipt": str(self.receipt_path),
            "manifest": str(self.manifest_path),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _resolve_inputs(
    report_path: str | Path,
    pdf_path: str | Path | None,
    parsed_json_path: str | Path | None,
) -> dict[str, Path]:
    report = Path(report_path).expanduser().resolve()
    if report.suffix.lower() not in {".md", ".markdown", ".pdf"} or not report.is_file():
        raise FinancialReportWorkflowError(
            f"A Markdown or PDF report is required: {report}"
        )
    is_markdown = report.suffix.lower() in {".md", ".markdown"}
    pdf = (
        Path(pdf_path).expanduser().resolve()
        if pdf_path
        else report.with_suffix(".pdf") if is_markdown else report
    )
    parsed = (
        Path(parsed_json_path).expanduser().resolve()
        if parsed_json_path
        else report.with_name(report.stem + "_parsed.json")
    )
    if not pdf.is_file():
        raise FinancialReportWorkflowError(
            "Mandatory citation PDF is missing: " + str(pdf)
        )
    if is_markdown:
        return {"markdown": report, "pdf": pdf, "parsed_json": parsed}
    return {"pdf": pdf, "parsed_json": parsed}


def _effective_generation_limits(
    report: Path,
    *,
    max_tokens: int | None,
    max_attempts: int | None,
    speaker_max_tokens: int | None,
    speaker_max_attempts: int | None,
) -> dict[str, int | None]:
    """Increase output budget and retries automatically for long reports."""

    if report.suffix.lower() in {".md", ".markdown"}:
        text = report.read_text(encoding="utf-8")
        line_count = len(text.splitlines())
        character_count = len(text)
        is_long = line_count > 300 or character_count > 30_000
    else:
        # PDF length is not cheaply known before parsing. Use the robust budget so
        # direct-PDF workflows do not fail merely because their extracted text is long.
        line_count = None
        character_count = None
        is_long = True
    return {
        "line_count": line_count,
        "character_count": character_count,
        "max_tokens": max_tokens if max_tokens is not None else (16000 if is_long else None),
        "max_attempts": max_attempts if max_attempts is not None else (4 if is_long else None),
        "speaker_max_tokens": (
            speaker_max_tokens
            if speaker_max_tokens is not None
            else (32000 if is_long else None)
        ),
        "speaker_max_attempts": (
            speaker_max_attempts
            if speaker_max_attempts is not None
            else (3 if is_long else None)
        ),
    }


def _safe_reset_stage(path: Path, output_dir: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != output_dir.resolve():
        raise FinancialReportWorkflowError(f"Refusing to reset unsafe stage path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _stage_passed(manifest: dict[str, Any], name: str, required: list[Path]) -> bool:
    return (
        manifest.get("stages", {}).get(name, {}).get("status") == "passed"
        and all(path.exists() for path in required)
    )


def _mark_running(manifest_path: Path, manifest: dict[str, Any], stage: str) -> None:
    previous = manifest.setdefault("stages", {}).get(stage, {})
    manifest["stages"][stage] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "attempts": int(previous.get("attempts", 0) or 0) + 1,
    }
    _write_json(manifest_path, manifest)


def _mark_failed(
    manifest_path: Path, manifest: dict[str, Any], stage: str, exc: Exception
) -> None:
    previous = manifest.setdefault("stages", {}).get(stage, {})
    manifest["stages"][stage] = {
        **previous,
        "status": "failed",
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    _write_json(manifest_path, manifest)


def _mark(manifest_path: Path, manifest: dict[str, Any], stage: str, **details: Any) -> None:
    previous = manifest.setdefault("stages", {}).get(stage, {})
    manifest.setdefault("stages", {})[stage] = {
        **previous,
        "status": "passed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    _write_json(manifest_path, manifest)


def _is_retryable_deck_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "did not produce",
            "incomplete slide",
            "generation is incomplete",
            "missing slide",
            "max iterations",
            "timed out",
            "timeout",
            "connection error",
            "network error",
            "rate limit",
            "temporarily unavailable",
            "no valid slide",
        )
    )


def _closing_slide_html() -> str:
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>
<style>*{box-sizing:border-box}html,body{width:1280px;height:720px;margin:0;overflow:hidden}body{position:relative;background:#9b1b30;color:#fff;font-family:"Microsoft YaHei",Arial,sans-serif}.closing{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.closing h1{margin:0 0 24px;font-size:54px;font-weight:700;letter-spacing:8px}.closing p{margin:0;font-size:24px;letter-spacing:3px}</style>
</head><body data-page-role="closing" data-slide-id="SLIDE_ID"><main class="closing"><h1>感谢聆听</h1><p>THANK YOU</p></main></body></html>"""


def _append_closing_manuscript_slide(payload: dict[str, Any], slide_id: str) -> None:
    slides = payload.get("slides")
    if not isinstance(slides, list) or not slides:
        raise FinancialReportWorkflowError("Legacy speaker manuscript is empty")
    if isinstance(slides[-1], dict):
        slides[-1]["transition_to_next"] = "下面进入结束页。"
    slides.append({
        "slide_id": slide_id, "slide_title": "感谢聆听", "narrative_role": "closing",
        "script": "感谢各位聆听。本次关于公司核心逻辑、盈利展望、估值判断与主要风险的分享至此结束，欢迎交流讨论。",
        "transition_to_next": "", "evidence_refs": [], "estimated_seconds": 20,
    })


def _write_legacy_manuscript_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = ["# 演讲稿", ""]
    for index, slide in enumerate(payload.get("slides", []), start=1):
        if isinstance(slide, dict):
            lines.extend([f"## {index}. {slide.get('slide_title', '')}", "", str(slide.get("script", "")), "", f"过渡：{slide.get('transition_to_next', '')}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


async def _migrate_legacy_run_without_models(
    *, research_dir: Path, deck_dir: Path, manifest_path: Path, manifest: dict[str, Any]
) -> bool:
    """Insert a closing page before citation appendices and locally re-export."""
    outline_path = research_dir / "slide_outline.json"
    speaker_path = research_dir / "speaker_manuscript.json"
    receipt_path = deck_dir / "financial_generation_receipt.json"
    if not all(path.is_file() for path in (outline_path, speaker_path, receipt_path)):
        return False
    outline = _read_json(outline_path)
    slides = outline.get("slides", []) if isinstance(outline, dict) else []
    roles = [slide.get("page_role") for slide in slides if isinstance(slide, dict)]
    if not roles or roles[0] != "title" or roles.count("title") != 1 or "closing" in roles:
        return False
    if any(role not in {"title", "content"} for role in roles):
        return False
    receipt = _read_json(receipt_path)
    html_dir = Path(receipt.get("outputs", {}).get("slide_html_dir", ""))
    if not html_dir.is_dir() or html_dir.resolve().parent != deck_dir.resolve():
        return False
    html_paths = sorted(html_dir.glob("slide_*.html"), key=lambda p: int(p.stem.split("_")[-1]))
    expected = [f"slide_{i:02d}.html" for i in range(1, len(html_paths) + 1)]
    if [path.name for path in html_paths] != expected or len(html_paths) <= len(slides):
        return False
    appendix_paths = html_paths[len(slides):]
    if not all("data-citation-appendix-page" in path.read_text(encoding="utf-8") for path in appendix_paths):
        return False

    closing_number = len(slides) + 1
    closing_id = f"slide_{closing_number:03d}"
    for path in reversed(appendix_paths):
        number = int(path.stem.split("_")[-1])
        path.replace(html_dir / f"slide_{number + 1:02d}.html")
    (html_dir / f"slide_{closing_number:02d}.html").write_text(
        _closing_slide_html().replace("SLIDE_ID", f"slide_{closing_number:02d}"), encoding="utf-8"
    )
    slides.append({
        "slide_id": closing_id, "page_role": "closing", "slide_type": "closing",
        "section_ref": "closing", "title": "感谢聆听", "key_message": "感谢聆听",
        "bullet_points": [], "source_refs": [], "evidence_refs": [],
        "visual_candidates": [], "section": "结束页",
    })
    _write_json(outline_path, outline)
    manuscript = _read_json(speaker_path)
    _append_closing_manuscript_slide(manuscript, closing_id)
    _write_json(speaker_path, manuscript)
    _write_legacy_manuscript_markdown(research_dir / "speaker_manuscript.md", manuscript)
    _write_json(deck_dir / "speaker_manuscript.json", manuscript)
    _write_legacy_manuscript_markdown(deck_dir / "speaker_manuscript.md", manuscript)

    from .html_brand_postprocess import apply_sjtu_brand_to_html
    from memslides.utils.webview import convert_html_to_pptx
    roles = [str(slide.get("page_role", "")) for slide in slides]
    titles = [str(slide.get("title", "")) for slide in slides]
    prefix_dir = deck_dir / ".legacy-migration-research-html"
    prefix_dir.mkdir(exist_ok=True)
    try:
        for index in range(1, len(slides) + 1):
            shutil.copy2(html_dir / f"slide_{index:02d}.html", prefix_dir / f"slide_{index:02d}.html")
        brand = apply_sjtu_brand_to_html(prefix_dir, roles, titles)
        for index in range(1, len(slides) + 1):
            shutil.copy2(prefix_dir / f"slide_{index:02d}.html", html_dir / f"slide_{index:02d}.html")
    finally:
        shutil.rmtree(prefix_dir, ignore_errors=True)
    brand["slide_html_dir"] = str(html_dir.resolve())
    _write_json(deck_dir / "sjtu_html_brand_report.json", brand)
    pptx_path = Path(receipt.get("outputs", {}).get("pptx", deck_dir / "manuscript.pptx"))
    await convert_html_to_pptx(html_dir, pptx_path, "16:9", speaker_notes_path=speaker_path)
    receipt["slide_count"] = len(slides)
    receipt.setdefault("outputs", {}).update({
        "pptx": str(pptx_path.resolve()), "slide_html_dir": str(html_dir.resolve()),
        "sjtu_html_brand_report": str((deck_dir / "sjtu_html_brand_report.json").resolve()),
    })
    receipt["legacy_migration"] = {
        "status": "passed", "mode": "local_only_no_models", "inserted_slide": closing_number,
        "citation_appendix_pages_shifted": len(appendix_paths),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(receipt_path, receipt)
    manifest["legacy_migration"] = receipt["legacy_migration"]
    _write_json(manifest_path, manifest)
    return True


def _final_compliance(
    *,
    research_dir: Path,
    citation_dir: Path,
    deck_dir: Path,
    pptx_path: Path,
    citations_required: bool = True,
) -> dict[str, Any]:
    outline = _read_json(research_dir / "slide_outline.json")
    slide_count = len(outline.get("slides", []))
    manuscript = _read_json(research_dir / "speaker_manuscript.json")
    scripts = manuscript.get("slides", []) if isinstance(manuscript, dict) else []
    units = _read_json(citation_dir / "citation_units.json") if citations_required else []
    catalog = _read_json(citation_dir / "citation_source_catalog.json") if citations_required else {}
    validation = (
        _read_json(citation_dir / "citation_validation_report.json")
        if citations_required
        else {"verified": [], "source_missing": []}
    )
    brand = _read_json(deck_dir / "sjtu_html_brand_report.json")
    generation_receipt = _read_json(deck_dir / "financial_generation_receipt.json")
    html_dir = Path(generation_receipt.get("outputs", {}).get("slide_html_dir", ""))
    if not html_dir.is_dir():
        candidates = [path for path in deck_dir.rglob("slide_01.html")]
        html_dir = candidates[0].parent if candidates else deck_dir / "slides"
    html_paths = sorted(
        html_dir.glob("slide_*.html"),
        key=lambda path: int(path.stem.rsplit("_", 1)[-1]),
    )
    html_texts = [path.read_text(encoding="utf-8") for path in html_paths]
    failures: list[str] = []
    if citations_required:
        if not units or not catalog or not validation.get("verified"):
            failures.append("citation artifacts contain no verified references")
        if not any('class="reference-mark"' in text or "reference-mark" in text for text in html_texts):
            failures.append("final HTML contains no citation marks")
        if not any("data-citation-appendix-page" in text for text in html_texts):
            failures.append("final HTML contains no citation appendix")
    if brand.get("slide_count") != slide_count:
        failures.append("SJTU brand report does not cover every research slide")
    if citations_required and generation_receipt.get("outputs", {}).get("citations_applied") is not True:
        failures.append("financial generation receipt does not confirm citations")
    research_html = html_texts[:slide_count]
    content_indices = [
        index
        for index, slide in enumerate(outline.get("slides", []))
        if isinstance(slide, dict) and slide.get("page_role") == "content"
    ]
    if any("sjtu-financial-brand-mark" not in research_html[index] for index in content_indices):
        failures.append("SJTU logo is missing from one or more content slides")
    if not research_html or 'data-sjtu-background="title"' not in research_html[0]:
        failures.append("SJTU title template background is missing")
    closing_indices = [
        index
        for index, slide in enumerate(outline.get("slides", []))
        if isinstance(slide, dict) and slide.get("page_role") == "closing"
    ]
    if closing_indices != [slide_count - 1]:
        failures.append("outline must contain exactly one final closing slide")
    elif 'data-sjtu-background="closing"' not in research_html[closing_indices[0]]:
        failures.append("SJTU closing template background is missing")
    if slide_count < 1 or len(scripts) != slide_count:
        failures.append("speaker manuscript is not aligned with the outline")
    if not pptx_path.is_file() or pptx_path.stat().st_size == 0:
        failures.append("PPTX is missing or empty")
    notes_count = 0
    if pptx_path.is_file():
        with zipfile.ZipFile(pptx_path) as archive:
            notes_count = len(
                [name for name in archive.namelist() if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")]
            )
    if notes_count < slide_count:
        failures.append("PPTX speaker notes do not cover every research slide")
    if failures:
        raise FinancialReportWorkflowError("Final compliance failed: " + "; ".join(failures))
    return {
        "status": "passed",
        "research_slide_count": slide_count,
        "pptx_notes_count": notes_count,
        "verified_citation_ids": len(validation["verified"]),
        "excluded_missing_citation_ids": list(validation.get("source_missing", [])),
        "citation_appendix_pages": sum("data-citation-appendix-page" in text for text in html_texts),
        "citations_required": citations_required,
        "sjtu_branding": True,
        "sjtu_template": "built_in_sjtu_visual_template",
    }


async def run_financial_report_workflow(
    markdown_path: str | Path,
    output_dir: str | Path,
    *,
    pdf_path: str | Path | None = None,
    parsed_json_path: str | Path | None = None,
    config_path: str | Path | None = None,
    resume: bool = False,
    overwrite: bool = False,
    instruction: str = "",
    generation_timeout: float = 3600,
    model: str | None = None,
    base_url: str | None = None,
    api_provider: str | None = None,
    citation_model: str = "deepseek-v4-flash",
    max_tokens: int | None = None,
    max_attempts: int | None = None,
    speaker_max_tokens: int | None = None,
    speaker_max_attempts: int | None = None,
    timeout: int | None = None,
) -> FinancialReportWorkflowResult:
    if resume and overwrite:
        raise FinancialReportWorkflowError("--resume and --overwrite cannot be used together")
    inputs = _resolve_inputs(markdown_path, pdf_path, parsed_json_path)
    root = Path(output_dir).expanduser().resolve()
    root_existed = root.exists()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "run_manifest.json"
    hashes = {name: _sha256(path) for name, path in inputs.items() if path.is_file()}
    if resume:
        if not manifest_path.is_file():
            raise FinancialReportWorkflowError("--resume requires an existing run_manifest.json")
        manifest = _read_json(manifest_path)
        if manifest.get("input_sha256") != hashes:
            raise FinancialReportWorkflowError("Inputs changed since the saved run; use --overwrite")
    else:
        if root_existed and any(root.iterdir()) and not overwrite:
            raise FinancialReportWorkflowError("Output already contains a run; use --resume or --overwrite")
        manifest = {"schema_version": "1.0.0", "inputs": {k: str(v) for k, v in inputs.items()}, "input_sha256": hashes, "stages": {}}

    research_input = inputs.get("markdown", inputs["pdf"])
    citations_required = "markdown" in inputs
    limits = _effective_generation_limits(
        research_input,
        max_tokens=max_tokens,
        max_attempts=max_attempts,
        speaker_max_tokens=speaker_max_tokens,
        speaker_max_attempts=speaker_max_attempts,
    )
    manifest["generation_limits"] = limits

    research_dir, citation_dir, deck_dir = root / "research", root / "citations", root / "deck"
    if overwrite:
        for stage_dir in (research_dir, citation_dir, deck_dir):
            _safe_reset_stage(stage_dir, root)
    _write_json(manifest_path, manifest)

    outline = research_dir / "slide_outline.json"
    speaker = research_dir / "speaker_manuscript.json"
    if not _stage_passed(manifest, "research", [outline, speaker]):
        _mark_running(manifest_path, manifest, "research")
        try:
            _, warnings = run_research_pipeline(
                research_input, research_dir, overwrite=overwrite, model=model,
                base_url=base_url, api_provider=api_provider,
                max_tokens=limits["max_tokens"],
                max_attempts=limits["max_attempts"], timeout=timeout,
                speaker_max_tokens=limits["speaker_max_tokens"],
                speaker_max_attempts=limits["speaker_max_attempts"],
                export_figure_sources=not citations_required,
            )
            _mark(manifest_path, manifest, "research", warnings=list(warnings))
        except Exception as exc:
            _mark_failed(manifest_path, manifest, "research", exc)
            raise FinancialReportWorkflowError(
                f"Research stage failed: {exc}"
            ) from exc

    finalized_outline = _read_json(outline)
    from memslides.research_pipeline.outline_generator.bundle_validation import (
        normalize_repeated_content_titles,
    )

    normalized_title_count = normalize_repeated_content_titles(finalized_outline)
    if normalized_title_count:
        _write_json(outline, finalized_outline)
        manifest.setdefault("local_repairs", {})["unique_content_titles"] = (
            normalized_title_count
        )
        _write_json(manifest_path, manifest)
    finalized_roles = [
        str(slide.get("page_role") or "")
        for slide in finalized_outline.get("slides", [])
        if isinstance(slide, dict)
    ]
    if (
        not finalized_roles
        or finalized_roles[0] != "title"
        or finalized_roles.count("closing") != 1
        or finalized_roles[-1] != "closing"
    ):
        migrated = resume and await _migrate_legacy_run_without_models(
            research_dir=research_dir,
            deck_dir=deck_dir,
            manifest_path=manifest_path,
            manifest=manifest,
        )
        if migrated:
            finalized_outline = _read_json(outline)
            finalized_roles = [
                str(slide.get("page_role") or "")
                for slide in finalized_outline.get("slides", [])
                if isinstance(slide, dict)
            ]
        else:
            raise FinancialReportWorkflowError(
                "Research outline does not satisfy the title/content/closing contract. "
                "This legacy run is not safely migratable; regenerate it with --overwrite."
            )

    source_catalog = citation_dir / "citation_source_catalog.json"
    citation_units = citation_dir / "citation_units.json"
    validation_report = citation_dir / "citation_validation_report.json"
    if not citations_required:
        _mark(
            manifest_path,
            manifest,
            "citations",
            skipped=True,
            reason="direct PDF input does not require citation processing",
        )
    elif not _stage_passed(manifest, "citations", [source_catalog, citation_units, validation_report]):
        _mark_running(manifest_path, manifest, "citations")
        try:
            if not source_catalog.is_file():
                parse_pdf_citation_appendix(inputs["pdf"], citation_dir)
            parsed_json = inputs["parsed_json"]
            if not parsed_json.is_file():
                parsed_json = citation_dir / "report_parsed.json"
                citation_text_source = inputs.get(
                    "markdown", citation_dir / "mineru_raw" / "document.md"
                )
                _write_json(parsed_json, parse_file(citation_text_source))
            if not citation_units.is_file():
                write_citation_units(parsed_json, citation_units)
            write_citation_validation_report(citation_units, source_catalog, validation_report)
            validation = _read_json(validation_report)
            if not validation.get("verified"):
                raise FinancialReportWorkflowError(
                    "Citation validation failed: at least one verified reference is required"
                )
            _mark(
                manifest_path,
                manifest,
                "citations",
                verified=len(validation["verified"]),
                excluded_missing=list(validation.get("source_missing", [])),
                parsed_json=str(parsed_json),
            )
        except Exception as exc:
            _mark_failed(manifest_path, manifest, "citations", exc)
            raise FinancialReportWorkflowError(
                f"Citations stage failed: {exc}"
            ) from exc

    receipt = deck_dir / "financial_generation_receipt.json"
    pptx_candidates = list(deck_dir.glob("*.pptx"))
    if not _stage_passed(manifest, "deck", [receipt]) or not pptx_candidates:
        # A resumed DeckDesigner workspace contains valuable HTML and execution
        # state. Keep it so the agent can inspect/finalize instead of regenerating
        # every page after an environment failure (for example missing Node.js).
        if not resume and deck_dir.exists() and any(deck_dir.iterdir()):
            _safe_reset_stage(deck_dir, root)
        _mark_running(manifest_path, manifest, "deck")
        try:
            generation_attempts = 0
            while True:
                generation_attempts += 1
                try:
                    result = await generate_financial_deck(
                        outline_path=outline,
                        visualization_manifest_path=research_dir / "visualizations" / "visualization_manifest.json",
                        numeric_audit_path=research_dir / "numeric_audit.json",
                        output_dir=deck_dir,
                        config_path=Path(config_path).expanduser() if config_path else None,
                        instruction=instruction,
                        generation_timeout=generation_timeout,
                        sjtu_branding=True,
                        citation_units_path=(citation_units if citations_required else None),
                        citation_validation_path=(validation_report if citations_required else None),
                        citation_source_catalog_path=(source_catalog if citations_required else None),
                        citation_model=citation_model,
                        reuse_complete_html=resume,
                    )
                    break
                except Exception as generation_exc:
                    if generation_attempts >= 2 or not _is_retryable_deck_error(
                        generation_exc
                    ):
                        raise
            pptx_path = result.pptx_path
            _mark(
                manifest_path,
                manifest,
                "deck",
                pptx=str(pptx_path),
                generation_attempts=generation_attempts,
            )
        except Exception as exc:
            _mark_failed(manifest_path, manifest, "deck", exc)
            raise FinancialReportWorkflowError(f"Deck stage failed: {exc}") from exc
    else:
        pptx_path = Path(manifest["stages"]["deck"].get("pptx", pptx_candidates[0])).resolve()
        if resume and not citations_required:
            html_dir = deck_dir / "outputs"
            if html_dir.is_dir():
                from memslides.integrations.research_report.html_brand_postprocess import (
                    apply_sjtu_brand_to_html,
                )
                from memslides.utils.webview import convert_html_to_pptx

                page_titles = [
                    str(slide.get("title") or "")
                    for slide in finalized_outline.get("slides", [])
                    if isinstance(slide, dict)
                ]
                brand_report = apply_sjtu_brand_to_html(
                    html_dir,
                    finalized_roles,
                    page_titles,
                )
                _write_json(deck_dir / "sjtu_html_brand_report.json", brand_report)
                base_stem = pptx_path.stem
                while base_stem.endswith("-quality-refreshed"):
                    base_stem = base_stem[: -len("-quality-refreshed")]
                refreshed_pptx_path = pptx_path.with_name(
                    f"{base_stem}-quality-refreshed{pptx_path.suffix}"
                )
                await convert_html_to_pptx(
                    html_dir,
                    refreshed_pptx_path,
                    "16:9",
                    speaker_notes_path=deck_dir / "speaker_manuscript.json",
                )
                pptx_path = refreshed_pptx_path
                manifest["stages"]["deck"]["pptx"] = str(pptx_path)
                manifest.setdefault("local_repairs", {})[
                    "resumed_deck_quality_refresh"
                ] = True
                _write_json(manifest_path, manifest)

    human_source_report: dict[str, Any] | None = None
    if not citations_required:
        source_manifest = research_dir / "figure_source_manifest.json"
        application_report = deck_dir / "human_pdf_figure_citation_report.json"
        if source_manifest.is_file():
            if not _stage_passed(
                manifest, "human_figure_citations", [application_report]
            ):
                _mark_running(manifest_path, manifest, "human_figure_citations")
                try:
                    from .human_pdf_citations import (
                        apply_human_pdf_figure_citations,
                    )

                    human_source_report = await apply_human_pdf_figure_citations(
                        research_directory=research_dir,
                        deck_directory=deck_dir,
                    )
                    _write_json(application_report, human_source_report)
                    _mark(
                        manifest_path,
                        manifest,
                        "human_figure_citations",
                        **human_source_report["summary"],
                        report=str(application_report),
                    )
                except Exception as exc:
                    _mark_failed(
                        manifest_path, manifest, "human_figure_citations", exc
                    )
                    raise FinancialReportWorkflowError(
                        f"Human figure citation stage failed: {exc}"
                    ) from exc
            else:
                human_source_report = _read_json(application_report)
            cited_pptx = str(
                (human_source_report or {}).get("outputs", {}).get(
                    "cited_pptx", ""
                )
            )
            if cited_pptx and Path(cited_pptx).is_file():
                pptx_path = Path(cited_pptx).resolve()

    _mark_running(manifest_path, manifest, "compliance")
    try:
        compliance = _final_compliance(
            research_dir=research_dir,
            citation_dir=citation_dir,
            deck_dir=deck_dir,
            pptx_path=pptx_path,
            citations_required=citations_required,
        )
        final_receipt = root / "final_receipt.json"
        mandatory_features = ["sjtu_branding", "speaker_notes"]
        if citations_required:
            mandatory_features.insert(0, "citations")
        elif int(
            (human_source_report or {}).get("summary", {}).get(
                "applied_count", 0
            )
            or 0
        ):
            mandatory_features.insert(0, "human_figure_sources")
        _write_json(final_receipt, {**compliance, "pptx": str(pptx_path), "mandatory_features": mandatory_features})
        _mark(manifest_path, manifest, "compliance", receipt=str(final_receipt))
    except Exception as exc:
        _mark_failed(manifest_path, manifest, "compliance", exc)
        raise FinancialReportWorkflowError(
            f"Compliance stage failed: {exc}"
        ) from exc
    return FinancialReportWorkflowResult(root, pptx_path, final_receipt, manifest_path)
