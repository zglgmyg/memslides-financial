"""Evidence-grounded narrative planning before slide planning."""

from .generator import (
    NarrativePlanError,
    generate_narrative_plan,
    validate_narrative_plan,
)

__all__ = [
    "NarrativePlanError",
    "generate_narrative_plan",
    "validate_narrative_plan",
]
