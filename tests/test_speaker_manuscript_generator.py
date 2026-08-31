import json
from types import SimpleNamespace

from memslides.research_pipeline.speaker_manuscript import generator
from memslides.research_pipeline.speaker_manuscript.generator import (
    _canonicalize_fixed_slide_fields,
    _failed_slide_indices,
)


def test_canonicalizes_only_fixed_slide_fields() -> None:
    manuscript = {"slides": [
        {
            "slide_id": "wrong",
            "slide_title": "wrong",
            "script": "keep script",
            "transition_to_next": "keep transition",
            "evidence_refs": [{"kind": "block", "id": "block-1"}],
        },
        {
            "slide_id": "wrong-again",
            "slide_title": "wrong-again",
            "script": "keep closing script",
            "transition_to_next": "remove final transition",
            "evidence_refs": [],
        },
    ]}
    outline = {"slides": [
        {"slide_id": "slide_001", "title": "Title"},
        {"slide_id": "slide_002", "title": "Closing"},
    ]}

    _canonicalize_fixed_slide_fields(manuscript, outline)

    assert manuscript["slides"][0] == {
        "slide_id": "slide_001",
        "slide_title": "Title",
        "script": "keep script",
        "transition_to_next": "keep transition",
        "evidence_refs": [{"kind": "block", "id": "block-1"}],
    }
    assert manuscript["slides"][1] == {
        "slide_id": "slide_002",
        "slide_title": "Closing",
        "script": "keep closing script",
        "transition_to_next": "",
        "evidence_refs": [],
    }


def test_does_not_touch_mismatched_or_malformed_slides() -> None:
    outline = {"slides": [{"slide_id": "slide_001", "title": "Title"}]}
    mismatched = {"slides": []}
    malformed = {"slides": ["not an object"]}

    _canonicalize_fixed_slide_fields(mismatched, outline)
    _canonicalize_fixed_slide_fields(malformed, outline)

    assert mismatched == {"slides": []}
    assert malformed == {"slides": ["not an object"]}


def _script(slide_id: str, title: str, *, evidence_refs: list[dict] | None = None) -> dict:
    return {
        "slide_id": slide_id,
        "slide_title": title,
        "narrative_role": "fact",
        "script": f"script for {slide_id}",
        "transition_to_next": "next",
        "evidence_refs": evidence_refs or [],
        "estimated_seconds": 30,
    }


def _manuscript(slides: list[dict]) -> dict:
    slides[-1]["transition_to_next"] = ""
    return {
        "schema_version": "2.0.0",
        "metadata": {
            "title": "Test",
            "audience": "Test",
            "presentation_purpose": "Test",
            "estimated_total_minutes": 1,
        },
        "opening": "Opening",
        "slides": slides,
        "closing": "Closing",
    }


def test_failed_slide_indices_rejects_global_errors() -> None:
    assert _failed_slide_indices(["slides[2].script is invalid"], 3) == [2]
    assert _failed_slide_indices(["speaker slides must match outline"], 3) == []


def test_generation_repairs_only_failed_slide(monkeypatch) -> None:
    outline = {"slides": [
        {"slide_id": "slide_001", "title": "One", "evidence_refs": []},
        {"slide_id": "slide_002", "title": "Two", "evidence_refs": []},
    ]}
    first = _manuscript([
        _script("slide_001", "One", evidence_refs=[{"kind": "block", "id": "bad"}]),
        _script("slide_002", "Two"),
    ])
    repaired_slide = _script("slide_001", "One")
    responses = [first, {"slides": [repaired_slide]}]
    requests = []

    def call(request, **kwargs):
        requests.append(request)
        return {"choices": [{"message": {"content": json.dumps(responses.pop(0))}}]}

    monkeypatch.setattr(generator, "call_deepseek", call)
    monkeypatch.setattr(generator, "_snapshot_evidence_refs", lambda snapshot: set())
    result = generator.generate_speaker_manuscript(
        SimpleNamespace(metadata={}), outline, {}, [], api_key="test", model="test",
        base_url="https://example.invalid", api_provider="deepseek", max_attempts=2,
    )

    assert len(requests) == 2
    assert result["slides"][0] == repaired_slide
    assert result["slides"][1] == first["slides"][1]
    repair_payload = json.loads(requests[1]["messages"][-1]["content"])
    assert repair_payload["required_slide_ids"] == ["slide_001"]


def test_invalid_local_repair_falls_back_to_complete_retry(monkeypatch) -> None:
    outline = {"slides": [
        {"slide_id": "slide_001", "title": "One", "evidence_refs": []},
    ]}
    first = _manuscript([
        _script("slide_001", "One", evidence_refs=[{"kind": "block", "id": "bad"}]),
    ])
    complete = _manuscript([_script("slide_001", "One")])
    responses = [first, {"slides": []}, complete]
    requests = []

    def call(request, **kwargs):
        requests.append(request)
        return {"choices": [{"message": {"content": json.dumps(responses.pop(0))}}]}

    monkeypatch.setattr(generator, "call_deepseek", call)
    monkeypatch.setattr(generator, "_snapshot_evidence_refs", lambda snapshot: set())
    result = generator.generate_speaker_manuscript(
        SimpleNamespace(metadata={}), outline, {}, [], api_key="test", model="test",
        base_url="https://example.invalid", api_provider="deepseek", max_attempts=2,
    )

    assert len(requests) == 3
    assert result == complete
    assert "corrected complete JSON object" in requests[2]["messages"][-1]["content"]
