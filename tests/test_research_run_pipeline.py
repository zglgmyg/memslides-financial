from __future__ import annotations

from pathlib import Path
from threading import Barrier

from memslides.research_pipeline.outline_generator.generate_outline import (
    PreparedOutlineContext,
)
from memslides.research_pipeline.research_run import pipeline


def _prepared_context(bundle: Path) -> PreparedOutlineContext:
    return PreparedOutlineContext(
        bundle_directory=bundle.resolve(),
        runtime_memories=(),
        input_chars=0,
        direct_planning_max_chars=300_000,
        context_mode="direct",
    )


def test_narrative_and_outline_context_start_in_parallel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    rendezvous = Barrier(2)
    prepared = _prepared_context(tmp_path)
    narrative_path = tmp_path / "narrative_plan.json"

    def fake_narrative(**kwargs):
        rendezvous.wait(timeout=2)
        return {"title": "Narrative"}, narrative_path

    def fake_context(**kwargs):
        rendezvous.wait(timeout=2)
        return prepared

    monkeypatch.setattr(pipeline, "_materialize_narrative_plan", fake_narrative)
    monkeypatch.setattr(pipeline, "_prepare_outline_runtime_context", fake_context)

    result = pipeline._prepare_narrative_and_outline_context(
        snapshot=object(),
        working_directory=tmp_path,
        model=None,
        base_url=None,
        api_provider=None,
        max_tokens=None,
        max_attempts=None,
        timeout=None,
    )

    assert result == ({"title": "Narrative"}, narrative_path, prepared)


def test_materialize_outline_reuses_prepared_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prepared = _prepared_context(tmp_path)

    def fake_main(argv, *, prepared_context):
        assert prepared_context is prepared
        output_path = Path(argv[argv.index("-o") + 1])
        output_path.write_text('{"slides": []}', encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline, "generate_outline_main", fake_main)

    outline = pipeline._materialize_outline(
        bundle_directory=tmp_path,
        working_directory=tmp_path,
        model=None,
        base_url=None,
        api_provider=None,
        max_tokens=None,
        max_attempts=None,
        timeout=None,
        narrative_plan_path=tmp_path / "narrative_plan.json",
        prepared_context=prepared,
    )

    assert outline == {"slides": []}
