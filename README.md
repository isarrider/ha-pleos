# Pleos Vehicle Data

A native Home Assistant integration for the official Pleos **personal Vehicle Data API**. Supports Hyundai, Kia and Genesis accounts, with one integration entry per manufacturer and a device for each returned vehicle. Requires Home Assistant **2026.10 or later**.

## Supported vehicles

**Tested and working with a Kia PV5 Passenger.** Available fields depend on the data returned by the vehicle's API.

Pleos documents access to European vehicles using **Hyundai Bluelink, Kia Connect or Genesis Connected services**. See the [official supported-country list](https://document.pleos.ai/en/api-reference/vehicle-data-api/faq).

The following models and variant labels appear in the [Vehicle Data API compatibility matrix](https://document.pleos.ai/en/api-reference/vehicle-data-api/api-compatibility), checked **12 October 2026**:

| Manufacturer | Documented models and variants |
| --- | --- |
| Hyundai | ELANTRA (NORMAL); INSTER (EV); IONIQ 5 (EV); IONIQ 6 (EV); IONIQ (EV, HEV, PHEV); KONA Electric (HEV); KONA N (NORMAL); KONA (EV, HEV, NORMAL); NEXO (FCEV); SANTA FE (HEV, NORMAL, PHEV); STARIA (HEV, NORMAL); TUCSON (HEV, NORMAL, PHEV); i10 (NORMAL); i20 / BAYON (NORMAL); i20 N (NORMAL); i30 N (NORMAL); i30 (NORMAL) |
| Kia | CEED (NORMAL, PHEV); EV3 (EV); EV6 (EV); EV9 (EV); NIRO (EV, HEV, PHEV); Picanto (NORMAL); SORENTO (HEV, NORMAL, PHEV); SOUL EV (EV); SPORTAGE (HEV, NORMAL, PHEV); STINGER (NORMAL); STONIC (NORMAL) |
| Genesis | G70 (NORMAL); G80 (EV, NORMAL); G90 (NORMAL); GV60 (EV); GV70 (EV, NORMAL); GV80 (NORMAL) |

These are the matrix's labels, including “KONA Electric (HEV)”. Pleos does not specify model years in that table. A listing does not guarantee every field. The integration discovers cars through [Get Vehicles](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/for-private-users/get-vehicles); it does not restrict setup to this list.

## Data available in Home Assistant

All ten common Vehicle Data API endpoints are queried. Fields appear when the API supplies valid values for the vehicle.

| API | Endpoint suffix | Exposed data |
| --- | --- | --- |
| [Battery charging](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-battery-charging-status) | `batteries` | Plug connection, charging state, charge percentage, capacity, charge targets and remaining charge time |
| [Powertrain status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-powertrain-status) | `powertrains` | Fuel level, battery percentage and remaining range |
| [Location](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-location-info) | `locations` | Coordinates, coordinate system, altitude, heading and speed; GPS tracker for valid WGS84 coordinates |
| [Driving status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-operation-status) | `driving` | Ignition, power states, sleep mode, odometer and parking position |
| [Vehicle status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-status) | `status` | Doors, bonnet, boot, sunroof, tyres, warnings (full list and count as attributes), climate, defrost, steering-wheel heat, seats and outside temperature |
| [Freezer status](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-freezer-status) | `freezer` | Operation state and temperature |
| [Powertrain trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-powertrain-data) | `powertrains/data` | Battery voltage, engine oil temperature and RPM samples |
| [Speed trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-speed-data) | `speed` | Dashboard speed, units and four wheel-speed series |
| [Driving trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-operation-data) | `driving/data` | Ignition, mileage, trip distance, acceleration, yaw, ISLA settings, seat belts, steering and gear samples |
| [Vehicle status trips](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-data) | `status/data` | Door, lamp, tyre-pressure, low-fuel-warning and outside-temperature samples |

Each endpoint has an **API diagnostic sensor** showing `available`, `no_data`, `error` or `disabled`. Its `rejected_values` and `rejected_examples` attributes count and explain values that were present but not turned into readings, for example out of the documented range or in an unknown unit; debug logging lists each one. Values the API leaves empty or marks `invalid` are not counted. Permission and unregistered-vehicle responses show `error`, while `4045` means `no_data`. Its `data` attribute contains the complete returned vehicle payload, including arrays and fields without a dedicated sensor; response metadata is under `api_metadata`. Vehicle information includes VIN, nickname, model code, model name and powertrain type, plus any other field Get Vehicles returns, as an attribute named after the field in snake case.

Trip queries normally cover **yesterday and today in UTC** and run every **60 minutes** by default, separately from snapshot polling. On first setup, and after Home Assistant was offline or a trip query failed, they reach back to the last day fetched successfully, at most **7 days** including today, so trips from the gap are still collected. Pleos does not document a maximum range; if it refuses a longer query, the integration falls back to two days. Each endpoint's diagnostic sensor shows the range in its `query` attribute. Pleos delivers trip data with a delay, so frequent trip queries add requests without adding data. Between trip queries, trip sensors keep their last results. A newly added vehicle is queried immediately, and a rate-limited poll is retried at the next poll. Full returned trips remain in the diagnostic sensor's `data` attribute. Individual trip sensors show the latest trip's final sample when valid, with trip start, duration and sample index. Numeric trip sensors also carry `trip_min`, `trip_max` and `trip_mean` attributes plus `valid_samples` and `total_samples`. These cover every valid sample of that trip in the unit of the final sample. The mean is taken over samples, not weighted by time. **Trip distance (last trip)** is the difference between the first and last valid odometer samples of the latest driving trip. Invalid final samples are omitted rather than replaced by older readings. Trip fields Pleos returns without documentation get a plain **(last trip)** reading too, with the same summary attributes for numbers. Pleos does not document individual sample timestamps, so none are invented. Trip readings are historical and are labelled separately from current-status snapshots.

### Trip events

Each new trip fires a `pleos_vehicle_data_trip` event once, carrying every sample the API returned for it. Use it in automations or to export complete trips, for example to InfluxDB through Node-RED or AppDaemon. Event data:

| Key | Content |
| --- | --- |
| `vehicle_id` | Stable vehicle ID used by this integration (not the VIN) |
| `device_id` | Home Assistant device, once the vehicle's device exists |
| `brand` | `hyundai`, `kia` or `genesis` |
| `endpoint` | `powertrains/data`, `speed`, `driving/data` or `status/data` |
| `trip_start`, `trip_length_ms` | Trip start time and duration as returned |
| `trip` | The complete trip object, including all sample arrays |

A trip is identified by its start time per vehicle and endpoint, so each endpoint announces it separately. Announced trips are remembered across restarts for four days. Trips arriving late within the two-day query window are still announced. Events are fired only after a complete poll, so a failed poll never marks a trip as announced. On first setup, trips already in the query window are announced once.

The recorder stores events, including their sample arrays. To keep them out of the database, add this to `configuration.yaml`:

```yaml
recorder:
  exclude:
    event_types:
      - pleos_vehicle_data_trip
```

Trip events can be turned off in the integration options.

Snapshot endpoints have reporting-time and data-age sensors when a valid vehicle timestamp is supplied. Valid last-reported values, including location, stay visible while the car is parked. Their `reported_at`, `data_age_minutes` and `data_stale` attributes show how old they are; they do not imply the car just uploaded new data. The location option disables both the location request and GPS tracking.

Entities use descriptive names such as **AC charge limit**, **Cabin fan speed** and **Front left seat heating**. Trip readings include **(last trip)** in their names. Updating display names preserves existing entity IDs and any names you have customised. Documented fields use Home Assistant name translations (English is provided), so they can be translated like any core integration. Fields the API returns without documentation keep a name generated from their path. New entity IDs are always built from the English name, whatever language Home Assistant uses, so they stay the same across languages and future translations.

### Response archive

Every API response is kept exactly as received, so nothing the API returns is lost, even fields this integration does not understand yet. The archive is on by default and can be turned off under **Storage** in the integration options.

- Files: `<config>/pleos_vehicle_data/archive/<manufacturer>/<YYYY-MM>/<YYYY-MM-DD>.jsonl.gz`, one gzip-compressed JSON object per line, by UTC day of the request.
- A snapshot or the vehicle list is written when its content changes; an unchanged response is not written again. Each trip is written once, and again only if Pleos later returns different content for it. Restarts do not cause duplicates.
- Each record has `schema`, `kind` (`vehicles`, `snapshot`, `trip` or `trip_response`), `fetched_at`, `endpoint`, `vehicle_id`, the trip `query` and the API's `api_metadata`, plus the response in `data` or the trip in `trip`.
- Nothing is deleted, also not when the integration is removed. Read it with `zcat` or `zgrep`, or `gzip.open` in Python.
- The **Archive** diagnostic sensor on the manufacturer's API device shows `ok` or `error`, the folder, the last write and the last error.

The archive contains the VIN in the vehicle list and the car's locations. Treat the folder like the rest of your Home Assistant configuration.

### Trip export to VictoriaMetrics or InfluxDB

Home Assistant's InfluxDB integration only exports entity states, so trip sample series never reach a time-series database on their own. Under **Trip export** in the integration options, the integration can write every sample of each new trip itself, in InfluxDB line protocol:

| Database | Setting | Request |
| --- | --- | --- |
| VictoriaMetrics | VictoriaMetrics or InfluxDB 1.x, URL such as `http://victoriametrics:8428`, database optional | `POST /write?db=…&precision=ms` |
| InfluxDB 1.x | VictoriaMetrics or InfluxDB 1.x, URL and database | `POST /write?db=…&precision=ms` |
| InfluxDB 2.x | InfluxDB 2.x, URL, bucket, organisation and token | `POST /api/v2/write?org=…&bucket=…&precision=ms` |

A token is sent as `Authorization: Bearer …` (InfluxDB 2.x: `Token …`); a user and password can instead be given in the URL (`http://user:password@host:port`) and are sent as basic authentication. Credentials never appear in the status sensor, logs or diagnostics.

What is written:

- `pleos_trip`, one line per sample time, tagged `vehicle` (this integration's vehicle ID, not the VIN), `brand`, `endpoint` and `timing`. Field names follow the API path in snake case with the unit appended, for example `location_speed_kmh`, `wheel_speed_front_left_kmh`, `engine_rpm`, `batteries_hv_voltage_v`, `tire_pressure_front_left_kpa`. Speeds are converted to km/h and tyre pressures to kPa; invalid samples are left out. Undocumented trip fields are written as they come. All numbers are written as floats.
- `pleos_trip_summary` at the trip start: `duration_s`, the number of sample times, and any single values of the trip.
- **Timing:** Pleos does not give a time for each sample. Samples are spread evenly over the trip's duration and tagged `timing=interpolated`; without a duration, one second apart and tagged `timing=assumed_1s`.

Each trip is written once, including trips found by the catch-up after downtime. If Pleos later returns more samples for a trip, the whole trip is written again. If a write fails, the trip is retried with the next trip query. The **Export** diagnostic sensor on the manufacturer's API device shows `ok` or `error`, the target and the last error.

VictoriaMetrics names series `pleos_trip_<field>` with the tags as labels and ignores text fields (such as the gear); those stay in the response archive. Start VictoriaMetrics with `-dedup.minScrapeInterval=1ms` so a trip written again does not leave duplicate samples.

### Diagnostics

**Settings → Devices & services → Pleos Vehicle Data → ⋮ → Download diagnostics** saves the integration's state for troubleshooting: options, polling and rate-limit status, trip windows and announced trips, archive status, and every car's parsed readings with the latest raw responses. The download from a car's device page contains only that car. Credentials, the VIN (also inside request paths and texts), vehicle nicknames and coordinates are removed, so it can be attached to an issue.

## Compared with kia_uvo

[kia_uvo](https://github.com/Hyundai-Kia-Connect/kia_uvo) (Kia Uvo / Hyundai Bluelink) is the widely used community integration. It is built on [hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api), which talks to the backend of the manufacturers' phone apps. This integration uses the official Pleos Vehicle Data API instead. The two can run side by side: they create separate devices and use different accounts and backends.

Compared with kia_uvo 3.17.1 (library 4.35.2) on 14 October 2026.

### How they connect

| | Pleos Vehicle Data (this integration) | kia_uvo |
| --- | --- | --- |
| API | Official, documented personal API | Unofficial; uses the phone app's backend |
| Login | Personal Client ID and Secret from Pleos Playground, renewed manually | App username and password, sometimes a one-time code |
| Regions | Europe | EU, US, CA, AU, NZ, IN, CN, BR |
| Brands | Hyundai, Kia, Genesis | Kia, Hyundai; Genesis in Canada only (untested) |
| Data freshness | Whatever the car last uploaded; trip data arrives with a delay | Cached data every 30 minutes, plus optional force refresh |
| Wakes the car | Never | On force refresh (every 4 hours by default) |
| Request limits | Not documented; the integration backs off on HTTP 429 | Shares the app backend's limit (about 200 per day in the EU, community figure) |
| Remote commands | None (read-only) | Lock, climate, charging, charge limits, windows, horn and lights, schedules, navigation |
| Breaks when the app changes | No | Can, until the library is updated |

### Data

✔ available · ◐ partial · ✖ not available

| Data | Pleos | kia_uvo |
| --- | :---: | :---: |
| State of charge, range, odometer, fuel level | ✔ | ✔ |
| Range for each powertrain (ICE, EV, HEV, PHEV, hydrogen) | ✔ | ◐ EV, fuel and total |
| Charging, plugged in, charge limits, time remaining | ✔ | ✔ |
| Charging status including V2L | ✔ | ◐ V2L and V2X flags |
| Charging power and current | ✖ | ✔ |
| 12 V battery level and warning | ✖ | ✔ |
| Battery state of health, remaining energy | ✖ | ✔ |
| Battery temperatures, chiller, preconditioning, winter mode (newer cars) | ✖ | ✔ |
| Charge port door, charging and departure schedules | ✖ | ✔ |
| Energy consumed and regenerated, 30-day and daily statistics | ✖ | ✔ |
| Location (latitude, longitude) | ✔ | ✔ |
| Altitude, heading, speed | ✔ | ✖ |
| Doors and locks, bonnet, boot, sunroof | ✔ | ✔ |
| Windows | ✖ | ✔ |
| Tyre pressures | ✔ | ✔ |
| Tyre pressure warnings | ✖ | ✔ |
| Climate state and target temperature | ✔ | ✔ |
| Climate mode and cabin fan speed | ✔ | ✖ |
| Seat heating, steering wheel heating, defrost | ✔ | ✔ |
| Seat ventilation | ✔ | ◐ in seat status |
| Mirror and rear window heating | ✖ | ✔ |
| Ignition, accessory and high-voltage power states, sleep mode | ✔ | ◐ no ign1 |
| Warnings | ◐ list (such as washer fluid, low fuel) | ✔ individual warnings |
| Lights (turn signals, brake lights, headlights) | ◐ light switch setting during trips | ✔ |
| Service intervals, fault code count | ✖ | ✔ |
| Reporting time and data age for each data group | ✔ | ◐ last updated, location updated |
| Trip summaries (distance, duration) | ✔ | ◐ in the library, daily statistics in HA |
| Trip sample series (wheel speeds, RPM, HV voltage, acceleration, yaw, steering, gear, seat belts) | ✔ | ✖ |
| Trip events with every sample, per-trip min, max and mean | ✔ | ✖ |
| Freezer (Kia PV5) | ✔ | ✖ |
| 360° camera images | ✖ | ✔ Hyundai |

Availability depends on the vehicle, model year and region in both integrations.

## Install with HACS

1. [Install and configure HACS](https://www.hacs.dev/docs/use/download/download/) if required.
2. Open **HACS → ⋮ → Custom repositories**. Add `https://github.com/jouwdan/ha-pleos` with type **Integration**.
3. Find **Pleos Vehicle Data**, download it, then restart Home Assistant.
4. Complete the setup below. HACS manages later integration downloads and updates.

## Install without HACS

1. Download the source ZIP from the [latest release](https://github.com/jouwdan/ha-pleos/releases/latest).
2. Extract it and copy `custom_components/pleos_vehicle_data` into your Home Assistant configuration directory's `custom_components` folder. The result must be `<config>/custom_components/pleos_vehicle_data/manifest.json`.
3. Restart Home Assistant and complete the setup below. For updates, replace that integration directory with the new release and restart.

## Set up your account

1. Sign into [Pleos Playground](https://pleos.ai/). Open the account menu → **My Vehicle Data API**. This menu is available to users in Europe.
2. Select the manufacturer, accept the required terms, choose **Get API Key**, and sign into the manufacturer's account. Copy **Client ID** and **Client Secret**. Private users do not need a business project or API-access approval. [Official key-generation guide](https://document.pleos.ai/en/api-reference/vehicle-data-api/getting-started/for-private-users/request-my-vehicle-api-key).
3. In Home Assistant, open **Settings → Devices & services → Add integration → Pleos Vehicle Data**. Select that manufacturer and enter both credentials. Setup validates them before saving.
4. Add the car's entities to dashboards or automations. Repeat setup for another manufacturer if needed.

## Expired or replaced API keys

Personal keys expire and require **manual renewal**. Check the expiry shown in Pleos. The integration renews access tokens automatically, not the personal key. [Official key-management guide](https://document.pleos.ai/en/api-reference/vehicle-data-api/getting-started/for-private-users/manage-my-vehicle-api-key).

Expired access tokens are replaced automatically and the failed request is retried once. A 403 that persists with a fresh token remains an API permission error; it does not ask you to replace a valid personal key.

When credentials are rejected, polling stops and Home Assistant requests reauthentication. Generate a new personal key and replace **both Client ID and Client Secret** in that request. To replace credentials before expiry, use the manufacturer's **⋮ → Reconfigure** menu. The existing entry reloads and retains entity IDs for the same vehicles.

Pleos invalidates old access tokens when a key expires, is revoked or is reissued. A newly registered or activated vehicle also needs a new key: vehicle access is fixed at issuance. [Get Vehicles rules](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/for-private-users/get-vehicles).

## Limitations and privacy

- Default polling is **15 minutes**. After **60 minutes**, snapshot readings are marked `data_stale: true` but remain available. Both settings are configurable under **Vehicle status** in the integration options, and the trip data interval and trip events under **Trips**; age is updated at each poll. Missing or invalid readings and failed account requests can still make entities unavailable. Automations that require recent information should check `data_stale`.
- Availability varies with vehicle, powertrain, model year, region and connected-service settings. Null or documented invalid values do not become vehicle readings. `no_data` is an API result, not a guarantee of future availability. [Pleos FAQ](https://document.pleos.ai/en/api-reference/vehicle-data-api/faq).
- Pleos says trip data is available only **24 hours after consent**, and also depends on connected services, data-collection consent and ePrivacy settings. A `4045` response means no data was returned. [Pleos FAQ](https://document.pleos.ai/en/api-reference/vehicle-data-api/faq).
- This is a read-only cloud integration. It does not implement remote vehicle commands, camera access, the in-car SDK, Fleet API or business-user authentication/consent flows. Status snapshots and returned trip samples are not live telemetry.
- A failing optional endpoint, including a timeout, does not hide other endpoint data. Rate limits stop remaining requests and keep readings already fetched in that poll; affected readings may be unavailable until recovery. Account-level failures are reported by the Connection sensor. Requests retry on later polls and respect `Retry-After`. Responses above **16 MiB** are rejected.
- Keys live in Home Assistant's private configuration; access tokens stay in memory. Credentials and VINs are not logged. Raw payloads and VIN attributes are excluded from recorder history, but remain accessible in current entity attributes. Protect configuration files and backups.
