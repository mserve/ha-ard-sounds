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
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorMode,
    TextSelector,
)

from .api import ArdSoundsError, ArdSoundsGraphQLClient
from .classes import ArdSoundsRequestCache, ArdSoundsService
from .const import (
    CONF_EPISODE_LIMIT,
    CONF_PAGE_SIZE,
    CONF_SONOS_COMPATIBILITY,
    DEFAULT_EPISODE_LIMIT,
    DEFAULT_PAGE_SIZE,
    DOMAIN,
    LOGGER,
    MAX_PAGE_SIZE,
)

MAX_SEARCH_LENGTH = 200

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .models import ArdSoundsRuntime, Podcast


def options_schema(options: dict[str, Any]) -> vol.Schema:
    """Keep catalog and episode requests within explicit limits."""
    return vol.Schema(
        {
            vol.Required(
                CONF_PAGE_SIZE, default=options.get(CONF_PAGE_SIZE, DEFAULT_PAGE_SIZE)
            ): vol.All(
                NumberSelector(
                    {
                        "min": 10,
                        "max": MAX_PAGE_SIZE,
                        "step": 1,
                        "mode": NumberSelectorMode.BOX,
                    }
                ),
                vol.Coerce(int),
            ),
            vol.Required(
                CONF_SONOS_COMPATIBILITY,
                default=options.get(CONF_SONOS_COMPATIBILITY, False),
            ): bool,
            vol.Required(
                CONF_EPISODE_LIMIT,
                default=options.get(CONF_EPISODE_LIMIT, DEFAULT_EPISODE_LIMIT),
            ): vol.All(
                NumberSelector(
                    {
                        "min": 10,
                        "max": MAX_PAGE_SIZE,
                        "step": 1,
                        "mode": NumberSelectorMode.BOX,
                    }
                ),
                vol.Coerce(int),
            ),
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
    """Manage browsing preferences and persisted stars with native HA forms."""

    def __init__(self) -> None:
        """Keep temporary search results scoped to this dialog."""
        super().__init__()
        self._matches: dict[str, Podcast] = {}
        self._query = ""

    def _runtime(self) -> ArdSoundsRuntime | None:
        """Use the loaded runtime, including after entry reloads."""
        if self.config_entry.state is ConfigEntryState.LOADED:
            return self.config_entry.runtime_data
        return None

    def _finished(self) -> ConfigFlowResult:
        """Close a stars-only dialog without changing preferences or reloading."""
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    @staticmethod
    def _choice(podcast: Podcast) -> dict[str, str]:
        """Disambiguate identical titles by publication service and broadcaster."""
        return {
            "value": podcast.core_id or podcast.id,
            "label": (
                f"{podcast.title} — {podcast.station.title} "
                f"({podcast.station.broadcaster})"
            ),
        }

    async def async_step_init(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer explicit preference, add, and remove actions."""
        return self.async_show_menu(
            step_id="init", menu_options=["preferences", "add_star", "remove_stars"]
        )

    async def async_step_preferences(
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
                return self.async_create_entry(
                    title="", data={**self.config_entry.options, **options}
                )
        return self.async_show_form(
            step_id="preferences", data_schema=schema, errors=errors
        )

    async def async_step_add_star(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search remotely only when the user submits a query."""
        runtime = self._runtime()
        if runtime is None:
            return self.async_abort(reason="not_loaded")
        errors = {}
        if user_input is not None:
            self._query = user_input["query"].strip()
            if not self._query or len(self._query) > MAX_SEARCH_LENGTH:
                errors["base"] = "invalid_query"
            else:
                try:
                    page = await runtime.service.search(self._query)
                except ArdSoundsError as err:
                    LOGGER.debug("ARD star search failed: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    self._matches = {
                        podcast.core_id or podcast.id: podcast
                        for podcast in page.items
                        if not runtime.stars.contains(podcast)
                    }
                    if self._matches:
                        return await self.async_step_choose_star()
                    errors["base"] = "no_results"
        return self.async_show_form(
            step_id="add_star",
            data_schema=vol.Schema(
                {vol.Required("query", default=self._query): TextSelector()}
            ),
            errors=errors,
        )

    async def async_step_choose_star(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save only an explicitly selected search result."""
        runtime = self._runtime()
        if runtime is None:
            return self.async_abort(reason="not_loaded")
        errors = {}
        if user_input is not None:
            podcast = self._matches.get(user_input["podcast_id"])
            if podcast is None:
                errors["base"] = "invalid_selection"
            else:
                try:
                    await runtime.stars.async_star(podcast)
                except (HomeAssistantError, OSError) as err:
                    LOGGER.warning("Failed to save starred ARD podcast: %s", err)
                    errors["base"] = "cannot_save"
                else:
                    return self._finished()
        return self.async_show_form(
            step_id="choose_star",
            data_schema=vol.Schema(
                {
                    vol.Required("podcast_id"): SelectSelector(
                        {
                            "options": [
                                self._choice(p) for p in self._matches.values()
                            ],
                            "mode": SelectSelectorMode.DROPDOWN,
                        }
                    )
                }
            ),
            description_placeholders={"query": self._query},
            errors=errors,
        )

    async def async_step_remove_stars(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove saved selections, even when ARD is offline or a show is empty."""
        runtime = self._runtime()
        if runtime is None:
            return self.async_abort(reason="not_loaded")
        if not runtime.stars.podcasts:
            return self.async_abort(reason="no_stars")
        errors = {}
        if user_input is not None:
            identifiers = user_input["podcast_ids"]
            allowed = {p.core_id or p.id for p in runtime.stars.podcasts}
            if not identifiers or not set(identifiers).issubset(allowed):
                errors["base"] = "invalid_selection"
            else:
                try:
                    await runtime.stars.async_unstar(identifiers)
                except (HomeAssistantError, OSError) as err:
                    LOGGER.warning("Failed to remove starred ARD podcasts: %s", err)
                    errors["base"] = "cannot_save"
                else:
                    return self._finished()
        return self.async_show_form(
            step_id="remove_stars",
            data_schema=vol.Schema(
                {
                    vol.Required("podcast_ids"): SelectSelector(
                        {
                            "options": [
                                self._choice(p) for p in runtime.stars.podcasts
                            ],
                            "mode": SelectSelectorMode.DROPDOWN,
                            "multiple": True,
                        }
                    )
                }
            ),
            errors=errors,
        )
