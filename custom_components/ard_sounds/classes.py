"""
ARD Sounds Client Classes.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT


# General Architecture:

Media Source
    │
    ▼
ArdSoundsService
    │
    ▼
ArdSoundsRequestCache
    │
    ├── cache hit
    │       │
    │       ▼
    │   serve cached response
    │
    └── cache miss / expired
            │
            ▼
        api.ArdSoundsGraphQLClient
            │
            ▼
        External request to ARD Sounds GraphQL API

"""


class ArdSoundsService:
    """
    Base class for ARD Sounds services.

    Attributes:

    """

    pass


class ArdSoundsRequestCache:
    """
    Base class for ARD Sounds request caches.

    Attributes:
        default_ttl (int): Default time-to-live for cached requests in seconds.

    """

    default_ttl: int = 3600  # Default TTL in seconds (1 hour)
