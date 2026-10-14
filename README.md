# Pleos Vehicle Data

A Home Assistant integration for the official Pleos **personal Vehicle Data API**. Supports Hyundai, Kia and Genesis accounts, with one entry per manufacturer and a device for each vehicle. Requires Home Assistant **2026.10 or later**.

## Supported vehicles

**Tested and working with a Kia PV5 Passenger.** Available fields depend on the data returned by the vehicle's API.

Pleos documents access to European vehicles using **Hyundai Bluelink, Kia Connect or Genesis Connected services**. See the [official supported-country list](https://document.pleos.ai/en/api-reference/vehicle-data-api/faq).

The following models and variant labels appear in the [Vehicle Data API compatibility matrix](https://document.pleos.ai/en/api-reference/vehicle-data-api/api-compatibility), checked **12 October 2026**:

| Manufacturer | Documented models and variants |
| --- | --- |
| Hyundai | ELANTRA (NORMAL); INSTER (EV); IONIQ 5 (EV); IONIQ 6 (EV); IONIQ (EV, HEV, PHEV); KONA Electric (HEV); KONA N (NORMAL); KONA (EV, HEV, NORMAL); NEXO (FCEV); SANTA FE (HEV, NORMAL, PHEV); STARIA (HEV, NORMAL); TUCSON (HEV, NORMAL, PHEV); i10 (NORMAL); i20 / BAYON (NORMAL); i20 N (NORMAL); i30 N (NORMAL); i30 (NORMAL) |
| Kia | CEED (NORMAL, PHEV); EV3 (EV); EV6 (EV); EV9 (EV); NIRO (EV, HEV, PHEV); Picanto (NORMAL); SORENTO (HEV, NORMAL, PHEV); SOUL EV (EV); SPORTAGE (HEV, NORMAL, PHEV); STINGER (NORMAL); STONIC (NORMAL) |
| Genesis | G70 (NORMAL); G80 (EV, NORMAL); G90 (NORMAL); GV60 (EV); GV70 (EV, NORMAL); GV80 (NORMAL) |

These are Pleos's labels, including “KONA Electric (HEV)”; the matrix does not specify model years. The integration discovers cars through [Get Vehicles](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/for-private-users/get-vehicles), so other models may work too. Available fields vary by vehicle.

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

Each endpoint has an **API diagnostic sensor** showing `available`, `no_data`, `error` or `disabled`. Permission and unregistered-vehicle errors show `error`; code `4045` means `no_data`. The `data` attribute holds the complete vehicle payload, including fields without a dedicated sensor, and `api_metadata` holds response metadata.

The `rejected_values` and `rejected_examples` attributes explain discarded readings, such as values outside the documented range or in an unknown unit. Empty fields and the API's `invalid` markers are not counted. Debug logs include the field and reason without the raw value. The **Vehicle information** sensor exposes VIN, nickname, model and powertrain details, plus other fields returned by Get Vehicles.

Trip queries run every **60 minutes** by default, separately from status polling, and normally cover **yesterday and today in UTC**. After a gap, they reach back to the last successful fetch, up to **7 days** including today. First setup also checks this wider window. If Pleos refuses the range, the integration retries with two days. The diagnostic sensor's `query` attribute shows the dates requested.

Trip sensors keep their results between queries. New vehicles are queried immediately; failed requests retry on the next status poll. `no_data` results wait for the normal trip interval. Pleos delivers trips with a delay, so polling more often may return the same data.

Full returned trips remain in the diagnostic sensor's `data` attribute. Individual trip sensors show the latest trip's final sample when valid, with trip start, duration and sample index.

Numeric trip sensors have `trip_min`, `trip_max`, `trip_mean`, `valid_samples` and `total_samples` attributes. Summaries use valid samples in the final sample's unit; the mean is per sample, not time-weighted. Home Assistant can convert the sensor state to your preferred unit without converting these attributes. Each endpoint reports its own latest trip, so its readings may refer to a different drive.

**Trip distance (last trip)** is the difference between the first and last valid odometer samples of the latest driving trip. Its `odometer_start` and `odometer_end` attributes are in kilometres. An invalid final sample does not become a reading. Undocumented fields also get **(last trip)** readings, with numeric summaries where possible. Individual samples have no documented timestamps.

### Units

Status readings use the unit returned by the API. For example, the [odometer response](https://document.pleos.ai/en/api-reference/vehicle-data-api/vehicle-data-api/common/get-vehicle-operation-status) can report `km`, `mile`, `meter` or `feet`; these become Home Assistant's `km`, `mi`, `m` or `ft`. Trip odometer samples are documented in kilometres.

Home Assistant converts readings for your unit preferences. To display miles, open the odometer entity's settings and set **Unit of measurement** to **mi**. This converts both the number and label: **1,609.344 km = 1,000 mi**. If a reading differs from the car or app, compare its value and unit with `data.odometer` on the **Driving status API** sensor and include both in an issue report.

### Trip events

Enable **Trip events** in the integration options to fire a `pleos_vehicle_data_trip` event for each new trip. It is off by default. Events contain every returned sample for use in automations or exports, for example through Node-RED or AppDaemon:

| Key | Content |
| --- | --- |
| `vehicle_id` | Stable vehicle ID used by this integration (not the VIN) |
| `device_id` | Home Assistant device, once the vehicle's device exists |
| `brand` | `hyundai`, `kia` or `genesis` |
| `endpoint` | `powertrains/data`, `speed`, `driving/data` or `status/data` |
| `trip_start`, `trip_length_ms` | Trip start time and duration as returned |
| `trip` | The complete trip object, including all sample arrays |

Trips are identified by start time, vehicle and endpoint. Each endpoint sends its own event, including for late arrivals within the query window. Sent trips are remembered across restarts for nine days. Events fire after a complete poll; first setup sends events for trips already in the window.

The recorder stores events, including their sample arrays. To keep them out of the database, add this to `configuration.yaml`:

```yaml
recorder:
  exclude:
    event_types:
      - pleos_vehicle_data_trip
```

Use trip events or the archive to export every returned trip. Sensor history contains the latest trip at each poll, so several trips arriving together produce one latest-trip state.

Status endpoints have reporting-time and data-age sensors when the vehicle supplies a valid timestamp. Readings stay visible while the car is parked; check `reported_at`, `data_age_minutes` and `data_stale` for their age. Turning off the location option disables both location requests and GPS tracking.

Entities use names such as **AC charge limit** and **Cabin fan speed**; historical readings include **(last trip)**. Name changes preserve existing IDs and customised names. Documented fields support Home Assistant translations, with English included. New IDs use the English name regardless of your display language. Undocumented fields use names derived from their API paths.

### Response archive

The optional archive keeps raw snapshot payloads and trip objects, including fields this integration does not understand yet. The archive is off by default and can be enabled under **Storage** in the integration options.

- Files: `<config>/pleos_vehicle_data/archive/<manufacturer>/<YYYY-MM>/<YYYY-MM-DD>.jsonl.gz`, one gzip-compressed JSON object per line, by UTC day of the request.
- A snapshot or the vehicle list is written when its content changes; an unchanged response is not written again. Each trip is written once, and again only if Pleos later returns different content for it. Restarts do not cause duplicates.
- Each record has `schema`, `kind` (`vehicles`, `snapshot`, `trip` or `trip_response`), `fetched_at`, `endpoint`, `vehicle_id`, the trip `query` and the API's `api_metadata`, plus the response in `data` or the trip in `trip`.
- Files remain until you delete them, including after removing the integration. Read them with `zcat`, `zgrep` or Python's `gzip.open`.
- The **Archive** diagnostic sensor on the manufacturer's API device shows `ok` or `error`, the folder, the last write and the last error.

The archive contains the VIN in the vehicle list and the car's locations. Treat the folder like the rest of your Home Assistant configuration.

### Diagnostics

Use **Settings → Devices & services → Pleos Vehicle Data → ⋮ → Download diagnostics** for troubleshooting. It includes options, polling and rate-limit status, trip windows, archive status, readings and raw responses. A download from a car's device page contains only that car. Credentials, VINs, nicknames and coordinates are removed. Review the file before attaching it to an issue, especially if the API adds new fields.

## Compared with kia_uvo

[kia_uvo](https://github.com/Hyundai-Kia-Connect/kia_uvo) (Kia Uvo / Hyundai Bluelink) uses [hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api) to talk to the manufacturers' phone-app backends. This integration uses the official Pleos Vehicle Data API. Both can run side by side, with separate devices and credentials.

Compared with kia_uvo 3.17.1 (library 4.35.2) on 14 October 2026, using its [documented entities](https://github.com/Hyundai-Kia-Connect/kia_uvo#supported-entities) and [trip sensors](https://github.com/Hyundai-Kia-Connect/kia_uvo/blob/master/custom_components/kia_uvo/sensor.py).

### How they connect

| | Pleos Vehicle Data (this integration) | kia_uvo |
| --- | --- | --- |
| API | Official, documented personal API | Unofficial; uses the phone app's backend |
| Login | Personal Client ID and Secret from Pleos Playground, renewed manually | App username and password, sometimes a one-time code |
| Regions | Europe | EU, US, CA, AU, NZ, IN, CN, BR |
| Brands | Hyundai, Kia, Genesis | Kia, Hyundai; Genesis in Canada only (untested) |
| Data freshness | Whatever the car last uploaded; trip data arrives with a delay | Cached data every 30 minutes, plus optional force refresh |
| Wakes the car | Never | On force refresh (every 4 hours by default) |
| Request limits | Not documented; the integration backs off on HTTP 429 | Shares the app backend's limits; force refresh should be used sparingly |
| Remote commands | None (read-only) | Lock, climate, charging, charge limits, windows, horn and lights, schedules, navigation |
| Backend changes | Depends on the official Pleos API | May need library updates when the app backend changes |

### Data

✅ available · 🟡 partial · ❌ not available

| Data | Pleos | kia_uvo |
| --- | :---: | :---: |
| State of charge, range, odometer, fuel level | ✅ | ✅ |
| Range for each powertrain (ICE, EV, HEV, PHEV, hydrogen) | ✅ | 🟡 EV, fuel and total |
| Charging, plugged in, charge limits, time remaining | ✅ | ✅ |
| Charging status including V2L | ✅ | 🟡 V2L and V2X flags |
| Charging power and current | ❌ | ✅ |
| 12 V battery level and warning | ❌ | ✅ |
| Battery state of health, remaining energy | ❌ | ✅ |
| Battery temperatures, chiller, preconditioning, winter mode (newer cars) | ❌ | ✅ |
| Charge port door, charging and departure schedules | ❌ | ✅ |
| Energy consumed and regenerated, 30-day and daily statistics | ❌ | ✅ |
| Location (latitude, longitude) | ✅ | ✅ |
| Altitude, heading, speed | ✅ | 🟡 heading and speed in trip details |
| Doors and locks, bonnet, boot, sunroof | ✅ | ✅ |
| Windows | ❌ | ✅ |
| Tyre pressures | ✅ | ✅ |
| Tyre pressure warnings | ❌ | ✅ |
| Climate state and target temperature | ✅ | ✅ |
| Climate mode and cabin fan speed | ✅ | ❌ |
| Seat heating, steering wheel heating, defrost | ✅ | ✅ |
| Seat ventilation | ✅ | 🟡 in seat status |
| Mirror and rear window heating | ❌ | ✅ |
| Ignition, accessory and high-voltage power states, sleep mode | ✅ | 🟡 no ign1 |
| Warnings | 🟡 list (such as washer fluid, low fuel) | ✅ individual warnings |
| Lights (turn signals, brake lights, headlights) | 🟡 light switch setting during trips | ✅ |
| Service intervals, fault code count | ❌ | ✅ |
| Reporting time and data age for each data group | ✅ | 🟡 last updated, location updated |
| Trip summaries (distance, duration) | ✅ | 🟡 trip details and daily statistics |
| Trip sample series (wheel speeds, RPM, HV voltage, acceleration, yaw, steering, gear, seat belts) | ✅ | ❌ |
| Trip events with every sample, per-trip min, max and mean | ✅ | ❌ |
| Freezer (Kia PV5) | ✅ | ❌ |
| 360° camera images | ❌ | ✅ Hyundai |

Availability depends on the vehicle, model year and region in both integrations. Marks describe the documented entities and trip attributes; either API may return additional fields.

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

The private API only allows the vehicle's **primary user** to authorise access. Shared or secondary app users cannot authorise a vehicle, even if they can see it in the manufacturer's app. Generate the key using the primary user's manufacturer account.

1. Sign into [Pleos Playground](https://pleos.ai/). Open the account menu → **My Vehicle Data API**. This menu is available to users in Europe.
2. Select the manufacturer, accept the required terms, choose **Get API Key**, and sign into the manufacturer's account. Copy **Client ID** and **Client Secret**. Private users do not need a business project or API-access approval. [Official key-generation guide](https://document.pleos.ai/en/api-reference/vehicle-data-api/getting-started/for-private-users/request-my-vehicle-api-key).
3. In Home Assistant, open **Settings → Devices & services → Add integration → Pleos Vehicle Data**. Select that manufacturer and enter both credentials. Setup validates them before saving.
4. Add the car's entities to dashboards or automations. Repeat setup for another manufacturer if needed.

## Example dashboard

[`examples/dashboard.yaml`](examples/dashboard.yaml) uses built-in cards for charge, range, doors, climate, a 48-hour map, trip summaries, 7-day graphs and data age.

1. Replace `my_car_` with your car's entity-ID prefix and `hyundai_api_` with `kia_api_` or `genesis_api_` if needed. Check the full IDs against your entity list: older installations and customised IDs may also have different suffixes. Update these in both cards and markdown templates; some templates assemble IDs from a prefix and suffix.
2. In Home Assistant, add a dashboard, open it, then choose **⋮ → Edit → ⋮ → Raw configuration editor** and paste the file.

Missing tiles, gauges, map and badges are hidden outside edit mode. Trip cards fill in as valid samples arrive; graphs need recorded history. Missing trip distance is shown as unavailable. Summary tables label API units separately from converted sensor states. The VIN is masked to its last six characters.

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

Trip features, translations, diagnostics and the example dashboard contributed by [Alex (@isarrider)](https://github.com/isarrider) in [PR #5](https://github.com/jouwdan/ha-pleos/pull/5).
