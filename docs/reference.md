# Pipeline reference

[← Project overview](../README.md) · [Development](development.md) · [Deployment](deployment.md)

Detailed behavior of recording, tracking, alerts, and replay. Commands run from
the repository root. Thresholds below describe the local configuration; the
hosted five-minute cadence uses different thresholds described in the deployment guide.

## Observations and tracks

Each usable position becomes an immutable `Observation`. It retains both the
source position timestamp (`time_position`) and UTC receipt time as epoch seconds.
Altitude is barometric meters, speed is meters/second, and heading is degrees
clockwise from north. Missing optional values remain null. Invalid rows or missing
positions are counted as rejected without discarding other aircraft in the response.

Tracks are keyed by `(source, object_id)`; ICAO24 identifiers are lowercased. The hosted adapter also uses `adsbfi` source IDs;
AIS vessel IDs are MMSIs under `aisstream`.
`first_seen` and `last_seen` refer to accepted position timestamps. Only strictly
newer positions enter history. Duplicate timestamps and out-of-order updates are
ignored by the position history. The alert engine independently examines squawk changes using
the source last-contact timestamp. Polling an
old position repeatedly does not keep its track alive. This is position freshness,
not proof of a transponder outage; Gap alerts apply additional source-health and airport checks.

The `[tracks]` settings control a 120-observation history limit and aging:

- Active until silence exceeds 60 seconds.
- Gap after 60 seconds, lost after 300 seconds.
- Removed after 1,800 seconds; a later fresh observation starts a new track.

A separate timer checks aging every second, including during pending network
requests and long retry waits. Newer positions can reactivate a track; old input
cannot recreate an already expired track. The manager never moves its clock
backward. Each successful poll prints added observations, rejected rows, and
active/gap/lost counts. Track state is in memory and starts empty on each launch.

## Raw recording

`[recording].raw_log_path` defaults to `data/raw.jsonl`, relative to the launch
working directory. Sentinel appends one JSON object per line and flushes each
record before processing. A recording error stops the pipeline. Flush does not
guarantee survival of a power failure; there is no per-record disk `fsync`.

Records carry schema `version: 1`, a session ID, a UTC epoch `timestamp`, and `kind`:

- `session`: track settings for the new, initially empty manager.
- `response`: source, HTTP status, exact decoded HTTP body bytes in `body_base64`,
  and an allowlist of content-type and retry headers. Written before validation,
  including malformed and error responses. HTTP compression is decoded by httpx.
- `tick`: an explicit clock advance, allowing future replay to reproduce silence.

OAuth token responses, credentials, and request authorization headers are not
recorded. A network failure has no response body to record; timer ticks continue.
Files append across launches, with session IDs separating runs. Use one process
per log file. Logs are excluded from Git and currently have no rotation or size
limit. Replay processes these recorded inputs and clock events offline; see Replay below.

## Map and API

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/sentinel --config config.toml --serve
```

Open http://127.0.0.1:8000. Use `--port 8001` if needed. The server binds only to
localhost and starts one poller and aging timer; do not run a separate poller or
multiple server workers against the same raw log. Stop with Ctrl-C. Closing the
browser does not stop the server or its API usage.

The map shows heading arrows (a circle for unknown heading), recent position
trails, and active/gap/lost colors. Select an aircraft marker or list row for
position, altitude, speed, heading, and squawk. Search by ICAO24, toggle trails,
and use Fit coverage to return to the configured bounding box. Trails accumulate
while this process runs and are limited by `history_size`. Kalman estimates are
displayed separately; see [tracking evaluation](validation.md).

The browser receives a full snapshot every second over `/ws/tracks`, reconnects
with backoff, and removes expired tracks from the map. These snapshots do not
increase the OpenSky polling rate. The header reports the browser connection;
the Data source panel separately reports successful polling, retrying, or stopped
ingestion. Data can remain visible during an interruption and should be treated
as stale. A stopped ingestion task leaves the map available for inspection.

- `GET /api/tracks`: snapshot with source status, counts, latest positions, and trails.
- `GET /api/health`: source status and the last successful receipt timestamp.
- `WS /ws/tracks`: full snapshots, including empty states and track removals.
- `GET /docs`: generated API documentation.

Leaflet 1.9.4 loads from unpkg with integrity checks; map tiles load from
OpenStreetMap with attribution. Internet access is required for both. A visible
message explains map-resource failures; the aircraft list still works if the map
library cannot load. This is a local development service without user accounts.

Web tests exercise static pages, REST snapshots, WebSocket connections and
reconnections, source failures, and pipeline shutdown with mocked HTTP data.

## Alerts

The pipeline emits `signal_gap` and `emergency_squawk` events. The map's Recent
alert events panel and colored rings show event locations, including events whose
tracks have since expired. Select an event to open its map popup. Red rings mark
emergency-code events; amber rings mark gaps. These are historical events, not
an assertion that an emergency is still active. Events without a usable position
appear in the list only. Search filters aircraft and alerts by ICAO24.

`GET /api/alerts` and WebSocket snapshots return the most recent 200 events from
the current session, newest first. All emitted events append to
`[recording].alert_log_path` (`data/alerts.jsonl` in the sample configuration).
When omitted, this file is `alerts.jsonl` beside the raw log. Each event includes
an ID, session, type, source, object ID, detection timestamp, last known coordinates
(or null), and rule details. Raw and alert files must be different. Events are
flushed before appearing in the UI; a logging failure stops ingestion. Logs have
no rotation and the UI does not reload historical sessions on restart.

### Signal gaps

`[alerts].gap_seconds` defaults to 60 seconds. An aircraft must first have had a
fresh accepted position during this session. The rule emits once per silent
episode, only while successful source responses remain recent. Fresh positions
rearm the rule. Ground positions, positions within `airport_radius_km` (8 km) of
an airport reference point, and positions outside reference coverage are suppressed.
Track coloring still uses the separate `[tracks]` thresholds.

Failed requests, entirely unusable position batches, stale source snapshots, or
more than `source_stale_seconds` (45) without a healthy response suppress gap
alerts. After recovery, a full `gap_seconds` grace period precedes any gap alert.
Thus source outages do not immediately produce a backlog of aircraft alerts.
Choose source_stale_seconds above your polling interval if changing that interval.
A gap means missing position data, not proof of a disabled transponder: reception
loss, coverage boundaries, and unlisted landing sites can still cause gaps.

The bundled airport reference includes 718 non-closed airports, heliports, and
other facilities from [OurAirports public-domain data](https://ourairports.com/data/),
retrieved October 23, 2026. Alert coverage is 36–40 N, 124–120 W, with facilities
collected one degree beyond that boundary. This community-maintained dataset is
not guaranteed complete. Outside this region, provide `airport_file` as a JSON
file containing `coverage` (lamin/lamax/lomin/lomax) and a nonempty `airports` list
with `id`, `lat`, and `lon`. Include a buffer around your coverage region at least
as wide as the configured suppression radius. The complete reference and rule
settings are recorded in the raw session entry for future replay.

### Emergency squawks

Observed 7500, 7600, and 7700 codes generate events for unlawful interference,
radio communication failure, and general emergency respectively, consistent with
the [FAA transponder guidance](https://www.faa.gov/Air_Traffic/publications/atpubs/atc_html/chap5_section_2.html).
They report a received code, not a confirmed incident.

Squawk evaluation is independent of position acceptance. A newer last-contact
message can trigger an alert even without a new or usable position; the last known
position is used when available and its timestamp is recorded. Stale contact data
and out-of-order/duplicate contact timestamps are ignored. Repeated emergency
codes emit once until a different observed code appears; missing codes do not
clear the episode. Deduplication expires after `dedup_retention_seconds` (1800),
so a long-unseen aircraft may produce a new event on return.

Raw logs also record `source_failure` and `source_stopped` events for replay.
A stopped event cannot be persisted if the raw log itself is no longer writable.
Tests cover rule boundaries, deduplication, recovery, source outages, startup
staleness, airport/ground suppression, missing coordinates, log failures, and
alert delivery through REST/WebSocket snapshots.

## Replay

Replay a controlled scenario without OpenSky credentials or network access:

```sh
.venv/bin/sentinel --replay tests/fixtures/scenario.jsonl
```

This fixture produces four events: a gap, squawk 7700, a second gap after recovery,
and squawk 7600. Its final tick expires the aircraft. The JSON report contains all
emitted alerts (including IDs and timestamps), final track states with full bounded
histories, source status, processed record count, and the final recorded time.

```sh
.venv/bin/sentinel --replay data/raw.jsonl --list-sessions
.venv/bin/sentinel --replay data/raw.jsonl --session SESSION_ID
.venv/bin/sentinel --replay tests/fixtures/opensky-live.jsonl --output replay-result.json
```

The default selects the last session header in an append-only log. Each session
starts with empty state; select an explicit ID to reproduce an older run. Output
files must not already exist, protecting inputs and previous reports. The input
log is never modified. Replay runs as fast as possible, with no sleeps, server,
network calls, credential reads, or writes to production raw/alert logs. `--serve`,
`--once`, and `--config` cannot be combined with `--replay`. The Python CLI provides
CLI replay and a Python frame callback. The separate Cloudflare demo provides
browser playback of precomputed frames.

Live and replay share the observation normalization, track manager, response
validation, and anomaly engine. Replay uses the session's embedded thresholds and
airport reference; it does not load the current config or airport file. File order
is authoritative, even if timestamps move backward. Recorded ticks advance aging,
and source failures control alert suppression; replay never invents retries or
extra end-of-file time. `source_stopped` records preserve fatal ingestion status.

A recording must have complete JSONL records and an M3-or-newer session header
with alert settings and airport reference. Unsupported versions, malformed log
records, unknown event kinds, and truncated lines fail with a line number. Invalid
HTTP bodies are valid raw evidence and are handled just as in live mode. Legacy
sessions remain listable but cannot promise reproducibility and are rejected.
Use a finished recording or stable copy: replay is not a tailing service. As with
any write-before-process log, a crash after a response was flushed but before it
was processed may leave an unapplied final event; replay applies the complete
recorded response. Exact parity assumes the live run processed its recorded events.

`tests/fixtures/scenario.jsonl` exercises duplicates, out-of-order positions, gaps,
emergency episodes, malformed responses, outages, recovery, bounded history, and
expiry. `opensky-live.jsonl` contains two real captured OpenSky responses and their
intervening ticks, with a checked-in expected result: 180 tracks and 333 history
points. Tests also compare freshly recorded live-pipeline output to replay,
including exact alert IDs and timestamps, and verify repeatable offline results.

## Position estimation

Observed markers and trails remain unchanged. Blue outlined markers show a
smoothed estimate at the last observation, or a short-term prediction while the
observation is missing. Dashed blue circles show model-based uncertainty; these
are **not calibrated confidence or accuracy guarantees**. Use “Estimated positions”
to toggle the overlay. Track details show prediction age and the 60-second limit.
No altitude estimation, sensor fusion, or threat classification is performed.

The Python service and Cloudflare snapshot generator implement the same horizontal
constant-velocity Kalman filter in local east/north coordinates, with independent
position/velocity covariance per axis. Observation timestamps drive filtering;
received timestamps never replace measurement time. Raw observations alone drive
alerts. Each estimate is rebuilt from the bounded track history, making it a
windowed filter that survives replay and checkpoint restoration without hidden state.
Reported speed/heading initialize velocity; subsequent updates use positions.

Configuration lives in `[tracks]`: `position_sigma_m`, `velocity_sigma_mps`,
`acceleration_sigma_mps2`, `prediction_seconds`, and `filter_reset_seconds`.
Long gaps and ground/air transitions reset the filter. Ground tracks are not
extrapolated; duplicate/out-of-order observations are ignored. Longitude differences
wrap across the dateline; near-polar tracks disable estimation because this local
projection is unsuitable there. Parameters are initial assumptions, not tuned values.


## Maritime tracking

The local service can ingest AIS Stream position messages alongside OpenSky aircraft.
Set `AISSTREAM_API_KEY` in the server environment, then run the existing `--serve`
command (or the continuous CLI). With no key, AIS is disabled and aircraft ingestion
continues. `--once` remains an aircraft-only poll. Keys are never sent to the browser
or included in recordings. Do not commit keys or pass them in URLs.

The client follows [AIS Stream's server-side subscription protocol](https://aisstream.io/documentation):
compressed WebSocket transport, immediate bounding-box subscription, bounded messages,
heartbeat/idle timeouts, and exponential reconnect backoff with jitter. Subscription
errors stop the AIS client with a generic status; network interruptions retry. Raw
position frames are recorded before normalization as `ais_message` events. Recording
failures stop the ingestion task group rather than silently discarding data. Actual
provider connectivity has **not** been tested: no AIS key was supplied.

Supported reports: PositionReport, StandardClassBPositionReport, and
ExtendedClassBPositionReport. Vessel tracks use MMSI and source-qualified identities.
Speed is converted from knots to m/s; unavailable speed/course/heading and invalid
positions are handled explicitly. Metadata reception time is used when present,
otherwise the recorded receive time is used. AIS seconds-within-minute is not an epoch
and is never treated as one. Ship names and voyage/static data are not yet ingested.

The map provides Air & sea / Aircraft / Vessels filters, vessel silhouettes, MMSI
search, status and movement details, trails, estimates, and loitering alert markers.
Anchored and moored vessels are not extrapolated. Aircraft signal-gap and squawk rules
remain separate; ship signal-gap/port-proximity detection is not implemented.

Loitering requires all of the following, configured under `[alerts]`:
- Movement within `loiter_radius_m` of an episode's initial position (default 500 m).
- At least `loiter_seconds` of continuous qualifying observations (300 s).
- Reported speed at least `loiter_min_speed_mps` (1 m/s) and accumulated observed
  displacement at least `loiter_min_distance_m` (100 m).
- No observation gap longer than `loiter_max_gap_seconds` (90 s).

An episode emits one alert. Leaving the radius, missing updates, or stopping resets
it. Anchored/moored statuses suppress the rule. Per-vessel episode memory is constant
and stale episodes expire. Loitering is a movement pattern, **not evidence of hostile
intent**; harbor maneuvering, positional noise, and legitimate operations can still
trigger it. Thresholds are not operationally validated. The rule currently applies
to vessels only.

The Cloudflare replay default is **Synthetic air & sea / loitering**: one aircraft, a circling
vessel, a transiting vessel, and a moored vessel. Its 29 frames span seven simulated
minutes and produce exactly one loitering alert; the synthetic identifiers and motion
are not real vessel observations. Earlier recorded-aircraft demos remain available.
Cloudflare still serves static replay; no live AIS connection or billing is added.

```sh
.venv/bin/python scripts/generate_maritime_fixture.py
.venv/bin/sentinel --replay tests/fixtures/maritime-synthetic.jsonl
```

Tests cover normalization, units/sentinels, metadata timestamps, both Class B reports,
reconnect behavior, credential exclusion, suppression/reset/deduplication, and full
Python/JavaScript replay parity for the mixed-domain fixture.

