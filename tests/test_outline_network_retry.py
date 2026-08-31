from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from memslides.research_pipeline.outline_generator import generate_outline
from memslides.research_pipeline.outline_generator.bundle_validation import (
    canonicalize_outline_from_bundle,
    canonicalize_slide_section_order,
    compact_figure_pages_into_content_slides,
    normalize_repeated_content_titles,
    normalize_section_evidence_and_visual_budget,
    validate_outline_evidence,
)
from memslides.research_pipeline.document_intelligence.models import (
    DocumentIntelligenceSnapshot,
    EvidenceRef,
)
from memslides.research_pipeline.visualization_generator.planning import (
    plan_visualizations,
)


def test_incomplete_http_response_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*args: object, **kwargs: object) -> object:
        raise http.client.IncompleteRead(b"partial", 100)

    monkeypatch.setattr(generate_outline.urllib.request, "urlopen", interrupted)

    with pytest.raises(generate_outline.DeepSeekAPIError) as captured:
        generate_outline.call_deepseek(
            {"messages": []},
            api_key="test-key",
            base_url="https://example.invalid",
            timeout=1,
        )

    assert captured.value.retryable is True
    assert "interrupted" in str(captured.value)


def test_timing_preserves_truncation_and_validation_retry_policy(monkeypatch, caplog):
    replies = iter([
        {"choices": [{"finish_reason": "length", "message": {"content": '{"slides":'}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": '{"slides": []}'}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": '{"slides": [], "ok": true}'}}]},
    ])
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(next(replies)).encode()

    def urlopen(request, *, timeout):
        requests.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr(generate_outline.urllib.request, "urlopen", urlopen)
    outline, issues, attempts = generate_outline.generate_with_retries(
        [{"role": "user", "content": "original"}],
        {"type": "object", "required": ["ok"]},
        api_key="private-key", base_url="https://example.invalid", timeout=7,
        model="test", max_tokens=16000, thinking="enabled", reasoning_effort="high",
        max_attempts=3,
    )
    assert attempts == 3 and outline == {"slides": [], "ok": True} and issues == []
    assert [r["max_tokens"] for r in requests] == [16000, 24000, 24000]
    assert [r["thinking"]["type"] for r in requests] == ["enabled", "disabled", "disabled"]
    assert [len(r["messages"]) for r in requests] == [1, 2, 3]
    assert "research.outline.request attempt=3 returned" in caplog.text
    assert "[retry] stage=outline attempt=2" in caplog.text
    assert "validation_errors=SCHEMA.INVALID $: 'ok' is a required property" in caplog.text
    assert "private-key" not in caplog.text


def test_outline_repairs_only_failed_slide(monkeypatch):
    responses = iter([
        {"slides": [
            {"slide_id": "slide_001"},
            {"slide_id": "slide_002", "ok": True},
        ]},
        {"slides": [{"slide_id": "slide_001", "ok": True}]},
    ])
    requests = []
    postprocess_calls = []

    def call(request, **kwargs):
        requests.append(request)
        return {"choices": [{"message": {"content": json.dumps(next(responses))}}]}

    def postprocess(value):
        postprocess_calls.append(value)
        for slide in value["slides"]:
            slide["normalized"] = True

    monkeypatch.setattr(generate_outline, "call_deepseek", call)
    outline, issues, attempts = generate_outline.generate_with_retries(
        [{"role": "user", "content": "original"}],
        {
            "type": "object",
            "required": ["slides"],
            "properties": {
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["ok", "normalized"],
                    },
                }
            },
        },
        api_key="test", base_url="https://example.invalid", timeout=7,
        model="test", max_tokens=16000, thinking="enabled", reasoning_effort="high",
        max_attempts=2, postprocess_outline=postprocess,
    )

    assert attempts == 2 and issues == [] and len(requests) == 2
    assert len(postprocess_calls) == 2
    assert outline["slides"] == [
        {"slide_id": "slide_001", "ok": True, "normalized": True},
        {"slide_id": "slide_002", "ok": True, "normalized": True},
    ]
    repair_payload = json.loads(requests[1]["messages"][-1]["content"])
    assert repair_payload["required_slide_ids"] == ["slide_001"]


def test_invalid_outline_local_repair_falls_back_to_complete_retry(monkeypatch):
    responses = iter([
        {"slides": [{"slide_id": "slide_001"}]},
        {"slides": []},
        {"slides": [{"slide_id": "slide_001", "ok": True}]},
    ])
    requests = []

    def call(request, **kwargs):
        requests.append(request)
        return {"choices": [{"message": {"content": json.dumps(next(responses))}}]}

    monkeypatch.setattr(generate_outline, "call_deepseek", call)
    outline, issues, attempts = generate_outline.generate_with_retries(
        [{"role": "user", "content": "original"}],
        {
            "type": "object",
            "properties": {
                "slides": {
                    "type": "array",
                    "items": {"type": "object", "required": ["ok"]},
                }
            },
        },
        api_key="test", base_url="https://example.invalid", timeout=7,
        model="test", max_tokens=16000, thinking="enabled", reasoning_effort="high",
        max_attempts=2,
    )

    assert attempts == 2 and issues == [] and len(requests) == 3
    assert outline == {"slides": [{"slide_id": "slide_001", "ok": True}]}
    assert "修正后的完整 JSON 对象" in requests[2]["messages"][-1]["content"]


def test_outline_local_repair_rejects_global_or_unstable_slide_errors():
    outline = {"slides": [{"slide_id": "slide_001"}]}
    global_issue = generate_outline.Issue(
        "error", "BUNDLE.SECTION_ORDER", "$.slides[0].section_ref", "wrong order"
    )
    missing_id_issue = generate_outline.Issue(
        "error", "SCHEMA.INVALID", "$.slides[0].slide_id", "required"
    )

    assert generate_outline._failed_outline_slide_indices([global_issue], outline) == []
    assert generate_outline._failed_outline_slide_indices(
        [missing_id_issue], {"slides": [{}]}
    ) == []


def test_outline_postprocessing_adds_one_terminal_closing_slide() -> None:
    outline = {
        "slides": [
            {"slide_id": "slide_001", "page_role": "title"},
            {"slide_id": "slide_002", "page_role": "content"},
        ]
    }

    first = generate_outline.ensure_terminal_closing_slide(
        outline, {"closing_message": "谢谢"}
    )
    second = generate_outline.ensure_terminal_closing_slide(
        outline, {"closing_message": "谢谢"}
    )

    assert first == 1
    assert second == 0
    assert [slide["page_role"] for slide in outline["slides"]] == [
        "title",
        "content",
        "closing",
    ]
    assert outline["slides"][-1]["key_message"] == "谢谢"


def test_outline_postprocessing_restores_document_section_order() -> None:
    snapshot = DocumentIntelligenceSnapshot(
        bundle_directory=Path("."), document_json={}, metadata={},
        sections_by_id={"sec-1": {}, "sec-2": {}, "sec-3": {}},
        section_order=("sec-1", "sec-2", "sec-3"), section_paths={},
        blocks_by_id={}, tables_by_id={}, figures_by_id={}, evidence_by_key={},
        ordered_block_ids=(), block_table_ids={}, block_figure_ids={},
    )
    outline = {"slides": [
        {"slide_id": "bad-title", "page_role": "title"},
        {"slide_id": "bad-1", "page_role": "content", "section_ref": "sec-1", "key_message": "one"},
        {"slide_id": "bad-3", "page_role": "content", "section_ref": "sec-3", "key_message": "three"},
        {"slide_id": "bad-2", "page_role": "content", "section_ref": "sec-2", "key_message": "two"},
        {"slide_id": "bad-close", "page_role": "closing"},
    ]}

    changes = canonicalize_slide_section_order(outline, snapshot)

    assert changes == 2
    assert [slide.get("section_ref") for slide in outline["slides"]] == [
        None, "sec-1", "sec-2", "sec-3", None
    ]
    assert [slide["slide_id"] for slide in outline["slides"]] == [
        "slide_001", "slide_002", "slide_003", "slide_004", "slide_005"
    ]


def test_outline_postprocessing_restores_figure_captions_and_pdf_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "fig-1.png").write_bytes(b"png")
    (tmp_path / "fig-2.png").write_bytes(b"png")
    snapshot = DocumentIntelligenceSnapshot(
        bundle_directory=tmp_path, document_json={}, metadata={},
        sections_by_id={"sec-1": {"title_block_id": "heading"}},
        section_order=("sec-1",), section_paths={"sec-1": ("sec-1",)},
        blocks_by_id={
            "heading": {"text_raw": "行业图表"},
            "cap-1": {"text_raw": "图1：第一张原始图注"},
            "cap-2a": {"text_raw": "图2：第二张"},
            "cap-2b": {"text_raw": "原始图注"},
        },
        tables_by_id={},
        figures_by_id={
            "fig-1": {"id": "fig-1", "section_id": "sec-1", "caption_block_id": "cap-1", "asset_path": "fig-1.png", "source_content_index": 1},
            "fig-2": {"id": "fig-2", "section_id": "sec-1", "caption_block_ids": ["cap-2a", "cap-2b"], "asset_path": "fig-2.png", "source_content_index": 2},
        },
        evidence_by_key={
            ("figure", "fig-1"): EvidenceRef("figure", "fig-1", "sec-1", 1, None),
            ("figure", "fig-2"): EvidenceRef("figure", "fig-2", "sec-1", 2, None),
        },
        ordered_block_ids=("heading", "cap-1", "cap-2a", "cap-2b"),
        block_table_ids={}, block_figure_ids={},
    )
    outline = {"slides": [
        {"slide_id": "slide_001", "page_role": "title"},
        {"slide_id": "slide_002", "page_role": "content", "slide_type": "figure_page", "section_ref": "wrong", "title": "rewritten 2", "source_refs": ["src"], "evidence_refs": [{"kind": "figure", "id": "fig-2"}], "visual_candidates": []},
        {"slide_id": "slide_003", "page_role": "content", "slide_type": "figure_page", "section_ref": "wrong", "title": "rewritten 1", "source_refs": ["src"], "evidence_refs": [{"kind": "figure", "id": "fig-1"}], "visual_candidates": []},
    ]}

    counts = canonicalize_outline_from_bundle(outline, snapshot)
    issues = validate_outline_evidence(outline, snapshot)

    assert counts["figure_titles"] == 2
    assert counts["figure_sections"] == 2
    assert [slide.get("title") for slide in outline["slides"][1:]] == [
        "图1：第一张原始图注", "图2：第二张 原始图注"
    ]
    assert [slide["evidence_refs"][0]["id"] for slide in outline["slides"][1:]] == [
        "fig-1", "fig-2"
    ]
    assert not [issue for issue in issues if issue.code.startswith("FIGURE.")]


def test_outline_postprocessing_removes_cross_section_refs_and_caps_visuals() -> None:
    snapshot = DocumentIntelligenceSnapshot(
        bundle_directory=Path("."),
        document_json={},
        metadata={},
        sections_by_id={"sec-7": {}, "sec-8": {}},
        section_order=("sec-7", "sec-8"),
        section_paths={"sec-7": ("sec-7",), "sec-8": ("sec-8",)},
        blocks_by_id={},
        tables_by_id={},
        figures_by_id={},
        evidence_by_key={
            ("block", "local-block"): EvidenceRef(
                "block", "local-block", "sec-7", 1, None
            ),
            ("block", "foreign-block"): EvidenceRef(
                "block", "foreign-block", "sec-8", 2, None
            ),
            ("figure", "foreign-figure"): EvidenceRef(
                "figure", "foreign-figure", "sec-8", 3, None
            ),
        },
        ordered_block_ids=(),
        block_table_ids={},
        block_figure_ids={},
    )
    outline = {
        "slides": [
            {
                "slide_id": "slide_009",
                "page_role": "content",
                "slide_type": "industry_analysis",
                "section_ref": "sec-7",
                "source_refs": ["src"],
                "evidence_refs": [
                    {"kind": "block", "id": "local-block"},
                    {"kind": "block", "id": "foreign-block"},
                    {"kind": "figure", "id": "foreign-figure"},
                ],
                "visual_candidates": [
                    {
                        "candidate_id": "visual_local_1",
                        "type": "chart",
                        "evidence_refs": [{"kind": "block", "id": "local-block"}],
                    },
                    {
                        "candidate_id": "visual_foreign",
                        "type": "image",
                        "display_mode": "embedded",
                        "evidence_refs": [
                            {"kind": "figure", "id": "foreign-figure"}
                        ],
                    },
                    {
                        "candidate_id": "visual_local_2",
                        "type": "table",
                        "evidence_refs": [{"kind": "block", "id": "local-block"}],
                    },
                    {
                        "candidate_id": "visual_over_budget",
                        "type": "chart",
                        "evidence_refs": [{"kind": "block", "id": "local-block"}],
                    },
                ],
            }
        ]
    }

    counts = normalize_section_evidence_and_visual_budget(outline, snapshot)
    issues = validate_outline_evidence(outline, snapshot)

    slide = outline["slides"][0]
    assert slide["evidence_refs"] == [{"kind": "block", "id": "local-block"}]
    assert [item["candidate_id"] for item in slide["visual_candidates"]] == [
        "visual_local_1",
        "visual_local_2",
    ]
    assert counts == {
        "cross_section_evidence_removed": 2,
        "cross_section_visuals_removed": 1,
        "over_budget_visuals_removed": 1,
        "orphan_figure_refs_removed": 0,
    }
    assert not [
        issue
        for issue in issues
        if issue.code
        in {
            "BUNDLE.CROSS_SECTION_EVIDENCE",
            "BUNDLE.CROSS_SECTION_VISUAL_EVIDENCE",
            "LAYOUT.VISUAL_BUDGET_EXCEEDED",
        }
    ]


def test_outline_postprocessing_uses_unique_takeaway_titles_after_section_opener() -> None:
    outline = {
        "slides": [
            {
                "page_role": "content",
                "slide_type": "industry_analysis",
                "section_ref": "sec-7",
                "title": "7 月空调内销出货量表现亮眼，后续预计将有压力。",
                "key_message": "产业在线数据，2025 年 7 月空调内销出货量 1058 万台，同比+14.3%。",
            },
            {
                "page_role": "content",
                "slide_type": "industry_analysis",
                "section_ref": "sec-7",
                "title": "7 月空调内销出货量表现亮眼，后续预计将有压力。",
                "key_message": "国补政策拉动下，空调终端需求持续向好。",
            },
            {
                "page_role": "content",
                "slide_type": "industry_analysis",
                "section_ref": "sec-7",
                "title": "7 月空调内销出货量表现亮眼，后续预计将有压力。",
                "key_message": "线下市场零售均价自 4 月以来持续下行，7 月达到 3978 元/台。",
            },
        ]
    }

    changes = normalize_repeated_content_titles(outline)

    assert changes == 2
    assert [slide["title"] for slide in outline["slides"]] == [
        "7 月空调内销出货量表现亮眼，后续预计将有压力。",
        "国补政策拉动下，空调终端需求持续向好",
        "线下市场零售均价自 4 月以来持续下行，7 月达到 3978 元/台",
    ]


def _hybrid_figure_snapshot(tmp_path: Path) -> DocumentIntelligenceSnapshot:
    blocks = {"heading": {"text_raw": "行业图表", "type": "heading"}}
    figures = {}
    evidence = {
        ("block", f"body-{index}"): EvidenceRef(
            "block", f"body-{index}", "sec-1", index, None
        )
        for index in range(1, 4)
    }
    for index in range(1, 4):
        blocks[f"body-{index}"] = {
            "text_raw": f"行业主题{index}的正文证据支持页面叙事。",
            "type": "paragraph",
            "section_id": "sec-1",
        }
    for index in range(1, 8):
        identity = f"fig-{index}"
        caption_id = f"cap-{index}"
        (tmp_path / f"{identity}.png").write_bytes(b"png")
        blocks[caption_id] = {
            "text_raw": f"图{index}：行业主题{((index - 1) // 2) + 1}原始图",
            "type": "figure_text",
            "section_id": "sec-1",
        }
        figures[identity] = {
            "id": identity,
            "section_id": "sec-1",
            "caption_block_id": caption_id,
            "asset_path": f"{identity}.png",
            "source_content_index": index,
        }
        evidence[("figure", identity)] = EvidenceRef(
            "figure", identity, "sec-1", index, None
        )
    return DocumentIntelligenceSnapshot(
        bundle_directory=tmp_path,
        document_json={},
        metadata={},
        sections_by_id={"sec-1": {"title_block_id": "heading"}},
        section_order=("sec-1",),
        section_paths={"sec-1": ("sec-1",)},
        blocks_by_id=blocks,
        tables_by_id={},
        figures_by_id=figures,
        evidence_by_key=evidence,
        ordered_block_ids=tuple(blocks),
        block_table_ids={},
        block_figure_ids={},
    )


def test_figure_page_compaction_pairs_images_and_caps_standalone_pages(
    tmp_path: Path,
) -> None:
    snapshot = _hybrid_figure_snapshot(tmp_path)
    slides = [
        {
            "slide_id": "slide_001",
            "page_role": "title",
            "slide_type": "summary",
            "title": "封面",
            "key_message": "封面",
            "bullet_points": [],
            "source_refs": ["src_report"],
            "visual_candidates": [],
        }
    ]
    for index in range(1, 4):
        slides.append(
            {
                "slide_id": f"slide_{index + 1:03d}",
                "page_role": "content",
                "slide_type": "industry_analysis",
                "section_ref": "sec-1",
                "section": "行业图表",
                "title": "行业图表",
                "key_message": f"行业主题{index}的正文证据支持页面叙事。",
                "bullet_points": [f"行业主题{index}"],
                "source_refs": ["src_report"],
                "evidence_refs": [{"kind": "block", "id": f"body-{index}"}],
                "visual_candidates": [],
            }
        )
    for index in range(1, 8):
        slides.append(
            {
                "slide_id": f"slide_{index + 4:03d}",
                "page_role": "content",
                "slide_type": "figure_page",
                "section_ref": "sec-1",
                "section": "行业图表",
                "title": f"图{index}：行业主题{((index - 1) // 2) + 1}原始图",
                "key_message": f"图{index}",
                "bullet_points": [],
                "source_refs": ["src_report"],
                "evidence_refs": [{"kind": "figure", "id": f"fig-{index}"}],
                "visual_candidates": [],
            }
        )
    slides.append(
        {
            "slide_id": "slide_012",
            "page_role": "closing",
            "slide_type": "summary",
            "title": "结束语",
            "key_message": "谢谢",
            "bullet_points": [],
            "source_refs": [],
            "visual_candidates": [],
        }
    )
    outline = {"slides": slides}

    counts = compact_figure_pages_into_content_slides(outline, snapshot)
    canonicalize_slide_section_order(outline, snapshot)
    issues = validate_outline_evidence(outline, snapshot)

    assert counts == {
        "embedded_figures": 6,
        "paired_slides": 3,
        "standalone_retained": 1,
        "figure_pages_omitted": 0,
    }
    assert len(outline["slides"]) == 6
    content_slides = [
        slide
        for slide in outline["slides"]
        if slide.get("page_role") == "content"
        and slide.get("slide_type") != "figure_page"
    ]
    assert all(
        slide["visual_candidates"][-1]["display_mode"] == "paired"
        for slide in content_slides
    )
    assert all(
        len(slide["visual_candidates"][-1]["evidence_refs"]) == 2
        for slide in content_slides
    )
    assert not [issue for issue in issues if issue.severity == "error"]


def test_paired_image_candidate_creates_two_source_image_plans(tmp_path: Path) -> None:
    snapshot = _hybrid_figure_snapshot(tmp_path)
    outline = {
        "slides": [
            {
                "slide_id": "slide_002",
                "page_role": "content",
                "slide_type": "industry_analysis",
                "source_refs": ["src_report"],
                "evidence_refs": [
                    {"kind": "block", "id": "body-1"},
                    {"kind": "figure", "id": "fig-1"},
                    {"kind": "figure", "id": "fig-2"},
                ],
                "visual_candidates": [
                    {
                        "candidate_id": "visual_pair",
                        "type": "image",
                        "display_mode": "paired",
                        "description": "两张原始图配对",
                        "source_refs": ["src_report"],
                        "evidence_refs": [
                            {"kind": "figure", "id": "fig-1"},
                            {"kind": "figure", "id": "fig-2"},
                        ],
                    }
                ],
            }
        ]
    }

    plans = plan_visualizations(outline, snapshot, candidate_mode="disabled")

    assert [plan.visualization_id for plan in plans] == [
        "visual_pair_01",
        "visual_pair_02",
    ]
    assert [plan.evidence_refs for plan in plans] == [
        (("figure", "fig-1"),),
        (("figure", "fig-2"),),
    ]


def test_slide_outline_schema_accepts_paired_original_images() -> None:
    schema_path = (
        Path(__file__).parents[1]
        / "src"
        / "memslides"
        / "research_pipeline"
        / "schemas"
        / "slide_outline.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    candidate = {
        "candidate_id": "visual_pair",
        "type": "image",
        "display_mode": "paired",
        "description": "两张原图",
        "source_refs": ["src_report"],
        "evidence_refs": [
            {"kind": "figure", "id": "fig-1"},
            {"kind": "figure", "id": "fig-2"},
        ],
    }

    errors = list(
        Draft202012Validator(
            {
                "$defs": schema["$defs"],
                "$ref": "#/$defs/visual_candidate",
            }
        ).iter_errors(candidate)
    )

    assert errors == []
