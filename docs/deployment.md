# Deployment guide

[← Project overview](../README.md) · [Cloudflare implementation](../cloudflare/README.md)

## Public deployments

| Site | Mode | Source |
| --- | --- | --- |
| [Live Sentinel](https://sentinel-live-2nt0.onrender.com/) | Python/FastAPI on Render Free | adsb.fi aircraft; one shared poll every 300 seconds |
| [Recorded scenarios](https://sentinel-airspace.mwieckin.workers.dev/) | Static playback on Cloudflare Workers | Recorded OpenSky aircraft and labeled synthetic scenarios |

The live service runs independently of a local computer. Render may sleep after
inactivity and take about a minute to wake. Browser WebSocket heartbeats represent
visible, non-idle visitors; hidden tabs disconnect and idle tabs pause after ten
minutes. No external keepalive job is configured. Tracks and logs reset
on restarts/redeploys/sleep. The public vessel feed is not enabled.

## Render configuration

[render.yaml](../render.yaml) defines an explicitly Free Python service with no
paid database, disk, scheduled job, or worker. It defaults to **probe mode** so a
new deployment can test provider reachability before enabling collection.

1. Connect the GitHub repository and create a Blueprint from `render.yaml`, or
   configure a Python web service with the same settings.
2. Use build command `pip install .` and start command:

   ```sh
   python -m uvicorn sentinel.hosting:create_hosted_app --factory \
     --host 0.0.0.0 --port "$PORT" --workers 1 --limit-concurrency 50 \
     --ws websockets --ws-per-message-deflate true
   ```

3. Set the health-check path to `/healthz`. This verifies process readiness only.
4. With `SENTINEL_MODE=probe`, inspect `/api/probe`. A failure includes a safe
   failure phase. If OpenSky fails, a single adsb.fi regional check appears under
   `alternative`. Refreshing the result does not repeat provider requests.
5. After a successful adsb.fi probe, set:

   ```text
   SENTINEL_MODE=live
   SENTINEL_AIRCRAFT_SOURCE=adsbfi
   ```

6. Redeploy. Verify `/api/health` reports `live`, the successful receipt time is
   recent, and `/api/tracks` contains valid observations. An empty map alone is
   not evidence of a healthy or unhealthy source.

The existing service was created manually, so its Environment settings control
these values; editing the Blueprint does not update that service's environment.
One worker is deliberate: additional workers would create additional collectors.
The concurrency limit does not cap total bandwidth.

## Why adsb.fi is the hosted source

On October 23, 2026, Render could not establish a connection to OpenSky before
its timeout; Cloudflare had also returned upstream errors. adsb.fi's documented
public regional endpoint succeeded from Render. The Python adapter normalizes
its units, preserves source identity, records native payloads, and supports replay.
The UI attributes adsb.fi. OpenSky remains available as a separate adapter.

No local relay, paid provider, or billing workaround is required for this setup.
Public provider access can change; a successful deployment is not an uptime SLA.
See [adsb.fi's terms](https://github.com/adsbfi/opendata) for permitted use.

## Cadence and free-plan limits

Hosted polling is 300 seconds, track gap/lost thresholds are 330/900 seconds,
and signal-gap/source-staleness thresholds are 600/330 seconds. Estimates still
stop after 60 seconds: five-minute sampling does not provide continuous motion.

No payment information was added for this deployment. Free-plan instance hours,
bandwidth, and build quotas are shared at the workspace level. Without a payment
method, Render documents suspension/build restrictions when applicable free
allowances are exhausted. This is not unlimited hosting. Consult
[Render's current free-plan limits](https://render.com/docs/free).

See [the bandwidth audit](bandwidth.md) for measured payload sizes and a conservative
portfolio traffic budget. Ingestion and aging remain active while the process is
awake to preserve alert continuity; snapshot/delta generation only runs for
connected viewers. These controls reduce accidental usage, not arbitrary traffic
or denial-of-service requests. No billing changes are required.

## Cloudflare replay

The checked-in `DEMO_MODE: "true"` configuration generates playback frames from
fixtures. Playback runs in the browser; it makes no live aircraft or AIS calls.
The separate Durable Object live implementation remains available in the codebase
but is not enabled on the public demo. See the [Cloudflare guide](../cloudflare/README.md)
for build, test, and deployment commands.
