"""Render the example dashboard with Home Assistant's template engine."""

from pathlib import Path

import yaml
from homeassistant.helpers.template import Template

from support import HomeAssistantTestCase


DASHBOARD = yaml.safe_load((Path(__file__).parents[1] / "examples/dashboard.yaml").read_text())
CARDS = [card for view in DASHBOARD["views"] for section in view["sections"] for card in section["cards"]]
TEMPLATES = [card["content"] for card in CARDS if card["type"] == "markdown"]
BADGES = [badge for view in DASHBOARD["views"] for badge in view["badges"]]
BADGES += [badge for card in CARDS for badge in card.get("badges", [])]


class DashboardTests(HomeAssistantTestCase):
    def render(self, marker):
        content = next(content for content in TEMPLATES if marker in content)
        return Template(content, self.hass).async_render(parse_result=False)

    async def test_templates_render_before_any_vehicle_data_arrives(self):
        for content in TEMPLATES:
            with self.subTest(template=content[:60]):
                Template(content, self.hass).async_render(parse_result=False)
        self.assertIn("Warnings unavailable", self.render("warnings"))

    async def test_missing_trip_distance_does_not_become_zero(self):
        self.hass.states.async_set("sensor.my_car_driving_trips_start", "2026-10-14T10:00:00+00:00")
        self.hass.states.async_set("sensor.my_car_driving_trips_duration", "600", {"unit_of_measurement": "s"})
        rendered = self.render("Max speed")
        self.assertIn("Distance unavailable", rendered)
        self.assertNotIn("0.0 km", rendered)

    async def test_trip_summary_respects_distance_duration_and_sample_units(self):
        self.hass.states.async_set("sensor.my_car_driving_trips_start", "2026-10-14T10:00:00+00:00")
        self.hass.states.async_set("sensor.my_car_driving_trips_duration", "10", {"unit_of_measurement": "min"})
        self.hass.states.async_set("sensor.my_car_trip_distance_last_trip", "6.2", {"unit_of_measurement": "mi"})
        self.hass.states.async_set("sensor.my_car_speed_unit_last_trip", "mph")
        self.hass.states.async_set("sensor.my_car_speed_last_trip", "160", {
            "unit_of_measurement": "km/h", "trip_min": 10, "trip_mean": 40, "trip_max": 100,
            "valid_samples": 5, "total_samples": 5, "trip_start": "2026-10-14T10:00:00+00:00",
        })
        rendered = self.render("Max speed")
        self.assertIn("6.2 mi", rendered)
        self.assertIn("10 min", rendered)
        self.assertIn("100", rendered)
        self.assertIn("mph", rendered)
        details = self.render("Wheel speed front left")
        row = next(line for line in details.splitlines() if line.startswith("| Speed |"))
        self.assertIn("mph", row)
        self.assertIn("160", row)
        self.assertIn("km/h", row)

    async def test_summary_does_not_mix_different_trips(self):
        self.hass.states.async_set("sensor.my_car_driving_trips_start", "2026-10-14T10:00:00+00:00")
        self.hass.states.async_set("sensor.my_car_speed_unit_last_trip", "kph")
        self.hass.states.async_set("sensor.my_car_speed_last_trip", "80", {
            "trip_start": "2026-10-13T09:00:00+00:00", "trip_max": 120,
        })
        self.assertIn("| Max speed | – km/h |", self.render("Max speed"))

    async def test_optional_cards_and_badges_hide_missing_entities(self):
        for card in CARDS + BADGES:
            if card["type"] in ("tile", "gauge", "entity"):
                with self.subTest(entity=card["entity"]):
                    self.assertIn({"condition": "state", "entity": card["entity"], "state_not": "unknown"},
                                  card.get("visibility", []))
