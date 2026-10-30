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
| Mean JSON message | 526,423 B | 85,808 B |
| Total JSON payload | 31,585,389 B | 1,029,693 B |
| Modeled deflate payload | 4,705,005 B | 114,059 B |

JSON transfer is **96.74% lower**. The mean includes the larger initial snapshot;
subsequent estimate-only updates are smaller. Quiet periods send no data deltas,
only heartbeat responses. Deflate figures use a persistent raw zlib context and
exclude framing, TLS, HTTP headers, and TCP overhead; they are not Render billing
measurements. Client negotiation and compression parameters affect actual totals.
A separate local network check verifies the actual Uvicorn upgrade negotiates
permessage-deflate and that both snapshot and delta frames are compressed. In that check, a 514,040-byte snapshot
occupied 74,619 frame bytes and a 48,331-byte delta occupied 4,233 frame bytes
(including WebSocket framing, excluding TLS/TCP).

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
Pushing code cannot restore a suspended workspace. After restoration, verify
that the page loads, `/api/health` reports live data, WebSocket frames begin with a
snapshot and continue with deltas, and hiding the tab closes the connection.
No payment method or paid plan is required by this change.
