from dataclasses import replace
import json

import httpx
import pytest

from sentinel.alerts import AlertConfig, AnomalyEngine, distance_km
from sentinel.pipeline import Pipeline
from sentinel.tracks import Observation, TrackConfig, TrackManager

REFERENCE = {"coverage": {"lamin":36,"lamax":40,"lomin":-124,"lomax":-120},
             "airports": [{"id":"TEST", "lat":37, "lon":-122}]}


def setup():
    events = []
    engine = AnomalyEngine(AlertConfig(), REFERENCE, events.append)
    manager = TrackManager()
    observation = Observation("opensky","abc123",100,100,38,-123,1000,100,90,"1200","airborne")
    manager.update(observation)
    engine.observe(observation,100)
    engine.healthy(100)
    return engine, manager, observation, events


def test_gap_boundary_dedup_and_rearm():
    e,m,o,events=setup()
    e.healthy(130); e.healthy(160)
    e.advance(160,m.tracks)
    assert events == []
    e.advance(161,m.tracks)
    assert len(events) == 1 and events[0]["type"] == "signal_gap"
    e.advance(162,m.tracks)
    assert len(events) == 1
    fresh=replace(o,timestamp=170)
    m.update(fresh); e.observe(fresh,170)
    e.healthy(190); e.healthy(220); e.advance(231,m.tracks)
    assert len(events) == 2


@pytest.mark.parametrize("changes", [{"lat":37,"lon":-122}, {"status":"ground"}, {"lat":42}])
def test_airports_ground_and_unknown_coverage_suppress_gap(changes):
    e,m,o,events=setup()
    fresh=replace(o, timestamp=101, **changes)
    m.update(fresh); e.observe(fresh,101)
    e.healthy(130); e.healthy(160); e.advance(162,m.tracks)
    assert not events


def test_outage_and_hung_source_suppress_until_recovery_grace():
    e,m,o,events=setup()
    e.failed(); e.advance(170,m.tracks)
    assert not events
    e.healthy(180); e.advance(181,m.tracks)
    assert not events
    e.healthy(210); e.healthy(240); e.advance(241,m.tracks)
    assert len(events) == 1
    e,m,o,events=setup()
    e.advance(170,m.tracks)  # No exception, but no recent successful poll either.
    assert not events


def test_startup_stale_position_does_not_create_gap_alert():
    e,m,o,events=setup()
    e.eligible.clear()
    e.observe(o,170)
    e.healthy(130); e.healthy(160); e.advance(170,m.tracks)
    assert not events


def data(code, contact=100, position=100, coords=True):
    return {"time":contact,"states":[["abc123","","",position,contact,
        -123 if coords else None,38 if coords else None,1000,False,100,90,0,None,1000,code,False,0]]}


@pytest.mark.parametrize("code", ["7500","7600","7700"])
def test_emergency_codes_dedup_and_rearm(code):
    e,m,o,events=setup()
    e.squawks(data(code),100,m.tracks)
    e.squawks(data(code,110),110,m.tracks)
    e.squawks(data(code,99),111,m.tracks)
    assert len(events) == 1
    assert events[0]["details"]["squawk"] == code
    e.squawks(data(None,115),115,m.tracks)  # Missing squawk does not clear an episode.
    e.squawks(data(code,116),116,m.tracks)
    assert len(events) == 1
    e.squawks(data("1200",120),120,m.tracks)
    e.squawks(data(code,130),130,m.tracks)
    assert len(events) == 2


def test_squawk_change_without_new_position_and_without_any_position():
    e,m,o,events=setup()
    e.squawks(data("1200"),100,m.tracks)
    e.squawks(data("7700",110,position=100),110,m.tracks)
    e.squawks(data("7600",120,coords=False),120,{})
    assert [a["details"]["squawk"] for a in events] == ["7700","7600"]
    assert events[-1]["lat"] is None
    e.squawks(data("7500",121),200,m.tracks)
    assert len(events) == 2  # Stale emergency input is not a current event.


def test_pipeline_alert_log_and_retention(tmp_path):
    p=Pipeline(tmp_path/'raw.jsonl',TrackConfig(),clock=lambda:100,alert_config=AlertConfig(recent_limit=1))
    try:
        for code in ("7700","7600"):
            timestamp=100 if code == "7700" else 110
            p.clock=lambda:timestamp
            response=httpx.Response(200,json=data(code,timestamp))
            p.record_response(response);p.accept(response.json())
        assert len(p.alerts) == 1
        assert p.alerts[0]["details"]["squawk"] == "7600"
        lines=[json.loads(line) for line in (tmp_path/'alerts.jsonl').read_text().splitlines()]
        assert len(lines)==2 and lines[0]["id"] != lines[1]["id"]
        p.failed("timeout")
        assert json.loads((tmp_path/'raw.jsonl').read_text().splitlines()[-1])["kind"] == "source_failure"
    finally:
        p.close()


def test_alert_recording_failure_is_not_swallowed(tmp_path):
    p=Pipeline(tmp_path/'raw.jsonl',TrackConfig(),clock=lambda:100)
    try:
        p.alert_stream.close()
        p.record_response(httpx.Response(200,json=data("7700")))
        with pytest.raises(ValueError,match="closed file"):
            p.accept(data("7700"))
        assert not p.alerts
    finally:
        p.close()


def test_distance_and_config():
    assert distance_km(37,-122,37,-122) == 0
    assert 110 < distance_km(37,-122,38,-122) < 112
    with pytest.raises(ValueError):
        AlertConfig(gap_seconds=0)
