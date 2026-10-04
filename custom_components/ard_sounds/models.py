"""
Models for the ARD Sounds integration.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(slots=True)
class Episode:
    """Represents an episode of a sound."""

    title: str
    description: str
    url: str
    duration: int
    published_at: datetime | None
    image_url: str = field(default="")

    def as_dict(self) -> dict:
        """Return a JSON-serializable representation of the episode."""
        return {
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "duration": self.duration,
            "published_at": self.published_at.isoformat()
            if self.published_at
            else None,
            "image_url": self.image_url,
        }


@dataclass(slots=True)
class Podcast:
    """Represents a podcast."""

    title: str
    description: str
    url: str
    episodes: list[Episode] = field(default_factory=list)
