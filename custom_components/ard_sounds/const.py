"""
Constants for ARD Sounds.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

import logging
from datetime import timedelta

DOMAIN = "ard_sounds"
LOGGER = logging.getLogger(__package__)
API_URL = "https://api.ardaudiothek.de/graphql"
REQUEST_TIMEOUT = 20
MAX_PAGE_SIZE = 100
MAX_CATALOG_PAGES = 20
MAX_CACHE_ENTRIES = 128
MAX_ROUTE_LENGTH = 2048
CONF_PAGE_SIZE = "page_size"
CONF_EPISODE_LIMIT = "episode_limit"
DEFAULT_PAGE_SIZE = 50
DEFAULT_EPISODE_LIMIT = 30
SERVICE_RELOAD = "reload_sources"
DEFAULT_TTL = {
    "Stations": timedelta(hours=1),
    "Shows": timedelta(hours=1),
    "Show": timedelta(hours=1),
    "Episodes": timedelta(minutes=15),
    "Search": timedelta(minutes=5),
}
