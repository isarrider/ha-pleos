"""Translation keys and English names for every entity with a documented meaning.

strings.json and translations/en.json carry the same names; a test keeps them in step.
Fields the API adds without documentation keep their generated English name instead.
"""

from .const import ENDPOINT_NAMES, ENDPOINTS, SNAPSHOT_ENDPOINTS, TRIP_ENDPOINTS
from .data import NAMES, TRIP_FIELDS, TRIP_NAMES, identifier, label

# Documented boolean fields, which become binary sensors.
BINARY_KEYS = ("batteries_charge_charging", "batteries_charge_plugin", "driving_transmission_parkingposition")


def _sensor_names():
    names = {key: name for key, name in NAMES.items() if key not in BINARY_KEYS}
    names["connection"] = "Connection"
    names["archive"] = "Archive"
    names["vehicle_information"] = "Vehicle information"
    for endpoint in ENDPOINTS:
        names[identifier(endpoint) + "_api"] = ENDPOINT_NAMES[endpoint] + " API"
    for endpoint in SNAPSHOT_ENDPOINTS:
        names[identifier(endpoint) + "_reported_at"] = ENDPOINT_NAMES[endpoint] + " last reported"
        names[identifier(endpoint) + "_data_age"] = ENDPOINT_NAMES[endpoint] + " data age"
    for endpoint in TRIP_ENDPOINTS:
        prefix = identifier(endpoint)
        names[prefix + "_trip_start"] = label((ENDPOINT_NAMES[endpoint], "start"))
        names[prefix + "_trip_duration"] = label((ENDPOINT_NAMES[endpoint], "duration"))
        for field in TRIP_FIELDS[endpoint]:
            names[prefix + "_" + identifier(field)] = TRIP_NAMES[field] + " (last trip)"
    names["driving_data_trip_distance"] = "Trip distance (last trip)"
    return names


ENTITY_NAMES = {
    "sensor": _sensor_names(),
    "binary_sensor": {key: NAMES[key] for key in BINARY_KEYS},
    "device_tracker": {"location": "Location"},
    "event": {"trip": "Trip"},
}
