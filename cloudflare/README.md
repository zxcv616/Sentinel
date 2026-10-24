# Sentinel on Cloudflare Workers

Deployment: https://sentinel-airspace.mwieckin.workers.dev

The Cloudflare deployment keeps the existing map and runs the tracking/alert core
in JavaScript inside one SQLite-backed Durable Object. The Python service and its
replay tooling remain the behavioral reference. No Python server or paid Container
is required.

## Commands

Use Node.js 22+ and npm. From this directory:

```sh
npm ci
npm test
npm run dev
npm run check
npm run deploy
```

`dev` serves the map at localhost:8787 using local Durable Object storage; it can
make real OpenSky requests. `check` runs tests and bundles a deployment dry run.
`deploy` requires an authenticated Wrangler session (`npx wrangler login`). It
publishes the map on the account's workers.dev subdomain. No custom domain needed.
Generated assets are copied from the Python frontend and are not committed.

## Runtime and free-tier budget

- One shared `bay-area-v1` object owns the feed, regardless of viewer count.
- Polling begins when an API/WebSocket client first accesses the service and
  continues through alarms even when there are no viewers.
- Default polling is 240 seconds: about 360 one-credit requests/day, below
  OpenSky's 400-credit anonymous allowance if this IP has no other consumption.
  Anonymous deployments enforce that minimum even if POLL_SECONDS is smaller.
- Set both OpenSky secrets to enable authenticated polling. With credentials,
  POLL_SECONDS can be 30–600 seconds; 60 seconds means about 1,440 polls/day.
- Alarms check track aging and broadcast snapshots at most 60 seconds apart,
  independently of the next network poll. They honor upstream retry delays.
  Scheduling is best effort, not a hard real-time guarantee.
- Track gap timing is poll interval + 30 seconds; lost timing is the greater of
  300 seconds and 3 intervals. Gap alerts wait 2 intervals (minimum 60 seconds);
  source freshness is interval + 30 seconds. These differ intentionally from the
  local 15-second poller's settings. Position history remains capped at 120.
- Hibernation WebSockets handle ping/pong without waking the object. No perpetual
  setInterval keeps the server running. The default is at most 20 viewers.
- Live state is compressed in SQLite and survives hibernation/restarts/deploys.
  Only the latest 200 alerts are retained. Raw recording retains up to 64 batches
  and 16 MiB of compressed data, whichever is reached first. Configuration allows
  1–128 batches. Each batch has an initial state checkpoint and its raw input
  events, so it remains reproducible after earlier batches are removed.
- Writes commit state and recording together before broadcasting. A failed write
  rolls memory and scheduling back and lets Cloudflare retry the alarm. Upstream
  reads may repeat after interruption; observation and alert deduplication applies.
- Fatal source authorization errors halt network polling; the map remains usable.
  After correcting credentials, invoke the protected resume endpoint below.

All selected services exist on Workers Free. No paid plan is activated by this
project. Free allowances are shared across an account, and operations can fail
when limits are exhausted. Traffic, API retries, and storage workloads must be
monitored; this is not a guarantee of unlimited free uptime. Cloudflare's docs:
[Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/),
[storage limits](https://developers.cloudflare.com/durable-objects/platform/limits/).

The state format is versioned. Changing its structure requires an explicit
migration. Changing the bundled airport reference does not silently replace the
reference inside an existing object's state; plan a migration if updating it.
The current deployment covers the same Bay Area bounds as the Python default.

## Credentials and configuration

Edit nonsecret settings in `wrangler.jsonc`. Store secrets interactively:

```sh
npx wrangler secret put OPENSKY_CLIENT_ID
npx wrangler secret put OPENSKY_CLIENT_SECRET
npx wrangler secret put ADMIN_TOKEN
```

Never commit their values. For local development, use an ignored `.dev.vars` file.
No OpenSky credentials are required for the default anonymous mode. Partial
credentials are rejected. Credentials and token responses are never recorded.

`ADMIN_TOKEN` is optional; without it the admin endpoints return 401. The public
map, tracks, and alerts are intentionally readable without authentication.
The admin endpoints require `Authorization: Bearer <ADMIN_TOKEN>`:

- `GET /api/admin/recording?limit=1`: export the latest checkpoint batch. Maximum
  export is 4 batches per request to bound memory. This is a diagnostic export,
  not an archival system or complete lifetime history.
- `POST /api/admin/resume`: resume a halted feed and request a new scheduled poll.

For private viewing, add Cloudflare Access before sharing the URL. Disable/delete
the Worker through Cloudflare when it is no longer needed; closing a browser tab
does not stop its poller.

## Replay and tests

```sh
node scripts/replay.mjs recording.json
```

This reads a protected checkpoint export locally and reports the final state.
Cloudflare exports use `sentinel-cloudflare-checkpoints-v1`; they are not the
Python JSONL format. Each retained batch can replay independently from its starting
checkpoint. The core also accepts the original Python JSONL event records.

Tests compare the port against the full frozen Python live-capture result (every
observation and track), exercise the known alert sequence, restore state between
every input event, check independent checkpoint replay, verify quota settings and
retry headers, and round-trip multi-megabyte state through compression. Browser
heartbeats work with both Python and Cloudflare services. Python tests still run
from the repository root with `.venv/bin/python -m pytest -q`.

## Source connectivity troubleshooting

The frontend and WebSocket service can remain reachable while OpenSky is
unavailable. HTTP 522 was observed when testing the production Worker on October
23, 2026, while the local service could fetch aircraft. This is an upstream
connectivity failure, not evidence that credentials alone will resolve it.
`/api/health` exposes the current retry status and `last_success`; do not treat
an empty map during an outage as zero aircraft. Retries continue with backoff.
The local Python deployment remains available during investigation.

## Recorded demo deployment

The public deployment currently uses `DEMO_MODE: "true"` in `wrangler.jsonc`.
The asset build generates browser playback from the existing replay fixtures:
recorded OpenSky aircraft and a separate, explicitly synthetic alert walkthrough.
Original timestamps are preserved. Each recorded event displays for two seconds;
this is accelerated event playback, not real-time movement or interpolation.
Pause, restart, and recording selection run entirely in the visitor's browser.

The page is prominently labeled **Recorded demo — not live**. No WebSocket or
live API is used. API health reports demo mode; other live API/WebSocket routes
return 503. Existing Durable Object alarms stop on their next invocation (within
one minute under normal scheduling), without contacting OpenSky. Static map tiles
still come from OpenStreetMap. No paid service or billing configuration is added.

To restore live mode after resolving feed access, set `DEMO_MODE` to `"false"`
and run `npm run deploy`. This rebuilds the live page and re-enables the existing
backend; the first live API request schedules collection. Historical state is
retained. Generated demo assets are not committed.

### M5 replay estimates

The default demo is now the longer “Recorded tracking + Kalman estimates” recording.
Estimation runs at asset-build time using the same equations as the local Python
service. Browser playback adds no live collection or paid resources. The blue
marker/circle overlay is separate from observed aircraft; prediction stops after
60 simulated seconds. See the root README for the evaluation and limitations.

### M6 mixed-domain demo

The default recording is now the explicitly synthetic air/sea loitering scenario.
It includes three vessel tracks and one aircraft, domain filters, ship symbols, and
one deterministic loitering alert. The Node replay core supports `ais_message` records
and matches Python replay output. Live AIS remains an optional Python server feature
requiring `AISSTREAM_API_KEY`; this deployment does not open an AIS WebSocket.
