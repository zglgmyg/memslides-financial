from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from memslides.agents.researcher import Researcher
from memslides.integrations.research_report.generate import (
    FinancialGenerationError,
    _assert_unchanged,
    _resolve_template_path,
    _snapshot,
    _validate_slide_html_dir,
)
from memslides.pipelines.generation import _write_content_asset_manifest
from memslides.utils.config import _is_non_retryable_llm_error
from memslides.utils.typings import InputRequest


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_researcher_returns_prebuilt_manuscript_without_llm(tmp_path: Path) -> None:
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("# Read-only deck\n", encoding="utf-8")
    researcher = object.__new__(Researcher)
    researcher.workspace = tmp_path
    request = InputRequest(
        instruction="design only",
        extra_info={"prebuilt_manuscript": str(manuscript)},
    )

    async def collect() -> list[object]:
        return [item async for item in researcher.loop(request)]

    yielded = asyncio.run(collect())

    assert yielded == [str(manuscript.resolve())]


def test_researcher_rejects_prebuilt_manuscript_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manuscript = tmp_path / "outside.md"
    manuscript.write_text("# Outside\n", encoding="utf-8")
    researcher = object.__new__(Researcher)
    researcher.workspace = workspace
    request = InputRequest(
        instruction="design only",
        extra_info={"prebuilt_manuscript": str(manuscript)},
    )

    async def collect() -> list[object]:
        return [item async for item in researcher.loop(request)]

    with pytest.raises(ValueError, match="inside the session workspace"):
        asyncio.run(collect())


def test_prebuilt_asset_manifest_is_preserved_byte_for_byte(tmp_path: Path) -> None:
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("# Audited deck\n", encoding="utf-8")
    asset = tmp_path / "verified.svg"
    asset.write_text("<svg></svg>", encoding="utf-8")
    manifest = tmp_path / "asset_manifest.json"
    _dump(
        manifest,
        {
            "manuscript": str(manuscript.resolve()),
            "workspace": str(tmp_path.resolve()),
            "assets": [
                {
                    "path": str(asset.resolve()),
                    "kind": "chart",
                    "verification": {
                        "status": "passed",
                        "numeric_audit_status": "passed",
                    },
                }
            ],
        },
    )
    before = manifest.read_bytes()

    result = _write_content_asset_manifest(
        SimpleNamespace(workspace=tmp_path),
        manuscript,
        prebuilt_manifest_path=manifest,
    )

    assert result == manifest
    assert manifest.read_bytes() == before


def test_integrity_guard_detects_modified_financial_asset(tmp_path: Path) -> None:
    asset = tmp_path / "verified.svg"
    asset.write_text("<svg>before</svg>", encoding="utf-8")
    before = _snapshot([asset])
    asset.write_text("<svg>after</svg>", encoding="utf-8")

    with pytest.raises(FinancialGenerationError, match="modified read-only"):
        _assert_unchanged(before)


def test_insufficient_balance_is_not_retried() -> None:
    error = RuntimeError(
        "Error code: 402 - {'error': {'message': 'Insufficient Balance'}}"
    )

    assert _is_non_retryable_llm_error(error) is True


def test_template_path_accepts_pptx(tmp_path: Path) -> None:
    template = tmp_path / "school.pptx"
    template.write_bytes(b"pptx fixture")

    assert _resolve_template_path(template) == template.resolve()


def test_template_path_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FinancialGenerationError, match="does not exist"):
        _resolve_template_path(tmp_path / "missing.pptx")


def test_template_path_rejects_non_pptx(tmp_path: Path) -> None:
    template = tmp_path / "school.ppt"
    template.write_bytes(b"legacy fixture")

    with pytest.raises(FinancialGenerationError, match="must be a .pptx"):
        _resolve_template_path(template)


def test_slide_html_validation_rejects_compacted_placeholder(tmp_path: Path) -> None:
    (tmp_path / "slide_01.html").write_text(
        "<html><body><h1>Complete slide</h1></body></html>", encoding="utf-8"
    )
    (tmp_path / "slide_02.html").write_text(
        "<html><head><style>[原 HTML 已压缩]</style></head></html>", encoding="utf-8"
    )

    with pytest.raises(FinancialGenerationError, match="slide_02.html"):
        _validate_slide_html_dir(tmp_path, 2)


def test_slide_html_validation_accepts_text_and_visual_slides(tmp_path: Path) -> None:
    (tmp_path / "slide_01.html").write_text(
        "<html><body><h1>Text slide</h1></body></html>", encoding="utf-8"
    )
    (tmp_path / "slide_02.html").write_text(
        '<html><body><img src="chart.svg" alt="chart"></body></html>', encoding="utf-8"
    )

    _validate_slide_html_dir(tmp_path, 2)
