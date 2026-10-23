from dataclasses import dataclass, fields
import math
from pathlib import Path
import tomllib

from .tracks import TrackConfig
from .alerts import AlertConfig


@dataclass(frozen=True)
class Config:
    alerts: AlertConfig = AlertConfig()
    alert_log_path: str = ""
    tracks: TrackConfig = TrackConfig()
    raw_log_path: str = "data/raw.jsonl"
    lamin: float = 37.0
    lamax: float = 39.0
    lomin: float = -123.0
    lomax: float = -121.0
    poll_interval: float = 15.0
    timeout: float = 20.0
    backoff_initial: float = 15.0
    backoff_max: float = 300.0

    def __post_init__(self):
        if not isinstance(self.alert_log_path, str):
            raise ValueError("alert_log_path must be a path string")
        if self.alerts.gap_seconds >= self.tracks.expire_after:
            raise ValueError("Alert gap_seconds must be less than track expire_after")
        if not isinstance(self.raw_log_path, str) or not self.raw_log_path.strip():
            raise ValueError("raw_log_path must be a nonempty path")
        for field in fields(self):
            if field.name in ("tracks", "raw_log_path", "alerts", "alert_log_path"):
                continue
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"{field.name} must be a finite number")
        if not -90 <= self.lamin < self.lamax <= 90:
            raise ValueError("Latitude bounds must satisfy -90 <= lamin < lamax <= 90")
        if not -180 <= self.lomin < self.lomax <= 180:
            raise ValueError("Longitude bounds must satisfy -180 <= lomin < lomax <= 180")
        if min(self.poll_interval, self.timeout, self.backoff_initial) <= 0:
            raise ValueError("Polling interval, timeout and initial backoff must be positive")
        if self.backoff_max < self.backoff_initial:
            raise ValueError("backoff_max must be >= backoff_initial")

    @property
    def bounds(self):
        return {key: getattr(self, key) for key in ("lamin", "lamax", "lomin", "lomax")}


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if "opensky" not in data or set(data) - {"opensky", "tracks", "recording", "alerts"} or not isinstance(data["opensky"], dict):
        raise ValueError("Config must contain an [opensky] table")
    unknown = set(data["opensky"]) - ({field.name for field in fields(Config)} - {"tracks", "raw_log_path", "alerts", "alert_log_path"})
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    try:
        tracks = TrackConfig(**data.get("tracks", {}))
        recording = data.get("recording", {})
        if set(recording) - {"raw_log_path", "alert_log_path"}:
            raise ValueError("Unknown recording configuration keys")
        return Config(**data["opensky"], tracks=tracks, alerts=AlertConfig(**data.get("alerts", {})), **recording)
    except TypeError as exc:
        raise ValueError("Invalid tracks or recording configuration") from exc
