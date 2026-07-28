"""MemSlides - an agentic and reflective presentation generation system.

Public runtime objects are imported lazily so lightweight integrations can be
used without initializing the complete agent and model dependency graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

if TYPE_CHECKING:
    from memslides.contracts import (
        DeckRequest,
        DeckResult,
        MemoryOptions,
        RevisionRequest,
        SessionOptions,
        TemplateOptions,
    )
    from memslides.session import MemSlidesSession

__all__ = [
    "MemSlidesSession",
    "DeckRequest",
    "RevisionRequest",
    "DeckResult",
    "SessionOptions",
    "MemoryOptions",
    "TemplateOptions",
]

_CONTRACT_EXPORTS = {
    "DeckRequest",
    "RevisionRequest",
    "DeckResult",
    "SessionOptions",
    "MemoryOptions",
    "TemplateOptions",
}


def __getattr__(name: str) -> Any:
    if name == "MemSlidesSession":
        from memslides.session import MemSlidesSession

        return MemSlidesSession
    if name in _CONTRACT_EXPORTS:
        from memslides import contracts

        return getattr(contracts, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
