"""Optional export of every trip sample to VictoriaMetrics or InfluxDB, as InfluxDB line protocol.

Pleos sample arrays carry no timestamps. Samples are spread evenly over the trip's
duration and tagged timing=interpolated (or timing=assumed_1s when the API gives no
duration), so derived times are never mistaken for measured ones.

Each trip is sent once per distinct content. Trips that could not be sent are not
remembered, so the next trip query (which still returns them) retries them.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit, urlunsplit

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, TRIP_MEMORY_DAYS
from .data import TRIP_FIELDS, UNITS, Spec, finite_number, reported_age, snake_case, trip_leaves

LOGGER = logging.getLogger(__name__)
FORMATS = ("off", "influx_v1", "influx_v2")
MEASUREMENT = "pleos_trip"
SUMMARY_MEASUREMENT = "pleos_trip_summary"
# Lines per request; a trip's lines are never split across requests.
BATCH_LINES = 5000
# Converted values are sent in one unit per field, named in the field key.
NORMALISE = {"mph": (1.609344, "km/h"), "psi": (6.894757, "kPa"), "bar": (100, "kPa")}
SUFFIXES = {"km/h": "kmh", "kPa": "kpa", "V": "v", "°C": "c", "rpm": "rpm", "km": "km", "g": "g",
            "°/s": "deg_per_s", "°": "deg", "m/s": "ms"}


def without_credentials(url):
    """The URL as safe to show and log: any user:password@ part is removed."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(netloc=parts.hostname + (f":{parts.port}" if parts.port else ""))) if parts.hostname else url


def export_store(hass, entry_id):
    """Content hashes of trips already exported, so restarts do not send them again."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.export")


def _escape(text, also=""):
    for char in "\\," + "= " + also:
        text = text.replace(char, "\\" + char)
    return text


def _field_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # Always floats: InfluxDB rejects a field that switches between integer and float.
        return repr(float(value))
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _line(measurement, tags, fields, timestamp_ms):
    tag_text = ",".join(f"{_escape(key)}={_escape(str(value))}" for key, value in sorted(tags.items()))
    field_text = ",".join(f"{_escape(key)}={_field_value(value)}" for key, value in sorted(fields.items()))
    return f"{_escape(measurement, ' ')},{tag_text} {field_text} {timestamp_ms}"


def _start_ms(trip):
    return round(datetime.fromisoformat(trip["triggerStartTime"].replace("Z", "+00:00")).timestamp() * 1000)


def trip_lines(car_id, brand, endpoint, trip):
    """Line protocol for one trip: one line per sample time, plus a summary line at the start."""
    start = _start_ms(trip)
    length = trip.get("tripLength")
    length = length if finite_number(length) and length >= 0 else None
    specs = TRIP_FIELDS.get(endpoint, {})
    unit_fields = {spec.unit_field for spec in specs.values() if isinstance(spec, Spec) and spec.unit_field}
    leaves = dict(trip_leaves(trip))
    samples, scalars = {}, {}
    for path, values in leaves.items():
        if not isinstance(values, list):
            if isinstance(values, (str, bool)) and len(str(values)) <= 255 or finite_number(values):
                scalars[snake_case(path.replace(".", "_"))] = values
            continue
        if path in unit_fields:
            continue  # Units are applied to their values and named in the field key.
        spec = specs.get(path)
        for index, raw in enumerate(values):
            if isinstance(spec, Spec):
                units = leaves.get(spec.unit_field) if spec.unit_field else None
                raw_unit = units[index] if isinstance(units, list) and index < len(units) else None
                value = spec.check(raw, raw_unit)
                if value is None:
                    continue
                unit = spec.unit or UNITS.get(raw_unit)
                factor, unit = NORMALISE.get(raw_unit, (1, unit)) if not spec.unit else (1, unit)
                value *= factor
                key = snake_case(path.replace(".", "_"))
                if unit in SUFFIXES and not key.endswith("_" + SUFFIXES[unit]) and key != SUFFIXES[unit]:
                    key += "_" + SUFFIXES[unit]
            else:
                if raw is None or raw == "invalid":
                    continue
                if not (isinstance(raw, (str, bool)) and len(str(raw)) <= 255 or finite_number(raw)):
                    continue
                value, key = raw, snake_case(path.replace(".", "_"))
            samples.setdefault((len(values), index), {})[key] = value

    timing = "interpolated" if length is not None else "assumed_1s"
    tags = {"vehicle": car_id, "brand": brand, "endpoint": endpoint, "timing": timing}
    by_time = {}
    for (count, index), fields in samples.items():
        if length is not None:
            offset = round(length * index / (count - 1)) if count > 1 else 0
        else:
            offset = index * 1000
        # Fields sampled at different rates can land on the same time; they share a line.
        by_time.setdefault(start + offset, {}).update(fields)
    lines = [_line(MEASUREMENT, tags, fields, time) for time, fields in sorted(by_time.items())]
    summary = {"sample_times": len(by_time)}
    if length is not None:
        summary["duration_s"] = length / 1000
    summary |= scalars
    lines.append(_line(SUMMARY_MEASUREMENT, tags | {"timing": "measured"}, summary, start))
    return lines


class TripExporter:
    def __init__(self, hass, entry_id, brand, options):
        self.hass, self.brand = hass, brand
        self.format = options["export_format"]
        parts = urlsplit(options["export_url"].rstrip("/"))
        # Basic-auth credentials in the URL are sent as a header, never as part of the URL.
        self.basic_auth = (aiohttp.BasicAuth(unquote(parts.username), unquote(parts.password or ""))
                           if parts.username else None)
        self.url = without_credentials(options["export_url"].rstrip("/"))
        self.database, self.org = options["export_database"], options["export_org"]
        self.token = options["export_token"]
        self.store = export_store(hass, entry_id)
        self.exported = None  # {"<car>/<endpoint>": {start: hash}}
        self.pending = []
        self.status = {"state": "ok", "target": self.target(), "last_export_at": None, "trips_last_poll": 0,
                       "trips_exported": 0, "lines_sent": 0, "last_error": None}

    def target(self):
        if self.format == "influx_v2":
            return f"{self.url}/api/v2/write?org={self.org}&bucket={self.database}"
        return f"{self.url}/write" + (f"?db={self.database}" if self.database else "")

    async def load(self):
        if self.exported is None:
            stored = await self.store.async_load()
            self.exported = stored if isinstance(stored, dict) else {}
        self.pending = []

    def consider(self, car_id, endpoint, data):
        trips = data.get("trips") if isinstance(data, dict) else None
        if not isinstance(trips, list):
            return
        known = self.exported.get(f"{car_id}/{endpoint}", {})
        for trip in trips:
            if not isinstance(trip, dict) or reported_age(trip.get("triggerStartTime")) is None:
                continue
            digest = hashlib.sha256(json.dumps(trip, sort_keys=True).encode()).hexdigest()
            if known.get(trip["triggerStartTime"]) != digest:
                self.pending.append((car_id, endpoint, trip, digest))

    async def flush(self):
        pending, self.pending = self.pending, []
        self.status["trips_last_poll"] = 0
        batch, lines = [], []
        changed = False
        for item in pending:
            car_id, endpoint, trip, _ = item
            trip_text = trip_lines(car_id, self.brand, endpoint, trip)
            if lines and len(lines) + len(trip_text) > BATCH_LINES:
                if not await self._send(batch, lines):
                    break
                changed = True
                batch, lines = [], []
            batch.append(item)
            lines.extend(trip_text)
        else:
            if batch and await self._send(batch, lines):
                changed = True
        changed |= self._prune()
        if changed:
            await self.store.async_save(self.exported)

    async def _send(self, batch, lines):
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if self.token:
            headers["Authorization"] = ("Token " if self.format == "influx_v2" else "Bearer ") + self.token
        url = self.target() + ("&" if "?" in self.target() else "?") + "precision=ms"
        try:
            session = async_get_clientsession(self.hass)
            async with session.post(url, data="\n".join(lines).encode(), headers=headers,
                                    auth=None if self.token else self.basic_auth,
                                    timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status >= 300:
                    body = (await response.text())[:200]
                    raise ExportError(f"HTTP {response.status}: {body}")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, ExportError) as error:
            message = str(error) or type(error).__name__
            self.status.update(state="error", last_error=message)
            LOGGER.warning("Could not export Pleos trips to %s: %s", self.status["target"], message)
            return False
        for car_id, endpoint, trip, digest in batch:
            self.exported.setdefault(f"{car_id}/{endpoint}", {})[trip["triggerStartTime"]] = digest
        self.status.update(state="ok", last_error=None, last_export_at=datetime.now().astimezone().isoformat(),
                           trips_last_poll=self.status["trips_last_poll"] + len(batch),
                           trips_exported=self.status["trips_exported"] + len(batch),
                           lines_sent=self.status["lines_sent"] + len(lines))
        return True

    def _prune(self):
        limit = timedelta(days=TRIP_MEMORY_DAYS).total_seconds()
        changed = False
        for key in list(self.exported):
            for start in list(self.exported[key]):
                if (age := reported_age(start)) is None or age > limit:
                    del self.exported[key][start]
                    changed = True
            if not self.exported[key]:
                del self.exported[key]
                changed = True
        return changed


class ExportError(Exception):
    """The database refused the write."""
