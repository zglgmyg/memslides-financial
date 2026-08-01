"""Runtime configuration with conservative MinerU defaults."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MinerUConfig:
    """Configuration for the official MinerU precise parsing API v4."""

    base_url: str = field(default="https://mineru.net/api/v4", init=False)
    model_version: str = field(default="vlm", init=False)
    language: str = field(default="ch", init=False)
    is_ocr: bool = field(default=False, init=False)
    enable_table: bool = field(default=True, init=False)
    enable_formula: bool = field(default=True, init=False)
    poll_interval_seconds: float = 2.0
    poll_timeout_seconds: float = 900.0
    request_timeout_seconds: float = 60.0
    transfer_timeout_seconds: float = 300.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        if self.poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be non-negative")
        if self.poll_timeout_seconds < 0:
            raise ValueError("poll_timeout_seconds must be non-negative")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.transfer_timeout_seconds <= 0:
            raise ValueError("transfer_timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
