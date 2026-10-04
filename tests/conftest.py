"""
Reusable recorded metadata and isolated Home Assistant integration fixtures.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.ard_sounds.api import ArdSoundsGraphQLClient
from custom_components.ard_sounds.classes import ArdSoundsRequestCache, ArdSoundsService


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations: Any) -> None:
    """Permit loading this repository's custom integration in HA tests."""


@pytest.fixture
def api_data() -> dict[str, Any]:
    """Load small real ARD responses without network access."""
    folder = Path(__file__).parent / "fixtures"
    return {
        path.stem: json.loads(path.read_text())["data"]
        for path in folder.glob("*.json")
    }


@pytest.fixture
def client(api_data: dict[str, Any]) -> AsyncMock:
    """Provide a query-aware fake transport."""
    fake = AsyncMock(spec=ArdSoundsGraphQLClient)

    async def request(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        names = {
            "Stations": "stations",
            "Shows": "shows",
            "Episodes": "episodes",
            "Search": "search0",
        }
        name = names[operation]
        if variables.get("after"):
            name += "_next"
        if operation == "Search" and variables.get("offset"):
            name = "search2"
        result = deepcopy(api_data[name])
        if name == "stations_next":
            result["permanentLivestreams"]["pageInfo"]["hasNextPage"] = False
        return result

    fake.request.side_effect = request
    return fake


@pytest.fixture
def service(client: AsyncMock) -> ArdSoundsService:
    """Create a normalized service backed entirely by the fake transport."""
    return ArdSoundsService(
        client, ArdSoundsRequestCache(client), page_size=2, episode_limit=2
    )
