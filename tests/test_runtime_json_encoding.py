from __future__ import annotations

import json

from memslides.runtime.agent_loop import AgentLoop


def test_save_results_writes_utf8_json_with_chinese_paths(tmp_path) -> None:
    runtime = object.__new__(AgentLoop)
    runtime.workspace = tmp_path
    runtime.intermediate_output = {
        "manuscript": tmp_path / "中文研报" / "文稿.md",
        "slide_html_dir": tmp_path / "输出",
    }

    runtime.save_results()

    raw = (tmp_path / "intermediate_output.json").read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    assert "中文研报" in payload["manuscript"]
    assert "输出" in payload["slide_html_dir"]


def test_runtime_json_writes_do_not_depend_on_windows_ansi_encoding() -> None:
    import inspect
    from memslides.pipelines import generation
    from memslides.runtime import agent_loop

    generation_source = inspect.getsource(generation.run_generation_flow)
    save_source = inspect.getsource(agent_loop.AgentLoop.save_results)
    resume_source = inspect.getsource(agent_loop.AgentLoop.resume)

    assert '".input_request.json", "w", encoding="utf-8"' in generation_source
    assert '"intermediate_output.json", "w", encoding="utf-8"' in save_source
    assert 'open(output_file, encoding="utf-8")' in resume_source
    assert 'open(request_file, encoding="utf-8")' in resume_source
