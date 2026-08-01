"""Generate a complete MemSlides deck from audited research-report artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapter import AdaptationResult, adapt_research_report


class FinancialGenerationError(RuntimeError):
    """Raised when the audited handoff or generated deck fails validation."""


@dataclass(frozen=True)
class FinancialGenerationResult:
    workspace: Path
    manuscript: Path
    asset_manifest: Path
    evidence_manifest: Path
    slide_html_dir: Path
    pptx_path: Path
    pdf_path: Path | None
    receipt: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinancialGenerationError(f"Unable to read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise FinancialGenerationError(f"{label} must contain a JSON object: {path}")
    return payload


def _resolve_template_path(template_path: str | Path | None) -> Path | None:
    if template_path is None:
        return None
    resolved = Path(template_path).expanduser().resolve()
    if not resolved.is_file():
        raise FinancialGenerationError(f"Template PPTX does not exist: {resolved}")
    if resolved.suffix.lower() != ".pptx":
        raise FinancialGenerationError(f"Template must be a .pptx file: {resolved}")
    return resolved


def _protected_files(adaptation: AdaptationResult) -> list[Path]:
    manifest = _read_object(adaptation.asset_manifest, "asset_manifest.json")
    paths = [adaptation.manuscript, adaptation.asset_manifest, adaptation.evidence_manifest]
    for index, asset in enumerate(manifest.get("assets") or []):
        if not isinstance(asset, dict) or not str(asset.get("path", "") or "").strip():
            raise FinancialGenerationError(f"asset_manifest.assets[{index}] has no path.")
        paths.append(Path(str(asset["path"])).resolve())
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        if not resolved.is_file():
            raise FinancialGenerationError(f"Protected financial artifact is missing: {resolved}")
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _snapshot(paths: list[Path]) -> dict[str, str]:
    return {str(path): _sha256(path) for path in paths}


def _assert_unchanged(before: dict[str, str]) -> None:
    changed: list[str] = []
    for raw_path, expected_hash in before.items():
        path = Path(raw_path)
        if not path.is_file() or _sha256(path) != expected_hash:
            changed.append(raw_path)
    if changed:
        raise FinancialGenerationError(
            "MemSlides modified read-only financial artifacts: " + ", ".join(changed)
        )


def _validate_slide_html_dir(slide_html_dir: Path, expected_count: int) -> None:
    """Reject partial DeckDesigner output before issuing a success receipt."""

    missing: list[str] = []
    invalid: list[str] = []
    for page_number in range(1, expected_count + 1):
        name = f"slide_{page_number:02d}.html"
        path = slide_html_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        try:
            html = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            invalid.append(name)
            continue
        lowered = html.lower()
        placeholder = "html 已压缩" in lowered or "html 已壓縮" in lowered
        has_body = "<body" in lowered and "</body>" in lowered
        has_visual = bool(re.search(r"<(?:img|svg|canvas|table)\b", lowered))
        body_match = re.search(r"<body\b[^>]*>(.*?)</body>", html, flags=re.I | re.S)
        body_text = ""
        if body_match:
            body_text = re.sub(r"<[^>]+>", " ", body_match.group(1))
            body_text = re.sub(r"\s+", " ", body_text).strip()
        if placeholder or not has_body or (not body_text and not has_visual):
            invalid.append(name)

    if missing or invalid:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if invalid:
            details.append("blank_or_placeholder=" + ",".join(invalid))
        raise FinancialGenerationError(
            "DeckDesigner produced an incomplete slide HTML set: " + "; ".join(details)
        )


async def generate_financial_deck(
    *,
    outline_path: str | Path,
    visualization_manifest_path: str | Path,
    numeric_audit_path: str | Path,
    output_dir: str | Path,
    config_path: str | Path | None = None,
    template_path: str | Path | None = None,
    instruction: str = "",
) -> FinancialGenerationResult:
    """Run audited adaptation, DeckDesigner, repair, and PPTX/PDF export."""

    resolved_template = _resolve_template_path(template_path)
    workspace = Path(output_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    stale_pptx = list(workspace.glob("*.pptx"))
    if stale_pptx:
        raise FinancialGenerationError(
            f"Output workspace already contains a PPTX; use a fresh directory: {workspace}"
        )

    adaptation = adapt_research_report(
        outline_path=outline_path,
        visualization_manifest_path=visualization_manifest_path,
        numeric_audit_path=numeric_audit_path,
        output_dir=workspace,
    )
    evidence = _read_object(adaptation.evidence_manifest, "financial_evidence_manifest.json")
    if evidence.get("status") != "passed":
        raise FinancialGenerationError("Financial evidence handoff did not pass.")
    summary = evidence.get("summary")
    if not isinstance(summary, dict) or int(summary.get("slide_count", 0) or 0) < 1:
        raise FinancialGenerationError("Financial evidence handoff has no slides.")
    slide_count = int(summary["slide_count"])

    protected_hashes = _snapshot(_protected_files(adaptation))

    # Imports stay local so the deterministic adapter remains usable without the
    # full agent runtime installed.
    from memslides.contracts import DeckRequest, MemoryOptions, SessionOptions
    from memslides.session import MemSlidesSession

    design_instruction = instruction.strip() or (
        "Create a professional Chinese financial research presentation from the supplied "
        "read-only manuscript. Preserve its exact page order, titles, claims, and values. "
        "Use every verified chart/table on its bound slide. Change presentation design only; "
        "do not add, remove, recalculate, redraw, or reinterpret financial evidence."
    )
    options = SessionOptions(
        config_file=Path(config_path).resolve() if config_path else None,
        workspace=workspace,
        language="zh",
        memory=MemoryOptions(enabled=False),
        check_llms=False,
    )
    request = DeckRequest(
        instruction=design_instruction,
        num_pages=slide_count,
        language="zh",
        template=resolved_template,
        template_as_reference=resolved_template is not None,
        extra_info={
            "prebuilt_manuscript": str(adaptation.manuscript),
            "prebuilt_asset_manifest": str(adaptation.asset_manifest),
            "financial_evidence_manifest": str(adaptation.evidence_manifest),
            "financial_artifacts_read_only": True,
        },
    )

    session = MemSlidesSession(options=options)
    try:
        try:
            deck_result = await session.generate(request)
        finally:
            _assert_unchanged(protected_hashes)
    finally:
        await session.close()

    pptx_path = Path(deck_result.pptx_path).resolve() if deck_result.pptx_path else None
    slide_html_dir = (
        Path(deck_result.slide_html_dir).resolve() if deck_result.slide_html_dir else None
    )
    if pptx_path is None or not pptx_path.is_file():
        raise FinancialGenerationError("MemSlides did not produce a PPTX file.")
    if slide_html_dir is None or not slide_html_dir.is_dir():
        raise FinancialGenerationError("MemSlides did not produce a slide HTML directory.")
    _validate_slide_html_dir(slide_html_dir, slide_count)
    pdf_path = Path(deck_result.pdf_path).resolve() if deck_result.pdf_path else None
    if pdf_path is not None and not pdf_path.is_file():
        pdf_path = None

    receipt_path = workspace / "financial_generation_receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "status": "passed",
                "workspace": str(workspace),
                "slide_count": slide_count,
                "outputs": {
                    "slide_html_dir": str(slide_html_dir),
                    "pptx": str(pptx_path),
                    "pdf": str(pdf_path) if pdf_path else "",
                },
                "integrity": {
                    "status": "passed",
                    "protected_file_count": len(protected_hashes),
                    "sha256": protected_hashes,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return FinancialGenerationResult(
        workspace=workspace,
        manuscript=adaptation.manuscript,
        asset_manifest=adaptation.asset_manifest,
        evidence_manifest=adaptation.evidence_manifest,
        slide_html_dir=slide_html_dir,
        pptx_path=pptx_path,
        pdf_path=pdf_path,
        receipt=receipt_path,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a complete MemSlides deck from audited research-report artifacts."
    )
    parser.add_argument("--outline", required=True, type=Path)
    parser.add_argument("--visualization-manifest", required=True, type=Path)
    parser.add_argument("--numeric-audit", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, help="Optional MemSlides YAML config")
    parser.add_argument("--template", type=Path, help="Optional PPTX design template")
    parser.add_argument("--instruction", default="", help="Optional design-only instruction")
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = asyncio.run(
        generate_financial_deck(
            outline_path=args.outline,
            visualization_manifest_path=args.visualization_manifest,
            numeric_audit_path=args.numeric_audit,
            output_dir=args.output_dir,
            config_path=args.config,
            template_path=args.template,
            instruction=args.instruction,
        )
    )
    print(
        json.dumps(
            {
                "workspace": str(result.workspace),
                "slide_html_dir": str(result.slide_html_dir),
                "pptx": str(result.pptx_path),
                "pdf": str(result.pdf_path) if result.pdf_path else "",
                "receipt": str(result.receipt),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
