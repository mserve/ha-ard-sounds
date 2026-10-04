"""
Normalized catalog models and identifier contract for ARD Sounds.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit

from .const import MAX_ROUTE_LENGTH

if TYPE_CHECKING:
    from .api import ArdSoundsGraphQLClient
    from .classes import ArdSoundsRequestCache, ArdSoundsService

MAX_ROUTE_PARTS = 3
CONTROL_CHAR_LIMIT = 32
DELETE_CHAR = 127
MAX_SEARCH_OFFSET = 10000

MIME_ALIASES = {
    "audio/mp3": "audio/mpeg",
    "audio/x-mpeg": "audio/mpeg",
    "application/x-mpegurl": "application/vnd.apple.mpegurl",
    "audio/x-mpegurl": "application/vnd.apple.mpegurl",
}
SUPPORTED_MIMES = (
    "audio/mpeg",
    "audio/aac",
    "audio/mp4",
    "audio/ogg",
    "application/ogg",
    "application/vnd.apple.mpegurl",
)


def normalize_url(value: Any) -> str | None:
    """Accept absolute HTTP audio/image URLs, including protocol-relative URLs."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return None
    value = f"https:{value}" if value.startswith("//") else value
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
        ):
            return value
    except ValueError:
        return None
    return None


def normalize_mime(value: str) -> str:
    """Normalize MIME aliases without converting HLS into an audio file."""
    mime = value.split(";", 1)[0].strip().lower()
    return MIME_ALIASES.get(mime, mime)


def parse_date(value: Any) -> datetime | None:
    """Parse optional timestamps, returning aware UTC values."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


@dataclass(frozen=True, slots=True)
class AudioCandidate:
    """A playable distribution with an optional availability window."""

    url: str
    mime_type: str
    distribution: str = ""
    available_from: datetime | None = None
    available_to: datetime | None = None

    def is_available(self, now: datetime) -> bool:
        """Check the current availability window."""
        return (
            self.mime_type in SUPPORTED_MIMES
            and (self.available_from is None or self.available_from <= now)
            and (self.available_to is None or now < self.available_to)
        )


def audio_candidates(data: dict[str, Any]) -> tuple[AudioCandidate, ...]:
    """Merge explicit streams and distribution metadata, retaining alternates."""
    streams = data.get("audios") or []
    binaries = data.get("audioList") or []
    result: dict[str, AudioCandidate] = {}
    for stream in streams:
        if not isinstance(stream, dict) or not (
            url := normalize_url(stream.get("url"))
        ):
            continue
        result[url] = AudioCandidate(url, normalize_mime(stream.get("mimeType") or ""))
    for binary in binaries:
        if not isinstance(binary, dict) or not (
            url := normalize_url(binary.get("href"))
        ):
            continue
        old = result.get(url)
        codec = str(binary.get("audioCodec") or "").lower()
        mime = (old.mime_type if old else "") or {
            "mp3": "audio/mpeg",
            "aac": "audio/aac",
        }.get(codec, "")
        if urlsplit(url).path.lower().endswith(".m3u8"):
            mime = "application/vnd.apple.mpegurl"
        result[url] = AudioCandidate(
            url,
            mime,
            binary.get("distributionType") or "",
            parse_date(binary.get("availableFrom")),
            parse_date(binary.get("availableTo")),
        )
    return tuple(
        sorted(
            result.values(),
            key=lambda a: (
                a.mime_type != "audio/mpeg",
                a.distribution not in ("onDemand", "livestream"),
                a.url,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class Station:
    """Publication service and its broadcaster."""

    id: str
    title: str
    broadcaster: str

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> Station:
        """Normalize optional publication-service metadata."""
        data = data or {}
        return cls(
            str(data.get("id") or "unknown"),
            data.get("title") or "ARD",
            data.get("organizationName") or "ARD",
        )


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Stable identity and optional display metadata shared by all content."""

    id: str
    title: str
    core_id: str | None = None
    description: str = ""
    image_url: str | None = None


@dataclass(frozen=True, slots=True)
class Stream(CatalogItem):
    """A station's named live or regional stream variant."""

    station: Station = field(default_factory=lambda: Station("unknown", "ARD", "ARD"))
    audios: tuple[AudioCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class Podcast(CatalogItem):
    """A show browsable through its GraphQL episodes, independent of RSS."""

    station: Station = field(default_factory=lambda: Station("unknown", "ARD", "ARD"))


@dataclass(frozen=True, slots=True)
class Episode(CatalogItem):
    """A published episode and its alternate audio distributions."""

    audios: tuple[AudioCandidate, ...] = ()
    duration: int = 0
    published_at: datetime | None = None
    is_published: bool = True


def item_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Extract safe common fields from an API node."""
    image = data.get("image") or {}
    return {
        "id": str(data["id"]),
        "title": data.get("title") or str(data["id"]),
        "core_id": data.get("coreId"),
        "description": data.get("synopsis") or "",
        "image_url": normalize_url(
            (image.get("url1X1") or image.get("url") or "").replace("{width}", "512")
        ),
    }


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One bounded page with a validated continuation token."""

    items: tuple[T, ...]
    next_cursor: str | None = None
    has_next: bool = False
    total: int = 0


@dataclass(slots=True)
class ArdSoundsRuntime:
    """Entry-owned objects; the HTTP session remains owned by Home Assistant."""

    client: ArdSoundsGraphQLClient
    cache: ArdSoundsRequestCache
    service: ArdSoundsService


@dataclass(frozen=True, slots=True)
class Route:
    """Percent-escaped route, also usable with the internal ard_sounds scheme."""

    kind: str = ""
    key: str = ""
    page: str = ""

    @property
    def identifier(self) -> str:
        """Build the identifier consumed by BrowseMediaSource."""
        return "/".join(
            quote(p, safe="") for p in (self.kind, self.key, self.page) if p
        )

    @property
    def internal_id(self) -> str:
        """Build an internal identifier; never return this as an HA media ID."""
        return f"ard_sounds://{self.identifier}"

    @classmethod
    def parse(cls, value: str) -> Route:
        """Reject malformed/reserved routes, preserving URNs and escaped slashes."""
        value = value.removeprefix("ard_sounds://")
        if len(value) > MAX_ROUTE_LENGTH or re.search(r"%(?![0-9a-fA-F]{2})", value):
            msg = "Invalid media identifier"
            raise ValueError(msg)
        parts = value.split("/") if value else []
        if any(not p for p in parts) or len(parts) > MAX_ROUTE_PARTS:
            msg = "Invalid media identifier"
            raise ValueError(msg)
        parts = [unquote(p, errors="strict") for p in parts]
        route = cls(*parts)
        counts = {
            "": (0,),
            "radio": (1,),
            "broadcaster": (2,),
            "station": (2,),
            "stream": (2,),
            "podcasts": (1, 2),
            "show": (2, 3),
            "episode": (2,),
            "search": (3,),
        }
        if len(parts) not in counts.get(route.kind, ()) or any(
            ord(c) < CONTROL_CHAR_LIMIT or ord(c) == DELETE_CHAR
            for p in parts
            for c in p
        ):
            msg = "Invalid media identifier"
            raise ValueError(msg)
        if route.kind == "search" and (
            not route.page.isdecimal() or int(route.page) > MAX_SEARCH_OFFSET
        ):
            msg = "Invalid search offset"
            raise ValueError(msg)
        return route
