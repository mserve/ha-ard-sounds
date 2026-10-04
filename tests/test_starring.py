"""
Native star management, persisted identity, and offline removals.

Copyright (c) 2026 Martin Stuckenbröker (mserve)
License: MIT License

SPDX-FileCopyrightText: 2026 Martin Stuckenbröker (mserve)
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ard_sounds.api import ArdSoundsConnectionError
from custom_components.ard_sounds.classes import StarredPodcasts
from custom_components.ard_sounds.const import DOMAIN, SERVICE_STAR, SERVICE_UNSTAR
from custom_components.ard_sounds.models import Podcast, Station, podcast_identifier

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, api_data: dict[str, Any], hass_storage: dict[str, Any]
) -> AsyncGenerator[tuple[MockConfigEntry, AsyncMock]]:
    """Set up the full integration with query-aware, recorded API responses."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    key = f"{DOMAIN}.{entry.entry_id}.stars"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": []}

    async def request(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "Stations":
            return api_data["stations"]
        if operation == "Search":
            return api_data["search0"]
        if operation == "Show":
            nodes = [
                *api_data["shows"]["programSets"]["nodes"],
                *api_data["search0"]["search"]["programSets"]["nodes"],
            ]
            return {
                "show": next(
                    (n for n in nodes if variables["id"] in (n["id"], n["coreId"])),
                    None,
                )
            }
        msg = f"Unexpected operation {operation}"
        raise AssertionError(msg)

    with patch(
        "custom_components.ard_sounds.api.ArdSoundsGraphQLClient.request",
        new_callable=AsyncMock,
        side_effect=request,
    ) as fake:
        assert await hass.config_entries.async_setup(entry.entry_id)
        yield entry, fake
        if hass.config_entries.async_get_entry(entry.entry_id):
            await hass.config_entries.async_unload(entry.entry_id)


async def test_storage_round_trip_and_concurrent_changes(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Persist identity/display metadata and serialize simultaneous additions."""
    stars = StarredPodcasts(hass, "test")
    podcast = Podcast(
        "1",
        "Äpfel",
        core_id="urn:ard:show:one",
        description="Description",
        image_url="https://images.example/one.png",
        station=Station("10", "Station", "ARD"),
    )
    second = Podcast("2", "Beta")
    await asyncio.gather(stars.async_star(podcast), stars.async_star(second))
    assert stars.podcasts == (podcast, second)
    stored = hass_storage[f"{DOMAIN}.test.stars"]
    assert stored["version"] == 1
    restored = StarredPodcasts(hass, "test")
    await restored.async_load()
    assert restored.podcasts == stars.podcasts
    assert restored.contains(replace(podcast, core_id=None))
    assert restored.contains(replace(podcast, id="changed"))
    await restored.async_unstar([podcast.core_id, second.id])
    assert not restored.podcasts
    await restored.async_remove()
    assert f"{DOMAIN}.test.stars" not in hass_storage


async def test_numeric_star_upgrades_to_core_identity(hass: HomeAssistant) -> None:
    """Upgrade numeric selections to core identities without creating duplicates."""
    stars = StarredPodcasts(hass, "identity")
    original = Podcast("1", "Old title")
    updated = replace(original, title="New title", core_id="urn:ard:show:one")
    with patch("custom_components.ard_sounds.classes.Store.async_save") as save:
        await stars.async_star(original)
        await stars.async_star(updated)
        await stars.async_star(updated)
        assert stars.podcasts == (updated,)
        expected_writes = 2
        assert save.await_count == expected_writes
        await stars.async_unstar(["missing"])
        assert save.await_count == expected_writes
        await stars.async_unstar([original.id])
        assert not stars.podcasts


async def test_unload_waits_for_active_star_write(hass: HomeAssistant) -> None:
    """Unload waits for an in-flight save and rejects changes through stale runtime."""
    stars = StarredPodcasts(hass, "unload")
    started = asyncio.Event()
    release = asyncio.Event()

    async def save(_data: Any) -> None:
        started.set()
        await release.wait()

    with patch(
        "custom_components.ard_sounds.classes.Store.async_save", side_effect=save
    ):
        addition = asyncio.create_task(stars.async_star(Podcast("1", "One")))
        await started.wait()
        closing = asyncio.create_task(stars.async_close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await asyncio.gather(addition, closing)
        assert stars.podcasts == (Podcast("1", "One"),)
        with pytest.raises(HomeAssistantError):
            await stars.async_star(Podcast("2", "Two"))
        with pytest.raises(HomeAssistantError):
            await stars.async_unstar(["1"])


async def test_storage_optional_fields_and_invalid_record(hass: HomeAssistant) -> None:
    """Keep valid stars when old or malformed optional display fields are present."""
    stars = StarredPodcasts(hass, "optional")
    with patch(
        "custom_components.ard_sounds.classes.Store.async_load",
        return_value=[None, {"id": "1", "title": "Valid", "image_url": 42}],
    ):
        await stars.async_load()
    assert stars.podcasts == (Podcast("1", "Valid"),)
    with (
        patch("custom_components.ard_sounds.classes.Store.async_load", return_value={}),
        pytest.raises(HomeAssistantError),
    ):
        await StarredPodcasts(hass, "invalid").async_load()


@pytest.mark.parametrize(
    "identifier",
    [
        "1110",
        "urn:ard:show:one",
        "media-source://ard_sounds/show/urn%3Aard%3Ashow%3Aone",
        "ard_sounds://show/urn%3Aard%3Ashow%3Aone",
    ],
)
def test_podcast_service_identifiers(identifier: str) -> None:
    """Support the identifiers callers can obtain from the media browser."""
    assert podcast_identifier(identifier) in ("1110", "urn:ard:show:one")


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "media-source://other/show/1",
        "media-source://ard_sounds/episode/1",
        "media-source://ard_sounds/show/1/page",
        "media-source://ard_sounds/radio",
        "ard_sounds://stream/1",
    ],
)
def test_invalid_podcast_service_identifiers(identifier: str) -> None:
    """Folders, episodes, other integrations, and malformed IDs cannot change stars."""
    with pytest.raises(ValueError, match=r"Invalid|Select"):
        podcast_identifier(identifier)


async def test_service_stars_survive_reload_and_options_changes(
    hass: HomeAssistant,
    loaded_entry: tuple[MockConfigEntry, AsyncMock],
    hass_storage: dict[str, Any],
) -> None:
    """Reload restores stars and deleting the config entry removes its saved data."""
    entry, _request = loaded_entry
    for identifier in ("1110", "media-source://ard_sounds/show/1110"):
        await hass.services.async_call(
            DOMAIN, SERVICE_STAR, {"podcast_id": identifier}, blocking=True
        )
    assert len(entry.runtime_data.stars.podcasts) == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.runtime_data.stars.podcasts[0].id == "1110"
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "preferences"}
    )
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"page_size": 20, "episode_limit": 10}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.stars.podcasts[0].id == "1110"
    assert await hass.config_entries.async_remove(entry.entry_id)
    assert f"{DOMAIN}.{entry.entry_id}.stars" not in hass_storage


async def test_unstar_without_remote_access(
    hass: HomeAssistant, loaded_entry: tuple[MockConfigEntry, AsyncMock]
) -> None:
    """Unstar remains usable for removed shows and never requires an API request."""
    entry, request = loaded_entry
    await hass.services.async_call(
        DOMAIN, SERVICE_STAR, {"podcast_id": "1110"}, blocking=True
    )
    request.reset_mock()
    request.side_effect = ArdSoundsConnectionError
    for identifier in ("1110", "urn:ard:show:missing"):
        await hass.services.async_call(
            DOMAIN, SERVICE_UNSTAR, {"podcast_id": identifier}, blocking=True
        )
    assert not entry.runtime_data.stars.podcasts
    request.assert_not_awaited()


async def test_star_service_failures(
    hass: HomeAssistant, loaded_entry: tuple[MockConfigEntry, AsyncMock]
) -> None:
    """Expected failures are HA-handled errors and cannot create invalid selections."""
    entry, request = loaded_entry
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_STAR, {"podcast_id": "missing"}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_STAR,
            {"podcast_id": "media-source://ard_sounds/episode/1"},
            blocking=True,
        )
    request.side_effect = ArdSoundsConnectionError
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, SERVICE_STAR, {"podcast_id": "1110"}, blocking=True
        )
    assert not entry.runtime_data.stars.podcasts
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_UNSTAR, {"podcast_id": "1110"}, blocking=True
        )


async def test_native_add_remove_dialog(
    hass: HomeAssistant, loaded_entry: tuple[MockConfigEntry, AsyncMock]
) -> None:
    """Require explicit selection, distinguish duplicate titles, and avoid reloads."""
    entry, _request = loaded_entry
    runtime = entry.runtime_data
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "add_star"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"query": "Wissen"}
    )
    assert flow["step_id"] == "choose_star"
    assert not runtime.stars.podcasts
    selector = next(iter(flow["data_schema"].schema.values()))
    choices = selector.config["options"]
    assert choices[0]["label"] != choices[1]["label"]
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            flow["flow_id"], {"podcast_id": "forged"}
        )
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"podcast_id": choices[0]["value"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data is runtime
    assert len(runtime.stars.podcasts) == 1
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "remove_stars"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"podcast_ids": []}
    )
    assert flow["errors"] == {"base": "invalid_selection"}
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"podcast_ids": [choices[0]["value"]]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data is runtime
    assert not runtime.stars.podcasts


async def test_native_search_errors_and_no_stars(
    hass: HomeAssistant, loaded_entry: tuple[MockConfigEntry, AsyncMock]
) -> None:
    """Report query and API failures without changing saved selections."""
    entry, request = loaded_entry
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "remove_stars"}
    )
    assert flow["reason"] == "no_stars"
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "add_star"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"query": "   "}
    )
    assert flow["errors"] == {"base": "invalid_query"}
    request.side_effect = ArdSoundsConnectionError
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"query": "Wissen"}
    )
    assert flow["errors"] == {"base": "cannot_connect"}
    request.side_effect = None
    request.return_value = {"search": {"programSets": {"nodes": []}}}
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"query": "Missing"}
    )
    assert flow["errors"] == {"base": "no_results"}
    hass.config_entries.options.async_abort(flow["flow_id"])
    assert not entry.runtime_data.stars.podcasts


async def test_options_manage_stars_requires_loaded_entry(
    hass: HomeAssistant,
) -> None:
    """Report a translated abort when the integration is unloaded."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "add_star"}
    )
    assert flow["reason"] == "not_loaded"


async def test_cancel_after_search_and_save_failure(
    hass: HomeAssistant, loaded_entry: tuple[MockConfigEntry, AsyncMock]
) -> None:
    """Canceling a choice changes nothing, and failed writes keep prior selections."""
    entry, _request = loaded_entry
    for fail_save in (False, True):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "add_star"}
        )
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"query": "Wissen"}
        )
        if fail_save:
            with patch(
                "custom_components.ard_sounds.classes.Store.async_save",
                side_effect=OSError("Disk unavailable"),
            ):
                flow = await hass.config_entries.options.async_configure(
                    flow["flow_id"],
                    {"podcast_id": "urn:ard:show:8ffdb152c2603d4f"},
                )
            assert flow["errors"] == {"base": "cannot_save"}
        hass.config_entries.options.async_abort(flow["flow_id"])
        assert not entry.runtime_data.stars.podcasts
