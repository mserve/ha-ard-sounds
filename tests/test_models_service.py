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
from unittest.mock import patch

import pytest

from custom_components.ard_sounds.api import (
    ArdSoundsNotFoundError,
    ArdSoundsResponseError,
)
from custom_components.ard_sounds.classes import ArdSoundsRequestCache, ArdSoundsService
from custom_components.ard_sounds.const import MAX_PAGE_SIZE
from custom_components.ard_sounds.models import (
    Route,
    audio_candidates,
    normalize_url,
    parse_date,
    podcast_initial,
)

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

PAGE_ITEMS = 2


def podcast_node(
    identifier: str, title: str, *, has_episodes: bool = True
) -> dict[str, Any]:
    """Create a show with an explicit published-episode presence response."""
    return {
        "id": identifier,
        "title": title,
        "availableEpisodes": {"nodes": [{"id": "episode"}] if has_episodes else []},
    }


@pytest.mark.parametrize(
    "route",
    [
        Route(),
        Route("radio"),
        Route("starred"),
        Route("podcasts", "letter", "A"),
        Route("podcasts", "letter", "#"),
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
        "podcasts/letter",
        "podcasts/letter/AA",
        "podcasts/letter/%C3%84",
        "podcasts/letter/a",
        "podcasts/unknown/A",
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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("ARD Wissen", "A"),
        ("  ard Wissen", "A"),
        ("Ärzte im Gespräch", "A"),
        ("Ökologie", "O"),
        ("Über Wissen", "U"),
        ("Échos", "E"),
        ("ßound", "S"),
        ("1000 Antworten", "#"),
        ("!Wissen", "#"),
        ("世界", "#"),
        ("", "#"),
    ],
)
def test_podcast_initial(title: str, expected: str) -> None:
    """Fold case and accents and keep digits, symbols, and other scripts under #."""
    assert podcast_initial(title) == expected


async def test_alphabetical_shows_all_pages(
    service: ArdSoundsService, client: AsyncMock
) -> None:
    """Collect a whole letter across API pages, deduplicating identities and sorting."""
    first = {
        "programSets": {
            "nodes": [
                podcast_node("1", "Azure"),
                podcast_node("2", "Beta"),
                podcast_node("3", "10 Nachrichten"),
                podcast_node("empty", "Absent", has_episodes=False),
            ],
            "pageInfo": {"hasNextPage": True, "endCursor": "next"},
            "totalCount": 999,
        }
    }
    second = {
        "programSets": {
            "nodes": [
                podcast_node("4", "Äpfel"),
                podcast_node("5", "alpha"),
                podcast_node("1", "Azure"),
                podcast_node("6", "alpha"),
                podcast_node("7", "!Wissen"),
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
    }
    client.request.side_effect = [first, second]
    shows = await service.alphabetical_shows("A")
    assert [(show.id, show.title) for show in shows] == [
        ("5", "alpha"),
        ("6", "alpha"),
        ("4", "Äpfel"),
        ("1", "Azure"),
    ]
    client.request.assert_awaited_with(
        "Shows", {"first": MAX_PAGE_SIZE, "after": "next"}
    )
    assert [show.title for show in await service.alphabetical_shows("#")] == [
        "!Wissen",
        "10 Nachrichten",
    ]
    assert [show.title for show in await service.alphabetical_shows("B")] == ["Beta"]
    assert await service.alphabetical_shows("Z") == ()
    expected_calls = 2
    assert client.request.await_count == expected_calls
    await service.cache.close()


async def test_alphabetical_shows_pagination_failure(
    service: ArdSoundsService, client: AsyncMock
) -> None:
    """Do not display a partial letter when a later API page fails."""
    client.request.side_effect = [
        {
            "programSets": {
                "nodes": [podcast_node("1", "Alpha")],
                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
            }
        },
        ArdSoundsResponseError,
    ]
    with pytest.raises(ArdSoundsResponseError):
        await service.alphabetical_shows("A")
    await service.cache.close()


async def test_alphabetical_shows_cursor_cycle(
    service: ArdSoundsService, client: AsyncMock
) -> None:
    """A multi-page cursor cycle cannot cause an unbounded catalog request."""
    client.request.side_effect = [
        {
            "programSets": {
                "nodes": [podcast_node(str(index), "Alpha")],
                "pageInfo": {"hasNextPage": True, "endCursor": cursor},
            }
        }
        for index, cursor in enumerate(("a", "b", "a"))
    ]
    with pytest.raises(ArdSoundsResponseError):
        await service.alphabetical_shows("A")
    await service.cache.close()


async def test_alphabetical_shows_page_bound(
    service: ArdSoundsService, client: AsyncMock
) -> None:
    """The catalog bound reports failure instead of a successful partial list."""
    client.request.side_effect = [
        {
            "programSets": {
                "nodes": [podcast_node(str(index), "Alpha")],
                "pageInfo": {"hasNextPage": True, "endCursor": str(index)},
            }
        }
        for index in range(2)
    ]
    with (
        patch("custom_components.ard_sounds.classes.MAX_SHOW_CATALOG_PAGES", 2),
        pytest.raises(ArdSoundsResponseError),
    ):
        await service.alphabetical_shows("A")
    await service.cache.close()


@pytest.mark.parametrize("operation", ["Shows", "Search"])
@pytest.mark.parametrize("has_episodes", [False, True])
async def test_empty_podcasts_keep_continuation(
    service: ArdSoundsService,
    client: AsyncMock,
    operation: str,
    *,
    has_episodes: bool,
) -> None:
    """Filter empty shows without trusting summary counts or skipping continuation."""
    populated = podcast_node("populated", "Available", has_episodes=has_episodes)
    populated["numberOfElements"] = 0  # ARD can underreport this summary field.
    empty = podcast_node("empty", "Absent", has_episodes=False)
    empty["numberOfElements"] = 99  # The actual episode connection is authoritative.
    connection = {
        "nodes": [populated, empty],
        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
    }
    payload = {"programSets": connection}
    client.request.side_effect = None
    client.request.return_value = (
        payload if operation == "Shows" else {"search": payload}
    )
    page = await service.shows() if operation == "Shows" else await service.search("A")
    assert [show.id for show in page.items] == (["populated"] if has_episodes else [])
    assert page.has_next
    assert page.next_cursor == ("next" if operation == "Shows" else "2")
    await service.cache.close()


async def test_alphabetical_shows_continues_past_empty_shows(
    service: ArdSoundsService, client: AsyncMock
) -> None:
    """A raw page containing only empty shows cannot hide later populated shows."""
    client.request.side_effect = [
        {
            "programSets": {
                "nodes": [podcast_node("empty", "Absent", has_episodes=False)],
                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
            }
        },
        {
            "programSets": {
                "nodes": [podcast_node("populated", "Available")],
                "pageInfo": {"hasNextPage": False},
            }
        },
    ]
    assert [show.id for show in await service.alphabetical_shows("A")] == ["populated"]
    await service.cache.close()


@pytest.mark.parametrize("presence", [None, {}, {"nodes": None}])
async def test_malformed_episode_presence(
    service: ArdSoundsService, client: AsyncMock, presence: Any
) -> None:
    """Malformed episode checks are visible API failures rather than empty shows."""
    node = podcast_node("show", "Available")
    node["availableEpisodes"] = presence
    client.request.side_effect = None
    client.request.return_value = {"programSets": {"nodes": [node]}}
    with pytest.raises(ArdSoundsResponseError, match="Missing ARD connection"):
        await service.shows()
    await service.cache.close()
