# Architecture and design decisions

[← Project overview](../README.md) · [Pipeline reference](reference.md)

## Runtime boundaries

The Python service owns one ingestion pipeline per process. FastAPI's lifespan
starts the aircraft collector, the track-aging task, and optionally the AIS
stream client. REST and WebSocket clients read that shared state; they never
start collectors. Run one server worker per recording file.

Aircraft adapters normalize OpenSky and adsb.fi responses into a shared aircraft
row format, then immutable observations. Source identity is retained: `opensky`,
`adsbfi`, or `aisstream`. Aircraft use ICAO24; vessels use MMSI. An observation
contains source time and receipt time, coordinates, altitude, speed, heading,
and squawk or navigation status.

## Decisions and tradeoffs

| Decision | Reason and consequence |
| --- | --- |
| Write raw inputs before processing | Allows failures and malformed responses to be inspected and replayed. Recording failure stops ingestion rather than silently losing the evidence. |
| Record clock events and session settings | Replay reproduces aging and alert behavior without relying on current time, configuration, or airport files. |
| Separate source health from track freshness | An upstream outage should not immediately become a fleet of signal-gap alerts. |
| Bound per-track history | Limits each track's memory and makes estimation reproducible from retained observations. This is not a total system capacity limit. |
| Reject duplicate/out-of-order positions | Repeated old reports cannot keep a track alive or rewrite its history. Squawk evaluation separately uses contact timestamps. |
| Keep estimates separate from measurements | The map labels model output; alerts continue to use observed data. |
| Full WebSocket snapshots | Reconnection and track removal require no client-side event reconstruction. Simplicity trades off against bandwidth at larger track/viewer counts. |
| In-memory tracks and JSONL files | Small deployment footprint and straightforward replay. No durable hosted history, database queries, or multi-process coordination yet. |

## Detection rules

- **Signal gap:** requires a previously fresh aircraft track and a healthy feed.
  Ground positions, airport proximity, and positions outside reference coverage
  suppress the alert. Fresh data rearms a silent episode.
- **Emergency squawk:** observes 7500, 7600, or 7700. Contact timestamps control
  freshness and deduplication, even when there is no new position.
- **Vessel loitering:** combines time within a radius, minimum speed and distance,
  and maximum observation gaps. Anchored/moored vessels are suppressed.

These rules identify observable conditions, not intent or confirmed incidents.
Thresholds live in configuration; algorithm details are in the [reference](reference.md).

## Estimation

The horizontal constant-velocity Kalman filter uses local east/north coordinates
with independent position/velocity covariance per axis. Reported speed and
heading initialize velocity; later updates use positions. Long gaps and motion
state transitions reset the filter. Ground, anchored, and moored tracks are not
extrapolated. Predictions stop after 60 seconds.

Each estimate is reconstructed from bounded history, supporting replay without
hidden filter state. The uncertainty overlay is model-based and uncalibrated.
See the [baseline comparison](validation.md) for measured limitations.

## Deployments

**Render:** Python/FastAPI live service using adsb.fi. Source polling is every
300 seconds; source and track-gap thresholds are adjusted to that cadence.
State and logs are ephemeral. AIS is optional and currently disabled publicly.

**Cloudflare:** static replay frames generated from recorded fixtures by the
JavaScript core. A separate Durable Object implementation exists in the codebase,
but is not the active public live backend. Python/JavaScript parity tests apply
to supported OpenSky and AIS recordings; adsb.fi recording/replay is Python-only.

## Not implemented

PostgreSQL persistence, durable hosted recording retention, multi-source track
correlation, sensor fusion, altitude estimation, speed/altitude-jump rules,
ship signal-gap/port filtering, and threat classification are not implemented.
