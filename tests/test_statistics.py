"""Hourly statistics at the hour each trip was driven."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import valid_statistic_id

from custom_components.pleos_vehicle_data import async_remove_entry
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.statistics import TripStatistics, statistics_store, trip_share

from support import CAR, HomeAssistantTestCase, stamp

CAR_ID = vehicle_id("kia", CAR["vin"])
IMPORT = "custom_components.pleos_vehicle_data.statistics.async_add_external_statistics"


def drive(start, km_from, km_to, minutes=30):
    return {"triggerStartTime": start, "tripLength": minutes * 60000, "odometer": {"value": [km_from, km_to]}}


def speeds(start, *values, unit="kph"):
    return {"triggerStartTime": start, "tripLength": 60000,
            "location": {"speed": list(values), "speedUnit": [unit] * len(values)}}


def same_hour(minute):
    """A time three hours ago at the given minute, so trips can share an hour reliably."""
    base = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    return (base + timedelta(minutes=minute)).isoformat()


def hour(text):
    return datetime.fromisoformat(text).replace(minute=0, second=0, microsecond=0)


class ShareTests(HomeAssistantTestCase):
    def test_driving_and_speed_trips_add_to_their_start_hour(self):
        start = stamp(90)
        self.assertEqual(trip_share("driving/data", drive(start, 100, 112.5, 45)),
                         {"hour": hour(start).isoformat(), "km": 12.5, "minutes": 45.0})
        self.assertEqual(trip_share("speed", speeds(start, 10, 50, 30)),
                         {"hour": hour(start).isoformat(), "speed_n": 3, "speed_sum": 90.0,
                          "speed_min": 10, "speed_max": 50})
        mph = trip_share("speed", speeds(start, 10, 20, unit="mph"))
        self.assertAlmostEqual(mph["speed_max"], 32.18688)
        self.assertIsNone(trip_share("status/data", {"triggerStartTime": start, "outsideTemperature": {"value": [5]}}))


class StatisticsTests(HomeAssistantTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hass.config.components.add("recorder")
        self.imports = []
        patcher = patch(IMPORT, side_effect=lambda hass, metadata, rows: self.imports.append((metadata, list(rows))))
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self, kind):
        return [row for metadata, rows in self.imports if metadata["statistic_id"].endswith("_" + kind) for row in rows]

    async def ledger(self):
        statistics = TripStatistics(self.hass, self.entry.entry_id)
        await statistics.load()
        return statistics

    async def test_sums_are_cumulative_by_drive_hour(self):
        statistics = await self.ledger()
        morning, evening = stamp(600), stamp(120)
        for start, km in ((morning, 10), (evening, 5)):
            statistics.add(CAR_ID, "driving/data", drive(start, 100, 100 + km), "Ioniq")
        await statistics.flush(True)
        self.assertEqual([(row["start"], row["sum"]) for row in self.rows("distance")],
                         [(hour(morning), 10.0), (hour(evening), 15.0)])
        self.assertEqual([row["sum"] for row in self.rows("driving_time")], [30.0, 60.0])
        metadata = {m["statistic_id"]: m for m, _ in self.imports}
        distance = metadata[f"pleos_vehicle_data:{CAR_ID}_distance"]
        self.assertEqual((distance["name"], distance["unit_of_measurement"], distance["unit_class"],
                          distance["mean_type"], distance["has_sum"], distance["source"]),
                         ("Ioniq distance driven", "km", "distance", StatisticMeanType.NONE, True, "pleos_vehicle_data"))
        self.assertTrue(all(valid_statistic_id(statistic_id) for statistic_id in metadata))

    async def test_a_late_trip_corrects_its_hour_and_every_later_sum(self):
        statistics = await self.ledger()
        early, late = stamp(600), stamp(120)
        statistics.add(CAR_ID, "driving/data", drive(late, 0, 5), "Ioniq")
        await statistics.flush(True)
        self.imports.clear()
        statistics.add(CAR_ID, "driving/data", drive(early, 0, 10), "Ioniq")
        await statistics.flush(True)
        self.assertEqual([(row["start"], row["sum"]) for row in self.rows("distance")],
                         [(hour(early), 10.0), (hour(late), 15.0)])

    async def test_a_trip_returned_again_replaces_its_share(self):
        statistics = await self.ledger()
        start = stamp(120)
        self.assertTrue(statistics.add(CAR_ID, "driving/data", drive(start, 0, 5), "Ioniq"))
        self.assertFalse(statistics.add(CAR_ID, "driving/data", drive(start, 0, 5), "Ioniq"))
        statistics.add(CAR_ID, "driving/data", drive(start, 0, 8), "Ioniq")
        await statistics.flush(True)
        self.assertEqual(self.rows("distance")[-1]["sum"], 8.0)

    async def test_speed_is_weighted_by_samples_and_extremes_follow_changes(self):
        statistics = await self.ledger()
        statistics.add(CAR_ID, "speed", speeds(same_hour(10), 100, 120), "Ioniq")
        other = speeds(same_hour(40), 10)
        statistics.add(CAR_ID, "speed", other, "Ioniq")
        await statistics.flush(True)
        row = self.rows("speed")[-1]
        self.assertEqual((row["mean"], row["min"], row["max"]), (76.667, 10, 120))
        self.imports.clear()
        statistics.add(CAR_ID, "speed", other | {"location": {"speed": [40], "speedUnit": ["kph"]}}, "Ioniq")
        await statistics.flush(True)
        row = self.rows("speed")[-1]
        self.assertEqual((row["mean"], row["min"], row["max"]), (86.667, 40, 120))
        speed_metadata = next(m for m, _ in self.imports if m["statistic_id"].endswith("_speed"))
        self.assertEqual((speed_metadata["mean_type"], speed_metadata["has_sum"]), (StatisticMeanType.ARITHMETIC, False))

    async def test_without_recorder_hours_wait_and_are_imported_later(self):
        self.hass.config.components.remove("recorder")
        statistics = await self.ledger()
        statistics.add(CAR_ID, "driving/data", drive(stamp(120), 0, 5), "Ioniq")
        await statistics.flush(True)
        self.assertEqual(self.imports, [])
        self.hass.config.components.add("recorder")
        restarted = await self.ledger()
        await restarted.flush(False)
        self.assertEqual(self.rows("distance")[-1]["sum"], 5.0)

    async def test_polls_import_returned_trips_once(self):
        self.client.vehicles = AsyncMock(return_value=[CAR | {"nickname": "Ioniq"}])
        trip = drive(stamp(120), 100, 110)
        self.client.get = AsyncMock(side_effect=lambda path: {"trips": [trip]} if "/driving/data?" in path else None)
        await self.coordinator._async_update_data()
        self.assertEqual(self.rows("distance")[-1]["sum"], 10.0)
        self.imports.clear()
        self.coordinator.trips.cache.clear()
        await self.coordinator._async_update_data()
        self.assertEqual(self.imports, [])

    async def test_removing_the_entry_clears_statistics_and_ledger(self):
        statistics = await self.ledger()
        statistics.add(CAR_ID, "driving/data", drive(stamp(120), 0, 5), "Ioniq")
        await statistics.flush(True)
        recorder = MagicMock()
        with patch("custom_components.pleos_vehicle_data.statistics.get_instance", return_value=recorder):
            await async_remove_entry(self.hass, self.entry)
        cleared = recorder.async_clear_statistics.call_args.args[0]
        self.assertEqual(sorted(cleared), sorted(f"pleos_vehicle_data:{CAR_ID}_{kind}"
                                                 for kind in ("distance", "driving_time", "speed")))
        self.assertIsNone(await statistics_store(self.hass, self.entry.entry_id).async_load())
