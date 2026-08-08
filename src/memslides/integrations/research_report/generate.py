"""Generate a complete MemSlides deck from audited research-report artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

from .adapter import AdaptationResult, adapt_research_report


class FinancialGenerationError(RuntimeError):
    """Raised when the audited handoff or generated deck fails validation."""


DEFAULT_GENERATION_TIMEOUT_SECONDS = 3600.0
_FINANCIAL_PALETTE = {
    "1E3A5F",
    "2563EB",
    "1E40AF",
    "D97706",
    "F59E0B",
    "CBD5E1",
    "DBEAFE",
    "93C5FD",
    "F8FAFC",
    "FFFFFF",
    "0F172A",
    "475569",
}
_SJTU_PALETTE = {"A62038", "BFBFBF", "E0CFBD"}


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


def _outline_page_roles(outline_path: Path, expected_count: int) -> list[str]:
    outline = _read_object(outline_path, "slide_outline.json")
    slides = outline.get("slides")
    if not isinstance(slides, list) or len(slides) != expected_count:
        raise FinancialGenerationError(
            "slide_outline.json page count does not match the audited handoff."
        )
    roles = [
        str(slide.get("page_role", "") or "").strip().lower()
        if isinstance(slide, dict)
        else ""
        for slide in slides
    ]
    invalid = [
        str(index)
        for index, role in enumerate(roles, start=1)
        if role not in {"title", "content", "closing"}
    ]
    if invalid:
        raise FinancialGenerationError(
            "Financial outline pages must declare title/content/closing page_role; "
            "invalid pages=" + ",".join(invalid)
        )
    if roles[0] != "title":
        raise FinancialGenerationError(
            "The first financial outline slide must declare page_role=title."
        )
    return roles


def _financial_design_guidance(page_roles: list[str]) -> str:
    role_map = "\n".join(
        f"- Page {page_number}: `page_role={role}`"
        for page_number, role in enumerate(page_roles, start=1)
    )
    return f"""Mandatory financial design constraints:

1. Preserve the audited manuscript's exact page order, titles, claims, values, and
   bound visual assets. Change presentation design only.
2. Preserve this page-role map exactly:
{role_map}
3. Every HTML body must declare its exact role with
   `data-page-role="title|content|closing"` and its immutable page identity with
   `data-slide-id="slide_XX"`, matching its filename and the corresponding manuscript
   page. Do not move, copy, merge, or reuse content between slide identities. When a
   page needs a structural correction, replace that page's complete HTML through the
   controlled `write_html_file(force_regenerate=true, expected_hash=...)` flow; never
   append a second version of its title, body, chart, or table.
   Title and closing pages must use distinct compositions and must not contain an
   element marked `data-financial-role="content-title-bar"`. A content-page title bar is optional.
4. Use only this exact source palette in slide HTML:
   primary=#1E3A5F, data_primary=#2563EB, data_dark=#1E40AF,
   accent=#D97706, accent_light=#F59E0B, border=#CBD5E1,
   tint=#DBEAFE, tint_strong=#93C5FD, background=#F8FAFC,
   surface=#FFFFFF, primary_text=#0F172A, muted_text=#475569,
   inverse_text=#FFFFFF.
   Do not invent substitute hex colors. Black/white alpha values may be used only for
   shadows or subtle overlays.
5. Freely design each page's composition, hierarchy, spacing, components, title
   placement, and visual structure within those content, role, and palette constraints.
   Create and refine `design_plan.md` using the normal MemSlides workflow."""


def _validate_financial_html_contract(
    slide_html_dir: Path,
    page_roles: list[str],
    *,
    sjtu_branding: bool = False,
) -> None:
    violations: list[str] = []
    color_pattern = re.compile(r"#([0-9a-fA-F]{6})(?![0-9a-fA-F])")
    for page_number, expected_role in enumerate(page_roles, start=1):
        name = f"slide_{page_number:02d}.html"
        path = slide_html_dir / name
        if not path.is_file():
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        body_match = re.search(r"<body\b(?P<attrs>[^>]*)>", html, flags=re.I | re.S)
        body_attrs = body_match.group("attrs") if body_match else ""
        role_match = re.search(
            r"\bdata-page-role\s*=\s*(['\"])(?P<role>[^'\"]+)\1",
            body_attrs,
            flags=re.I,
        )
        actual_role = role_match.group("role").strip().lower() if role_match else ""
        if actual_role != expected_role:
            violations.append(
                f"{name}: data-page-role={actual_role or '<missing>'}, expected={expected_role}"
            )
        slide_id_match = re.search(
            r"\bdata-slide-id\s*=\s*(['\"])(?P<slide_id>[^'\"]+)\1",
            body_attrs,
            flags=re.I,
        )
        actual_slide_id = (
            slide_id_match.group("slide_id").strip().lower() if slide_id_match else ""
        )
        expected_slide_id = f"slide_{page_number:02d}"
        if actual_slide_id != expected_slide_id:
            violations.append(
                f"{name}: data-slide-id={actual_slide_id or '<missing>'}, "
                f"expected={expected_slide_id}"
            )

        title_bars = list(
            re.finditer(
                r"<(?P<tag>[a-z][a-z0-9:-]*)\b(?P<attrs>[^>]*\bdata-financial-role\s*=\s*"
                r"(['\"])content-title-bar\3[^>]*)>",
                html,
                flags=re.I | re.S,
            )
        )
        if expected_role == "content" and sjtu_branding:
            body_style_match = re.search(
                r"\bstyle\s*=\s*(['\"])(?P<style>.*?)\1",
                body_attrs,
                flags=re.I | re.S,
            )
            body_style = body_style_match.group("style") if body_style_match else ""
            body_background_match = re.search(
                r"(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)",
                body_style,
                flags=re.I,
            )
            body_background = (
                body_background_match.group(1).strip() if body_background_match else ""
            )
            if not re.fullmatch(
                r"#f8fafc(?:\s*!important)?",
                body_background,
                flags=re.I,
            ):
                violations.append(
                    f"{name}: SJTU HTML branding must leave a solid #F8FAFC content canvas"
                )
        elif expected_role in {"title", "closing"} and title_bars:
            violations.append(f"{name}: {expected_role} page must not use a content title bar")

        allowed_palette = _FINANCIAL_PALETTE | (_SJTU_PALETTE if sjtu_branding else set())
        unexpected_colors = sorted(
            {match.group(1).upper() for match in color_pattern.finditer(html)}
            - allowed_palette
        )
        if unexpected_colors:
            violations.append(
                f"{name}: colors outside financial palette="
                + ",".join(f"#{color}" for color in unexpected_colors)
            )

    if violations:
        raise FinancialGenerationError(
            "DeckDesigner violated the financial HTML contract: " + "; ".join(violations)
        )


def _duplicate_visible_text_fragments(html: str) -> list[str]:
    """Return repeated, substantial visible text fragments within one slide.

    This deliberately ignores short labels/numbers, which are commonly repeated in
    tables and charts. It catches the failure mode where a repair leaves a second
    title or body text box on the same page.
    """
    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", html, flags=re.I | re.S)
    if not body_match:
        return []
    body = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        body_match.group(1),
        flags=re.I | re.S,
    )
    fragments = [
        re.sub(r"\s+", " ", unescape(fragment)).strip()
        for fragment in re.findall(r">([^<>]+)<", body)
    ]
    counts: dict[str, int] = {}
    for fragment in fragments:
        # Short labels, dates, and values are valid repeated content in tables.
        if len(fragment) < 12 or not re.search(r"[A-Za-z\u4e00-\u9fff]", fragment):
            continue
        counts[fragment] = counts.get(fragment, 0) + 1
    return sorted(fragment for fragment, count in counts.items() if count > 1)


def _validate_slide_html_dir(
    slide_html_dir: Path,
    expected_count: int,
    *,
    page_roles: list[str] | None = None,
    sjtu_branding: bool = False,
) -> None:
    """Reject partial DeckDesigner output before issuing a success receipt."""

    missing: list[str] = []
    invalid: list[str] = []
    duplicates: list[str] = []
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
            continue
        repeated = _duplicate_visible_text_fragments(html)
        if repeated:
            duplicates.append(f"{name}=" + " | ".join(repeated[:3]))

    if missing or invalid:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if invalid:
            details.append("blank_or_placeholder=" + ",".join(invalid))
        raise FinancialGenerationError(
            "DeckDesigner produced an incomplete slide HTML set: " + "; ".join(details)
        )
    if duplicates:
        raise FinancialGenerationError(
            "DeckDesigner produced duplicate visible text on a slide; "
            "regenerate the affected page instead of exporting: " + "; ".join(duplicates)
        )
    if page_roles is not None:
        _validate_financial_html_contract(
            slide_html_dir,
            page_roles,
            sjtu_branding=sjtu_branding,
        )


async def _generate_with_timeout(
    session: Any,
    request: Any,
    timeout_seconds: float,
) -> Any:
    try:
        return await asyncio.wait_for(
            session.generate(request),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise FinancialGenerationError(
            "DeckDesigner generation exceeded the financial timeout "
            f"of {timeout_seconds:g} seconds."
        ) from exc


async def generate_financial_deck(
    *,
    outline_path: str | Path,
    visualization_manifest_path: str | Path,
    numeric_audit_path: str | Path,
    output_dir: str | Path,
    config_path: str | Path | None = None,
    template_path: str | Path | None = None,
    instruction: str = "",
    generation_timeout: float = DEFAULT_GENERATION_TIMEOUT_SECONDS,
    sjtu_branding: bool = False,
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
    if generation_timeout <= 0:
        raise FinancialGenerationError("generation_timeout must be greater than zero.")
    page_roles = _outline_page_roles(Path(outline_path).expanduser().resolve(), slide_count)

    protected_hashes = _snapshot(_protected_files(adaptation))

    # Imports stay local so the deterministic adapter remains usable without the
    # full agent runtime installed.
    from memslides.contracts import DeckRequest, MemoryOptions, SessionOptions
    from memslides.session import MemSlidesSession

    design_instruction = instruction.strip() or (
        "Create a professional Chinese financial research presentation from the supplied "
        "read-only manuscript. Preserve its exact page order, titles, claims, and values. "
        "Use every verified chart/table on its bound slide. Change presentation design only; "
        "Verified tables must occupy at least 70% of the slide width and should normally use a full-width layout. "
        "Never place a table with more than four columns or six rows in a half-width side column; put the takeaway above or below it. "
        "do not add, remove, recalculate, redraw, or reinterpret financial evidence."
    )
    design_instruction = design_instruction + "\n\n" + _financial_design_guidance(page_roles)
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
            "financial_page_roles": page_roles,
            "sjtu_html_branding": sjtu_branding,
        },
    )

    session = MemSlidesSession(options=options)
    try:
        try:
            deck_result = await _generate_with_timeout(
                session,
                request,
                generation_timeout,
            )
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
    _validate_slide_html_dir(
        slide_html_dir,
        slide_count,
        page_roles=page_roles,
        sjtu_branding=sjtu_branding,
    )
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
                    "sjtu_html_brand_report": (
                        str(workspace / "sjtu_html_brand_report.json")
                        if sjtu_branding
                        else ""
                    ),
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
    parser.add_argument(
        "--generation-timeout",
        type=float,
        default=DEFAULT_GENERATION_TIMEOUT_SECONDS,
        help="Total DeckDesigner timeout in seconds (default: 3600)",
    )
    parser.add_argument(
        "--sjtu-branding",
        action="store_true",
        help="Apply optional SJTU colors and seal to financial HTML before export",
    )
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
            generation_timeout=args.generation_timeout,
            sjtu_branding=args.sjtu_branding,
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
