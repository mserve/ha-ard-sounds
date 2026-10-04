"""
Metadata expiry, concurrency, cancellation, and reload behavior.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.ard_sounds.api import ArdSoundsResponseError
from custom_components.ard_sounds.classes import ArdSoundsRequestCache


async def test_expiry_pages_and_eviction() -> None:
    """Use an injected clock to test independent pages and LRU eviction."""
    now = [0.0]
    client = AsyncMock()
    client.request.return_value = {"value": 1}
    cache = ArdSoundsRequestCache(client, clock=lambda: now[0], max_entries=2)
    await cache.request("Shows", {"first": 2, "after": None})
    await cache.request("Shows", {"after": None, "first": 2})
    client.request.assert_awaited_once()
    now[0] = 3601
    await cache.request("Shows", {"first": 2, "after": None})
    await cache.request("Shows", {"first": 2, "after": "a"})
    await cache.request("Search", {"query": "Wissen", "offset": 0})
    await cache.request("Shows", {"first": 2, "after": None})
    expected_calls = 5
    assert client.request.await_count == expected_calls
    await cache.close()


async def test_concurrent_misses() -> None:
    """Identical concurrent requests share one successful result."""
    client = AsyncMock()
    client.request.return_value = {"value": 1}
    cache = ArdSoundsRequestCache(client)
    results = await asyncio.gather(*(cache.request("Shows", {}) for _ in range(8)))
    assert results == [{"value": 1}] * 8
    client.request.assert_awaited_once()
    await cache.close()


async def test_failures_not_cached() -> None:
    """A failed request is retried by the next caller."""
    client = AsyncMock()
    client.request.side_effect = [ArdSoundsResponseError, {"value": 2}]
    cache = ArdSoundsRequestCache(client)
    with pytest.raises(ArdSoundsResponseError):
        await cache.request("Shows", {})
    assert await cache.request("Shows", {}) == {"value": 2}
    expected_calls = 2
    assert client.request.await_count == expected_calls
    await cache.close()


async def test_invalidate_inflight() -> None:
    """Old requests cannot repopulate an invalidated cache or replace new data."""
    started, release = asyncio.Event(), asyncio.Event()
    client = AsyncMock()

    async def request(*_args: object) -> dict:
        started.set()
        await release.wait()
        return {"value": 1}

    client.request.side_effect = request
    cache = ArdSoundsRequestCache(client)
    old = asyncio.create_task(cache.request("Shows", {}))
    await started.wait()
    cache.invalidate()
    release.set()
    await old
    assert cache.cached_requests() == []
    await cache.request("Shows", {})
    expected_calls = 2
    assert client.request.await_count == expected_calls
    await cache.close()


async def test_cancel_waiter_and_unload() -> None:
    """Cancelling a waiter preserves shared work; unloading cancels owned tasks."""
    started = asyncio.Event()
    cancelled = asyncio.Event()
    client = AsyncMock()

    async def request(*_args: object) -> dict:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return {}

    client.request.side_effect = request
    cache = ArdSoundsRequestCache(client)
    waiter = asyncio.create_task(cache.request("Shows", {}))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not cancelled.is_set()
    cache.invalidate()
    await cache.close()
    assert cancelled.is_set()
    with pytest.raises(ArdSoundsResponseError):
        await cache.request("Shows", {})


async def test_new_generation_wins() -> None:
    """An older in-flight response cannot overwrite a successful reload request."""
    started, release = asyncio.Event(), asyncio.Event()
    client = AsyncMock()
    requests = 0

    async def request(*_args: object) -> dict:
        nonlocal requests
        requests += 1
        if requests == 1:
            started.set()
            await release.wait()
            return {"generation": "old"}
        return {"generation": "new"}

    client.request.side_effect = request
    cache = ArdSoundsRequestCache(client)
    old = asyncio.create_task(cache.request("Shows", {}))
    await started.wait()
    cache.invalidate()
    assert await cache.request("Shows", {}) == {"generation": "new"}
    release.set()
    assert await old == {"generation": "old"}
    assert await cache.request("Shows", {}) == {"generation": "new"}
    expected_requests = 2
    assert requests == expected_requests
    await cache.close()
