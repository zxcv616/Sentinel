"""Local map service. One lifespan owns one ingestion pipeline."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
import os
from pathlib import Path
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
import hashlib
from .streaming import DeltaEncoder, presentation
import httpx

from .adsbfi import AdsbFiClient
from .ais import run_ais
from .config import Config
from .estimation import estimate
from .opensky import OpenSkyClient, run_poller
from .pipeline import Pipeline

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)
ASSET_VERSION = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(STATIC.glob("*")) if p.is_file())).hexdigest()[:12]


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        version = scope.get("query_string", b"").decode()
        response.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
            if version == f"v={ASSET_VERSION}" else "public, max-age=0, must-revalidate")
        return response


def snapshot(pipeline, config):
    tracks = []
    for key, track in sorted(pipeline.manager.tracks.items()):
        tracks.append({"source": key[0], "object_id": key[1], "state": track.state,
                       "first_seen": track.first_seen, "last_seen": track.last_seen,
                       "position": asdict(track.history[-1]),
                       "estimate": estimate(track.history, pipeline.manager.now, config.tracks),
                       "history": [{"lat": o.lat, "lon": o.lon, "timestamp": o.timestamp}
                                   for o in track.history]})
    return {"type": "snapshot", "timestamp": time.time(), "bounds": config.bounds,
            "aircraft_provider": pipeline.aircraft_source, "poll_interval": config.poll_interval,
            "source": dict(pipeline.source_status), "maritime_source": dict(pipeline.ais_status), "counts": pipeline.manager.counts(), "tracks": tracks,
            "alerts": list(reversed(pipeline.alerts))}


def create_app(config: Config | None = None, *, transport=None, aircraft_source="opensky", push_interval=5, idle_timeout=600):
    config = config or Config()

    @asynccontextmanager
    async def lifespan(app):
        pipeline = Pipeline(config.raw_log_path, config.tracks, alert_config=config.alerts, alert_log_path=config.alert_log_path, aircraft_source=aircraft_source)
        app.state.pipeline = pipeline

        async def ingest():
            try:
                async with httpx.AsyncClient(timeout=config.timeout, transport=transport,
                        headers={"User-Agent": "Sentinel/0.1"}) as http:
                    client = (AdsbFiClient(http, config, pipeline.record_response) if aircraft_source == "adsbfi" else
                        OpenSkyClient(http, config, os.getenv("OPENSKY_CLIENT_ID"),
                        os.getenv("OPENSKY_CLIENT_SECRET"), record_response=pipeline.record_response))
                    async with asyncio.TaskGroup() as tasks:
                        tasks.create_task(pipeline.age_tracks())
                        if os.getenv("AISSTREAM_API_KEY"):
                            tasks.create_task(run_ais(config,pipeline,os.environ["AISSTREAM_API_KEY"]))
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
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.mount("/static", CachedStaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        html = (STATIC / "index.html").read_text()
        for asset in ("app.js", "stream.js", "style.css"):
            html = html.replace(f"/static/{asset}", f"/static/{asset}?v={ASSET_VERSION}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/api/tracks")
    async def tracks():
        return presentation(snapshot(app.state.pipeline, config))

    @app.get("/api/alerts")
    async def alerts():
        return presentation({"alerts": list(reversed(app.state.pipeline.alerts))})

    @app.get("/api/health")
    async def health():
        return {**app.state.pipeline.source_status, "maritime_source":dict(app.state.pipeline.ais_status)}

    @app.websocket("/ws/tracks")
    async def stream(socket: WebSocket):
        await socket.accept()
        encoder = DeltaEncoder()
        loop = asyncio.get_running_loop()
        last_activity = loop.time()
        next_push = loop.time()
        try:
            while True:
                now = loop.time()
                if now - last_activity >= idle_timeout:
                    await socket.close(code=4001, reason="Paused to save bandwidth")
                    return
                if now >= next_push:
                    update = encoder.encode(snapshot(app.state.pipeline, config))
                    if update is not None:
                        await asyncio.wait_for(socket.send_json(update), timeout=5)
                    next_push = loop.time() + push_interval
                try:
                    message = await asyncio.wait_for(socket.receive(), timeout=max(.01, min(next_push-loop.time(), idle_timeout-(loop.time()-last_activity))))
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") == "activity":
                        last_activity = loop.time()
                    elif message.get("text") == "ping":
                        await asyncio.wait_for(socket.send_text("pong"), timeout=5)
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, TimeoutError, OSError, RuntimeError):
            pass

    return app
