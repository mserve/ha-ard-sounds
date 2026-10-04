"""
ARD Sounds Component Constants.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from datetime import timedelta

DEFAULT_TTL = {
    "home": timedelta(minutes=5),
    "program": timedelta(hours=1),
    "episodes": timedelta(minutes=15),
    "categories": timedelta(hours=12),
}
