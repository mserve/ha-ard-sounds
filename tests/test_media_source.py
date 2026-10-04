"""
Media browsing uses valid HA IDs, current runtime data, and typed errors.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import (
    BrowseError,
    MediaClass,
    SearchMediaQuery,
)
from homeassistant.components.media_source import MediaSourceItem, Unresolvable
from homeassistant.config_entries import ConfigEntryState

from custom_components.ard_sounds.api import (
    ArdSoundsConnectionError,
    ArdSoundsResponseError,
)
from custom_components.ard_sounds.classes import StarredPodcasts
from custom_components.ard_sounds.const import DOMAIN, PODCAST_INITIALS
from custom_components.ard_sounds.media_source import ArdSoundsMediaSource
from custom_components.ard_sounds.models import (
    AudioCandidate,
    Episode,
    Page,
    Podcast,
    Route,
    Stream,
)

if TYPE_CHECKING:
    from custom_components.ard_sounds.classes import ArdSoundsService

PAGE_ITEMS = 2


@pytest.fixture
def source(service: ArdSoundsService) -> ArdSoundsMediaSource:
    """Attach a fake loaded entry through the public config-entry interface."""
    hass = MagicMock()
    hass.config.language = "en"
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(
            service=service,
            stars=StarredPodcasts(hass, "test"),
            sonos_compatibility=False,
        ),
    )
    hass.config_entries.async_entries.return_value = [entry]
    return ArdSoundsMediaSource(hass)


def item(identifier: str = "") -> MediaSourceItem:
    """Build a media-source selection for isolated platform tests."""
    return MediaSourceItem(MagicMock(), DOMAIN, identifier, None)


async def test_root_and_podcast_letters(
    source: ArdSoundsMediaSource, service: ArdSoundsService
) -> None:
    """Browse letter folders, complete sorted shows, and bounded episode pages."""
    with patch(
        "custom_components.ard_sounds.media_source.async_get_translations",
        new_callable=AsyncMock,
        return_value={},
    ):
        root = await source.async_browse_media(item())
        assert root.can_search
        assert [child.media_content_id for child in root.children] == [
            "media-source://ard_sounds/starred",
            "media-source://ard_sounds/radio",
            "media-source://ard_sounds/podcasts",
        ]
        podcasts = await source.async_browse_media(item("podcasts"))
        assert podcasts.can_search
        assert [child.title for child in podcasts.children] == list(PODCAST_INITIALS)
        assert all(not child.can_search for child in podcasts.children)
        letter = await source.async_browse_media(item("podcasts/letter/P"))
        assert [child.title for child in letter.children] == [
            "Plattdeutsche Nachrichten"
        ]
        assert letter.children[0].media_class is MediaClass.PODCAST
        assert not letter.children[0].can_play
        assert all(child.can_expand for child in letter.children)
        assert not letter.can_search
        empty = await source.async_browse_media(item("podcasts/letter/B"))
        assert not empty.children  # Brunners Welt has no published episodes.
        for child in podcasts.children:
            uri = MediaSourceItem.from_uri(source.hass, child.media_content_id, None)
            assert Route.parse(uri.identifier).page == child.title
        episode_page = await source.async_browse_media(item("show/62520168"))
        assert episode_page.children[0].can_play
        assert not episode_page.children[0].can_expand
    await service.cache.close()


@pytest.mark.parametrize(
    ("mime_type", "expected"),
    [
        ("audio/mpeg", "audio/mpeg"),
        ("audio/aac", "audio/aac"),
        ("audio/mp4", "audio/mp4"),
        ("application/ogg", "audio/ogg"),
        ("application/vnd.apple.mpegurl", "audio/x-mpegurl"),
    ],
)
@pytest.mark.parametrize("content_class", [Episode, Stream])
async def test_sonos_content_types(
    source: ArdSoundsMediaSource,
    mime_type: str,
    expected: str,
    content_class: type[Episode | Stream],
) -> None:
    """Expose Sonos-compatible MIME types for both episodes and live streams."""
    runtime = source.hass.config_entries.async_entries.return_value[0].runtime_data
    runtime.sonos_compatibility = True
    content = content_class(
        "audio",
        "Audio",
        audios=(AudioCandidate("https://audio.example/play", mime_type),),
    )
    fake = AsyncMock()
    fake.episodes.return_value = (Podcast("show", "Show"), Page((content,)))
    fake.stations.return_value = (content,)
    fake.alphabetical_shows.return_value = (Podcast("show", "Show"),)
    runtime.service = fake
    route = "show/show" if content_class is Episode else "station/unknown"
    with patch(
        "custom_components.ard_sounds.media_source.async_get_translations",
        return_value={},
    ):
        page = await source.async_browse_media(item(route))
        browsed = page.children[0]
        assert browsed.media_content_type == expected
        assert browsed.can_play
        assert not browsed.can_expand
        page = await source.async_browse_media(item("podcasts/letter/S"))
        podcast = page.children[0]
        assert podcast.media_content_type == "podcast"
        assert not podcast.can_play
        runtime.sonos_compatibility = False
        page = await source.async_browse_media(item(route))
        assert page.children[0].media_content_type == "music"


async def test_radio_groups(
    source: ArdSoundsMediaSource, service: ArdSoundsService
) -> None:
    """Follow broadcaster -> station -> distinct playable variants."""
    with patch(
        "custom_components.ard_sounds.media_source.async_get_translations",
        new_callable=AsyncMock,
        return_value={},
    ):
        radio = await source.async_browse_media(item("radio"))
        broadcaster = await source.async_browse_media(
            item(radio.children[0].identifier)
        )
        station = await source.async_browse_media(
            item(broadcaster.children[0].identifier)
        )
        assert station.children[0].can_play
        assert Route.parse(station.children[0].identifier).kind == "stream"
    await service.cache.close()


async def test_search_filters(
    source: ArdSoundsMediaSource, service: ArdSoundsService
) -> None:
    """Honor search class filters and supported browsing locations."""
    with patch(
        "custom_components.ard_sounds.media_source.async_get_translations",
        new_callable=AsyncMock,
        return_value={},
    ):
        result = await source.async_search_media(
            item(),
            SearchMediaQuery(
                search_query="Wissen", media_filter_classes=[MediaClass.PODCAST]
            ),
        )
        assert len(result.result) == PAGE_ITEMS
        assert all(show.media_class is MediaClass.PODCAST for show in result.result)
        empty = await source.async_search_media(
            item(),
            SearchMediaQuery(
                search_query="Wissen", media_filter_classes=[MediaClass.VIDEO]
            ),
        )
        assert not empty.result
        with pytest.raises(BrowseError):
            await source.async_search_media(
                item("radio"), SearchMediaQuery(search_query="Wissen")
            )
    await service.cache.close()


async def test_resolution_and_invalid_routes(source: ArdSoundsMediaSource) -> None:
    """Return actual final audio and reject browse folders as playback targets."""
    fake = AsyncMock()
    fake.resolve.return_value = AudioCandidate(
        "https://audio.example/final.mp3", "audio/mpeg"
    )
    source.hass.config_entries.async_entries.return_value[0].runtime_data.service = fake
    result = await source.async_resolve_media(
        item(Route("stream", "urn:ard:permanent-livestream:x").identifier)
    )
    assert result.url == "https://audio.example/final.mp3"
    assert result.mime_type == "audio/mpeg"
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(item("radio"))
    with pytest.raises(BrowseError):
        await source.async_browse_media(item("unknown/id"))
    fake.resolve.side_effect = ArdSoundsResponseError
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(item("episode/gone"))


async def test_reload_runtime_and_unloaded(source: ArdSoundsMediaSource) -> None:
    """Reuse the platform safely across reload, remove, and re-add."""
    fake = AsyncMock()
    fake.resolve.return_value = AudioCandidate(
        "https://audio.example/new.mp3", "audio/mpeg"
    )
    source.hass.config_entries.async_entries.return_value = []
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(item("episode/id"))
    source.hass.config_entries.async_entries.return_value = [
        SimpleNamespace(
            state=ConfigEntryState.LOADED, runtime_data=SimpleNamespace(service=fake)
        )
    ]
    assert (
        await source.async_resolve_media(item("episode/id"))
    ).url == "https://audio.example/new.mp3"


async def test_starred_folder_hides_empty_and_missing_shows(
    source: ArdSoundsMediaSource, service: ArdSoundsService, client: AsyncMock
) -> None:
    """Refresh saved shows without dropping selections that are empty or removed."""
    stars = source.hass.config_entries.async_entries.return_value[0].runtime_data.stars
    with patch("custom_components.ard_sounds.classes.Store.async_save"):
        for identifier, title in (("1", "Old title"), ("2", "Empty"), ("3", "Removed")):
            await stars.async_star(Podcast(identifier, title))
    saved = stars.podcasts
    populated = {
        "id": "1",
        "title": "Current title",
        "availableEpisodes": {"nodes": [{"id": "episode"}]},
    }
    empty = {"id": "2", "title": "Empty", "availableEpisodes": {"nodes": []}}
    shows = {"1": populated, "2": empty, "3": None}

    async def request(_operation: str, variables: dict[str, str]) -> dict[str, object]:
        return {"show": shows[variables["id"]]}

    client.request.side_effect = request
    with patch(
        "custom_components.ard_sounds.media_source.async_get_translations",
        return_value={},
    ):
        folder = await source.async_browse_media(item("starred"))
        assert [child.title for child in folder.children] == ["★ Current title"]
        assert folder.children[0].media_content_id == "media-source://ard_sounds/show/1"
        assert not folder.can_search
        assert stars.podcasts == saved
        empty["availableEpisodes"]["nodes"] = [{"id": "new-episode"}]
        service.cache.invalidate()
        folder = await source.async_browse_media(item("starred"))
        assert [child.title for child in folder.children] == [
            "★ Current title",
            "★ Empty",
        ]
        assert stars.podcasts == saved
        service.cache.invalidate()
        client.request.side_effect = ArdSoundsConnectionError
        with pytest.raises(BrowseError):
            await source.async_browse_media(item("starred"))
    await service.cache.close()


async def test_star_indicator_in_search(
    source: ArdSoundsMediaSource, service: ArdSoundsService
) -> None:
    """Star indicators preserve identities and distinguish titles shared by shows."""
    page = await service.search("Wissen")
    stars = source.hass.config_entries.async_entries.return_value[0].runtime_data.stars
    with patch("custom_components.ard_sounds.classes.Store.async_save"):
        await stars.async_star(page.items[0])
    with patch(
        "custom_components.ard_sounds.media_source.async_get_translations",
        return_value={},
    ):
        results = await source.async_search_media(
            item(),
            SearchMediaQuery(
                search_query="Wissen", media_filter_classes=[MediaClass.PODCAST]
            ),
        )
    assert [child.title for child in results.result] == ["★ Wissen", "Wissen"]
    assert results.result[0].media_content_id != results.result[1].media_content_id
    await service.cache.close()
