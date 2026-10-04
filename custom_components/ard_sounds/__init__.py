"""
Set up the entry-owned ARD Sounds media catalog.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArdSoundsError, ArdSoundsGraphQLClient
from .classes import ArdSoundsRequestCache, ArdSoundsService
from .const import (
    CONF_EPISODE_LIMIT,
    CONF_PAGE_SIZE,
    DEFAULT_EPISODE_LIMIT,
    DEFAULT_PAGE_SIZE,
    DOMAIN,
    LOGGER,
    SERVICE_RELOAD,
)
from .models import ArdSoundsRuntime

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.typing import ConfigType

type ArdSoundsConfigEntry = ConfigEntry[ArdSoundsRuntime]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register the integration-wide reload action once."""

    async def reload_sources(_call: ServiceCall) -> None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries or entries[0].state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        entry: ArdSoundsConfigEntry = entries[0]
        try:
            await entry.runtime_data.service.reload()
        except ArdSoundsError as err:
            LOGGER.warning("Failed to reload ARD sources: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="reload_failed"
            ) from err

    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD, reload_sources, schema=vol.Schema({})
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ArdSoundsConfigEntry) -> bool:
    """Validate small required setup data and create typed runtime objects."""
    client = ArdSoundsGraphQLClient(async_get_clientsession(hass))
    cache = ArdSoundsRequestCache(client)
    service = ArdSoundsService(
        client,
        cache,
        page_size=entry.options.get(CONF_PAGE_SIZE, DEFAULT_PAGE_SIZE),
        episode_limit=entry.options.get(CONF_EPISODE_LIMIT, DEFAULT_EPISODE_LIMIT),
    )
    try:
        await service.validate()
    except ArdSoundsError as err:
        await cache.close()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err
    entry.runtime_data = ArdSoundsRuntime(client, cache, service)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ArdSoundsConfigEntry
) -> None:
    """Apply preferences using Home Assistant's entry reload lifecycle."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(_hass: HomeAssistant, entry: ArdSoundsConfigEntry) -> bool:
    """Close entry-owned tasks without closing the shared session or service."""
    await entry.runtime_data.cache.close()
    return True
