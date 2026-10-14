"""Pleos Vehicle Data native Home Assistant integration."""

from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PleosClient
from .archive import archive_store
from .coordinator import PleosCoordinator
from .statistics import TripStatistics
from .trips import fired_store, trip_store, window_store

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.EVENT]


async def async_setup_entry(hass, entry):
    coordinator = PleosCoordinator(hass, entry, PleosClient(
        async_get_clientsession(hass), entry.data["brand"], entry.data["client_id"], entry.data["client_secret"],
    ))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass, entry):
    # Forget which trips were announced and archived. The archive files themselves are kept.
    await trip_store(hass, entry.entry_id).async_remove()
    await window_store(hass, entry.entry_id).async_remove()
    await fired_store(hass, entry.entry_id).async_remove()
    await TripStatistics.async_clear(hass, entry.entry_id)
    await archive_store(hass, entry.entry_id).async_remove()
