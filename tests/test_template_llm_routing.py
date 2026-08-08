from __future__ import annotations

import asyncio
from types import SimpleNamespace

from memslides.memory.config_helper import MemorySystem
from memslides.runtime.agent_loop import AgentLoop
from memslides.utils.llm_routing import resolve_task_llm


def _config(*, task_reference: str = "template_model") -> SimpleNamespace:
    return SimpleNamespace(
        memory=SimpleNamespace(
            enabled=False,
            llm_ref="design_agent",
            llm={"template_analyze": task_reference},
        ),
        design_agent=object(),
        template_model=object(),
    )


def test_task_llm_resolution_does_not_require_enabled_memory() -> None:
    config = _config()

    assert resolve_task_llm(config, "template_analyze") is config.template_model
    assert config.memory.enabled is False


def test_task_llm_resolution_falls_back_to_configured_default() -> None:
    config = _config(task_reference="missing_model")

    assert resolve_task_llm(config, "template_analyze") is config.design_agent


def test_template_analysis_uses_config_route_when_memory_is_disabled() -> None:
    config = _config()
    runtime = object.__new__(AgentLoop)
    runtime.config = config
    runtime.memory_system = None

    template_llm, vision_llm = runtime._resolve_template_analysis_llms()

    assert template_llm is config.template_model
    assert vision_llm is config.design_agent
    assert runtime.memory_system is None


def test_disabled_memory_runtime_stays_empty_while_template_llm_is_available() -> None:
    config = _config()
    memory_system = asyncio.run(MemorySystem.from_config(config))
    runtime = object.__new__(AgentLoop)
    runtime.config = config
    runtime.memory_system = memory_system

    template_llm, vision_llm = runtime._resolve_template_analysis_llms()

    assert memory_system.db is None
    assert memory_system.retriever is None
    assert memory_system.template_store is None
    assert template_llm is config.template_model
    assert vision_llm is config.design_agent


def test_template_analysis_preserves_initialized_memory_routing() -> None:
    config = _config()
    memory_template_llm = object()
    memory_vision_llm = object()
    runtime = object.__new__(AgentLoop)
    runtime.config = config
    runtime.memory_system = SimpleNamespace(
        llm=None,
        llm_objects_by_task={
            "template_analyze": memory_template_llm,
            "vision": memory_vision_llm,
        },
    )

    template_llm, vision_llm = runtime._resolve_template_analysis_llms()

    assert template_llm is memory_template_llm
    assert vision_llm is memory_vision_llm
