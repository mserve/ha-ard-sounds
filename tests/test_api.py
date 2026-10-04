"""
Transport failures must remain typed and bounded without real HTTP calls.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientConnectionError

from custom_components.ard_sounds.api import (
    ArdSoundsConnectionError,
    ArdSoundsGraphQLClient,
    ArdSoundsNotFoundError,
    ArdSoundsRateLimitError,
    ArdSoundsResponseError,
)
from custom_components.ard_sounds.api.queries import QUERIES
from custom_components.ard_sounds.models import AudioCandidate


def response(payload: Any = None, status: int = 200, **headers: str) -> MagicMock:
    """Create an aiohttp-style async response context."""
    result = MagicMock()
    result.status = status
    result.headers = headers
    result.url = "https://audio.example/final.mp3"
    result.json = AsyncMock(return_value=payload)
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    return result


async def test_named_operation() -> None:
    """Send variables separately and retain the HA-owned session."""
    session = MagicMock()
    session.post.return_value = response({"data": {"show": None}})
    client = ArdSoundsGraphQLClient(session)
    assert await client.request("Show", {"id": "urn:ard:show:a"}) == {"show": None}
    body = session.post.call_args.kwargs["json"]
    assert body == {
        "operationName": "Show",
        "query": QUERIES["Show"],
        "variables": {"id": "urn:ard:show:a"},
    }
    session.close.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"data": None},
        {"errors": [{"message": "invalid"}]},
        {"data": {"show": None}, "errors": [{"message": "partial"}]},
    ],
)
async def test_invalid_graphql(payload: Any) -> None:
    """Reject GraphQL errors even with HTTP 200 and partial data."""
    session = MagicMock()
    session.post.return_value = response(payload)
    with pytest.raises(ArdSoundsResponseError):
        await ArdSoundsGraphQLClient(session).request("Show", {"id": "1"})
    session.post.assert_called_once()


async def test_invalid_json() -> None:
    """Do not retry malformed JSON as a transient transport failure."""
    session = MagicMock()
    item = response()
    item.json.side_effect = ValueError
    session.post.return_value = item
    with pytest.raises(ArdSoundsResponseError):
        await ArdSoundsGraphQLClient(session).request("Show", {"id": "1"})
    session.post.assert_called_once()


@pytest.mark.parametrize("failure", [TimeoutError, ClientConnectionError])
async def test_bounded_transport_retry(failure: type[Exception]) -> None:
    """Timeouts and connection failures retry only once, without test sleeps."""
    session = MagicMock()
    session.post.side_effect = failure
    with (
        patch(
            "custom_components.ard_sounds.api.api_client.asyncio.sleep",
            new_callable=AsyncMock,
        ),
        pytest.raises(ArdSoundsConnectionError),
    ):
        await ArdSoundsGraphQLClient(session).request("Show", {"id": "1"})
    expected_attempts = 2
    assert session.post.call_count == expected_attempts


@pytest.mark.parametrize(
    ("status", "error", "calls"),
    [
        (400, ArdSoundsResponseError, 1),
        (429, ArdSoundsRateLimitError, 1),
        (503, ArdSoundsConnectionError, 2),
    ],
)
async def test_http_failures(status: int, error: type[Exception], calls: int) -> None:
    """Honor rate limits without immediately retrying, and bound server retries."""
    session = MagicMock()
    session.post.return_value = response(status=status, **{"Retry-After": "30"})
    with (
        patch(
            "custom_components.ard_sounds.api.api_client.asyncio.sleep",
            new_callable=AsyncMock,
        ),
        pytest.raises(error),
    ):
        await ArdSoundsGraphQLClient(session).request("Show", {"id": "1"})
    assert session.post.call_count == calls


async def test_redirect_resolution() -> None:
    """Return the response's final URL and MIME without consuming its body."""
    session = MagicMock()
    item = response(status=206, **{"Content-Type": "audio/mp3; charset=utf-8"})
    session.get.return_value = item
    audio = await ArdSoundsGraphQLClient(session).resolve_audio(
        AudioCandidate("https://audio.example/redirect", "audio/mpeg")
    )
    assert audio.url == "https://audio.example/final.mp3"
    assert audio.mime_type == "audio/mpeg"
    redirect_limit = 5
    assert session.get.call_args.kwargs["max_redirects"] == redirect_limit
    item.read.assert_not_called()
    item.__aexit__.assert_awaited_once()


@pytest.mark.parametrize(("status", "mime"), [(404, "audio/mpeg"), (200, "text/html")])
async def test_unplayable_response(status: int, mime: str) -> None:
    """Removed URLs and HTML landing pages cannot be treated as playable audio."""
    session = MagicMock()
    session.get.return_value = response(status=status, **{"Content-Type": mime})
    with pytest.raises(ArdSoundsNotFoundError):
        await ArdSoundsGraphQLClient(session).resolve_audio(
            AudioCandidate("https://audio.example/a", "audio/mpeg")
        )


async def test_rate_limit_window() -> None:
    """A Retry-After window blocks later calls without sleeping or extra HTTP."""
    session = MagicMock()
    session.post.return_value = response(status=429, **{"Retry-After": "30"})
    client = ArdSoundsGraphQLClient(session)
    with (
        patch(
            "custom_components.ard_sounds.api.api_client.time.monotonic",
            return_value=100,
        ),
        pytest.raises(ArdSoundsRateLimitError),
    ):
        await client.request("Show", {"id": "1"})
    with (
        patch(
            "custom_components.ard_sounds.api.api_client.time.monotonic",
            return_value=110,
        ),
        pytest.raises(ArdSoundsRateLimitError),
    ):
        await client.request("Show", {"id": "2"})
    session.post.assert_called_once()
    session.post.return_value = response({"data": {"show": None}})
    with patch(
        "custom_components.ard_sounds.api.api_client.time.monotonic", return_value=131
    ):
        assert await client.request("Show", {"id": "1"}) == {"show": None}


async def test_hls_resolution() -> None:
    """A redirecting HLS playlist remains HLS instead of being mislabeled MP3."""
    session = MagicMock()
    playlist = response(**{"Content-Type": "application/x-mpegurl"})
    playlist.url = "https://audio.example/final.m3u8"
    session.get.return_value = playlist
    audio = await ArdSoundsGraphQLClient(session).resolve_audio(
        AudioCandidate("https://audio.example/live", "application/vnd.apple.mpegurl")
    )
    assert audio.mime_type == "application/vnd.apple.mpegurl"
    assert audio.url.endswith(".m3u8")
