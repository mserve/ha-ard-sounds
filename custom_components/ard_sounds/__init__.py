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

from .api import ArdSoundsError, ArdSoundsGraphQLClient, ArdSoundsNotFoundError
from .classes import ArdSoundsRequestCache, ArdSoundsService, StarredPodcasts
from .const import (
    CONF_EPISODE_LIMIT,
    CONF_PAGE_SIZE,
    CONF_PODCAST_ID,
    CONF_SONOS_COMPATIBILITY,
    DEFAULT_EPISODE_LIMIT,
    DEFAULT_PAGE_SIZE,
    DOMAIN,
    LOGGER,
    MAX_ROUTE_LENGTH,
    SERVICE_RELOAD,
    SERVICE_STAR,
    SERVICE_UNSTAR,
)
from .models import ArdSoundsRuntime, podcast_identifier

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.typing import ConfigType

type ArdSoundsConfigEntry = ConfigEntry[ArdSoundsRuntime]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register integration-wide actions once, resolving runtime on each call."""

    def loaded_runtime() -> ArdSoundsRuntime:
        """Validate the currently loaded entry through the public HA API."""
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries or entries[0].state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        entry: ArdSoundsConfigEntry = entries[0]
        return entry.runtime_data

    async def reload_sources(_call: ServiceCall) -> None:
        runtime = loaded_runtime()
        try:
            await runtime.service.reload()
        except ArdSoundsError as err:
            LOGGER.warning("Failed to reload ARD sources: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="reload_failed"
            ) from err

    async def change_star(call: ServiceCall) -> None:
        """Validate additions remotely and permit offline, idempotent removals."""
        runtime = loaded_runtime()
        try:
            identifier = podcast_identifier(call.data[CONF_PODCAST_ID])
        except (ValueError, UnicodeError) as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_media"
            ) from err
        try:
            if call.service == SERVICE_STAR:
                podcast = await runtime.service.podcast(identifier, allow_empty=True)
                await runtime.stars.async_star(podcast)
            else:
                await runtime.stars.async_unstar([identifier])
        except ArdSoundsNotFoundError as err:
            LOGGER.warning("Cannot star unavailable ARD podcast: %s", err)
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unavailable_podcast"
            ) from err
        except (ArdSoundsError, OSError) as err:
            LOGGER.warning("Failed to update starred ARD podcasts: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="star_failed"
            ) from err

    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD, reload_sources, schema=vol.Schema({})
    )
    schema = vol.Schema(
        {
            vol.Required(CONF_PODCAST_ID): vol.All(
                cv.string, vol.Length(min=1, max=MAX_ROUTE_LENGTH)
            )
        }
    )
    for name in (SERVICE_STAR, SERVICE_UNSTAR):
        hass.services.async_register(DOMAIN, name, change_star, schema=schema)
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
    stars = StarredPodcasts(hass, entry.entry_id)
    try:
        await service.validate()
        await stars.async_load()
    except ArdSoundsError as err:
        await cache.close()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err
    except (HomeAssistantError, OSError) as err:
        await cache.close()
        LOGGER.warning("Failed to load starred ARD podcasts: %s", err)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="star_failed"
        ) from err
    entry.runtime_data = ArdSoundsRuntime(
        client,
        cache,
        service,
        stars,
        sonos_compatibility=entry.options.get(CONF_SONOS_COMPATIBILITY, False),
    )
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
    await entry.runtime_data.stars.async_close()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ArdSoundsConfigEntry) -> None:
    """Remove locally persisted selections when the integration is deleted."""
    await StarredPodcasts(hass, entry.entry_id).async_remove()
