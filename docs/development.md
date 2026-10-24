# Development guide

[← Project overview](../README.md) · [Pipeline reference](reference.md) · [Deployment](deployment.md)

## Setup

Use Python 3.11+ and Node.js 22+ for the full suite. Commands below use a POSIX
shell and run from the repository root.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm --prefix cloudflare ci
```

## Start with offline replay

```sh
sentinel --replay tests/fixtures/scenario.jsonl
sentinel --replay tests/fixtures/maritime-synthetic.jsonl
```

The first scenario produces four aircraft events; the second produces one vessel
loitering alert. These commands need no credentials or network access. They emit
JSON, not a map. For browser playback, use the [recorded demo](https://sentinel-airspace.mwieckin.workers.dev/)
or run `npm --prefix cloudflare run dev` with the checked-in demo configuration.

```sh
sentinel --replay data/raw.jsonl --list-sessions
sentinel --replay data/raw.jsonl --session SESSION_ID
sentinel --replay tests/fixtures/opensky-live.jsonl --output replay-result.json
```

The default replays the last session. Output paths must not already exist.
Recorded settings and airport references are used instead of current config.

## Live aircraft with adsb.fi

```sh
SENTINEL_MODE=live SENTINEL_AIRCRAFT_SOURCE=adsbfi \
  python -m uvicorn sentinel.hosting:create_hosted_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1
```

Open [localhost:8000](http://127.0.0.1:8000). This uses the hosted configuration:
five-minute source polling, Bay Area bounds, and adjusted gap thresholds. It does
not load `config.toml`. adsb.fi's circular regional query covers the configured
bounds and may include aircraft beyond them. Provider terms and access limits apply.

## OpenSky and configurable local polling

```sh
sentinel --config config.toml --serve
sentinel --config config.toml --once
```

The CLI uses OpenSky; the adsb.fi environment selector applies to the hosted
factory command above. `--port 8001` selects another local port. Without
`--serve`, the continuous CLI prints aircraft counts. Stop with Ctrl-C.

Edit [config.toml](../config.toml) for bounds, polling, backoff, track aging,
recording paths, and alert/filter thresholds. Default local polling is 15 seconds;
choose a cadence appropriate to provider quotas. Closing a browser does not stop
the local process or its provider requests.

Optional OpenSky OAuth credentials are `OPENSKY_CLIENT_ID` and
`OPENSKY_CLIENT_SECRET`; set both in the server environment. Basic/password
authentication is not supported. `.env` is ignored by Git but is not loaded
automatically. See the [provider documentation](https://openskynetwork.github.io/opensky-api/rest.html)
for current access policies and quotas.

## Optional AIS

Set `AISSTREAM_API_KEY` in the server environment before starting the live server.
The client subscribes to the configured bounding box. Without a key, aircraft
tracking continues and the UI reports that the vessel feed is not configured.
Live provider connectivity has not been validated; automated coverage uses mocked
connections and synthetic reports. Never commit keys or expose them in browser code.

## Test and evaluate

```sh
python -m pytest -q
npm --prefix cloudflare test
npm --prefix cloudflare run build:assets
python scripts/evaluate_tracking.py tests/fixtures/opensky-tracking.jsonl
```

Tests require no provider credentials and use fixtures/mocked transports. Some
Python tests call Node.js for cross-runtime parity. The evaluation uses recorded
observations and is described in [Validation](validation.md).

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/tracks` | Tracks, bounded trails, estimates, counts, and source status |
| `GET /api/alerts` | Recent events in the current session |
| `GET /api/health` | Ingestion state and last successful source receipt |
| `WS /ws/tracks` | Full replacement snapshots; reconnecting clients need no prior state |
| `GET /docs` | Generated API documentation |
| `GET /healthz` | Hosted factory only: process readiness, not upstream health |

Raw inputs default to `data/raw.jsonl`; alerts to `data/alerts.jsonl`. Logs append
across sessions and have no automatic rotation. Use one process per log file.
The [reference](reference.md) describes record formats and failure semantics.
