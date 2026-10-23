import time

from fastapi.testclient import TestClient
import httpx

from sentinel.config import Config
from sentinel.web import create_app


def transport(status=200):
    def handler(request):
        now = int(time.time())
        row = ["abc123", "TEST", "US", now, now, -122.4, 37.7, 1000,
               False, 100, 90, 0, None, 1100, "7700", False, 0]
        return httpx.Response(status, json={"time": now, "states": [row]})
    return httpx.MockTransport(handler)


def test_map_api_and_websocket_share_pipeline(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    app = create_app(Config(raw_log_path=str(tmp_path / "raw.jsonl")), transport=transport())
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "SENTINEL" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        with client.websocket_connect("/ws/tracks") as ws:
            data = ws.receive_json()
            for _ in range(3):
                if data["tracks"]:
                    break
                data = ws.receive_json()
            assert data["type"] == "snapshot"
            assert data["counts"]["active"] == 1
            track = data["tracks"][0]
            assert track["object_id"] == "abc123"
            assert track["position"]["heading"] == 90
            assert data["alerts"][0]["type"] == "emergency_squawk"
            assert data["alerts"][0]["details"]["squawk"] == "7700"
            assert client.get("/api/alerts").json()["alerts"] == data["alerts"]
            assert track["history"][0]["lat"] == 37.7
            assert client.get("/api/tracks").json()["tracks"] == data["tracks"]
            assert client.get("/api/health").json()["state"] == "live"
        # Reconnection receives a complete snapshot, without requiring prior messages.
        with client.websocket_connect("/ws/tracks") as ws:
            assert ws.receive_json()["tracks"][0]["object_id"] == "abc123"
    assert app.state.pipeline.stream.closed


def test_retrying_source_is_visible(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    app = create_app(Config(raw_log_path=str(tmp_path / "raw.jsonl")), transport=transport(503))
    with TestClient(app) as client, client.websocket_connect("/ws/tracks") as ws:
        data = ws.receive_json()
        for _ in range(3):
            if data["source"]["state"] == "retrying":
                break
            data = ws.receive_json()
        assert data["source"]["state"] == "retrying"
        assert "503" in data["source"]["message"]
        assert data["tracks"] == []


def test_fatal_ingestion_keeps_map_available(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    app = create_app(Config(raw_log_path=str(tmp_path / "raw.jsonl")), transport=transport(403))
    with TestClient(app) as client, client.websocket_connect("/ws/tracks") as ws:
        data = ws.receive_json()
        for _ in range(3):
            if data["source"]["state"] == "stopped":
                break
            data = ws.receive_json()
        assert data["source"]["state"] == "stopped"
        assert client.get("/").status_code == 200


def test_snapshot_preserves_trails_and_removes_expired_tracks(tmp_path):
    from dataclasses import replace
    from sentinel.pipeline import Pipeline
    from sentinel.tracks import Observation
    from sentinel.web import snapshot

    config = Config(raw_log_path=str(tmp_path / "raw.jsonl"))
    pipeline = Pipeline(config.raw_log_path, config.tracks, clock=lambda: 100)
    try:
        first = Observation("opensky", "abc123", 100, 101, 37.7, -122.4,
                            1000, 100, 90, None, "airborne")
        pipeline.manager.update(first)
        pipeline.manager.update(replace(first, timestamp=115, lat=37.8))
        data = snapshot(pipeline, config)
        assert [p["lat"] for p in data["tracks"][0]["history"]] == [37.7, 37.8]
        assert data["tracks"][0]["position"]["lat"] == 37.8
        pipeline.manager.advance(2000)
        assert snapshot(pipeline, config)["tracks"] == []
    finally:
        pipeline.close()
