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

from custom_components.ard_sounds.api import ArdSoundsResponseError
from custom_components.ard_sounds.const import DOMAIN, PODCAST_INITIALS
from custom_components.ard_sounds.media_source import ArdSoundsMediaSource
from custom_components.ard_sounds.models import AudioCandidate, Route

if TYPE_CHECKING:
    from custom_components.ard_sounds.classes import ArdSoundsService

PAGE_ITEMS = 2


@pytest.fixture
def source(service: ArdSoundsService) -> ArdSoundsMediaSource:
    """Attach a fake loaded entry through the public config-entry interface."""
    hass = MagicMock()
    hass.config.language = "en"
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED, runtime_data=SimpleNamespace(service=service)
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
