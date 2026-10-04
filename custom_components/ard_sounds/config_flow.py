"""
UI configuration and browsing preferences for the public ARD catalog.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
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
    MAX_PAGE_SIZE,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


def options_schema(options: dict[str, Any]) -> vol.Schema:
    """Keep catalog and episode requests within explicit limits."""
    return vol.Schema(
        {
            vol.Required(
                CONF_PAGE_SIZE, default=options.get(CONF_PAGE_SIZE, DEFAULT_PAGE_SIZE)
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=MAX_PAGE_SIZE)),
            vol.Required(
                CONF_EPISODE_LIMIT,
                default=options.get(CONF_EPISODE_LIMIT, DEFAULT_EPISODE_LIMIT),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=MAX_PAGE_SIZE)),
        }
    )


class ArdSoundsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one configuration for the public catalog after validating access."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the API before creating an entry, with translated failures."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            client = ArdSoundsGraphQLClient(async_get_clientsession(self.hass))
            cache = ArdSoundsRequestCache(client)
            try:
                await ArdSoundsService(client, cache).validate()
            except ArdSoundsError as err:
                LOGGER.debug("ARD config flow validation failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="ARD Sounds", data={})
            finally:
                await cache.close()
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ConfigEntry) -> ArdSoundsOptionsFlow:
        """Offer adjustable bounded browsing preferences."""
        return ArdSoundsOptionsFlow()


class ArdSoundsOptionsFlow(OptionsFlow):
    """Store entry options through the public options flow API."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save page preferences."""
        schema = options_schema(dict(self.config_entry.options))
        errors = {}
        if user_input is not None:
            try:
                options = schema(user_input)
            except vol.Invalid:
                errors["base"] = "invalid_options"
            else:
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
