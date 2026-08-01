"""Resolve task-specific LLMs without initializing the memory runtime."""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


def _lookup_llm(config: Any, reference: str) -> Any | None:
    try:
        return config[reference]
    except (KeyError, TypeError, AttributeError):
        return getattr(config, reference, None)


def resolve_task_llm(config: Any, task_type: str | None = None) -> Any:
    """Resolve a task LLM without creating any memory services or stores."""

    memory_config = getattr(config, "memory", None)
    default_reference = str(
        getattr(memory_config, "llm_ref", "") or "design_agent"
    )
    default_llm = _lookup_llm(config, default_reference)
    if default_llm is None:
        raise ValueError(
            f"memory.llm_ref={default_reference!r} does not match any LLM in config"
        )

    if not task_type:
        return default_llm

    task_routes = getattr(memory_config, "llm", None) or {}
    reference = str(task_routes.get(task_type, default_reference) or default_reference)
    task_llm = _lookup_llm(config, reference)
    if task_llm is not None:
        return task_llm

    logger.warning(
        "memory.llm.%s=%r not found, fallback to %r",
        task_type,
        reference,
        default_reference,
    )
    return default_llm
