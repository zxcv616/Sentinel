"""Local map service. One lifespan owns one ingestion pipeline."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
import os
from pathlib import Path
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

from .config import Config
from .opensky import OpenSkyClient, run_poller
from .pipeline import Pipeline

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


def snapshot(pipeline, config):
    tracks = []
    for key, track in sorted(pipeline.manager.tracks.items()):
        tracks.append({"source": key[0], "object_id": key[1], "state": track.state,
                       "first_seen": track.first_seen, "last_seen": track.last_seen,
                       "position": asdict(track.history[-1]),
                       "history": [{"lat": o.lat, "lon": o.lon, "timestamp": o.timestamp}
                                   for o in track.history]})
    return {"type": "snapshot", "timestamp": time.time(), "bounds": config.bounds,
            "source": dict(pipeline.source_status), "counts": pipeline.manager.counts(), "tracks": tracks,
            "alerts": list(reversed(pipeline.alerts))}


def create_app(config: Config | None = None, *, transport=None):
    config = config or Config()

    @asynccontextmanager
    async def lifespan(app):
        pipeline = Pipeline(config.raw_log_path, config.tracks, alert_config=config.alerts, alert_log_path=config.alert_log_path)
        app.state.pipeline = pipeline

        async def ingest():
            try:
                async with httpx.AsyncClient(timeout=config.timeout, transport=transport,
                        headers={"User-Agent": "Sentinel/0.1"}) as http:
                    client = OpenSkyClient(http, config, os.getenv("OPENSKY_CLIENT_ID"),
                        os.getenv("OPENSKY_CLIENT_SECRET"), record_response=pipeline.record_response)
                    async with asyncio.TaskGroup() as tasks:
                        tasks.create_task(pipeline.age_tracks())
                        tasks.create_task(run_poller(client, pipeline=pipeline))
            except Exception:
                # Keep the map available, but never present stopped ingestion as live.
                try:
                    pipeline.stopped()
                except OSError:
                    pipeline.source_status.update(state="stopped", message="Ingestion and recording stopped; check server logs")
                log.exception("Sentinel ingestion stopped")

        worker = asyncio.create_task(ingest())
        try:
            yield
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            pipeline.close()

    app = FastAPI(title="Sentinel", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/tracks")
    async def tracks():
        return snapshot(app.state.pipeline, config)

    @app.get("/api/alerts")
    async def alerts():
        return {"alerts": list(reversed(app.state.pipeline.alerts))}

    @app.get("/api/health")
    async def health():
        return dict(app.state.pipeline.source_status)

    @app.websocket("/ws/tracks")
    async def stream(socket: WebSocket):
        await socket.accept()
        try:
            # Full replacement snapshots make reconnects and track expiry unambiguous.
            # No queued history accumulates for a slow browser.
            while True:
                await asyncio.wait_for(socket.send_json(snapshot(app.state.pipeline, config)), timeout=5)
                try:
                    message = await asyncio.wait_for(socket.receive(), timeout=1)
                    if message["type"] == "websocket.disconnect":
                        return
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, TimeoutError, OSError):
            pass

    return app
