"""Readings and account connection status."""

from datetime import datetime
import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRANDS, DOMAIN
from .entity import VehicleEntity, discover
from .names import ENTITY_NAMES


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ConnectionSensor(entry.runtime_data)])
    discover(entry, async_add_entities, "sensor", ReadingSensor)


class ReadingSensor(VehicleEntity, SensorEntity):
    platform_domain = "sensor"
    # Complete trip arrays are available now, without copying them into recorder history.
    _unrecorded_attributes = frozenset({"data", "vin"})

    def __init__(self, coordinator, car_id, key, spec):
        super().__init__(coordinator, car_id, key, spec)
        self._attr_native_unit_of_measurement = spec.get("unit_of_measurement")
        self._attr_device_class = spec.get("device_class")
        self._attr_state_class = spec.get("state_class")
        self._attr_options = spec.get("options")

    @property
    def native_unit_of_measurement(self):
        return self.reading.get("unit_of_measurement") if self.reading else self.spec.get("unit_of_measurement")

    @property
    def native_value(self):
        value = self.reading["value"] if self.reading else None
        if value is not None and self.device_class == "timestamp":
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class ConnectionSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = "enum"
    _attr_translation_key = "connection"
    _attr_options = ["connected", "no_vehicles", "key_required", "api_unavailable", "rate_limited"]

    # The entity ID stays English, like every other entity of this integration.
    suggested_object_id = ENTITY_NAMES["sensor"]["connection"]

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.brand + "_api_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.brand + "_api")}, name=BRANDS[coordinator.brand] + " API", manufacturer="Pleos",
        )

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        if self.coordinator.last_update_success:
            return self.coordinator.data["status"]
        if isinstance(self.coordinator.last_exception, ConfigEntryAuthFailed):
            return "key_required"
        return "rate_limited" if self.coordinator.client.retry_at > time.monotonic() else "api_unavailable"
