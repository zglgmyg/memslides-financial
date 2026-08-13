"""Evidence-grounded speaker manuscript generation."""

from .generator import (
    SpeakerManuscriptError,
    generate_speaker_manuscript,
    render_speaker_manuscript_markdown,
    validate_speaker_manuscript,
    validate_speaker_manuscript_for_snapshot,
)

__all__ = [
    "SpeakerManuscriptError",
    "generate_speaker_manuscript",
    "render_speaker_manuscript_markdown",
    "validate_speaker_manuscript",
    "validate_speaker_manuscript_for_snapshot",
]
