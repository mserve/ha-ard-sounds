"""
Browse and resolve ARD radio and podcasts through Home Assistant Media Source.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.media_player import (
    BrowseError,
    MediaClass,
    MediaType,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.translation import async_get_translations

from .api import ArdSoundsError, ArdSoundsNotFoundError
from .const import DOMAIN, PODCAST_INITIALS
from .models import CatalogItem, Route

MAX_SEARCH_LENGTH = 200

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import ArdSoundsConfigEntry
    from .classes import ArdSoundsService


async def async_get_media_source(hass: HomeAssistant) -> ArdSoundsMediaSource:
    """Return an integration platform, without forwarding any entity platforms."""
    return ArdSoundsMediaSource(hass)


class ArdSoundsMediaSource(MediaSource):
    """Resolve the currently loaded entry for every operation, including reloads."""

    name = "ARD Sounds"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the media platform without retaining a stale entry/service."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _service(self) -> ArdSoundsService:
        """Require a currently loaded catalog."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                loaded: ArdSoundsConfigEntry = entry
                return loaded.runtime_data.service
        msg = "ARD Sounds is not loaded"
        raise ArdSoundsNotFoundError(msg)

    async def _labels(self) -> dict[str, str]:
        """Use translated browse labels supplied by the integration."""
        values = await async_get_translations(
            self.hass, self.hass.config.language, "selector", {DOMAIN}
        )
        defaults = {
            "radio": "Live radio",
            "podcasts": "Podcasts",
            "next": "Next page",
            "search": "Search results",
        }
        return {
            key: values.get(
                f"component.{DOMAIN}.selector.browse.options.{key}", default
            )
            for key, default in defaults.items()
        }

    @staticmethod
    def _folder(
        route: Route,
        title: str,
        *,
        children: list[BrowseMediaSource] | None = None,
        can_search: bool = False,
    ) -> BrowseMediaSource:
        """Construct HA-facing IDs at the platform boundary."""
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=route.identifier or None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title=title,
            can_play=False,
            can_expand=True,
            can_search=can_search,
            search_media_classes=[MediaClass.PODCAST] if can_search else None,
            children=children,
        )

    @staticmethod
    def _content(kind: str, content: CatalogItem) -> BrowseMediaSource:
        """Build a show folder or a playable stream/episode."""
        podcast = kind == "show"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=Route(kind, content.core_id or content.id).identifier,
            title=content.title,
            media_class=MediaClass.PODCAST if podcast else MediaClass.MUSIC,
            media_content_type=MediaType.PODCAST if podcast else MediaType.MUSIC,
            can_play=not podcast,
            can_expand=podcast,
            thumbnail=content.image_url,
        )

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Expose bounded catalog pages with explicit continuation folders."""
        try:
            route = Route.parse(item.identifier or "")
            service = self._service()
            return await self._browse(route, service, await self._labels())
        except (ValueError, UnicodeError) as err:
            raise BrowseError(
                translation_domain=DOMAIN, translation_key="invalid_media"
            ) from err
        except ArdSoundsError as err:
            raise BrowseError(
                translation_domain=DOMAIN, translation_key="browse_failed"
            ) from err

    async def _browse(
        self, route: Route, service: ArdSoundsService, labels: dict[str, str]
    ) -> BrowseMediaSource:
        """Build the selected folder using only normalized service models."""
        if not route.kind:
            return self._folder(
                route,
                self.name,
                can_search=True,
                children=[
                    self._folder(Route("radio"), labels["radio"]),
                    self._folder(
                        Route("podcasts"), labels["podcasts"], can_search=True
                    ),
                ],
            )
        if route.kind in ("radio", "broadcaster", "station"):
            return await self._radio(route, service, labels)
        if route.kind == "podcasts" and not route.key:
            return self._folder(
                route,
                labels["podcasts"],
                can_search=True,
                children=[
                    self._folder(Route("podcasts", "letter", initial), initial)
                    for initial in PODCAST_INITIALS
                ],
            )
        if route.kind == "podcasts" and route.key == "letter":
            shows = await service.alphabetical_shows(route.page)
            return self._folder(
                route,
                route.page,
                children=[self._content("show", show) for show in shows],
            )
        if route.kind == "podcasts":
            page = await service.shows(route.key or None)
            title = labels["podcasts"]
            next_route = Route("podcasts", page.next_cursor or "")
        elif route.kind == "show":
            show, page = await service.episodes(route.key, route.page or None)
            title = show.title
            next_route = Route("show", route.key, page.next_cursor or "")
        elif route.kind == "search":
            page = await service.search(route.key, int(route.page))
            title = f"{labels['search']}: {route.key}"
            next_route = Route("search", route.key, page.next_cursor or "0")
        else:
            msg = "Media item cannot be browsed"
            raise ValueError(msg)
        kind = "episode" if route.kind == "show" else "show"
        children = [self._content(kind, content) for content in page.items]
        if page.has_next and page.next_cursor:
            children.append(self._folder(next_route, labels["next"]))
        return self._folder(
            route,
            title,
            children=children,
            can_search=route.kind in ("podcasts", "search"),
        )

    async def _radio(
        self, route: Route, service: ArdSoundsService, labels: dict[str, str]
    ) -> BrowseMediaSource:
        """Group broadcasters, stations, and named regional variants by stable IDs."""
        streams = await service.stations()
        if route.kind == "radio":
            children = [
                self._folder(Route("broadcaster", broadcaster), broadcaster)
                for broadcaster in sorted({s.station.broadcaster for s in streams})
            ]
            title = labels["radio"]
        elif route.kind == "broadcaster":
            stations = {
                s.station.id: s.station
                for s in streams
                if s.station.broadcaster == route.key
            }
            if not stations:
                msg = "Broadcaster is no longer available"
                raise ArdSoundsNotFoundError(msg)
            children = [
                self._folder(Route("station", station.id), station.title)
                for station in sorted(stations.values(), key=lambda s: (s.title, s.id))
            ]
            title = route.key
        else:
            variants = [s for s in streams if s.station.id == route.key]
            if not variants:
                msg = "Station is no longer available"
                raise ArdSoundsNotFoundError(msg)
            children = [self._content("stream", stream) for stream in variants]
            title = variants[0].station.title
        return self._folder(route, title, children=children)

    async def async_search_media(
        self, item: MediaSourceItem, query: SearchMediaQuery
    ) -> SearchMedia:
        """Search podcast shows from supported locations and honor class filters."""
        try:
            route = Route.parse(item.identifier or "")
            service = self._service()
            self._require_search_location(route)
            if (
                query.media_filter_classes
                and MediaClass.PODCAST not in query.media_filter_classes
            ):
                return SearchMedia(result=[])
            term = self._search_term(query.search_query)
            if not term:
                return SearchMedia(result=[])
            page = await service.search(term)
            children = [self._content("show", show) for show in page.items]
            if page.has_next and page.next_cursor:
                children.append(
                    self._folder(
                        Route("search", term, page.next_cursor),
                        (await self._labels())["next"],
                    )
                )
            if query.media_filter_classes:
                children = [
                    child
                    for child in children
                    if child.media_class in query.media_filter_classes
                ]
            return SearchMedia(result=children)
        except (ValueError, UnicodeError) as err:
            raise BrowseError(
                translation_domain=DOMAIN, translation_key="invalid_media"
            ) from err
        except ArdSoundsError as err:
            raise BrowseError(
                translation_domain=DOMAIN, translation_key="browse_failed"
            ) from err

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Return a final playable URL with the actual audio/playlist content type."""
        try:
            route = Route.parse(item.identifier)
            self._require_kind(route, ("stream", "episode"))
            audio = await self._service().resolve(route.kind, route.key)
            return PlayMedia(audio.url, audio.mime_type)
        except (ValueError, UnicodeError) as err:
            raise Unresolvable(
                translation_domain=DOMAIN, translation_key="invalid_media"
            ) from err
        except ArdSoundsError as err:
            raise Unresolvable(
                translation_domain=DOMAIN, translation_key="unavailable_media"
            ) from err

    @staticmethod
    def _require_search_location(route: Route) -> None:
        """Keep catalog search on advertised locations rather than letter folders."""
        ArdSoundsMediaSource._require_kind(route, ("", "podcasts", "search"))
        if route.kind == "podcasts" and route.key == "letter":
            msg = "Search is unavailable inside a letter folder"
            raise ValueError(msg)

    @staticmethod
    def _require_kind(route: Route, allowed: tuple[str, ...]) -> None:
        """Require a route supporting the selected operation."""
        if route.kind not in allowed:
            msg = "This selection does not support the requested operation"
            raise ValueError(msg)

    @staticmethod
    def _search_term(value: str) -> str:
        """Bound search terms before sending them to the API."""
        term = value.strip()
        if len(term) > MAX_SEARCH_LENGTH:
            msg = "Search query is too long"
            raise ValueError(msg)
        return term
