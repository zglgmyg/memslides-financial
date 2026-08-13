from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path

import pytest
import yaml

from memslides.pipelines.generation import (
    _append_deck_designer_runtime_context,
    _financial_design_system_prompt,
    _financial_page_source_bundle,
)
from memslides.runtime.deck_execution_state import (
    financial_source_packet_path,
    initialize_deck_execution_state,
    load_financial_source_packets,
    record_html_written,
    record_slide_inspected,
    render_deck_progress_prompt,
)
from memslides.tools.deck_runtime import list_files, read_file, set_current_agent


def _workspace(tmp_path):
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text(
        """<!-- research-report page_role=title -->
# Cover

Investment thesis

Evidence: block:block_001

<!-- research-report slide_id=slide_001 -->

---

<!-- research-report page_role=content -->
# Earnings

- Revenue grew to 100.

![Audited chart](verified_assets/earnings.png)

Evidence: table:table_001, block:block_002

<!-- research-report slide_id=slide_002 -->
""",
        encoding="utf-8",
    )
    (tmp_path / "speaker_manuscript.json").write_text(
        json.dumps(
            {
                "slides": [
                    {
                        "slide_id": "slide_001",
                        "script": "Open with the investment thesis.",
                        "transition_to_next": "Move to earnings.",
                    },
                    {
                        "slide_id": "slide_002",
                        "script": "Explain the audited earnings evidence.",
                        "transition_to_next": "",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "asset_manifest.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "path": "verified_assets/earnings.png",
                        "kind": "chart",
                        "caption": "Audited earnings chart",
                        "verification": {
                            "status": "passed",
                            "slide_id": "slide_002",
                            "visualization_id": "visual_001",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bundle = _financial_page_source_bundle(
        workspace=tmp_path,
        manuscript_path=manuscript,
        expected_slide_count=2,
        page_roles=["title", "content"],
        page_titles=["Cover", "Earnings"],
    )
    return manuscript, bundle


def test_financial_page_packets_preserve_source_assets_evidence_and_script(tmp_path) -> None:
    _, bundle = _workspace(tmp_path)

    first = bundle["pages"]["1"]
    second = bundle["pages"]["2"]
    assert first["source_text"].startswith("<!-- research-report page_role=title -->")
    assert first["speaker_script"] == "Open with the investment thesis."
    assert second["assets"] == [
        {
            "slide_id": "slide_002",
            "visualization_id": "visual_001",
            "title": "Audited earnings chart",
            "kind": "chart",
            "width": None,
            "height": None,
            "path": "verified_assets/earnings.png",
        }
    ]
    assert second["evidence_refs"] == ["table:table_001", "block:block_002"]
    assert second["speaker_script"] == "Explain the audited earnings evidence."
    assert bundle["source_path"] == "manuscript.md"
    assert bundle["source_sha256"] == hashlib.sha256(
        (tmp_path / "manuscript.md").read_bytes()
    ).hexdigest()
    assert bundle["source_index"][1]["asset_count"] == 1
    assert "asset_paths" not in bundle["source_index"][1]


def test_financial_packet_payload_is_stored_outside_compact_execution_state(tmp_path) -> None:
    _, bundle = _workspace(tmp_path)
    state_path = initialize_deck_execution_state(
        tmp_path,
        expected_slide_count=2,
        source_bundle=bundle,
    )

    state_text = state_path.read_text(encoding="utf-8")
    state = json.loads(state_text)
    stored = load_financial_source_packets(tmp_path)

    assert financial_source_packet_path(tmp_path).is_file()
    assert stored == bundle
    assert "source_text" not in state_text
    assert "speaker_script" not in state_text
    assert "source_packet_id" not in state["slides"]["1"]
    assert "source_packet_store" not in state


def test_financial_page_packets_do_not_positionally_fallback_speaker_scripts(
    tmp_path,
) -> None:
    manuscript, _ = _workspace(tmp_path)
    speaker_path = tmp_path / "speaker_manuscript.json"
    payload = json.loads(speaker_path.read_text(encoding="utf-8"))
    payload["slides"][0]["slide_id"] = "wrong_slide"
    speaker_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="no script for financial slide_id slide_001"):
        _financial_page_source_bundle(
            workspace=tmp_path,
            manuscript_path=manuscript,
            expected_slide_count=2,
            page_roles=["title", "content"],
            page_titles=["Cover", "Earnings"],
        )


def test_progress_injects_only_the_active_financial_page(tmp_path) -> None:
    _, bundle = _workspace(tmp_path)
    initialize_deck_execution_state(
        tmp_path,
        expected_slide_count=2,
        source_bundle=bundle,
    )

    first_prompt = render_deck_progress_prompt(tmp_path)
    assert "<current_page_source>" in first_prompt
    assert "<financial_deck_source_index>" not in first_prompt
    first_source = re.search(
        r"<current_page_source>\n(.*?)\n</current_page_source>",
        first_prompt,
        flags=re.DOTALL,
    ).group(1)
    assert "Investment thesis" in first_source
    assert "Revenue grew to 100" not in first_source

    slide_one = tmp_path / "outputs" / "slide_01.html"
    record_html_written(slide_one, workspace=tmp_path)

    inspect_prompt = render_deck_progress_prompt(tmp_path)
    assert "next_action=inspect or fix `outputs/slide_01.html`" in inspect_prompt
    assert "Investment thesis" in inspect_prompt
    assert "Revenue grew to 100" not in inspect_prompt

    record_slide_inspected(slide_one, success=True, workspace=tmp_path)

    second_prompt = render_deck_progress_prompt(tmp_path)
    second_source = re.search(
        r"<current_page_source>\n(.*?)\n</current_page_source>",
        second_prompt,
        flags=re.DOTALL,
    ).group(1)
    assert "Revenue grew to 100" in second_source
    assert "Investment thesis" not in second_source
    assert "verified_assets/earnings.png" in second_source


def test_plan_stage_gets_index_without_all_page_source_payloads(tmp_path) -> None:
    _, bundle = _workspace(tmp_path)
    initialize_deck_execution_state(
        tmp_path,
        expected_slide_count=2,
        source_bundle=bundle,
    )
    (tmp_path / ".design_plan_state.json").write_text(
        json.dumps(
            {
                "status": "plan_read_back",
                "requires_refinement": True,
                "current_hash": "scaffold",
                "scaffold_hash": "scaffold",
            }
        ),
        encoding="utf-8",
    )

    prompt = render_deck_progress_prompt(tmp_path)

    assert "<financial_deck_source_index>" in prompt
    assert "<current_page_source>" not in prompt
    assert "speaker_script" not in prompt


def test_financial_manuscript_read_is_disabled_in_all_stages(
    tmp_path, monkeypatch
) -> None:
    manuscript, bundle = _workspace(tmp_path)
    initialize_deck_execution_state(
        tmp_path,
        expected_slide_count=2,
        source_bundle=bundle,
    )
    monkeypatch.setenv("MEMSLIDES_WORKSPACE", str(tmp_path))
    set_current_agent("DeckDesigner", workspace=tmp_path)

    plan_result = read_file(str(manuscript), offset=100, limit=1)

    (tmp_path / ".design_plan_state.json").write_text(
        json.dumps({"status": "unlocked"}), encoding="utf-8"
    )
    slide_result = read_file(str(manuscript), offset=100, limit=1)

    for result in (plan_result, slide_result):
        assert "Financial manuscript reads are disabled" in result
        assert "<current_page_source>" in result
        assert "Investment thesis" not in result
        assert "Revenue grew to 100" not in result


def test_financial_prompt_replaces_generic_full_manuscript_read_requirement() -> None:
    prompt = _financial_design_system_prompt(
        "You are a slide designer.\n\n"
        "<available_resources>Read the manuscript with read_file.</available_resources>\n"
        "<workflow>Use template layout_mapping.yaml.</workflow>\n"
        "<style_guidelines>Keep text readable.</style_guidelines>\n"
        "<template_driven_mode>Use template tools.</template_driven_mode>"
    )

    assert "Read the manuscript with read_file" not in prompt
    assert "layout_mapping.yaml" not in prompt
    assert "template tools" not in prompt
    assert "<financial_page_source_contract>" in prompt
    assert "Do not read the full or segmented manuscript" in prompt
    assert "write_html_file" in prompt
    assert "inspect_slide" in prompt
    assert "<style_guidelines>Keep text readable.</style_guidelines>" in prompt


def test_financial_prompt_preserves_real_role_style_without_template_contract() -> None:
    role_path = (
        Path(__file__).parents[1] / "src" / "memslides" / "roles" / "DeckDesigner.yaml"
    )
    role = yaml.safe_load(role_path.read_text(encoding="utf-8"))

    prompt = _financial_design_system_prompt(role["system"]["zh"])

    assert prompt.startswith("你是一位专业的幻灯片视觉设计专家")
    assert "<风格说明>" in prompt
    assert "<金融研报工作流>" in prompt
    assert "<模板驱动模式>" not in prompt
    assert "layout_mapping.yaml" not in prompt
    assert "读取 manuscript" not in prompt


def test_runtime_context_append_preserves_the_complete_system_prompt() -> None:
    system = (
        "Use the literal `<workspace_context>` marker as documentation.\n"
        "<workflow>write_html_file then inspect_slide</workflow>\n"
        "<financial_page_source_contract>keep me</financial_page_source_contract>"
    )
    workspace = "<workspace_context>actual runtime paths</workspace_context>"

    result = _append_deck_designer_runtime_context(system, workspace, "asset contract")

    assert system in result
    assert result.count("<workspace_context>") == 2
    assert "<workflow>write_html_file then inspect_slide</workflow>" in result
    assert "<financial_page_source_contract>keep me" in result


def test_financial_internal_control_files_are_hidden_and_not_readable(
    tmp_path, monkeypatch
) -> None:
    _, bundle = _workspace(tmp_path)
    state_path = initialize_deck_execution_state(
        tmp_path,
        expected_slide_count=2,
        source_bundle=bundle,
    )
    (tmp_path / ".design_plan_state.json").write_text(
        json.dumps({"status": "unlocked"}), encoding="utf-8"
    )
    monkeypatch.setenv("MEMSLIDES_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    set_current_agent("DeckDesigner", workspace=tmp_path)

    listing = list_files(".", max_depth=3)
    state_read = read_file(str(state_path))
    packet_read = read_file(str(financial_source_packet_path(tmp_path)))
    asset_manifest_read = read_file(str(tmp_path / "asset_manifest.json"))

    assert "deck_execution_state.json" not in listing
    assert "financial_source_packets.json" not in listing
    assert "manuscript.md" not in listing
    assert "asset_manifest.json" not in listing
    assert "speaker_manuscript.json" not in listing
    assert "Internal financial execution state is not a design input" in state_read
    assert "Internal financial execution state is not a design input" in packet_read
    assert "already represented in the runtime packet" in asset_manifest_read


def test_non_financial_read_and_progress_behavior_is_unchanged(tmp_path, monkeypatch) -> None:
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("line one\nline two\n", encoding="utf-8")
    initialize_deck_execution_state(tmp_path, expected_slide_count=2)
    monkeypatch.setenv("MEMSLIDES_WORKSPACE", str(tmp_path))
    set_current_agent("DeckDesigner", workspace=tmp_path)

    result = read_file(str(manuscript), offset=1, limit=1)
    state_result = read_file(str(tmp_path / "deck_execution_state.json"), limit=3)
    record_html_written(tmp_path / "outputs" / "slide_01.html", workspace=tmp_path)
    prompt = render_deck_progress_prompt(tmp_path)

    assert "line two" in result
    assert "File:" in state_result
    assert "financial_deck_source_index" not in prompt
    assert "current_page_source" not in prompt
    assert "next_action=write `outputs/slide_02.html`" in prompt
