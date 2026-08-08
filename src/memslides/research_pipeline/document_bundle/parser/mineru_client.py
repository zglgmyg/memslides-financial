"""Strict client for MinerU official precise parsing API v4."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from memslides.research_pipeline.document_bundle.config import MinerUConfig
from memslides.research_pipeline.document_bundle.errors import (
    MinerUConfigurationError,
    MinerUError,
    MinerUTimeoutError,
)
from memslides.research_pipeline.document_bundle.parser.artifacts import map_raw_artifacts, safe_extract_zip

LOGGER = logging.getLogger(__name__)
# httpx/httpcore include full request URLs in their own logs. Signed transfer
# URLs are secrets, so this client suppresses those third-party records.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
POLLING_STATES = {"waiting-file", "pending", "running", "converting"}
TERMINAL_STATES = {"done", "failed"}


@dataclass(frozen=True, slots=True)
class MinerUResult:
    batch_id: str
    trace_id: str | None
    full_zip_url: str


class MinerUClient:
    """Upload, poll, and download MinerU results without leaking credentials."""

    def __init__(
        self,
        config: MinerUConfig | None = None,
        *,
        token: str | None = None,
        api_transport: httpx.BaseTransport | None = None,
        transfer_transport: httpx.BaseTransport | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or MinerUConfig()
        self._token = token if token is not None else os.getenv("MINERU_API_TOKEN")
        if not self._token:
            raise MinerUConfigurationError("MINERU_API_TOKEN is not configured")
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._api_client = httpx.Client(
            base_url=self.config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=self.config.request_timeout_seconds,
            transport=api_transport,
        )
        # Signed upload/download hosts must never receive MinerU credentials.
        self._transfer_client = httpx.Client(
            timeout=self.config.transfer_timeout_seconds,
            transport=transfer_transport,
        )
        self._closed = False

    def __enter__(self) -> "MinerUClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._api_client.close()
            self._transfer_client.close()
            self._closed = True

    def _request_with_retry(
        self,
        client: httpx.Client,
        method: str,
        endpoint: str,
        *,
        endpoint_label: str,
        batch_id: str | None = None,
        trace_id: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = client.request(method, endpoint, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    continue
                raise MinerUError(
                    f"Network failure during {endpoint_label} after limited retries",
                    batch_id=batch_id,
                    trace_id=trace_id,
                ) from exc

            if response.status_code in {401, 403}:
                code, message, response_trace_id = self._http_error_details(response)
                raise MinerUError(
                    f"Authentication failed during {endpoint_label}",
                    http_status=response.status_code,
                    code=code,
                    mineru_message=message,
                    batch_id=batch_id,
                    trace_id=response_trace_id or trace_id,
                )
            if response.status_code in RETRYABLE_HTTP_STATUSES:
                if attempt < self.config.max_retries:
                    response.close()
                    continue
            if not response.is_success:
                code, message, response_trace_id = self._http_error_details(response)
                raise MinerUError(
                    f"HTTP failure during {endpoint_label}",
                    http_status=response.status_code,
                    code=code,
                    mineru_message=message,
                    batch_id=batch_id,
                    trace_id=response_trace_id or trace_id,
                )
            return response
        raise MinerUError(
            f"Request failed during {endpoint_label}",
            batch_id=batch_id,
            trace_id=trace_id,
        ) from last_error

    def _http_error_details(
        self, response: httpx.Response
    ) -> tuple[int | str | None, str | None, str | None]:
        try:
            payload = response.json()
        except ValueError:
            return None, None, None
        if not isinstance(payload, dict):
            return None, None, None
        trace_id = payload.get("trace_id")
        return (
            payload.get("code"),
            self._sanitize(payload.get("msg")),
            str(trace_id) if trace_id is not None else None,
        )

    def _checked_json(
        self,
        response: httpx.Response,
        operation: str,
        *,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUError(
                f"Invalid JSON during {operation}",
                http_status=response.status_code,
                batch_id=batch_id,
            ) from exc
        if not isinstance(payload, dict):
            raise MinerUError(
                f"Unexpected JSON shape during {operation}",
                http_status=response.status_code,
                batch_id=batch_id,
            )
        if payload.get("code") != 0:
            raise MinerUError(
                f"MinerU rejected {operation}",
                http_status=response.status_code,
                code=payload.get("code"),
                mineru_message=self._sanitize(payload.get("msg")),
                trace_id=payload.get("trace_id"),
                batch_id=batch_id,
            )
        return payload

    def _sanitize(self, value: object) -> str | None:
        if value is None:
            return None
        sanitized = str(value).replace(self._token, "[REDACTED]")
        return re.sub(r"https?://\S+", "[REDACTED_URL]", sanitized)

    def request_upload_url(self, pdf_path: Path, data_id: str) -> tuple[str, str, str | None]:
        payload = {
            "files": [
                {
                    "name": pdf_path.name,
                    "data_id": data_id,
                    "is_ocr": self.config.is_ocr,
                }
            ],
            "model_version": self.config.model_version,
            "language": self.config.language,
            "enable_table": self.config.enable_table,
            "enable_formula": self.config.enable_formula,
        }
        response = self._request_with_retry(
            self._api_client,
            "POST",
            "/file-urls/batch",
            endpoint_label="upload URL request",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        body = self._checked_json(response, "upload URL request")
        data = body.get("data")
        if not isinstance(data, dict):
            raise MinerUError(
                "Upload URL response has no data object",
                http_status=response.status_code,
                code=body.get("code"),
                mineru_message=self._sanitize(body.get("msg")),
                trace_id=body.get("trace_id"),
            )
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls")
        if not isinstance(batch_id, str) or not batch_id:
            raise MinerUError(
                "Upload URL response has no batch_id",
                http_status=response.status_code,
                code=body.get("code"),
                mineru_message=self._sanitize(body.get("msg")),
                trace_id=body.get("trace_id"),
            )
        if not isinstance(file_urls, list) or len(file_urls) != 1 or not file_urls[0]:
            raise MinerUError(
                "Upload URL response must contain exactly one file URL",
                http_status=response.status_code,
                code=body.get("code"),
                mineru_message=self._sanitize(body.get("msg")),
                trace_id=body.get("trace_id"),
                batch_id=batch_id,
            )
        return batch_id, str(file_urls[0]), body.get("trace_id")

    def upload_pdf(
        self,
        pdf_path: Path,
        upload_url: str,
        batch_id: str,
        trace_id: str | None = None,
    ) -> None:
        if not pdf_path.is_file():
            raise FileNotFoundError(pdf_path)
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                # Reopen for every attempt so a retry always starts at byte zero.
                with pdf_path.open("rb") as source:
                    response = self._transfer_client.put(upload_url, content=source)
                if response.status_code in {401, 403}:
                    raise MinerUError(
                        "Signed PDF upload was rejected",
                        http_status=response.status_code,
                        batch_id=batch_id,
                        trace_id=trace_id,
                    )
                if response.status_code in RETRYABLE_HTTP_STATUSES:
                    if attempt < self.config.max_retries:
                        response.close()
                        continue
                if not response.is_success:
                    raise MinerUError(
                        "HTTP failure during signed PDF upload",
                        http_status=response.status_code,
                        batch_id=batch_id,
                        trace_id=trace_id,
                    )
                return
            except MinerUError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    continue
                raise MinerUError(
                    "Network failure during signed PDF upload after limited retries",
                    batch_id=batch_id,
                    trace_id=trace_id,
                ) from exc
            except OSError as exc:
                raise MinerUError(
                    "Cannot stream local PDF for upload", batch_id=batch_id
                ) from exc
        raise MinerUError(
            "Signed PDF upload failed", batch_id=batch_id, trace_id=trace_id
        ) from last_error

    @staticmethod
    def _result_records(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("extract_result", "extract_results", "results", "files"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if "state" in data:
                return [data]
        return []

    def poll_result(
        self,
        batch_id: str,
        *,
        initial_trace_id: str | None = None,
    ) -> MinerUResult:
        deadline = self._monotonic() + self.config.poll_timeout_seconds
        trace_id = initial_trace_id
        while True:
            response = self._request_with_retry(
                self._api_client,
                "GET",
                f"/extract-results/batch/{batch_id}",
                endpoint_label="batch result polling",
                batch_id=batch_id,
                trace_id=trace_id,
            )
            body = self._checked_json(response, "batch result polling", batch_id=batch_id)
            trace_id = body.get("trace_id") or trace_id
            records = self._result_records(body.get("data"))
            if len(records) != 1:
                raise MinerUError(
                    "Batch result must contain exactly one file record",
                    http_status=response.status_code,
                    code=body.get("code"),
                    mineru_message=self._sanitize(body.get("msg")),
                    trace_id=trace_id,
                    batch_id=batch_id,
                )
            record = records[0]
            state = record.get("state")
            if state not in POLLING_STATES | TERMINAL_STATES:
                raise MinerUError(
                    "MinerU returned an unknown parsing state",
                    http_status=response.status_code,
                    code=body.get("code"),
                    mineru_message=self._sanitize(body.get("msg")),
                    trace_id=trace_id,
                    batch_id=batch_id,
                    state=str(state),
                )
            if state == "failed":
                raise MinerUError(
                    "MinerU parsing failed",
                    http_status=response.status_code,
                    code=body.get("code"),
                    mineru_message=self._sanitize(record.get("err_msg")),
                    trace_id=trace_id,
                    batch_id=batch_id,
                    state=state,
                )
            if state == "done":
                full_zip_url = record.get("full_zip_url")
                if not isinstance(full_zip_url, str) or not full_zip_url:
                    raise MinerUError(
                        "Completed MinerU result has no full_zip_url",
                        http_status=response.status_code,
                        code=body.get("code"),
                        mineru_message=self._sanitize(body.get("msg")),
                        trace_id=trace_id,
                        batch_id=batch_id,
                        state=state,
                    )
                return MinerUResult(batch_id, trace_id, full_zip_url)
            if self._monotonic() >= deadline:
                raise MinerUTimeoutError(
                    "MinerU polling timed out",
                    http_status=response.status_code,
                    code=body.get("code"),
                    mineru_message=self._sanitize(body.get("msg")),
                    trace_id=trace_id,
                    batch_id=batch_id,
                    state=state,
                )
            self._sleep(self.config.poll_interval_seconds)

    def download_zip(
        self,
        full_zip_url: str,
        destination: Path,
        batch_id: str,
        trace_id: str | None = None,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with self._transfer_client.stream("GET", full_zip_url) as response:
                    if response.status_code in {401, 403}:
                        raise MinerUError(
                            "Authentication failed during result ZIP download",
                            http_status=response.status_code,
                            batch_id=batch_id,
                            trace_id=trace_id,
                        )
                    if response.status_code in RETRYABLE_HTTP_STATUSES:
                        if attempt < self.config.max_retries:
                            continue
                    if not response.is_success:
                        raise MinerUError(
                            "HTTP failure during result ZIP download",
                            http_status=response.status_code,
                            batch_id=batch_id,
                            trace_id=trace_id,
                        )
                    with destination.open("wb") as output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                    return
            except MinerUError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    continue
                raise MinerUError(
                    "Network failure during result ZIP download after limited retries",
                    batch_id=batch_id,
                    trace_id=trace_id,
                ) from exc
            except OSError as exc:
                raise MinerUError(
                    "Cannot save downloaded result ZIP", batch_id=batch_id
                ) from exc
        raise MinerUError(
            "Result ZIP download failed", batch_id=batch_id, trace_id=trace_id
        ) from last_error

    def parse_to_raw(self, pdf_path: Path, raw_directory: Path, data_id: str) -> None:
        """Run the fixed local-file flow and materialize the strict raw mapping."""

        batch_id, upload_url, trace_id = self.request_upload_url(pdf_path, data_id)
        LOGGER.info("MinerU upload URL acquired for batch_id=%s", batch_id)
        self.upload_pdf(pdf_path, upload_url, batch_id, trace_id)
        result = self.poll_result(batch_id, initial_trace_id=trace_id)
        with tempfile.TemporaryDirectory(prefix="mineru-result-") as temporary:
            temp_root = Path(temporary)
            zip_path = temp_root / "result.zip"
            extracted = temp_root / "extracted"
            self.download_zip(result.full_zip_url, zip_path, batch_id, result.trace_id)
            safe_extract_zip(zip_path, extracted)
            map_raw_artifacts(extracted, raw_directory)
        LOGGER.info("MinerU raw artifacts saved for batch_id=%s", batch_id)
