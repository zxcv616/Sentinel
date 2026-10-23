# Replay fixtures

- `scenario.jsonl`: synthetic OpenSky-shaped responses recorded through `Pipeline`
  with an injected clock. The stable session ID is `fixture-scenario`. A synthetic
  airport and intentionally unavailable airport-file path exercise embedded
  reference replay. Expected events are asserted independently in `test_replay.py`.
- `scenario.expected.json`: frozen Python output for exact cross-runtime alert
  comparison, including IDs, timestamps, and distance details.
- `opensky-live.jsonl`: a prefix of the local live M3 recording captured October 23,
  2026, containing its session header, two real Bay Area OpenSky responses, and
  intervening clock ticks. The original response bodies are preserved in base64;
  outer JSON formatting was normalized. No credentials or token responses occur.
- `opensky-live.expected.json`: frozen full replay output for regression checking,
  including 180 tracks, 333 position-history points, and no alert events. Counts
  are also independently asserted in tests. Do not regenerate this file merely
  to make a regression pass; review behavioral changes first.

The embedded airport reference is from OurAirports public-domain data, as described
in the project README. Fixtures are test inputs, not current aircraft information.

`opensky-tracking.jsonl`: the first 40 successful responses from local session
5461f15f-dd07-4721-9e46-1d9e7a3ebb69 on October 23, 2026, spanning 585 seconds.
Bodies are retained unchanged; only successful response records and the session
header are included. Original one-second tick events are omitted. The copied header
adds the explicit M5 estimation defaults. This fixture supports rolling-origin
holdouts and the hosted M5 demo, which advances a simulated clock between responses.
It contains 248 aircraft and 6,423 distinct object/timestamp observations. It was not
selected by prediction-error outcome. No credentials are included.
