# Validation and measured results

[← Project overview](../README.md) · [Raw evaluation report](m5-evaluation.json) · [Fixture provenance](../tests/fixtures/README.md)

## Automated checks

The documented local validation snapshot contains **116 Python tests and 16
JavaScript tests**. Counts may change; GitHub Actions is the current pass/fail
record. Tests use mocked transports, synthetic scenarios, and recorded inputs,
not live provider availability.

| Area | Evidence |
| --- | --- |
| Ingestion | Timeout/retry behavior, rate-limit delays, OAuth refresh, malformed data, gzip decoding, and recording before processing |
| Tracking | Timestamp ordering, duplicate rejection, bounded history, and stale-track transitions |
| Alerts | Emergency episodes, source outage suppression, airport/ground filtering, recovery, and vessel loitering |
| Replay | Exact histories and alert IDs/timestamps, embedded settings, and deterministic clock events |
| API | Shared collector across clients, REST snapshots, WebSocket reconnection, and stopped-source visibility |
| Cross-runtime behavior | Python/JavaScript parity for supported OpenSky/AIS fixtures and estimation; Cloudflare checkpoint restore |

```sh
python -m pytest -q
npm --prefix cloudflare test
```

## Recorded-data evaluation

The tracking fixture contains **248 aircraft**, **6,423 unique observations**, and
**40 responses spanning 585 seconds**. These are dataset measurements, not service
capacity, sustained throughput, or accuracy guarantees.

Evaluation uses rolling-origin holdouts with at least four earlier positions.
Actual horizons fall within five seconds of each bucket, capped at 60 seconds.
Future positions are withheld from the estimator. Cases overlap and are not
independent; held-out ADS-B positions are reference observations, not surveyed
physical ground truth.

| Horizon | Cases | Kalman RMSE | Reported-speed/heading baseline RMSE |
| --- | ---: | ---: | ---: |
| 15 s | 3,735 | 248.91 m | **149.46 m** |
| 30 s | 3,205 | 657.83 m | **493.55 m** |
| 60 s | 1,569 | 1,642.12 m | **1,411.88 m** |

**The baseline performs better on this recording.** The untuned position-update
model lags maneuvers; the implementation demonstrates estimation and reproducible
evaluation, not improved real-world accuracy. Next work should separate tuning
and evaluation recordings, incorporate velocity measurements, and calibrate
uncertainty before making stronger claims.

```sh
python scripts/evaluate_tracking.py tests/fixtures/opensky-tracking.jsonl
```

The recorded mean estimate reconstruction time (0.0224 ms) is specific to the
development machine and workload. It is not a hosted performance benchmark.

## Repeatable demo walkthrough

1. Open the [recorded demo](https://sentinel-airspace.mwieckin.workers.dev/).
2. Select **Synthetic air & sea / loitering**. Three vessels and one aircraft
   represent seven simulated minutes; these are not real-world vessel observations.
3. A moving vessel remains within the configured radius long enough to produce
   one loitering alert. The transit and moored examples do not trigger it.
4. Pause or restart to inspect the same sequence again. Switch to **Synthetic
   alert walkthrough** for aircraft gaps and emergency squawks, or to the
   recorded tracking scenario for Kalman estimates and prediction expiry.

## Current limits

Live AIS connectivity has not been validated. Detection thresholds and uncertainty
are not operationally calibrated. Hosted history is ephemeral; deployment counts
are observations from a particular moment, not load tests. A signal gap or
loitering event does not establish malicious intent.
