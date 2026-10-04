"""Async, bounded transport for the public ARD GraphQL API."""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError, ClientTimeout

from custom_components.ard_sounds.const import API_URL, REQUEST_TIMEOUT
from custom_components.ard_sounds.models import (
    AudioCandidate,
    normalize_mime,
    normalize_url,
)

from .queries import QUERIES

if TYPE_CHECKING:
    from aiohttp import ClientSession


class ArdSoundsError(Exception):
    """Base error for transport, API, and unavailable-content failures."""


class ArdSoundsConnectionError(ArdSoundsError):
    """The endpoint cannot currently be reached."""


class ArdSoundsResponseError(ArdSoundsError):
    """An HTTP or GraphQL response violates the query contract."""


class ArdSoundsRateLimitError(ArdSoundsError):
    """The server asks the caller to wait before trying again."""


class ArdSoundsNotFoundError(ArdSoundsError):
    """A selected item has been removed or has no playable audio."""


class ArdSoundsGraphQLClient:
    """Use an injected session without taking ownership of its lifecycle."""

    def __init__(self, session: ClientSession) -> None:
        """Initialize transport with explicit per-request timeouts."""
        self.session = session
        self.timeout = ClientTimeout(total=REQUEST_TIMEOUT)
        self._rate_limit_until = 0.0

    async def request(
        self, operation: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one named operation, retrying transient failures only once."""
        for attempt in range(2):
            self._check_rate_limit()
            try:
                return await self._request(operation, variables)
            except ArdSoundsConnectionError:
                if attempt:
                    raise
                await asyncio.sleep(0.5)
        msg = "Retry limit reached"
        raise ArdSoundsConnectionError(msg)

    async def _request(
        self, operation: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate HTTP, JSON, and GraphQL errors, including partial data."""
        try:
            async with self.session.post(
                API_URL,
                json={
                    "operationName": operation,
                    "query": QUERIES[operation],
                    "variables": variables,
                },
                timeout=self.timeout,
            ) as response:
                if response.status == HTTPStatus.TOO_MANY_REQUESTS:
                    retry_after = response.headers.get("Retry-After", "unspecified")
                    self._rate_limit_until = time.monotonic() + self._retry_delay(
                        retry_after
                    )
                    msg = f"ARD rate limit; retry after {retry_after}"
                    raise ArdSoundsRateLimitError(msg)
                if response.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    msg = f"ARD HTTP {response.status}"
                    raise ArdSoundsConnectionError(msg)
                if response.status != HTTPStatus.OK:
                    msg = f"ARD HTTP {response.status}"
                    raise ArdSoundsResponseError(msg)
                try:
                    payload = await response.json()
                except (ValueError, ClientError) as err:
                    msg = "Invalid ARD JSON response"
                    raise ArdSoundsResponseError(msg) from err
        except (TimeoutError, ClientError) as err:
            msg = "ARD request failed"
            raise ArdSoundsConnectionError(msg) from err
        if (
            not isinstance(payload, dict)
            or payload.get("errors")
            or not isinstance(payload.get("data"), dict)
        ):
            msg = "Invalid or partial ARD GraphQL response"
            raise ArdSoundsResponseError(msg)
        return payload["data"]

    def _check_rate_limit(self) -> None:
        """Reject new requests until the server's Retry-After window elapses."""
        if time.monotonic() < self._rate_limit_until:
            msg = "ARD rate limit is still active"
            raise ArdSoundsRateLimitError(msg)

    @staticmethod
    def _retry_delay(value: str) -> float:
        """Support both Retry-After formats, with a safe default for invalid headers."""
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (
                    parsedate_to_datetime(value) - datetime.now(UTC)
                ).total_seconds()
            except ValueError, TypeError:
                delay = 30.0
        return max(delay, 1.0) if math.isfinite(delay) else 30.0

    async def resolve_audio(self, candidate: AudioCandidate) -> AudioCandidate:
        """Follow bounded redirects and close the stream without downloading audio."""
        try:
            async with self.session.get(
                candidate.url,
                timeout=self.timeout,
                headers={"Range": "bytes=0-0"},
                allow_redirects=True,
                max_redirects=5,
            ) as response:
                if response.status not in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
                    msg = f"Audio HTTP {response.status}"
                    raise ArdSoundsNotFoundError(msg)
                url = normalize_url(str(response.url))
                mime = normalize_mime(response.headers.get("Content-Type", ""))
                if mime in ("", "application/octet-stream", "binary/octet-stream"):
                    mime = candidate.mime_type
                if not url or not (
                    mime.startswith("audio/")
                    or mime in ("application/ogg", "application/vnd.apple.mpegurl")
                ):
                    msg = "Unsupported audio content type"
                    raise ArdSoundsNotFoundError(msg)
                return AudioCandidate(url, mime)
        except (TimeoutError, ClientError) as err:
            msg = "Audio resolution failed"
            raise ArdSoundsConnectionError(msg) from err
