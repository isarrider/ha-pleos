"""Trip export to VictoriaMetrics and InfluxDB in line protocol."""

import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, web

from custom_components.pleos_vehicle_data.coordinator import PleosCoordinator
from custom_components.pleos_vehicle_data.data import vehicle_id
from custom_components.pleos_vehicle_data.diagnostics import async_get_config_entry_diagnostics
from custom_components.pleos_vehicle_data.export import BATCH_LINES, trip_lines
from custom_components.pleos_vehicle_data.sensor import ExportSensor, async_setup_entry

from support import CAR, HomeAssistantTestCase, stamp

START = "2026-10-14T08:00:00Z"
START_MS = round(datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() * 1000)


class TripLineTests(unittest.TestCase):
    def lines(self, endpoint, **trip):
        return trip_lines("kia_abc", "kia", endpoint, {"triggerStartTime": START, "tripLength": 2000} | trip)

    def test_samples_are_spread_over_the_trip_and_normalised(self):
        lines = self.lines("speed", location={"speed": [10, 20, 30], "speedUnit": ["kph", "kph", "mph"]},
                           wheel={"speedFrontLeft": [5, 6]})
        tags = "brand=kia,endpoint=speed,timing=interpolated,vehicle=kia_abc"
        self.assertEqual(lines[:3], [
            f"pleos_trip,{tags} location_speed_kmh=10.0,wheel_speed_front_left_kmh=5.0 {START_MS}",
            f"pleos_trip,{tags} location_speed_kmh=20.0 {START_MS + 1000}",
            f"pleos_trip,{tags} location_speed_kmh=48.28032,wheel_speed_front_left_kmh=6.0 {START_MS + 2000}",
        ])
        self.assertEqual(lines[3], f"pleos_trip_summary,brand=kia,endpoint=speed,timing=measured,vehicle=kia_abc "
                                   f"duration_s=2.0,sample_times=3.0 {START_MS}")

    def test_units_are_named_and_tyre_pressure_is_in_kpa(self):
        lines = self.lines("status/data", tire={"pressureFrontLeft": [36, 72], "pressureUnit": ["psi", "kpa"]},
                           outsideTemperature={"value": [12]})
        self.assertIn("tire_pressure_front_left_kpa=248.211252", lines[0])
        self.assertIn("outside_temperature_value_c=12.0", lines[0])
        self.assertIn("tire_pressure_front_left_kpa=360.0", lines[1])
        self.assertNotIn("pressure_unit", "\n".join(lines))
        powertrain = self.lines("powertrains/data", engine={"rpm": [800]}, batteries={"hv": {"voltage": [650]}})
        self.assertIn("batteries_hv_voltage_v=650.0,engine_rpm=800.0", powertrain[0])

    def test_text_booleans_undocumented_fields_and_escaping(self):
        lines = self.lines("driving/data", transmission={"gear": ["P", 'D "sport"']}, extra={"heaterOn": [True, False]},
                           tripId="a b,c")
        self.assertIn('transmission_gear="P"', lines[0])
        self.assertIn('extra_heater_on=true', lines[0])
        self.assertIn('transmission_gear="D \\"sport\\""', lines[1])
        self.assertIn('trip_id="a b,c"', lines[-1])
        escaped = trip_lines("kia x,y", "kia", "speed", {"triggerStartTime": START, "location": {"speed": [1]}})
        self.assertIn("vehicle=kia\\ x\\,y", escaped[0])

    def test_invalid_samples_are_skipped_and_missing_length_assumes_seconds(self):
        lines = trip_lines("v", "kia", "powertrains/data",
                           {"triggerStartTime": START, "engine": {"rpm": [800, "invalid", 99999, 900]}})
        self.assertEqual([line.split(" ")[-1] for line in lines[:-1]], [str(START_MS), str(START_MS + 3000)])
        self.assertIn("timing=assumed_1s", lines[0])
        self.assertNotIn("duration_s", lines[-1])

    def test_integers_are_sent_as_floats(self):
        self.assertIn("engine_rpm=800.0", self.lines("powertrains/data", engine={"rpm": [800]})[0])


class ExportTests(HomeAssistantTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.received, self.fail_with = [], None
        app = web.Application()

        async def write(request):
            if self.fail_with:
                return web.Response(status=self.fail_with, text="no space left")
            self.received.append({"path": request.path, "query": dict(request.query),
                                  "auth": request.headers.get("Authorization"), "body": await request.text()})
            return web.Response(status=204)
        app.router.add_post("/write", write)
        app.router.add_post("/api/v2/write", write)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        # Home Assistant's shared session needs its network setup; a plain session does the same job here.
        self.session = ClientSession()
        session_patch = patch("custom_components.pleos_vehicle_data.export.async_get_clientsession",
                              return_value=self.session)
        session_patch.start()
        self.addCleanup(session_patch.stop)

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()
        await super().asyncTearDown()

    def exporting(self, **options):
        self.hass.config_entries.async_update_entry(self.entry, options={
            "export_format": "influx_v1", "export_url": self.url, "export_database": "car"} | options)
        coordinator = PleosCoordinator(self.hass, self.entry, self.client)
        self.addAsyncCleanup(coordinator.async_shutdown)
        return coordinator

    def serve(self, *trips):
        self.client.vehicles = AsyncMock(return_value=[CAR])
        self.client.get = AsyncMock(side_effect=lambda path: {"trips": list(trips)} if "/speed?" in path else None)

    async def poll(self, coordinator):
        coordinator.trips.cache.clear()
        return await coordinator._async_update_data()

    def trip(self, minutes_ago, *speeds):
        return {"triggerStartTime": stamp(minutes_ago), "tripLength": 1000 * len(speeds),
                "location": {"speed": list(speeds), "speedUnit": ["kph"] * len(speeds)}}

    async def test_victoriametrics_receives_each_trip_once(self):
        coordinator = self.exporting(export_token="secret")
        first, second = self.trip(120, 10, 20), self.trip(30, 30)
        self.serve(first, second)
        await self.poll(coordinator)
        self.assertEqual(len(self.received), 1)
        request = self.received[0]
        self.assertEqual((request["path"], request["query"], request["auth"]),
                         ("/write", {"db": "car", "precision": "ms"}, "Bearer secret"))
        body = request["body"].splitlines()
        self.assertEqual(sum(line.startswith("pleos_trip,") for line in body), 3)
        self.assertEqual(sum(line.startswith("pleos_trip_summary,") for line in body), 2)
        self.assertIn(f"vehicle={vehicle_id('kia', CAR['vin'])}", body[0])
        self.assertNotIn(CAR["vin"], request["body"])

        await self.poll(coordinator)
        restarted = self.exporting(export_token="secret")
        await self.poll(restarted)
        self.assertEqual(len(self.received), 1)

        # A trip whose samples were completed later is sent again; the new one is sent too.
        self.serve(first | {"location": {"speed": [10, 20, 25], "speedUnit": ["kph"] * 3}}, second, self.trip(5, 50))
        await self.poll(restarted)
        self.assertEqual(len(self.received), 2)
        self.assertEqual(sum(line.startswith("pleos_trip_summary,") for line in self.received[1]["body"].splitlines()), 2)

    async def test_influxdb_v2_target_and_token(self):
        coordinator = self.exporting(export_format="influx_v2", export_database="bucket1", export_org="home",
                                     export_token="tkn")
        self.serve(self.trip(30, 10))
        await self.poll(coordinator)
        request = self.received[0]
        self.assertEqual((request["path"], request["query"], request["auth"]),
                         ("/api/v2/write", {"org": "home", "bucket": "bucket1", "precision": "ms"}, "Token tkn"))

    async def test_failed_writes_are_reported_and_retried(self):
        coordinator = self.exporting()
        self.serve(self.trip(30, 10))
        self.fail_with = 507
        await self.poll(coordinator)
        status = coordinator.exporter.status
        self.assertEqual((status["state"], status["last_error"]), ("error", "HTTP 507: no space left"))
        self.fail_with = None
        await self.poll(coordinator)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(coordinator.exporter.status["state"], "ok")

    async def test_unreachable_database_is_an_error_not_a_crash(self):
        coordinator = self.exporting(export_url="http://127.0.0.1:9")
        self.serve(self.trip(30, 10))
        result = await self.poll(coordinator)
        self.assertEqual(result["status"], "connected")
        self.assertEqual(coordinator.exporter.status["state"], "error")

    async def test_large_exports_are_split_between_trips(self):
        coordinator = self.exporting()
        speeds = list(range(1, 201))
        trips = [self.trip(30 + minute, *speeds) for minute in range(40)]
        self.serve(*trips)
        await self.poll(coordinator)
        self.assertGreater(len(self.received), 1)
        for request in self.received:
            lines = request["body"].splitlines()
            self.assertLessEqual(len(lines), BATCH_LINES)
            # Every request ends with complete trips.
            self.assertEqual(lines[-1].split(",")[0], "pleos_trip_summary")
        self.assertEqual(sum(r["body"].count("pleos_trip_summary,") for r in self.received), 40)

    async def test_export_off_creates_no_exporter_or_sensor(self):
        self.entry.runtime_data = self.coordinator
        self.assertIsNone(self.coordinator.exporter)
        added = []
        with patch("custom_components.pleos_vehicle_data.sensor.discover"):
            await async_setup_entry(self.hass, self.entry, added.extend)
        self.assertNotIn("ExportSensor", [type(entity).__name__ for entity in added])

    async def test_credentials_in_the_url_are_never_shown_or_logged(self):
        url = self.url.replace("http://", "http://user:pa55@")
        coordinator = self.exporting(export_url=url)
        self.assertEqual(coordinator.exporter.status["target"], f"{self.url}/write?db=car")
        self.fail_with = 500
        self.serve(self.trip(30, 10))
        with self.assertLogs("custom_components.pleos_vehicle_data.export", "WARNING") as logs:
            await self.poll(coordinator)
        self.assertNotIn("pa55", "\n".join(logs.output))
        self.entry.runtime_data = coordinator
        diagnostics = await async_get_config_entry_diagnostics(self.hass, self.entry)
        text = json.dumps(diagnostics)
        self.assertNotIn("pa55", text)
        self.assertEqual(diagnostics["export"]["status"]["state"], "error")

        # The credentials are sent as basic authentication.
        self.fail_with = None
        await self.poll(coordinator)
        self.assertEqual(self.received[-1]["auth"], "Basic dXNlcjpwYTU1")

    async def test_token_is_redacted_from_diagnostics(self):
        coordinator = self.exporting(export_token="tkn-secret")
        self.serve(self.trip(30, 10))
        await self.poll(coordinator)
        self.entry.runtime_data = coordinator
        self.assertNotIn("tkn-secret", json.dumps(await async_get_config_entry_diagnostics(self.hass, self.entry)))

    async def test_export_sensor(self):
        coordinator = self.exporting()
        self.serve(self.trip(30, 10, 20))
        await self.poll(coordinator)
        sensor = ExportSensor(coordinator)
        self.assertEqual(sensor.native_value, "ok")
        attributes = sensor.extra_state_attributes
        self.assertEqual(attributes["target"], f"{self.url}/write?db=car")
        self.assertEqual((attributes["trips_exported_since_start"], attributes["lines_sent_since_start"]), (1, 3))
        self.assertEqual(sensor.suggested_object_id, "Export")


if __name__ == "__main__":
    unittest.main()
