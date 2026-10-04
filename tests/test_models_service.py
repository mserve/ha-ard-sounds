"""
Recorded catalog normalization, audio selection, and route edge cases.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.ard_sounds.api import (
    ArdSoundsNotFoundError,
    ArdSoundsResponseError,
)
from custom_components.ard_sounds.classes import ArdSoundsRequestCache, ArdSoundsService
from custom_components.ard_sounds.models import (
    Route,
    audio_candidates,
    normalize_url,
    parse_date,
)

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

PAGE_ITEMS = 2


@pytest.mark.parametrize(
    "route",
    [
        Route(),
        Route("radio"),
        Route("show", "urn:ard:show:abc/a%?", "cursor/+="),
        Route("episode", "urn:ard:publication:abc"),
        Route("search", "Wissen & Natur", "20"),
    ],
)
def test_identifier_round_trip(route: Route) -> None:
    """Internal IDs retain escaped URNs, reserved characters, and slashes."""
    assert Route.parse(route.identifier) == route
    assert Route.parse(route.internal_id) == route


@pytest.mark.parametrize(
    "identifier",
    [
        "/",
        "show/",
        "unknown/a",
        "episode/a/extra",
        "stream/%zz",
        "stream/%FF",
        "stream/%00",
        "show/a/b/c",
        "search/test/-1",
        "search/test/10001",
    ],
)
def test_invalid_identifier(identifier: str) -> None:
    """Invalid selections cannot escape their route into another folder."""
    with pytest.raises((ValueError, UnicodeError)):
        Route.parse(identifier)


def test_optional_metadata_and_availability() -> None:
    """Normalize protocol-relative audio and exclude expired distributions."""
    now = datetime(2026, 10, 4, tzinfo=UTC)
    candidates = audio_candidates(
        {
            "audios": [
                {"url": "//audio.example/a.mp3", "mimeType": "audio/mp3"},
                {
                    "url": "https://audio.example/a.m3u8",
                    "mimeType": "application/x-mpegurl",
                },
            ],
            "audioList": [
                {
                    "href": "//audio.example/a.mp3",
                    "distributionType": "onDemand",
                    "availableTo": "2026-10-03T00:00:00Z",
                }
            ],
        }
    )
    assert candidates[0].mime_type == "audio/mpeg"
    assert not candidates[0].is_available(now)
    assert candidates[1].mime_type == "application/vnd.apple.mpegurl"
    assert candidates[1].is_available(now)
    assert audio_candidates({}) == ()
    assert normalize_url("crid://not-an-audio-url") is None
    assert parse_date("bad") is None
    assert parse_date("2026-10-04T00:00:00").tzinfo is UTC


async def test_show_and_episode_pages(service: ArdSoundsService) -> None:
    """Recorded queries normalize show, episode, and onDemand selection."""
    shows = await service.shows()
    assert len(shows.items) == PAGE_ITEMS
    assert shows.next_cursor
    next_page = await service.shows(shows.next_cursor)
    assert {s.id for s in shows.items}.isdisjoint(s.id for s in next_page.items)
    show, episodes = await service.episodes("62520168")
    assert show.title == "1000 Antworten"
    assert len(episodes.items) == PAGE_ITEMS
    assert episodes.items[0].audios[0].distribution == "onDemand"
    assert episodes.items[0].published_at.tzinfo is UTC
    assert episodes.items[0].image_url
    await service.cache.close()


async def test_search_offsets(service: ArdSoundsService, client: AsyncMock) -> None:
    """Search continuation uses offsets rather than the null API cursor."""
    first = await service.search(" Wissen ")
    assert first.next_cursor == "2"
    second = await service.search("Wissen", int(first.next_cursor))
    assert {s.id for s in first.items}.isdisjoint(s.id for s in second.items)
    client.request.assert_awaited_with(
        "Search", {"query": "Wissen", "limit": 2, "offset": 2}
    )
    await service.cache.close()


async def test_distinct_ids_and_missing_fields(
    client: AsyncMock, api_data: dict[str, Any]
) -> None:
    """Duplicate titles survive deduplication; missing optional fields are safe."""
    connection = deepcopy(api_data["shows"])
    nodes = connection["programSets"]["nodes"]
    nodes[1]["title"] = nodes[0]["title"]
    nodes.append(nodes[0])
    nodes[0].pop("image")
    nodes[0].pop("publicationService")
    client.request.return_value = connection
    client.request.side_effect = None
    service = ArdSoundsService(client, ArdSoundsRequestCache(client))
    page = await service.shows()
    assert len(page.items) == PAGE_ITEMS
    assert page.items[0].title == page.items[1].title
    assert page.items[0].image_url is None
    await service.cache.close()


@pytest.mark.parametrize(
    "connection",
    [
        None,
        {"nodes": None},
        {"nodes": [], "pageInfo": ["invalid"]},
        {"nodes": [{"id": "1"}], "pageInfo": {"endCursor": 42}},
    ],
)
async def test_malformed_connection(
    connection: Any, service: ArdSoundsService, client: AsyncMock
) -> None:
    """Malformed pages raise a typed error instead of appearing as empty data."""
    client.request.side_effect = None
    client.request.return_value = {"programSets": connection}
    with pytest.raises(ArdSoundsResponseError):
        await service.shows()
    await service.cache.close()


async def test_empty_and_repeated_pages(
    service: ArdSoundsService, client: AsyncMock
) -> None:
    """Empty pages terminate and repeated cursors fail explicitly."""
    client.request.side_effect = None
    client.request.return_value = {
        "programSets": {
            "nodes": [],
            "totalCount": 100,
            "pageInfo": {"hasNextPage": True, "endCursor": "a"},
        }
    }
    assert not (await service.shows()).has_next
    service.cache.invalidate()
    client.request.return_value = {
        "programSets": {
            "nodes": [{"id": "1"}],
            "pageInfo": {"hasNextPage": True, "endCursor": "a"},
        }
    }
    with pytest.raises(ArdSoundsResponseError):
        await service.shows("a")
    await service.cache.close()


async def test_unavailable_episodes(
    service: ArdSoundsService, api_data: dict[str, Any], client: AsyncMock
) -> None:
    """No-audio and unpublished episodes are omitted while paging continues."""
    data = deepcopy(api_data["episodes"])
    nodes = data["show"]["items"]["nodes"]
    nodes[0]["audios"] = []
    nodes[0]["audioList"] = []
    nodes[1]["isPublished"] = False
    client.request.side_effect = None
    client.request.return_value = data
    _show, page = await service.episodes("62520168")
    assert page.items == ()
    assert page.next_cursor
    await service.cache.close()


async def test_resolution_refresh_and_alternate(
    service: ArdSoundsService, client: AsyncMock, api_data: dict[str, Any]
) -> None:
    """Playback bypasses cache and falls back when the preferred URL has gone."""
    node = api_data["episodes"]["show"]["items"]["nodes"][0]
    client.request.side_effect = None
    client.request.return_value = {"item": node}
    client.resolve_audio.side_effect = [
        ArdSoundsNotFoundError,
        audio_candidates(node)[1],
    ]
    resolved = await service.resolve("episode", node["coreId"])
    assert resolved.url == node["audios"][0]["url"]
    client.request.assert_awaited_with("Episode", {"id": node["coreId"]})
    await service.cache.close()


async def test_null_details_and_reload_errors(
    service: ArdSoundsService, client: AsyncMock
) -> None:
    """Removed content and partially failed reloads remain visible failures."""
    client.request.side_effect = None
    client.request.return_value = {"item": None}
    with pytest.raises(ArdSoundsNotFoundError):
        await service.resolve("episode", "gone")
    client.request.side_effect = ArdSoundsResponseError
    with pytest.raises(ArdSoundsResponseError):
        await service.reload()
    await service.cache.close()


async def test_regional_variants(
    service: ArdSoundsService, client: AsyncMock, api_data: dict[str, Any]
) -> None:
    """Variants with matching station IDs survive even when titles are identical."""
    payload = deepcopy(api_data["stations"])
    connection = payload["permanentLivestreams"]
    first = connection["nodes"][0]
    variant = deepcopy(first)
    variant["id"] = "regional-variant"
    variant["coreId"] = "urn:ard:permanent-livestream:regional"
    connection["nodes"] = [first, variant, first]
    connection["pageInfo"]["hasNextPage"] = False
    client.request.side_effect = None
    client.request.return_value = payload
    variants = await service.stations()
    assert len(variants) == PAGE_ITEMS
    assert variants[0].station.id == variants[1].station.id
    assert variants[0].id != variants[1].id
    await service.cache.close()


async def test_expired_episode(
    service: ArdSoundsService, client: AsyncMock, api_data: dict[str, Any]
) -> None:
    """Expired distributions are hidden and rejected at fresh playback resolution."""
    payload = deepcopy(api_data["episodes"])
    node = payload["show"]["items"]["nodes"][0]
    for binary in node["audioList"]:
        binary["availableTo"] = "2000-01-01T00:00:00Z"
    payload["show"]["items"]["nodes"] = [node]
    client.request.side_effect = None
    client.request.return_value = payload
    _show, page = await service.episodes("62520168")
    assert not page.items
    client.request.return_value = {"item": node}
    with pytest.raises(ArdSoundsNotFoundError):
        await service.resolve("episode", node["id"])
    await service.cache.close()
