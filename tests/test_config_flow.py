"""Setup, reauthentication, reconfiguration and options flows."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import probatio
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv

from custom_components.pleos_vehicle_data import async_setup_entry
from custom_components.pleos_vehicle_data.api import AuthError, PleosClient
from custom_components.pleos_vehicle_data.config_flow import PleosOptionsFlow
from custom_components.pleos_vehicle_data.const import BRANDS
from custom_components.pleos_vehicle_data.sensor import ConnectionSensor

from support import CAR, HomeAssistantTestCase, NEW, OLD


class ConfigFlowTests(HomeAssistantTestCase):
    async def test_coordinator_stops_polling_and_starts_native_reauth(self):
        await self.populate()
        self.client.vehicles.side_effect = AuthError()
        with patch.object(self.entry, "async_start_reauth_if_available") as reauth, patch.object(self.coordinator, "_schedule_refresh") as schedule:
            await self.coordinator.async_refresh()
            reauth.assert_called_once_with(self.hass)
            schedule.assert_not_called()
        self.assertIsInstance(self.coordinator.last_exception, ConfigEntryAuthFailed)
        self.assertEqual(ConnectionSensor(self.coordinator).native_value, "key_required")

    async def test_reauth_validates_and_preserves_entry(self):
        flow = self.flow("reauth")
        with patch.object(flow, "_get_reauth_entry", return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])), patch.object(self.hass.config_entries, "async_schedule_reload") as reload:
            result = await flow.async_step_reauth_confirm(NEW)
        self.assertEqual(result["reason"], "reauth_successful")
        self.assertEqual(self.entry.data, OLD | NEW)
        self.assertEqual(self.entry.unique_id, "kia")
        reload.assert_called_once_with(self.entry.entry_id)

    async def test_rejected_replacement_keeps_previous_credentials(self):
        flow = self.flow("reauth")
        with patch.object(flow, "_get_reauth_entry", return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock(side_effect=AuthError())):
            result = await flow.async_step_reauth_confirm(NEW)
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual(self.entry.data, OLD)

    async def test_proactive_key_replacement(self):
        flow = self.flow("reconfigure")
        with patch.object(flow, "_get_reconfigure_entry", return_value=self.entry), patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])), patch.object(self.hass.config_entries, "async_schedule_reload"):
            result = await flow.async_step_reconfigure(NEW)
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(self.entry.data["client_id"], NEW["client_id"])

    async def test_user_flow_for_all_brands_and_secret_selector(self):
        with patch.object(PleosClient, "vehicles", AsyncMock(return_value=[CAR])):
            for brand in BRANDS:
                flow = self.flow()
                with patch.object(flow, "_abort_if_unique_id_configured"):
                    result = await flow.async_step_user(NEW | {"brand": brand})
                self.assertEqual(result["type"], "create_entry")
                self.assertEqual(result["data"]["brand"], brand)
        result = await self.flow().async_step_user()
        selector = next(v for k, v in result["data_schema"].schema.items() if str(k) == "client_secret")
        self.assertEqual(selector.config["type"], "password")

    async def test_options_and_limits(self):
        flow = PleosOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        form = await flow.async_step_init()
        submitted = form["data_schema"]({"polling": {"poll_minutes": 20, "stale_minutes": 60, "location": False},
                                         "trips": {}, "storage": {}, "export": {}})
        self.assertEqual(submitted["polling"]["poll_minutes"], 20)
        # Unchanged fields keep their defaults inside each section.
        self.assertEqual(submitted["trips"], {"trip_poll_minutes": 60, "trip_events": True})
        for invalid in ({"poll_minutes": 1}, {"stale_minutes": 10081}):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                form["data_schema"]({"polling": {"poll_minutes": 15, "stale_minutes": 60, "location": True} | invalid,
                                     "trips": {}, "storage": {}, "export": {}})
        self.assertTrue(flow.automatic_reload)
        # The frontend receives two expanded sections with their fields.
        fields = probatio.to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
        self.assertEqual([(field["name"], field["type"], field["expanded"]) for field in fields],
                         [("polling", "expandable", True), ("trips", "expandable", True), ("storage", "expandable", True),
                          ("export", "expandable", False)])
        self.assertEqual([item["name"] for item in fields[0]["schema"]], ["poll_minutes", "stale_minutes", "location"])

        # Saved options stay flat, so the coordinator and existing entries read them unchanged.
        result = await flow.async_step_init(submitted)
        self.assertEqual(result["data"], {"poll_minutes": 20, "stale_minutes": 60, "location": False,
                                          "trip_poll_minutes": 60, "trip_events": True, "archive": True,
                                          "export_format": "off", "export_url": "", "export_database": "",
                                          "export_org": "", "export_token": ""})

    async def test_options_form_shows_saved_values(self):
        self.hass.config_entries.async_update_entry(self.entry, options={"poll_minutes": 30, "trip_events": False})
        flow = PleosOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        defaults = (await flow.async_step_init())["data_schema"]({"polling": {}, "trips": {}, "storage": {}, "export": {}})
        self.assertEqual(defaults, {"polling": {"poll_minutes": 30, "stale_minutes": 60, "location": True},
                                    "trips": {"trip_poll_minutes": 60, "trip_events": False},
                                    "storage": {"archive": True},
                                    "export": {"export_format": "off", "export_url": "", "export_database": "",
                                               "export_org": "", "export_token": ""}})

    async def test_setup_uses_replacement_credentials_and_fresh_token(self):
        self.hass.config_entries.async_update_entry(self.entry, data=OLD | NEW)
        with patch("custom_components.pleos_vehicle_data.async_get_clientsession", return_value=MagicMock()), patch.object(PleosClient, "vehicles", AsyncMock(return_value=[])), patch.object(self.hass.config_entries, "async_forward_entry_setups", AsyncMock()):
            await async_setup_entry(self.hass, self.entry)
        self.assertEqual(self.entry.runtime_data.client.credentials["clientId"], NEW["client_id"])
        self.assertIsNone(self.entry.runtime_data.client.token)
        await self.entry.runtime_data.async_shutdown()


    async def test_export_settings_are_validated(self):
        flow = PleosOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        form = await flow.async_step_init()
        base = form["data_schema"]({"polling": {}, "trips": {}, "storage": {}, "export": {}})
        cases = (
            ({"export_format": "influx_v1", "export_url": "victoriametrics:8428"}, "invalid_export_url"),
            ({"export_format": "influx_v2", "export_url": "http://influx:8086", "export_database": "car"}, "missing_bucket"),
            ({"export_format": "influx_v1", "export_url": "http://u:p@vm:8428", "export_token": "t"}, "credentials_and_token"),
        )
        for export, error in cases:
            with self.subTest(error=error):
                result = await flow.async_step_init(base | {"export": base["export"] | export})
                self.assertEqual(result["errors"], {"base": error})
                # The form keeps what was entered and opens the export section.
                shown = result["data_schema"]({"polling": {}, "trips": {}, "storage": {}, "export": {}})
                self.assertEqual(shown["export"]["export_url"], export["export_url"])
                fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
                self.assertTrue(fields[-1]["expanded"])
        result = await flow.async_step_init(base | {"export": base["export"] | {
            "export_format": "influx_v1", "export_url": " http://victoriametrics:8428 "}})
        self.assertEqual((result["data"]["export_format"], result["data"]["export_url"]),
                         ("influx_v1", "http://victoriametrics:8428"))


if __name__ == "__main__":
    unittest.main()
