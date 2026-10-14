"""Normalize documented vehicle fields without inventing missing readings."""

import hashlib
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .const import ENDPOINT_NAMES

LOGGER = logging.getLogger(__name__)
# Rejections kept per endpoint as examples on its diagnostic sensor.
REJECTION_EXAMPLES = 5

UNITS = {"C": "°C", "F": "°F", "km": "km", "mile": "mi", "miles": "mi",
         "meter": "m", "feet": "ft", "psi": "psi", "kpa": "kPa", "bar": "bar",
         "kph": "km/h", "mph": "mph", "mps": "m/s"}


NAMES = {"batteries_charge_stateofcharge": "Battery level", "batteries_charge_charging": "Charging",
         "batteries_charge_plugin": "Plugged in", "batteries_charge_remaintime": "Charge time remaining",
         "batteries_charge_status": "Charging status", "batteries_charge_batterycapacity": "Battery capacity",
         "batteries_charge_targetstateofcharge_standard": "AC charge limit",
         "batteries_charge_targetstateofcharge_quick": "DC charge limit",
         "powertrains_engine_fuellevel": "Fuel level", "powertrains_batteries_main_remainratio": "Traction battery level",
         "powertrains_distancetoempties_ev": "EV range", "driving_odometer": "Odometer",
         "powertrains_distancetoempties_ice": "Fuel range", "powertrains_distancetoempties_hev": "Hybrid range",
         "powertrains_distancetoempties_phev": "Plug-in hybrid range", "powertrains_distancetoempties_fcev": "Hydrogen range",
         "driving_ignition_status": "Ignition", "driving_sleepmode": "Sleep mode",
         "driving_ignition_ignitiondetails_ign1_status": "Ignition power",
         "driving_ignition_ignitiondetails_ign3_status": "High-voltage system power",
         "driving_ignition_ignitiondetails_acc_status": "Accessory power",
         "driving_transmission_parkingposition": "In park",
         "status_hood": "Bonnet", "status_trunk": "Boot", "status_sunroof": "Sunroof",
         "status_warnings": "Warnings", "status_climatecontrol_mode": "Climate mode",
         "status_climatecontrol_status": "Climate control", "status_climatecontrol_speed": "Cabin fan speed",
         "status_climatecontrol_temperature": "Cabin target temperature",
         "status_outsidetemperature": "Outside temperature", "status_windshield_defrost": "Windscreen defrost",
         "status_steeringwheelheat": "Steering wheel heating",
         "freezer_freezer_status": "Freezer", "freezer_freezer_temperature": "Freezer temperature",
         "locations_location_latitude": "Latitude", "locations_location_longitude": "Longitude",
         "locations_location_altitude": "Altitude", "locations_location_heading": "Heading",
         "locations_location_speed": "Speed",
         "locations_location_type": "Coordinate system",
         "status_tire_row1_left": "Front left tyre pressure", "status_tire_row1_right": "Front right tyre pressure",
         "status_tire_row2_left": "Rear left tyre pressure", "status_tire_row2_right": "Rear right tyre pressure",
         **{f"status_doors_row{row}_{side}": f"{position} {side} door"
            for row, position in ((1, "Front"), (2, "Rear")) for side in ("left", "right")},
         **{f"status_seats_row{row}_{side}_{field}": f"{position} {side} seat {name}"
            for row, position in ((1, "Front"), (2, "Rear")) for side in ("left", "right")
            for field, name in (("heat", "heating"), ("ventilation", "ventilation"))}}

TRIP_NAMES = {
    "batteries.hv.voltage": "Traction battery voltage", "engine.oilTemperature": "Engine oil temperature",
    "engine.rpm": "Engine speed", "location.speed": "Speed", "location.speedUnit": "Speed unit",
    "ignition.status": "Ignition", "odometer.value": "Odometer", "acceleration.longitudinal": "Forward acceleration",
    "angularVelocity.yaw": "Turning rate", "isla.optionUsm": "Speed limit assist setting",
    "isla.optionUsm2": "Speed limit assist secondary setting", "seatbelt.assistance": "Passenger seat belt",
    "seatbelt.driver": "Driver seat belt", "steeringWheel.angle": "Steering wheel angle",
    "steeringWheel.rotationSpeed": "Steering wheel turning speed", "transmission.gear": "Gear",
    "lamp.setting": "Exterior light setting", "tire.pressureUnit": "Tyre pressure unit",
    "warnings.lowFuelLevel": "Low fuel warning", "outsideTemperature.value": "Outside temperature",
    "doors.openAssistance": "Front passenger door", "doors.openDriver": "Driver door",
    "doors.openRearLeft": "Rear left door", "doors.openRearRight": "Rear right door",
    **{f"{group}.{field}{part}": f"{name} {side} {suffix}"
       for part, name, side in (("FrontLeft", "Front", "left"), ("FrontRight", "Front", "right"),
                                ("RearLeft", "Rear", "left"), ("RearRight", "Rear", "right"))
       for group, field, suffix in (("wheel", "speed", "wheel speed"), ("tire", "pressure", "tyre pressure"))},
}


def identifier(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def label(path):
    return " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", str(part)).replace("_", " ") for part in path).capitalize()


def reported_age(timestamp):
    try:
        stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        return max(0, age) if age >= -300 else None
    except (TypeError, ValueError, AttributeError):
        return None


def finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def scale_pressure(value, unit):
    """Pleos reports kPa in steps of 5 and bar in tenths; psi is reported as is."""
    return value * 5 if unit == "kpa" else value / 10 if unit == "bar" else value


@dataclass(frozen=True)
class Spec:
    """Validation and Home Assistant metadata for one documented numeric field."""

    low: float
    high: float
    units: tuple | None = None  # Accepted raw units; None accepts any.
    unit: str | None = None  # Fixed Home Assistant unit, replacing the raw one.
    device_class: str | None = None
    state_class: str | None = None  # Replaces the default "measurement" when set.
    integer: bool = False
    numeric_text: bool = False  # Also accept numbers sent as strings; the value becomes a float.
    convert: Callable | None = None  # convert(value, raw_unit) after validation.
    options: tuple | None = None  # Enum: the value is an index into these options.
    unit_field: str | None = None  # Trips: the sample array holding each sample's raw unit.

    def check(self, value, raw_unit):
        """Return the validated, converted value, or None when it must be dropped."""
        if isinstance(value, bool):
            return None
        if self.numeric_text and isinstance(value, (str, int, float)):
            try:
                value = float(value)
            except (ValueError, TypeError, OverflowError):
                return None
        if not finite_number(value) or not self.low <= value <= self.high:
            return None
        if self.units is not None and raw_unit not in self.units:
            return None
        if self.integer and not isinstance(value, int):
            return None
        if self.convert:
            value = self.convert(value, raw_unit)
        return self.options[value] if self.options else value

    def describe(self):
        """The rule a rejected value broke, for logs and diagnostics."""
        text = f"expected {self.low} to {self.high}"
        if self.units is not None:
            text += " in " + "/".join(self.units)
        return text + (" as a whole number" if self.integer else "")

    def metadata(self):
        if self.options:
            return {"device_class": "enum", "options": list(self.options)}
        return {key: value for key, value in (("unit_of_measurement", self.unit), ("device_class", self.device_class),
                                              ("state_class", self.state_class)) if value}


def exact(*paths):
    return lambda path: path in paths


def prefix(*parts):
    return lambda path: path[:len(parts)] == parts


def last(*names):
    return lambda path: path[-1] in names


PERCENT = Spec(0, 100, unit="%", device_class="battery")
# Documented snapshot fields: (endpoint or None for any, path matcher, spec). The first match applies.
SNAPSHOT_FIELDS = (
    ("status", prefix("tire"), Spec(0, 254, units=("psi", "kpa", "bar"), convert=scale_pressure,
                                     device_class="pressure")),
    (None, last("stateOfCharge", "remainRatio"), PERCENT),
    (None, prefix("charge", "targetStateOfCharge"), PERCENT),
    (None, last("fuelLevel"), Spec(0, 100, unit="%")),
    (None, exact(("charge", "remainTime")), Spec(0, 65535, unit="min", device_class="duration")),
    ("powertrains", prefix("distanceToEmpties"), Spec(1, 4095, units=("feet", "km", "meter", "miles"),
                                                      numeric_text=True, device_class="distance")),
    ("driving", exact(("odometer",)), Spec(0, 16777214, units=("km", "mile", "meter", "feet"),
                                           device_class="distance", state_class="total_increasing")),
    ("status", exact(("climateControl", "temperature"), ("outsideTemperature",)), Spec(-100, 100, units=("C", "F"))),
    ("status", exact(("climateControl", "speed")), Spec(-1, 8, integer=True)),
    ("freezer", exact(("freezer", "temperature")), Spec(-40, 62.3, unit="°C", device_class="temperature")),
    ("locations", last("latitude"), Spec(-90, 90, unit="°")),
    ("locations", last("longitude"), Spec(-180, 180, unit="°")),
    ("locations", last("heading"), Spec(0, 359, unit="°")),
    ("locations", last("altitude"), Spec(-100, 10000, unit="m", device_class="distance")),
    ("locations", last("speed"), Spec(0, 1000, units=("kph", "mph", "mps"), device_class="speed")),
    ("locations", last("type"), Spec(0, 2, integer=True, options=("WGS84", "Bessel", "CoordShift"))),
)


def snapshot_spec(endpoint, path):
    return next((spec for scope, matches, spec in SNAPSHOT_FIELDS
                 if scope in (None, endpoint) and matches(path)), None)


class Rejections:
    """Values present in a response but not turned into a reading, with the reason."""

    def __init__(self, endpoint):
        self.endpoint, self.count, self.examples = endpoint, 0, []

    def add(self, path, value, reason):
        path = ".".join(map(str, path)) if isinstance(path, tuple) else path
        if isinstance(value, (int, float)) and (not finite_number(value) or isinstance(value, int) and not -(2**63) <= value < 2**63):
            value = repr(value)[:100]
        self.count += 1
        if len(self.examples) < REJECTION_EXAMPLES:
            self.examples.append({"path": path, "value": value if isinstance(value, (str, int, float, bool)) else repr(value)[:100],
                                  "reason": reason})
        LOGGER.debug("Pleos %s: ignored %s (%s)", self.endpoint, path, reason)

    def attributes(self):
        return {"rejected_values": self.count, "rejected_examples": self.examples}


def readings(endpoint, data, vehicle_type, rejections=None):
    """Keep unknown fields as plain sensors; apply only documented semantics.

    Values present but not usable are reported to `rejections`; absent values (null)
    and values the API itself marks "invalid" are not rejections.
    """
    result = {}
    if not isinstance(data, dict):
        return result
    reject = rejections.add if rejections is not None else lambda *args: None

    def walk(value, path=(), unit=None):
        if isinstance(value, dict):
            local_unit = value.get("unit", unit)
            if local_unit is not None and local_unit != "invalid" and not isinstance(local_unit, str):
                reject((*path, "unit"), local_unit, "unit is not text")
            if local_unit == "invalid" or (local_unit is not None and not isinstance(local_unit, str)):
                return
            for key, child in value.items():
                if key not in ("unit", "type", "timestamp") or (endpoint == "locations" and path == ("location",) and key == "type"):
                    walk(child, path if key == "value" else (*path, key), local_unit)
            return
        if isinstance(value, list):
            if path == ("warnings",):
                if not all(isinstance(item, str) for item in value):
                    reject(path, value, "warnings must all be text")
                if all(isinstance(item, str) for item in value):
                    walk(", ".join(value)[:255] or "None", path)
                    # The state is cut at 255 characters; the attribute keeps every warning.
                    if (key := identifier(endpoint + "_warnings")) in result:
                        result[key]["attributes"] = {"warnings": list(value), "warning_count": len(value)}
                return
            for index, child in enumerate(value):
                part = child.get("type") if isinstance(child, dict) else None
                if part is not None and part != "invalid" and not isinstance(part, str):
                    reject((*path, f"row{index + 1}", "type"), part, "type is not text")
                if part == "invalid" or (part is not None and not isinstance(part, str)):
                    continue
                walk(child, (*path, part or f"row{index + 1}"), unit)
            return
        if value is None or value == "invalid" or not path:
            return
        if not isinstance(value, (str, int, float, bool)):
            reject(path, value, "unsupported type")
            return
        if isinstance(value, str) and len(value) > 255:
            reject(path, value[:100] + "…", "text longer than 255 characters")
            return
        if endpoint == "powertrains" and path == ("engine", "fuelLevel") and vehicle_type in ("EV", "FV"):
            return
        spec = snapshot_spec(endpoint, path)
        if spec:
            raw, value = value, spec.check(value, unit)
            if value is None:
                reject(path, raw, spec.describe() + (f", got unit {unit}" if unit else ""))
                return
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if numeric and not finite_number(value):
            reject(path, value, "not a finite number")
            return
        opts = {}
        if numeric:
            opts["state_class"] = "measurement"
            if unit in UNITS:
                opts["unit_of_measurement"] = UNITS[unit]
                if unit in ("C", "F"):
                    opts["device_class"] = "temperature"
        if spec:
            opts = spec.metadata() if spec.options else opts | spec.metadata()
        domain = "sensor"
        if path == ("charge", "plugin"):
            if value not in ("connected", "disconnected"):
                reject(path, value, "expected connected or disconnected")
                return
            value = value == "connected"
            opts["device_class"] = "plug"
        if isinstance(value, bool):
            domain, value = "binary_sensor", "ON" if value else "OFF"
            if path == ("charge", "charging"):
                opts["device_class"] = "battery_charging"
            opts.pop("state_class", None)
        key = identifier(endpoint + "_" + "_".join(map(str, path)))
        result[key] = {"value": value, "name": NAMES.get(key, label(path)), "domain": domain, **opts}

    walk(data)
    return result


def vehicle_id(brand, vin):
    return brand + "_" + hashlib.sha256(vin.encode()).hexdigest()[:16]


def location(data):
    if not isinstance(data, dict) or reported_age(data.get("timestamp")) is None:
        return None
    point = data.get("location")
    if not isinstance(point, dict) or type(point.get("type")) is not int or point.get("type") != 0:
        return None
    lat, lon = point.get("latitude"), point.get("longitude")
    if not all(finite_number(v) for v in (lat, lon)):
        return None
    return point if -90 <= lat <= 90 and -180 <= lon <= 180 else None


# Paths, ranges and units from the four Pleos trip-data response schemas. None marks a text field.
SPEED_SAMPLE = Spec(0, 254, units=("kph", "mph"), unit_field="location.speedUnit", device_class="speed")
TYRE_SAMPLE = Spec(0, 254, units=("psi", "kpa", "bar"), unit_field="tire.pressureUnit", convert=scale_pressure,
                   device_class="pressure")
TRIP_FIELDS = {
    "powertrains/data": {
        "batteries.hv.voltage": Spec(0, 6553, unit="V", device_class="voltage"),
        "engine.oilTemperature": Spec(0, 150.5, unit="°C", device_class="temperature"),
        "engine.rpm": Spec(0, 16383.5, unit="rpm"),
    },
    "speed": {
        "location.speed": SPEED_SAMPLE,
        "location.speedUnit": None,
        **{f"wheel.speed{side}": Spec(0, 511.9375, unit="km/h", device_class="speed")
           for side in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")},
    },
    "driving/data": {
        "ignition.status": None,
        "odometer.value": Spec(0, 1677721.4, unit="km", device_class="distance"),
        "acceleration.longitudinal": Spec(-4.177, 4.177, unit="g"),
        "angularVelocity.yaw": Spec(-40.95, 40.95, unit="°/s"),
        "isla.optionUsm": None,
        "isla.optionUsm2": None,
        "seatbelt.assistance": None,
        "seatbelt.driver": None,
        "steeringWheel.angle": Spec(-3276.8, 3276.7, unit="°"),
        "steeringWheel.rotationSpeed": Spec(0, 1016, unit="°/s"),
        "transmission.gear": None,
    },
    "status/data": {
        **{f"doors.open{side}": None for side in ("Assistance", "Driver", "RearLeft", "RearRight")},
        "lamp.setting": None,
        **{f"tire.pressure{side}": TYRE_SAMPLE for side in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")},
        "tire.pressureUnit": None,
        "warnings.lowFuelLevel": None,
        "outsideTemperature.value": Spec(-40, 60, unit="°C", device_class="temperature"),
    },
}


def new_trips(data, seen):
    """Return valid trips whose start time is not in `seen`, oldest first."""
    trips = data.get("trips") if isinstance(data, dict) else None
    if not isinstance(trips, list):
        return []
    fresh = {}
    for trip in trips:
        if not isinstance(trip, dict) or reported_age(trip.get("triggerStartTime")) is None:
            continue
        start = trip["triggerStartTime"]
        if start not in seen:
            # A start time identifies a trip; keep the first copy if the API repeats one.
            fresh.setdefault(start, trip)
    return sorted(fresh.values(), key=lambda t: datetime.fromisoformat(t["triggerStartTime"].replace("Z", "+00:00")))


def prune_seen(starts, max_age):
    """Drop remembered trip starts older than `max_age` seconds, or ones no longer parseable."""
    return sorted(start for start in starts if (age := reported_age(start)) is not None and age <= max_age)


def trip_leaves(trip, path=()):
    """Every non-dict value of a trip with its dotted path, except the trip's start and length."""
    for key, value in trip.items():
        if not path and key in ("triggerStartTime", "tripLength"):
            continue
        if isinstance(value, dict):
            yield from trip_leaves(value, (*path, str(key)))
        else:
            yield ".".join((*path, str(key))), value


def summary(values):
    return {"trip_min": min(values), "trip_max": max(values), "trip_mean": round(sum(values) / len(values), 3),
            "valid_samples": len(values)}


def trip_summary(endpoint, trip):
    """Flat attributes describing one trip, for the Trip event entity.

    Field names follow the trip sensors without their endpoint prefix; numbers also get
    _min, _max and _mean over the trip's valid samples. Units are those of the matching
    "(last trip)" sensor.
    """
    current, start = trip_readings(endpoint, {"trips": [trip]})
    prefix = identifier(endpoint) + "_"
    summary = {"trip_start": start}
    for key, reading in current.items():
        name = key.removeprefix(prefix)
        if name == "trip_start":
            continue
        if name == "trip_duration":
            summary["duration_s"] = reading["value"]
            continue
        if name == "trip_distance":
            summary["distance_km"] = reading["value"]
            continue
        summary[name] = reading["value"]
        attributes = reading.get("attributes", {})
        for statistic in ("min", "max", "mean"):
            if f"trip_{statistic}" in attributes:
                summary[f"{name}_{statistic}"] = attributes[f"trip_{statistic}"]
    return summary


def trip_readings(endpoint, data, rejections=None):
    """Expose the latest trip's last samples; the API does not supply sample times.

    Documented fields use their spec; any other field becomes a plain reading. Present but
    unusable last samples are reported to `rejections`.
    """
    reject = rejections.add if rejections is not None else lambda *args: None
    trips = data.get("trips") if isinstance(data, dict) else None
    if not isinstance(trips, list):
        return {}, None
    valid = [trip for trip in trips if isinstance(trip, dict) and reported_age(trip.get("triggerStartTime")) is not None]
    if not valid:
        return {}, None
    trip = max(valid, key=lambda t: datetime.fromisoformat(t["triggerStartTime"].replace("Z", "+00:00")))
    start = trip["triggerStartTime"]
    prefix = identifier(endpoint)
    result = {
        prefix + "_trip_start": {"name": label((ENDPOINT_NAMES[endpoint], "start")), "value": start,
                                "device_class": "timestamp", "domain": "sensor", "historical": True},
    }
    length = trip.get("tripLength")
    if finite_number(length) and length >= 0:
        result[prefix + "_trip_duration"] = {
            "name": label((ENDPOINT_NAMES[endpoint], "duration")), "value": length / 1000,
            "unit_of_measurement": "s", "device_class": "duration", "domain": "sensor", "historical": True,
        }

    def samples(path):
        value = trip
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value if isinstance(value, list) else []

    def numeric_sample(field, spec, index):
        """Return one valid sample as (value, unit), converted like the last-sample sensor."""
        values = samples(field)
        units = samples(spec.unit_field) if spec.unit_field else []
        raw_unit = units[index] if spec.unit_field and 0 <= index < len(units) else None
        value = spec.check(values[index] if 0 <= index < len(values) else None, raw_unit)
        return None if value is None else (value, spec.unit or UNITS.get(raw_unit))

    for field, spec in TRIP_FIELDS[endpoint].items():
        values = samples(field)
        if not values or values[-1] is None or values[-1] == "invalid":
            continue
        value, index = values[-1], len(values) - 1
        reading = {"name": TRIP_NAMES[field] + " (last trip)", "value": value,
                   "domain": "sensor", "historical": True,
                   "attributes": {"trip_start": start, "trip_length_ms": length, "sample_index": index}}
        if spec is None:
            if not isinstance(value, str) or len(value) > 255:
                reject(field, value if isinstance(value, (int, float, bool)) else repr(value)[:100], "expected text up to 255 characters")
                continue
        else:
            last = numeric_sample(field, spec, index)
            if last is None:
                reject(field, value if isinstance(value, (str, int, float, bool)) else repr(value)[:100], spec.describe())
                continue
            reading["value"], unit = last
            # Summarise every valid sample of the trip in the last sample's unit.
            valid = [sample[0] for i in range(len(values))
                     if (sample := numeric_sample(field, spec, i)) is not None and sample[1] == unit]
            reading["attributes"].update(
                trip_min=min(valid), trip_max=max(valid), trip_mean=round(sum(valid) / len(valid), 3),
                valid_samples=len(valid), total_samples=len(values),
            )
            reading["state_class"] = "measurement"
            if unit:
                reading["unit_of_measurement"] = unit
            if spec.device_class:
                reading["device_class"] = spec.device_class
        result[prefix + "_" + identifier(field)] = reading

    # Fields Pleos returns without documentation: the last sample as a plain reading.
    for field, value in trip_leaves(trip):
        key = prefix + "_" + identifier(field)
        if field in TRIP_FIELDS[endpoint] or key in result:
            continue
        values = value if isinstance(value, list) else None
        last = values[-1] if values else value
        if last is None or last == "invalid" or values == []:
            continue
        name = label(tuple(field.split("."))) + " (last trip)"
        attributes = {"trip_start": start, "trip_length_ms": length}
        if values is not None:
            attributes |= {"sample_index": len(values) - 1, "total_samples": len(values)}
        reading = {"name": name, "domain": "sensor", "historical": True, "attributes": attributes}
        if isinstance(last, bool):
            reading.update(domain="binary_sensor", value="ON" if last else "OFF")
        elif isinstance(last, (int, float)):
            if not finite_number(last):
                reject(field, repr(last), "not a finite number")
                continue
            reading.update(value=last, state_class="measurement")
            if values is not None:
                attributes |= summary([sample for sample in values if finite_number(sample)])
        elif isinstance(last, str) and len(last) <= 255:
            reading["value"] = last
        else:
            reject(field, last[:100] + "…" if isinstance(last, str) else repr(last)[:100],
                   "text longer than 255 characters" if isinstance(last, str) else "unsupported type")
            continue
        result[key] = reading

    if endpoint == "driving/data":
        spec = TRIP_FIELDS[endpoint]["odometer.value"]
        mileage = [sample[0] for i in range(len(samples("odometer.value")))
                   if (sample := numeric_sample("odometer.value", spec, i)) is not None]
        if len(mileage) >= 2 and mileage[-1] >= mileage[0]:
            result[prefix + "_trip_distance"] = {
                "name": "Trip distance (last trip)", "value": round(mileage[-1] - mileage[0], 1),
                "unit_of_measurement": "km", "device_class": "distance", "state_class": "measurement",
                "domain": "sensor", "historical": True,
                "attributes": {"trip_start": start, "trip_length_ms": length,
                               "odometer_start": mileage[0], "odometer_end": mileage[-1]},
            }
    return result, start
