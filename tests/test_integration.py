"""
UI flow, entry lifecycle, and the integration-wide reload service.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ard_sounds.api import (
    ArdSoundsConnectionError,
    ArdSoundsResponseError,
)
from custom_components.ard_sounds.const import DEFAULT_PAGE_SIZE, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OPTIONS_PAGE_SIZE = 20


async def test_flow_success_duplicate(
    hass: HomeAssistant, api_data: dict[str, Any]
) -> None:
    """Validate with one small API request and prevent duplicate catalog entries."""
    with (
        patch(
            "custom_components.ard_sounds.api.ArdSoundsGraphQLClient.request",
            new_callable=AsyncMock,
            return_value=api_data["stations"],
        ) as request,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"] == {}
        request.assert_awaited_with("Stations", {"first": 1, "after": None})
        assert all(
            call.args == ("Stations", {"first": 1, "after": None})
            for call in request.await_args_list
        )
        await hass.async_block_till_done()
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert duplicate["reason"] == "single_instance_allowed"


async def test_flow_error(hass: HomeAssistant) -> None:
    """Connection errors return a translated form without creating an entry."""
    with patch(
        "custom_components.ard_sounds.api.ArdSoundsGraphQLClient.request",
        side_effect=ArdSoundsConnectionError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["errors"] == {"base": "cannot_connect"}


async def test_setup_unload_and_reload_service(
    hass: HomeAssistant, api_data: dict[str, Any]
) -> None:
    """Unloading cleans runtime while retaining the integration-wide action."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ard_sounds.api.ArdSoundsGraphQLClient.request",
        new_callable=AsyncMock,
        return_value=api_data["stations"],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.service.page_size == DEFAULT_PAGE_SIZE
        with patch.object(
            entry.runtime_data.service, "reload", new_callable=AsyncMock
        ) as reload:
            await hass.services.async_call(DOMAIN, "reload_sources", {}, blocking=True)
            reload.assert_awaited_once()
        with (
            patch.object(
                entry.runtime_data.service, "reload", side_effect=ArdSoundsResponseError
            ),
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(DOMAIN, "reload_sources", {}, blocking=True)
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert hass.services.has_service(DOMAIN, "reload_sources")
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, "reload_sources", {}, blocking=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert await hass.config_entries.async_remove(entry.entry_id)
        new_entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
        new_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(new_entry.entry_id)
        assert await hass.config_entries.async_unload(new_entry.entry_id)


async def test_setup_retry(hass: HomeAssistant) -> None:
    """Required-data failures trigger HA retry rather than a successful setup."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ard_sounds.api.ArdSoundsGraphQLClient.request",
        side_effect=ArdSoundsConnectionError,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.SETUP_RETRY
    await hass.config_entries.async_unload(entry.entry_id)


async def test_options_flow(hass: HomeAssistant, api_data: dict[str, Any]) -> None:
    """Options persist via public HA APIs and reload the runtime safely."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ard_sounds.api.ArdSoundsGraphQLClient.request",
        new_callable=AsyncMock,
        return_value=api_data["stations"],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {"page_size": 101, "episode_limit": 20}
            )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"page_size": 20, "episode_limit": 10}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.options == {"page_size": 20, "episode_limit": 10}
        assert entry.runtime_data.service.page_size == OPTIONS_PAGE_SIZE
        assert await hass.config_entries.async_unload(entry.entry_id)
