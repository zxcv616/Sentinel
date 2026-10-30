"""Local map service. One lifespan owns one ingestion pipeline."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
import hashlib
from .bandwidth import DailyBudget, PAUSED
from .streaming import DeltaEncoder, presentation
import httpx
from anyio import CancelScope

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


def create_app(config: Config | None = None, *, transport=None, aircraft_source="opensky", push_interval=5, idle_timeout=600, session_timeout=1800, max_connections=20, daily_budget=None, budget_path=None):
    config = config or Config()
    budget = DailyBudget(daily_budget if daily_budget is not None else int(os.getenv('SENTINEL_WS_DAILY_BYTES', '120000000')),
                         budget_path or os.getenv('SENTINEL_WS_BUDGET_PATH') or Path(config.raw_log_path).parent / 'ws-budget.json')
    connections = set()

    def trip_connections():
        for event in connections:
            event.set()


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
    app.state.ws_budget = budget
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
        budget.refresh()
        if budget.tripped:
            await socket.close(code=4003, reason=PAUSED)
            return
        if len(connections) >= max_connections:
            await socket.close(code=4004, reason="Live feed busy; click to retry")
            return
        stopped = asyncio.Event()
        connections.add(stopped)
        stop_task = asyncio.create_task(stopped.wait())
        receive_task = None
        encoder = DeltaEncoder()
        loop = asyncio.get_running_loop()
        started = last_activity = next_push = loop.time()
        last_pong = -float('inf')

        async def send(text):
            # Count uncompressed UTF-8 application bytes, including app pongs.
            # Reservation and persistence are synchronous, so clients cannot race
            # past the shared limit. Failed writes remain conservatively charged.
            try:
                allowed = budget.reserve(len(text.encode('utf-8')))
            except OSError:
                log.exception('WebSocket budget persistence failed; closing feeds')
                budget.tripped = True
                allowed = False
            if not allowed:
                trip_connections()
                return False
            await asyncio.wait_for(socket.send_text(text), timeout=5)
            return True

        try:
            while True:
                now = loop.time()
                if stopped.is_set():
                    await socket.close(code=4003, reason=PAUSED)
                    return
                if now - started >= session_timeout:
                    await socket.close(code=4002, reason="Session paused; click to resume")
                    return
                if now - last_activity >= idle_timeout:
                    await socket.close(code=4001, reason="Paused to save bandwidth")
                    return
                if now >= next_push:
                    update = encoder.encode(snapshot(app.state.pipeline, config))
                    if update is not None:
                        if not await send(json.dumps(update, separators=(',', ':'), ensure_ascii=False)):
                            continue
                    next_push = loop.time() + push_interval
                if receive_task is None:
                    receive_task = asyncio.create_task(socket.receive())
                deadline = min(next_push, started + session_timeout, last_activity + idle_timeout)
                done, _ = await asyncio.wait((receive_task, stop_task), timeout=max(0, deadline-loop.time()),
                                             return_when=asyncio.FIRST_COMPLETED)
                if stop_task in done:
                    continue
                if receive_task in done:
                    message = receive_task.result()
                    receive_task = None
                    if message['type'] == 'websocket.disconnect':
                        return
                    if message.get('text') == 'activity':
                        last_activity = loop.time()
                    elif message.get('text') == 'ping' and loop.time()-last_pong >= 10:
                        last_pong = loop.time()
                        await send('pong')
                # Inbound traffic never changes next_push.
        except (WebSocketDisconnect, TimeoutError, OSError, RuntimeError):
            pass
        finally:
            connections.discard(stopped)
            pending = [t for t in (receive_task, stop_task) if t is not None]
            for task in pending:
                task.cancel()
            with CancelScope(shield=True):
                await asyncio.gather(*pending, return_exceptions=True)

    return app
