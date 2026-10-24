"""Append raw input before processing; record clock ticks for later replay."""
import asyncio
import base64
from dataclasses import asdict
from collections import deque

from .alerts import AlertConfig, AnomalyEngine, load_airports
import json
import io
from pathlib import Path
import time
import uuid

from .maritime import normalize_ais, Loitering
from .tracks import TrackConfig, TrackManager, normalize_opensky


class Pipeline:
    def __init__(self, path, track_config, *, clock=time.time, alert_config=None, alert_log_path=""):
        alert_config = alert_config or AlertConfig()
        reference = load_airports(alert_config)
        self.initialize(track_config, alert_config, reference, clock, str(uuid.uuid4()))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        alert_path = Path(alert_log_path) if alert_log_path else path.with_name("alerts.jsonl")
        if path.resolve() == alert_path.resolve():
            raise ValueError("Raw and alert logs must be different files")
        alert_path.parent.mkdir(parents=True, exist_ok=True)
        self.alert_stream = None
        self.stream = path.open("a", encoding="utf-8")
        try:
            self.alert_stream = alert_path.open("a", encoding="utf-8")
            self.write("session", self.clock(), tracks=asdict(track_config), alerts=asdict(alert_config), airport_reference=reference)
        except Exception:
            self.close()
            raise

    def initialize(self, track_config, alert_config, reference, clock, session):
        self.recording = True
        self.clock = clock
        self.session = session
        self.manager = TrackManager(track_config)
        self.alerts = deque(maxlen=alert_config.recent_limit)
        self.alert_sequence = 0
        self.received_at = None
        self.source_status = {"state": "starting", "message": "Waiting for OpenSky", "last_success": None}
        self.ais_status = {"state":"disabled","message":"AIS disabled; set AISSTREAM_API_KEY on server","last_success":None}
        self.loitering = Loitering(alert_config, self.emit_alert)
        self.engine = AnomalyEngine(alert_config, reference, self.emit_alert)

    @classmethod
    def for_replay(cls, session, clock):
        """Use the same processing methods without opening any files or network clients."""
        pipeline = cls.__new__(cls)
        pipeline.initialize(TrackConfig(**session["tracks"]), AlertConfig(**session["alerts"]),
                            session["airport_reference"], clock, session["session"])
        pipeline.recording = False
        pipeline.stream = io.StringIO()
        pipeline.alert_stream = io.StringIO()
        return pipeline

    def write(self, kind, timestamp, **fields):
        if not self.recording:
            return
        self.stream.write(json.dumps({"version": 1, "session": self.session,
            "kind": kind, "timestamp": timestamp, **fields}, allow_nan=False) + "\n")
        self.stream.flush()

    def record_response(self, response):
        self.received_at = self.clock()
        self.write("response", self.received_at, source="opensky", status=response.status_code,
            body_base64=base64.b64encode(response.content).decode("ascii"),
            headers={key: response.headers[key] for key in
                ("content-type", "retry-after", "x-rate-limit-retry-after-seconds") if key in response.headers})

    def emit_alert(self, alert):
        self.alert_sequence += 1
        event = {"version": 1, "session": self.session, "id": f"{self.session}:{self.alert_sequence}", **alert}
        self.alert_stream.write(json.dumps(event, allow_nan=False) + "\n")
        self.alert_stream.flush()
        self.alerts.append(event)

    def failed(self, reason):
        self.write("source_failure", self.clock(), reason=reason)
        self.engine.failed()
        self.source_status.update(state="retrying", message=reason)

    def stopped(self):
        message = "Ingestion stopped; check server logs"
        self.write("source_stopped", self.clock(), reason=message)
        self.engine.failed()
        self.source_status.update(state="stopped", message=message)

    def accept(self, data):
        self.source_status.update(state="live", message="Receiving OpenSky positions", last_success=self.received_at)
        observations, rejected = normalize_opensky(data, self.received_at)
        self.manager.advance(self.received_at)
        if (rejected and not observations) or not 0 <= self.received_at - data["time"] <= self.engine.config.source_stale_seconds:
            self.failed("Source response has unusable or stale positions")
        else:
            self.engine.healthy(self.received_at)
        accepted = 0
        for observation in observations:
            if self.manager.update(observation):
                accepted += 1
                self.engine.observe(observation, self.received_at)
        self.engine.squawks(data, self.received_at, self.manager.tracks)
        self.engine.advance(self.received_at, self.manager.tracks)
        return accepted, rejected

    def accept_ais(self, raw, timestamp=None):
        timestamp = self.clock() if timestamp is None else timestamp
        body = raw.encode() if isinstance(raw,str) else raw
        self.write("ais_message", timestamp, source="aisstream", body_base64=base64.b64encode(body).decode())
        try: observation = normalize_ais(json.loads(body), timestamp)
        except (ValueError, UnicodeDecodeError): observation = None
        if observation is None: return False
        self.ais_status.update(state="live",message="Receiving AIS vessel positions",last_success=timestamp)
        self.manager.advance(timestamp)
        if self.manager.update(observation): self.loitering.observe(observation)
        self.loitering.advance(timestamp)
        return True

    def tick(self):
        timestamp = self.clock()
        self.write("tick", timestamp)
        self.loitering.advance(timestamp)
        self.manager.advance(timestamp)
        self.engine.advance(timestamp, self.manager.tracks)

    async def age_tracks(self):
        while True:
            self.tick()
            await asyncio.sleep(self.manager.config.tick_interval)

    def close(self):
        self.stream.close()
        if self.alert_stream is not None:
            self.alert_stream.close()
