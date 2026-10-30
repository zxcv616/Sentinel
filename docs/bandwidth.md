# Bandwidth audit and controls

[← Project overview](../README.md) · [Deployment](deployment.md)

## Diagnosis (October 29, 2026)

The reported Render workspace usage was 10.96 GB, including two hours with
approximately 1.5–2 GB transferred. The previous handler sent an entire snapshot
roughly every second **per connected client**: every track, retained trail,
Kalman estimate, source status, counts, and recent alerts. Client messages could
also wake that loop early. Tabs kept receiving while hidden, had no inactivity
limit, and sent heartbeats indefinitely.

A recorded 248-aircraft workload, averaging 25.9 trail points per aircraft,
produces about **526 KB per full snapshot**, **31.59 MB/minute**, or **1.90 GB/hour**
uncompressed. That is consistent with one unattended, uncompressed connection
causing the reported hourly spikes. It does **not prove** which clients caused
the historical usage. With modeled deflate compression the same stream is about
282 MB/hour; multiple clients or longer retained trails could still explain the
spikes. Render access logs and historical compression negotiation were unavailable.
The first diagnostic run measured 31.56 MB/min; the reproducible benchmark below
also advances pipeline clock events and measures 31.59 MB/min (less than 0.1% difference).

There was no broadcast loop with zero connections. The shared collector and
track aging continued without viewers. The live browser did not poll REST
endpoints. The original local HTML/CSS/JavaScript totaled about 25 KB; Leaflet
and map tiles came from other hosts, not Render. Static responses had validators
but no long-lived cache policy, and HTTP gzip was absent. Health responses were
small. Those observations make the repeated full snapshots the leading explanation,
although unrelated requests cannot be excluded without request logs.

Uvicorn already supported compression by default with the installed backend;
compression was not necessarily absent from the old deployment. The new Blueprint
makes the option explicit. The manually created Render service must retain a
compression-capable backend; the local check verified its default backend too.

## Changes

- One full snapshot per connection, followed by sequenced deltas every five seconds
  when something changes. New tracks include full state; existing tracks send only
  changed fields; expired tracks send removal keys. Estimate-only updates no longer
  resend trails. Alerts and source-health changes travel in the same stream.
- Presentation coordinates rounded to five decimals, speed/heading to one,
  altitude/uncertainty to whole meters, and remaining floats to three decimals.
  Raw observations, logs, rule evaluation, and deterministic replay are unchanged.
- Hidden tabs close their socket immediately. Visible tabs reconnect with fresh
  state unless already idle. After ten minutes without interaction, a clickable
  pause notice replaces automatic reconnection. The server also enforces inactivity;
  client activity messages extend the deadline, heartbeat pings do not.
- Supporting WebSocket clients negotiate permessage-deflate; HTTP supports gzip.
  Versioned static URLs cache for a year; HTML and unversioned assets revalidate.
- No snapshot generation or delta encoding without a connection. Ingestion and
  aging continue while the process is awake so alert episodes, deduplication, and
  recording remain continuous across visits. Render can still sleep the instance.

## Reproduce the measurements

```sh
python scripts/measure_bandwidth.py
```

The script loads all 40 responses from `opensky-tracking.jsonl`, then advances
60 seconds after the last response. It compares old one-second full snapshots
with five-second deltas over **the same state sequence**, including connection
startup and active prediction updates. No upstream calls are made. This is a
fixture benchmark, not a production load test or a claim about current ADS-B traffic.

| Per viewer, first minute | Before | After |
| --- | ---: | ---: |
| Data messages | 60 | 12 |
| Mean JSON message | 526,423 B | 85,572 B |
| Total JSON payload | 31,585,389 B | 1,026,864 B |
| Modeled deflate payload | 4,705,005 B | 113,865 B |

JSON transfer is **96.75% lower**. The mean includes the larger initial snapshot;
subsequent estimate-only updates are smaller. Quiet periods send no data deltas,
only heartbeat responses. Deflate figures use a persistent raw zlib context and
exclude framing, TLS, HTTP headers, and TCP overhead; they are not Render billing
measurements. Client negotiation and compression parameters affect actual totals.
A separate local network check verifies the actual Uvicorn upgrade negotiates
permessage-deflate and that both snapshot and delta frames are compressed. In that check, a 514,040-byte snapshot
occupied 74,619 frame bytes and a 48,331-byte delta occupied 4,233 frame bytes
(including WebSocket framing, excluding TLS/TCP).

The public Render connection did **not** negotiate permessage-deflate during the
post-deploy check. Local support and explicit Blueprint flags do not establish
end-to-end proxy support; the remote cause is not confirmed. Do not count on
compression savings on Render. The budget below uses uncompressed bytes.

## Server-side limits

These safeguards apply even if a client ignores the frontend JavaScript:

- No `activity` signal for 600 seconds closes the feed (4001). Heartbeats do not
  renew activity. The browser sends signals only for trusted input events, but a
  custom client can forge them; these are not proof of a human.
- A hard 1,800-second session deadline closes even continuously active clients
  (4002). The UI requires a resume click. Reconnecting starts a new session but
  does not reset the daily counter.
- At most 20 active feed connections per server process; excess attempts close
  with 4004 and a retry notice. The handshake is accepted briefly so browsers can
  receive the close reason, but no snapshot or feed slot is allocated.
- Client messages do not move the five-second data deadline. Application pong
  replies are limited to one per ten seconds to prevent ping amplification.
- `SENTINEL_WS_DAILY_BYTES` defaults to **150000000** (decimal 150 MB). Each outgoing
  UTF-8 application payload, including app pongs, is reserved against one shared
  UTC-day counter before writing. A message that would exceed the remaining
  budget is not sent. Reservations for failed sends are conservatively retained.
- Exhaustion logs a warning, closes every existing feed with 4003, and refuses
  new feeds for the day. The map displays **“Live feed paused for today to stay
  within hosting limits.”** and does not automatically reconnect. At the next UTC
  day, the server allows new connections; the visitor can click to resume.

The counter is atomically saved to `ws-budget.json` beside the raw log, or to
`SENTINEL_WS_BUDGET_PATH` if set. It survives process restarts **only when that file
survives**. Render Free storage is ephemeral; redeploys or instance replacement
can reset it. Corrupt accounting or a write failure pauses feeds rather than
sending unaccounted data. Run one worker: this local counter and connection cap
are not coordinated across multiple processes or instances. A durable cross-deploy
quota would require persistent/shared storage.

This counter measures uncompressed application bytes, not hosting invoices:
HTTP traffic (including REST downloads and rejected upgrades), WebSocket control
frames, and TLS/TCP overhead are outside it. It is not an account-wide or DDoS cap.
150 MB × 31 days is 4.65 GB of application payload if the counter survives the month;
leave headroom for the other traffic and other workspace services.

Trails and alert records are now incremental. Trail updates send prefix removals
and every newly received point, not repeated retained history. Alerts send only
new/changed records, removed IDs, and display-order IDs when necessary. The
post-capture prediction benchmark above improves only slightly from the previous
1,029,693 B/min because it contains no subsequent position observations; the
additional trail savings occur on actual observation updates. Tests reconstruct
both retained and completely replaced trails and alert lists in the browser reducer.

## Portfolio budget

For **100 visits/month × five minutes**, plus **one tab left open for 24 hours**:

- The abandoned visible tab disconnects after ten minutes; a hidden one disconnects
  immediately. Count 510 connected minutes in total.
- Conservatively charge the entire busy first-minute benchmark for **every** minute,
  including a redundant initial snapshot each time, although real connections send
  it only once. This also leaves room for the hosted five-minute source refreshes.
- Add 1 KiB/minute for heartbeats and a fresh uncompressed static load plus 4 KiB
  overhead for all 101 visits. Ignore compression and caching savings.

This model totals approximately **0.53 GB/month**; doubling it for additional
headroom gives **1.06 GB**, comfortably below the reported 5 GB allowance.
It assumes the measured 248-track/trail workload and normal browser behavior.
More aircraft, longer histories, repeated API downloads, automated traffic, other
workspace services, or deliberately bypassed client controls change the budget.
This is not a hard account-wide spending or traffic cap.

## Deployment verification

At diagnosis the public Render URL returned HTTP 503 with
`x-render-routing: suspend-by-user`, consistent with the reported suspension.
The service subsequently resumed responding, and the pushed change auto-deployed.
The post-deploy check confirmed HTTP 200, the new versioned transport asset,
`/api/health` reporting live adsb.fi data, and a 127-track initial snapshot
(sequence 0, 85,149 JSON bytes) followed 4.9 seconds later by a delta
(sequence 1, 28,759 bytes, 85 changed tracks). These startup values have shorter
histories than the benchmark and are not directly comparable to its steady workload.
Hidden/idle/resume behavior is covered by browser-session tests; local browser
verification also rendered the map without console errors.
No payment method, paid plan, or Render account setting was changed.
