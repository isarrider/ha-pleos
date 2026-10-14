"""Diagnostics download: what the integration received, parsed and stored, with personal data removed."""

import time
from datetime import datetime

from homeassistant.components.diagnostics import REDACTED, async_redact_data

from .const import DOMAIN
from .export import without_credentials

# Credentials, the VIN, the user's own vehicle name and precise coordinates.
TO_REDACT = {"client_id", "client_secret", "vin", "nickname", "latitude", "longitude", "accessToken", "export_token"}
# Readings whose state is a coordinate.
REDACTED_READINGS = {"locations_location_latitude", "locations_location_longitude"}


def scrub(value, vins):
    """Redact keys anywhere, and VINs inside any text (for example request paths)."""
    value = async_redact_data(value, TO_REDACT)

    def walk(item):
        if isinstance(item, dict):
            return {walk(key): walk(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(child) for child in item]
        if isinstance(item, str):
            for vin in vins:
                item = item.replace(vin, REDACTED)
            return item
        if isinstance(item, datetime):
            return item.isoformat()
        return item
    return walk(value)


def _coordinator_state(coordinator):
    retry_in = coordinator.client.retry_at - time.monotonic()
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        "status": (coordinator.data or {}).get("status"),
        "update_interval_minutes": coordinator.update_interval.total_seconds() / 60,
        "rate_limited_for_seconds": round(retry_in) if retry_in > 0 else 0,
        "api_metadata": list(coordinator.client.response_meta.values()),
    }


def _trip_state(trips):
    return {
        "interval_minutes": trips.interval.total_seconds() / 60,
        "max_query_days": trips.max_days,
        "fetched_up_to": trips.window,
        "announced_trips": trips.seen,
        "cached_results_fetched_at": {car_id: cached["fetched_at"] for car_id, cached in trips.cache.items()},
    }


def _archive_state(archive):
    if archive is None:
        return {"enabled": False}
    hashes = archive.hashes or {}
    return {"enabled": True, "directory": str(archive.directory), "status": archive.status,
            "remembered_snapshots": len(hashes.get("snapshots", {})),
            "remembered_trips": sum(len(slots) for slots in hashes.get("trips", {}).values())}


def _cars(coordinator):
    cars = (coordinator.data or {}).get("cars") or {}
    return {car_id: car | {"readings": {key: reading | {"value": REDACTED} if key in REDACTED_READINGS else reading
                                         for key, reading in car.get("readings", {}).items()}}
            for car_id, car in cars.items()}


def _export_state(exporter):
    if exporter is None:
        return {"enabled": False}
    return {"enabled": True, "format": exporter.format, "status": exporter.status,
            "remembered_trips": sum(len(starts) for starts in (exporter.exported or {}).values())}


def _vins(coordinator):
    return [car["vehicle"]["vin"] for car in ((coordinator.data or {}).get("cars") or {}).values()
            if isinstance(car.get("vehicle", {}).get("vin"), str)]


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return scrub({
        "entry": {"title": entry.title, "data": dict(entry.data),
                  "options": dict(entry.options) | ({"export_url": without_credentials(entry.options["export_url"])}
                                                    if entry.options.get("export_url") else {})},
        "coordinator": _coordinator_state(coordinator),
        "trips": _trip_state(coordinator.trips),
        "archive": _archive_state(coordinator.archive),
        "export": _export_state(coordinator.exporter),
        "cars": _cars(coordinator),
    }, _vins(coordinator))


async def async_get_device_diagnostics(hass, entry, device):
    coordinator = entry.runtime_data
    cars = _cars(coordinator)
    car_id = next((identifier for domain, identifier in device.identifiers if domain == DOMAIN), None)
    if car_id not in cars:
        # The manufacturer's API device: account-level state only.
        data = await async_get_config_entry_diagnostics(hass, entry)
        data.pop("cars")
        return data
    return scrub({"vehicle_id": car_id, "car": cars[car_id]}, _vins(coordinator))
