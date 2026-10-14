"""Shared vehicle identity and dynamic discovery."""

from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRANDS, DOMAIN
from .data import reported_age
from .names import ENTITY_NAMES


def discover(entry, async_add_entities, domain, factory):
    coordinator = entry.runtime_data
    seen = set()

    def add_new():
        entities = []
        for car_id, car in coordinator.data["cars"].items():
            items = {"location": {"name": "Location", "source": "locations"}} if domain == "device_tracker" and car["location"] else car["readings"] if domain != "device_tracker" else {}
            for key, spec in items.items():
                if (domain == "device_tracker" or spec["domain"] == domain) and (car_id, key) not in seen:
                    seen.add((car_id, key))
                    entities.append(factory(coordinator, car_id, key, spec))
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class VehicleEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    platform_domain = None  # Set by each platform's entity class.

    def __init__(self, coordinator, car_id, key, spec):
        super().__init__(coordinator)
        self.car_id, self.key, self.spec = car_id, key, spec
        self._attr_unique_id = car_id + "_" + key
        if key in ENTITY_NAMES[self.platform_domain]:
            self._attr_translation_key = key
        else:
            # Undocumented fields keep the English name generated from their path.
            self._attr_name = spec["name"]
        vehicle = coordinator.data["cars"][car_id]["vehicle"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, car_id)},
            name=vehicle.get("nickname") or vehicle.get("sellname") or BRANDS[coordinator.brand] + " vehicle",
            manufacturer=BRANDS[coordinator.brand], model=vehicle.get("sellname") or vehicle.get("name") or "Vehicle",
        )
        self._attr_entity_category = EntityCategory(spec["entity_category"]) if spec.get("entity_category") else None

    @property
    def suggested_object_id(self):
        # New entity IDs always come from the English name, whatever the system language.
        return ENTITY_NAMES[self.platform_domain].get(self.key, self.spec["name"])

    @property
    def car(self):
        return self.coordinator.data["cars"].get(self.car_id)

    @property
    def reading(self):
        return self.car["readings"].get(self.key) if self.car else None

    @property
    def available(self):
        if not super().available or not self.car:
            return False
        if self.key == "location":
            present = self.car["location"] is not None
        else:
            present = self.reading is not None
        age = reported_age(self.car["sources"].get(self.spec["source"]))
        if self.spec.get("entity_category") == "diagnostic" or self.spec.get("historical"):
            return present
        # Parked cars can return the same valid snapshot for days.
        return present and age is not None

    @property
    def extra_state_attributes(self):
        if not self.car:
            return None
        attrs = {"source": self.spec["source"]}
        if not self.spec.get("historical"):
            attrs["reported_at"] = self.car["sources"].get(self.spec["source"])
            age = reported_age(attrs["reported_at"])
            if age is not None:
                attrs["data_age_minutes"] = round(age / 60, 1)
                attrs["data_stale"] = age > self.coordinator.options["stale_minutes"] * 60
        return attrs | (self.reading.get("attributes", {}) if self.reading else {})
