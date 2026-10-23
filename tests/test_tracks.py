import asyncio
import base64
from dataclasses import replace
import json

import httpx
import pytest

from sentinel.config import Config, load_config
from sentinel.opensky import OpenSkyClient, RetryableError, run_poller
from sentinel.pipeline import Pipeline
from sentinel.tracks import Observation, TrackConfig, TrackManager, normalize_opensky


def row(timestamp=100):
    return ["AbC123", "TEST", "US", timestamp, timestamp, -122.4, 37.7,
            1000, False, 100, 90, 0, None, 1100, "7700", False, 0]


def observation(timestamp=100, **changes):
    return replace(Observation("opensky", "abc123", timestamp, timestamp + 1,
                              37.7, -122.4, 1000, 100, 90, "7700", "airborne"), **changes)


def test_normalization_units_id_and_nullable_fields():
    valid = row()
    optional = row()
    for index in (7, 9, 10, 14):
        optional[index] = None
    observations, rejected = normalize_opensky({"time": 101, "states": [valid, optional]}, 102)
    assert rejected == 0
    assert observations[0] == observation(received_at=102)
    assert observations[1].altitude is None
    assert observations[1].squawk is None


@pytest.mark.parametrize("index,value", [(0, "bad"), (3, None), (3, 200), (5, None), (6, 91),
                                        (7, float("nan")), (9, -1), (10, True), (14, "9999"), (8, None)])
def test_invalid_rows_do_not_discard_valid_rows(index, value):
    bad = row()
    bad[index] = value
    observations, rejected = normalize_opensky({"time": 101, "states": [bad, [], None, row()]}, 102)
    assert len(observations) == 1
    assert rejected == 3


def test_history_order_identity_and_bound():
    manager = TrackManager(TrackConfig(history_size=2))
    assert manager.update(observation())
    assert not manager.update(observation(received_at=999))
    assert not manager.update(observation(99))
    assert manager.update(observation(101))
    assert manager.update(observation(102))
    assert manager.update(observation(source="another-source"))
    track = manager.tracks[("opensky", "abc123")]
    assert [o.timestamp for o in track.history] == [101, 102]
    assert (track.first_seen, track.last_seen) == (100, 102)
    assert len(manager.tracks) == 2


def test_aging_boundaries_recovery_expiry_and_stale_repeated_data():
    manager = TrackManager(TrackConfig(gap_after=10, lost_after=20, expire_after=30))
    manager.update(observation())
    track = manager.tracks[("opensky", "abc123")]
    manager.advance(110)
    assert track.state == "active"
    manager.advance(111)
    assert track.state == "gap"
    assert not manager.update(observation(received_at=111))
    assert track.state == "gap"
    manager.advance(121)
    assert track.state == "lost"
    manager.update(observation(122))
    assert track.state == "active"
    manager.advance(153)
    assert not manager.tracks
    assert not manager.update(observation())
    manager.update(observation(153))
    assert manager.tracks[("opensky", "abc123")].first_seen == 153
    manager.advance(100)  # wall-clock correction must not revive stale tracks
    assert manager.now == 153


def test_raw_append_before_validation_and_tick(tmp_path):
    async def scenario():
        path = tmp_path / "raw.jsonl"
        pipeline = Pipeline(path, TrackConfig(), clock=lambda: 102)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"malformed\xff"))) as http:
                client = OpenSkyClient(http, Config(), record_response=pipeline.record_response)
                with pytest.raises(RetryableError):
                    await client.poll()
            pipeline.tick()
        finally:
            pipeline.close()
        with path.open() as stream:
            records = [json.loads(line) for line in stream]
        assert [r["kind"] for r in records] == ["session", "response", "tick"]
        assert base64.b64decode(records[1]["body_base64"]) == b"malformed\xff"
        pipeline = Pipeline(path, TrackConfig(), clock=lambda: 103)
        pipeline.close()
        records2 = [json.loads(line) for line in path.read_text().splitlines()]
        assert records2[:3] == records
        assert records2[-1]["session"] != records[0]["session"]
    asyncio.run(scenario())


def test_pipeline_http_integration(tmp_path, capsys):
    async def scenario():
        pipeline = Pipeline(tmp_path / "raw.jsonl", TrackConfig(), clock=lambda: 101)
        body = {"time": 101, "states": [row(), []]}
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as http:
                client = OpenSkyClient(http, Config(), record_response=pipeline.record_response)
                assert await run_poller(client, once=True, pipeline=pipeline) == 0
                assert await run_poller(client, once=True, pipeline=pipeline) == 0
                assert len(pipeline.manager.tracks[("opensky", "abc123")].history) == 1
        finally:
            pipeline.close()
    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert "observations_added=1 rejected_rows=1" in output
    assert "observations_added=0 rejected_rows=1" in output


def test_ticks_age_tracks_without_new_responses(tmp_path):
    now = [101]
    pipeline = Pipeline(tmp_path / "raw.jsonl", TrackConfig(), clock=lambda: now[0])
    try:
        pipeline.manager.update(observation())
        now[0] = 161
        pipeline.tick()
        assert pipeline.manager.counts()["gap"] == 1
        now[0] = 401
        pipeline.tick()
        assert pipeline.manager.counts()["lost"] == 1
        now[0] = 1901
        pipeline.tick()
        assert not pipeline.manager.tracks
    finally:
        pipeline.close()


def test_recording_failure_prevents_processing(tmp_path):
    async def scenario():
        pipeline = Pipeline(tmp_path / "raw.jsonl", TrackConfig(), clock=lambda: 101)
        pipeline.close()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"time": 101, "states": [row()]}))) as http:
            client = OpenSkyClient(http, Config(), record_response=pipeline.record_response)
            with pytest.raises(ValueError, match="closed file"):
                await run_poller(client, once=True, pipeline=pipeline)
        assert not pipeline.manager.tracks
    asyncio.run(scenario())


@pytest.mark.parametrize("values", [{"gap_after": 0}, {"lost_after": 50}, {"expire_after": 100},
                                  {"history_size": 1.5}, {"tick_interval": 0}])
def test_track_config_validation(values):
    with pytest.raises(ValueError):
        TrackConfig(**values)


def test_load_full_config():
    from pathlib import Path
    config = load_config(Path(__file__).parents[1] / "config.toml")
    assert config.tracks.history_size == 120
    assert config.raw_log_path == "data/raw.jsonl"


def test_background_timer_ages_during_pending_request(tmp_path):
    async def scenario():
        now = [101]
        pipeline = Pipeline(tmp_path / "raw.jsonl", TrackConfig(tick_interval=0.001), clock=lambda: now[0])
        pipeline.manager.update(observation())
        request_started = asyncio.Event()
        finish_request = asyncio.Event()
        async def handler(request):
            request_started.set()
            await finish_request.wait()
            return httpx.Response(200, json={"time": 101, "states": []})
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                client = OpenSkyClient(http, Config(), record_response=pipeline.record_response)
                async with asyncio.TaskGroup() as tasks:
                    aging = tasks.create_task(pipeline.age_tracks())
                    poll = tasks.create_task(client.poll())
                    await request_started.wait()
                    now[0] = 401
                    async def wait_for_lost():
                        while pipeline.manager.counts()["lost"] != 1:
                            await asyncio.sleep(0.001)
                    await asyncio.wait_for(wait_for_lost(), timeout=1)
                    assert not poll.done()
                    finish_request.set()
                    await poll
                    aging.cancel()
        finally:
            pipeline.close()
    asyncio.run(scenario())


def test_error_response_is_logged_without_sensitive_headers(tmp_path):
    pipeline = Pipeline(tmp_path / "raw.jsonl", TrackConfig(), clock=lambda: 101)
    try:
        pipeline.record_response(httpx.Response(429, text="quota exceeded", headers={
            "Retry-After": "120", "Set-Cookie": "secret", "Authorization": "secret"}))
    finally:
        pipeline.close()
    records = [json.loads(line) for line in (tmp_path / "raw.jsonl").read_text().splitlines()]
    assert records[-1]["status"] == 429
    assert records[-1]["headers"]["retry-after"] == "120"
    assert "secret" not in json.dumps(records)
