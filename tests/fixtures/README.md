# Replay fixtures

- `scenario.jsonl`: synthetic OpenSky-shaped responses recorded through `Pipeline`
  with an injected clock. The stable session ID is `fixture-scenario`. A synthetic
  airport and intentionally unavailable airport-file path exercise embedded
  reference replay. Expected events are asserted independently in `test_replay.py`.
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
