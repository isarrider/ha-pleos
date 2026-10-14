"""Hourly long-term statistics placed at the hour each trip was driven.

Trip data reaches Home Assistant up to a day after the drive, so states and events carry the
time it arrived. These statistics are imported at the hour the trip started instead, so
Home Assistant's statistics graphs show distance, driving time and speed on the right day:

    pleos_vehicle_data:<vehicle>_distance      km driven, cumulative (graph "change" per day)
    pleos_vehicle_data:<vehicle>_driving_time  minutes driven, cumulative
    pleos_vehicle_data:<vehicle>_speed         km/h, hourly mean, minimum and maximum

Pleos gives no time per sample, so a whole trip counts in the hour it started. Hourly totals
are kept in a ledger; a trip returned again with other content replaces its earlier share,
and every hour from the earliest changed one onwards is imported again so sums stay right.
"""

import logging
from datetime import datetime, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.helpers.storage import Store

from .const import DOMAIN, TRIP_MEMORY_DAYS
from .data import finite_number, reported_age, trip_readings

LOGGER = logging.getLogger(__name__)
MPH_TO_KMH = 1.609344
STATISTICS = {
    "distance": ("Distance driven", "km", "distance", StatisticMeanType.NONE),
    "driving_time": ("Driving time", "min", "duration", StatisticMeanType.NONE),
    "speed": ("Speed while driving", "km/h", "speed", StatisticMeanType.ARITHMETIC),
}


def statistics_store(hass, entry_id):
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.statistics")


def statistic_id(car_id, kind):
    return f"{DOMAIN}:{car_id}_{kind}"


def _hour(start):
    stamp = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
    return stamp.replace(minute=0, second=0, microsecond=0).isoformat()


def trip_share(endpoint, trip):
    """What one trip adds to its hour, or None when it adds nothing."""
    current, start = trip_readings(endpoint, {"trips": [trip]})
    if start is None:
        return None
    share = {}
    if endpoint == "driving/data":
        if "driving_data_trip_distance" in current:
            share["km"] = current["driving_data_trip_distance"]["value"]
        length = trip.get("tripLength")
        if finite_number(length) and length >= 0:
            share["minutes"] = length / 60000
    elif endpoint == "speed" and "speed_location_speed" in current:
        reading = current["speed_location_speed"]
        factor = MPH_TO_KMH if reading.get("unit_of_measurement") == "mph" else 1
        attributes = reading["attributes"]
        count = attributes["valid_samples"]
        share |= {"speed_n": count, "speed_sum": attributes["trip_mean"] * count * factor,
                  "speed_min": attributes["trip_min"] * factor, "speed_max": attributes["trip_max"] * factor}
    return {"hour": _hour(start), **share} if share else None


class TripStatistics:
    def __init__(self, hass, entry_id):
        self.hass = hass
        self.store = statistics_store(hass, entry_id)
        # {"trips": {car: {"<endpoint>|<start>": share}}, "hours": {car: {hour: totals}},
        #  "pending": {car: earliest hour not yet imported}}
        self.ledger = None
        self.names = {}

    async def load(self):
        if self.ledger is None:
            stored = await self.store.async_load()
            self.ledger = stored if isinstance(stored, dict) else {}
            for key in ("trips", "hours", "pending"):
                self.ledger.setdefault(key, {})

    def add(self, car_id, endpoint, trip, name):
        """Account for one returned trip; returns True when an hour changed."""
        self.names[car_id] = name
        share = trip_share(endpoint, trip)
        trips = self.ledger["trips"].setdefault(car_id, {})
        key = f"{endpoint}|{trip['triggerStartTime']}"
        old = trips.get(key)
        if old == share:
            return False
        hours = self.ledger["hours"].setdefault(car_id, {})
        for part, sign in ((old, -1), (share, 1)):
            if not part:
                continue
            totals = hours.setdefault(part["hour"], {})
            for field in ("km", "minutes", "speed_n", "speed_sum"):
                if field in part:
                    totals[field] = totals.get(field, 0) + sign * part[field]
            self._mark_pending(car_id, part["hour"])
        if share:
            trips[key] = share
        else:
            trips.pop(key, None)
        # Minimum and maximum cannot be subtracted: rebuild them for the affected hours.
        for hour in {part["hour"] for part in (old, share) if part}:
            self._rebuild_extremes(car_id, hour)
        return True

    def _mark_pending(self, car_id, hour):
        pending = self.ledger["pending"].get(car_id)
        self.ledger["pending"][car_id] = min(pending, hour) if pending else hour

    def _rebuild_extremes(self, car_id, hour):
        totals = self.ledger["hours"][car_id][hour]
        shares = [share for share in self.ledger["trips"][car_id].values()
                  if share["hour"] == hour and "speed_min" in share]
        if shares:
            totals["speed_min"] = min(share["speed_min"] for share in shares)
            totals["speed_max"] = max(share["speed_max"] for share in shares)
        else:
            for field in ("speed_min", "speed_max", "speed_n", "speed_sum"):
                totals.pop(field, None)

    async def flush(self, changed):
        """Import every hour from the earliest changed one, then save the ledger if anything changed."""
        if "recorder" in self.hass.config.components and self.ledger["pending"]:
            # Without the recorder, hours stay pending and are imported once it is available.
            for car_id, since in list(self.ledger["pending"].items()):
                self._import(car_id, since)
                del self.ledger["pending"][car_id]
            changed = True
        if self._prune() or changed:
            await self.store.async_save(self.ledger)

    def _import(self, car_id, since):
        name = self.names.get(car_id, car_id)
        rows = {"distance": [], "driving_time": [], "speed": []}
        totals = {"km": 0.0, "minutes": 0.0}
        for hour, values in sorted(self.ledger["hours"].get(car_id, {}).items()):
            totals["km"] += values.get("km", 0)
            totals["minutes"] += values.get("minutes", 0)
            if hour < since:
                continue
            start = datetime.fromisoformat(hour)
            for kind, field in (("distance", "km"), ("driving_time", "minutes")):
                rows[kind].append({"start": start, "state": round(totals[field], 3), "sum": round(totals[field], 3)})
            if values.get("speed_n"):
                rows["speed"].append({"start": start, "mean": round(values["speed_sum"] / values["speed_n"], 3),
                                      "min": values["speed_min"], "max": values["speed_max"]})
        for kind, (label, unit, unit_class, mean_type) in STATISTICS.items():
            if rows[kind]:
                async_add_external_statistics(self.hass, {
                    "statistic_id": statistic_id(car_id, kind), "source": DOMAIN, "name": f"{name} {label.lower()}",
                    "unit_of_measurement": unit, "unit_class": unit_class, "mean_type": mean_type,
                    "has_sum": mean_type is StatisticMeanType.NONE,
                }, rows[kind])

    def _prune(self):
        # Trips older than any query reaches back cannot change any more; their hours stay.
        limit = TRIP_MEMORY_DAYS * 86400
        pruned = False
        for trips in self.ledger["trips"].values():
            for key in [key for key in trips if (age := reported_age(key.split("|", 1)[1])) is None or age > limit]:
                del trips[key]
                pruned = True
        return pruned

    @staticmethod
    async def async_clear(hass, entry_id):
        """Remove the ledger and the statistics it imported."""
        store = statistics_store(hass, entry_id)
        ledger = await store.async_load() or {}
        cars = set(ledger.get("hours", {})) | set(ledger.get("trips", {}))
        if cars and "recorder" in hass.config.components:
            get_instance(hass).async_clear_statistics(
                [statistic_id(car_id, kind) for car_id in cars for kind in STATISTICS])
        await store.async_remove()
