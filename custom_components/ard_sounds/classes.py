"""Catalog service and bounded metadata cache; no audio is cached."""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .api import ArdSoundsNotFoundError, ArdSoundsResponseError
from .const import (
    DEFAULT_TTL,
    DOMAIN,
    LOGGER,
    MAX_CACHE_ENTRIES,
    MAX_CATALOG_PAGES,
    MAX_PAGE_SIZE,
    MAX_SHOW_CATALOG_PAGES,
    PODCAST_INITIALS,
    STARRED_STORAGE_VERSION,
)
from .models import (
    AudioCandidate,
    Episode,
    Page,
    Podcast,
    Station,
    Stream,
    audio_candidates,
    item_fields,
    normalize_url,
    parse_date,
    podcast_initial,
    podcast_sort_title,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from .api import ArdSoundsGraphQLClient


class StarredPodcasts:
    """Persist one entry's shared selection independently of browsing preferences."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Use versioned HA storage without owning a file path."""
        self._store = Store[list[dict[str, Any]]](
            hass,
            STARRED_STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.stars",
            atomic_writes=True,
        )
        self._podcasts: dict[str, Podcast] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def podcasts(self) -> tuple[Podcast, ...]:
        """Return saved selections, including temporarily unavailable shows."""
        return tuple(
            sorted(
                self._podcasts.values(),
                key=lambda podcast: (podcast_sort_title(podcast.title), podcast.id),
            )
        )

    def contains(self, podcast: Podcast) -> bool:
        """Recognize both numeric IDs and canonical core IDs."""
        return any(
            stored.id == podcast.id
            or bool(podcast.core_id and stored.core_id == podcast.core_id)
            for stored in self._podcasts.values()
        )

    async def async_load(self) -> None:
        """Restore valid saved metadata without querying the remote catalog."""
        data = await self._store.async_load()
        if data is None:
            return
        if not isinstance(data, list):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_star_storage"
            )
        for record in data:
            if not isinstance(record, dict) or not all(
                isinstance(record.get(key), str) and record[key]
                for key in ("id", "title")
            ):
                LOGGER.warning("Skipping invalid saved ARD podcast selection")
                continue
            podcast = Podcast(
                id=record["id"],
                title=record["title"],
                core_id=self._text(record, "core_id") or None,
                description=self._text(record, "description"),
                image_url=normalize_url(record.get("image_url")),
                station=Station(
                    self._text(record, "station_id") or "unknown",
                    self._text(record, "station_title") or "ARD",
                    self._text(record, "broadcaster") or "ARD",
                ),
            )
            if not self.contains(podcast):
                self._podcasts[podcast.core_id or podcast.id] = podcast

    @staticmethod
    def _text(record: dict[str, Any], key: str) -> str:
        """Default missing optional saved metadata safely."""
        value = record.get(key)
        return value if isinstance(value, str) else ""

    @staticmethod
    def _serialize(podcast: Podcast) -> dict[str, Any]:
        """Keep only identity and display metadata in local storage."""
        return {
            "id": podcast.id,
            "core_id": podcast.core_id,
            "title": podcast.title,
            "description": podcast.description,
            "image_url": podcast.image_url,
            "station_id": podcast.station.id,
            "station_title": podcast.station.title,
            "broadcaster": podcast.station.broadcaster,
        }

    async def async_star(self, podcast: Podcast) -> None:
        """Save an idempotent addition, serializing concurrent changes."""
        async with self._lock:
            self._require_open()
            updated = {
                key: stored
                for key, stored in self._podcasts.items()
                if stored.id != podcast.id
                and not (podcast.core_id and stored.core_id == podcast.core_id)
            }
            updated[podcast.core_id or podcast.id] = podcast
            if updated != self._podcasts:
                await self._save(updated)

    async def async_unstar(self, identifiers: list[str]) -> None:
        """Remove selections without requiring API access; save a batch once."""
        async with self._lock:
            self._require_open()
            updated = {
                key: podcast
                for key, podcast in self._podcasts.items()
                if not {key, podcast.id, podcast.core_id}.intersection(identifiers)
            }
            if updated != self._podcasts:
                await self._save(updated)

    async def _save(self, podcasts: dict[str, Podcast]) -> None:
        """Use HA's awaited atomic save before publishing the updated collection."""
        await self._store.async_save([self._serialize(p) for p in podcasts.values()])
        self._podcasts = podcasts

    def _require_open(self) -> None:
        """Reject changes through a runtime that has already been unloaded."""
        if self._closed:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )

    async def async_close(self) -> None:
        """Await active writes before a replacement runtime can restore saved state."""
        async with self._lock:
            self._closed = True

    async def async_remove(self) -> None:
        """Delete entry-owned state only when the config entry is removed."""
        await self._store.async_remove()


class ArdSoundsRequestCache:
    """Cache successful metadata, coalescing misses and preserving cancellation."""

    def __init__(
        self,
        client: ArdSoundsGraphQLClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = MAX_CACHE_ENTRIES,
    ) -> None:
        """Initialize an entry-owned cache with an injectable monotonic clock."""
        self.client = client
        self.clock = clock
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._pending: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self._generation = 0
        self._closed = False

    @staticmethod
    def _key(operation: str, variables: dict[str, Any]) -> str:
        """Include the operation and every normalized variable in the cache key."""
        return json.dumps([operation, variables], sort_keys=True, separators=(",", ":"))

    async def request(
        self, operation: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Return fresh metadata or await one shared request."""
        if self._closed:
            msg = "ARD cache is unloaded"
            raise ArdSoundsResponseError(msg)
        key = self._key(operation, variables)
        if entry := self._entries.get(key):
            if entry[0] > self.clock():
                self._entries.move_to_end(key)
                return entry[1]
            del self._entries[key]
        if key not in self._pending:
            self._pending[key] = asyncio.create_task(
                self._fetch(key, operation, variables, self._generation)
            )
            self._pending[key].add_done_callback(self._consume_exception)
            self._tasks.add(self._pending[key])
            self._pending[key].add_done_callback(self._tasks.discard)
        return await asyncio.shield(self._pending[key])

    @staticmethod
    def _consume_exception(task: asyncio.Task[dict[str, Any]]) -> None:
        """Retrieve exceptions even when every waiter has been cancelled."""
        if not task.cancelled():
            task.exception()

    async def _fetch(
        self, key: str, operation: str, variables: dict[str, Any], generation: int
    ) -> dict[str, Any]:
        """Cache only a successful response belonging to the current generation."""
        try:
            result = await self.client.request(operation, variables)
            if generation == self._generation and not self._closed:
                self._entries[key] = (
                    self.clock() + DEFAULT_TTL[operation].total_seconds(),
                    result,
                )
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
            return result
        finally:
            if self._pending.get(key) is asyncio.current_task():
                del self._pending[key]

    def cached_requests(self) -> list[tuple[str, dict[str, Any]]]:
        """Snapshot bounded catalog pages for explicit reload."""
        return [tuple(json.loads(key)) for key in self._entries]

    def invalidate(self) -> None:
        """Discard cached data and detach old requests from the current generation."""
        self._generation += 1
        self._entries.clear()
        self._pending.clear()

    async def close(self) -> None:
        """Cancel and await all entry-owned requests, leaving the HA session open."""
        self._closed = True
        self._entries.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()


class ArdSoundsService:
    """Expose normalized catalog pages and fresh playable audio to Media Source."""

    def __init__(
        self,
        client: ArdSoundsGraphQLClient,
        cache: ArdSoundsRequestCache,
        *,
        page_size: int = 50,
        episode_limit: int = 30,
    ) -> None:
        """Initialize public-catalog preferences and serialize explicit reloads."""
        self.client = client
        self.cache = cache
        self.page_size = page_size
        self.episode_limit = episode_limit
        self._reload_lock = asyncio.Lock()

    @staticmethod
    def _connection(
        data: Any, after: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, bool, int]:
        """Validate page shapes and stop empty pages and repeated cursors."""
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            msg = "Missing ARD connection"
            raise ArdSoundsResponseError(msg)
        nodes = [
            node for node in data["nodes"] if isinstance(node, dict) and node.get("id")
        ]
        info = data.get("pageInfo") or {}
        if not isinstance(info, dict):
            msg = "Invalid ARD page information"
            raise ArdSoundsResponseError(msg)
        cursor = info.get("endCursor")
        if cursor is not None and not isinstance(cursor, str):
            msg = "Invalid ARD page cursor"
            raise ArdSoundsResponseError(msg)
        has_next = bool(info.get("hasNextPage") and nodes)
        if has_next and after is not None and cursor == after:
            msg = "ARD repeated a page cursor"
            raise ArdSoundsResponseError(msg)
        total = data.get("totalCount")
        return (
            nodes,
            cursor if has_next else None,
            has_next,
            total if isinstance(total, int) else 0,
        )

    async def validate(self) -> None:
        """Validate access with one small request, rather than loading the catalog."""
        data = await self.client.request("Stations", {"first": 1, "after": None})
        self._connection(data.get("permanentLivestreams"))

    async def stations(self) -> tuple[Stream, ...]:
        """Fetch bounded station pages, preserving distinct regional variants."""
        result: dict[str, Stream] = {}
        after = None
        seen: set[str] = set()
        for _ in range(MAX_CATALOG_PAGES):
            data = await self.cache.request(
                "Stations", {"first": MAX_PAGE_SIZE, "after": after}
            )
            nodes, cursor, has_next, _total = self._connection(
                data.get("permanentLivestreams"), after
            )
            for node in nodes:
                stream = Stream(
                    **item_fields(node),
                    station=Station.from_api(node.get("publicationService")),
                    audios=audio_candidates(node),
                )
                if any(a.is_available(datetime.now(UTC)) for a in stream.audios):
                    result.setdefault(stream.id, stream)
            if not has_next:
                return tuple(
                    sorted(
                        result.values(),
                        key=lambda s: (
                            s.station.broadcaster,
                            s.station.title,
                            s.title,
                            s.id,
                        ),
                    )
                )
            if not cursor or cursor in seen:
                msg = "Invalid ARD station pagination"
                raise ArdSoundsResponseError(msg)
            seen.add(cursor)
            after = cursor
        msg = "ARD station catalog exceeded the page bound"
        raise ArdSoundsResponseError(msg)

    async def shows(self, after: str | None = None) -> Page[Podcast]:
        """Return one catalog page with stable primary-key ordering."""
        data = await self.cache.request(
            "Shows", {"first": self.page_size, "after": after}
        )
        return self._show_page(data.get("programSets"), after)

    async def alphabetical_shows(self, initial: str) -> tuple[Podcast, ...]:
        """Collect all matching shows through bounded, cached catalog pages."""
        if initial not in tuple(PODCAST_INITIALS):
            msg = "Invalid podcast letter"
            raise ValueError(msg)
        shows: dict[str, Podcast] = {}
        seen: set[str] = set()
        after = None
        for _ in range(MAX_SHOW_CATALOG_PAGES):
            data = await self.cache.request(
                "Shows", {"first": MAX_PAGE_SIZE, "after": after}
            )
            page = self._show_page(data.get("programSets"), after)
            for show in page.items:
                if podcast_initial(show.title) == initial:
                    shows.setdefault(show.core_id or show.id, show)
            if not page.has_next:
                return tuple(
                    sorted(
                        shows.values(),
                        key=lambda show: (
                            podcast_sort_title(show.title),
                            show.title,
                            show.id,
                        ),
                    )
                )
            if not page.next_cursor or page.next_cursor in seen:
                msg = "Invalid ARD show pagination"
                raise ArdSoundsResponseError(msg)
            seen.add(page.next_cursor)
            after = page.next_cursor
        msg = "ARD show catalog exceeded the page bound"
        raise ArdSoundsResponseError(msg)

    def _show_page(self, connection: Any, after: str | None = None) -> Page[Podcast]:
        """Normalize nonempty shows, keeping pagination based on the raw page."""
        nodes, cursor, has_next, total = self._connection(connection, after)
        items = {
            node["id"]: Podcast(
                **item_fields(node),
                station=Station.from_api(node.get("publicationService")),
            )
            for node in nodes
            if self._has_episodes(node)
        }
        if has_next and not cursor:
            msg = "Missing ARD show continuation"
            raise ArdSoundsResponseError(msg)
        return Page(tuple(items.values()), cursor, has_next, total)

    @staticmethod
    def _has_episodes(node: dict[str, Any]) -> bool:
        """Use the published-episode connection rather than summary counts."""
        nodes, *_ = ArdSoundsService._connection(node.get("availableEpisodes"))
        return bool(nodes)

    async def search(self, query: str, offset: int = 0) -> Page[Podcast]:
        """Search via offsets; the search API does not supply usable cursors."""
        data = await self.cache.request(
            "Search",
            {"query": query.strip(), "limit": self.page_size, "offset": offset},
        )
        search = data.get("search")
        if not isinstance(search, dict):
            msg = "Missing ARD search result"
            raise ArdSoundsResponseError(msg)
        nodes, _cursor, has_next, total = self._connection(search.get("programSets"))
        items = {
            node["id"]: Podcast(
                **item_fields(node),
                station=Station.from_api(node.get("publicationService")),
            )
            for node in nodes
            if self._has_episodes(node)
        }
        return Page(
            tuple(items.values()),
            str(offset + len(nodes)) if has_next else None,
            has_next,
            total,
        )

    async def episodes(
        self, show_id: str, after: str | None = None
    ) -> tuple[Podcast, Page[Episode]]:
        """Return a show and one bounded page of currently playable episodes."""
        data = await self.cache.request(
            "Episodes", {"id": show_id, "first": self.episode_limit, "after": after}
        )
        show = data.get("show")
        if not isinstance(show, dict) or not show.get("id"):
            msg = "Show is no longer available"
            raise ArdSoundsNotFoundError(msg)
        nodes, cursor, has_next, total = self._connection(show.get("items"), after)
        if has_next and not cursor:
            msg = "Missing ARD episode continuation"
            raise ArdSoundsResponseError(msg)
        items: dict[str, Episode] = {}
        for node in nodes:
            episode = self._episode(node)
            if episode.is_published and any(
                a.is_available(datetime.now(UTC)) for a in episode.audios
            ):
                items.setdefault(episode.id, episode)
        return Podcast(
            **item_fields(show),
            station=Station.from_api(show.get("publicationService")),
        ), Page(tuple(items.values()), cursor, has_next, total)

    async def podcast(self, identifier: str, *, allow_empty: bool = False) -> Podcast:
        """Look up one show using either its numeric ID or canonical core ID."""
        data = await self.cache.request("Show", {"id": identifier})
        show = data.get("show")
        if not isinstance(show, dict) or not show.get("id"):
            msg = "Show is no longer available"
            raise ArdSoundsNotFoundError(msg)
        if not allow_empty and not self._has_episodes(show):
            msg = "Show has no published episodes"
            raise ArdSoundsNotFoundError(msg)
        return Podcast(
            **item_fields(show),
            station=Station.from_api(show.get("publicationService")),
        )

    async def starred_shows(
        self, selections: tuple[Podcast, ...]
    ) -> tuple[Podcast, ...]:
        """Refresh a small collection while retaining unavailable selections locally."""
        semaphore = asyncio.Semaphore(4)

        async def available(podcast: Podcast) -> Podcast | None:
            async with semaphore:
                try:
                    return await self.podcast(podcast.core_id or podcast.id)
                except ArdSoundsNotFoundError:
                    return None

        shows = await asyncio.gather(*(available(p) for p in selections))
        return tuple(
            sorted(
                (show for show in shows if show is not None),
                key=lambda show: (podcast_sort_title(show.title), show.id),
            )
        )

    @staticmethod
    def _episode(node: dict[str, Any]) -> Episode:
        """Normalize optional episode metadata."""
        return Episode(
            **item_fields(node),
            audios=audio_candidates(node),
            duration=node.get("duration") or 0,
            published_at=parse_date(node.get("publishDate")),
            is_published=node.get("isPublished") is not False,
        )

    async def resolve(self, kind: str, identifier: str) -> AudioCandidate:
        """Refresh audio metadata at playback time, trying valid alternate URLs."""
        operation, field_name = (
            ("Stream", "permanentLivestream")
            if kind == "stream"
            else ("Episode", "item")
        )
        data = await self.client.request(operation, {"id": identifier})
        node = data.get(field_name)
        if (
            not isinstance(node, dict)
            or not node.get("id")
            or node.get("isPublished") is False
        ):
            msg = "Content is no longer available"
            raise ArdSoundsNotFoundError(msg)
        last_error = None
        for candidate in audio_candidates(node):
            if candidate.is_available(datetime.now(UTC)):
                try:
                    return await self.client.resolve_audio(candidate)
                except ArdSoundsNotFoundError as err:
                    last_error = err
        msg = "No playable audio is available"
        raise ArdSoundsNotFoundError(msg) from last_error

    async def reload(self) -> None:
        """Invalidate and refresh bounded known catalog pages, reporting failures."""
        async with self._reload_lock:
            requests = self.cache.cached_requests()
            self.cache.invalidate()
            first_pages = [
                ("Stations", {"first": MAX_PAGE_SIZE, "after": None}),
                ("Shows", {"first": self.page_size, "after": None}),
            ]
            requests.extend(page for page in first_pages if page not in requests)
            semaphore = asyncio.Semaphore(4)

            async def refresh(operation: str, variables: dict[str, Any]) -> None:
                async with semaphore:
                    await self.cache.request(operation, variables)

            results = await asyncio.gather(
                *(refresh(op, var) for op, var in requests), return_exceptions=True
            )
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, Exception):
                    msg = "ARD catalog reload failed or partially failed"
                    raise ArdSoundsResponseError(msg) from result
