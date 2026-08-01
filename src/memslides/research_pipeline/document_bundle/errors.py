"""Explicit, sanitized failures for parsing and bundle construction."""

from __future__ import annotations


class DocumentBundleError(RuntimeError):
    """Base exception for this package."""


class MinerUError(DocumentBundleError):
    """A MinerU request or parsing failure without secret URLs or tokens."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        code: int | str | None = None,
        mineru_message: str | None = None,
        trace_id: str | None = None,
        batch_id: str | None = None,
        state: str | None = None,
    ) -> None:
        self.http_status = http_status
        self.code = code
        self.mineru_message = mineru_message
        self.trace_id = trace_id
        self.batch_id = batch_id
        self.state = state
        details = [message]
        for name, value in (
            ("http_status", http_status),
            ("code", code),
            ("msg", mineru_message),
            ("trace_id", trace_id),
            ("batch_id", batch_id),
            ("state", state),
        ):
            if value is not None:
                details.append(f"{name}={value}")
        super().__init__("; ".join(details))


class MinerUConfigurationError(MinerUError):
    """MinerU is not configured for an explicit integration run."""


class MinerUTimeoutError(MinerUError):
    """MinerU parsing exceeded the configured total polling timeout."""


class RawArtifactError(DocumentBundleError):
    """The downloaded MinerU archive cannot map strictly to required raw files."""


class UnsafeArchiveError(RawArtifactError):
    """An archive member could escape the extraction directory."""
