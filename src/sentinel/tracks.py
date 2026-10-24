"""Source-neutral observations and deterministic, event-time track management."""
from collections import deque
from dataclasses import dataclass, field
import math
import re


@dataclass(frozen=True)
class Observation:
    source: str
    object_id: str
    timestamp: float
    received_at: float
    lat: float
    lon: float
    altitude: float | None
    speed: float | None
    heading: float | None
    squawk: str | None
    status: str | None


def number(value, low=-math.inf, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid numeric observation field")
    return float(value)


def normalize_opensky(data, received_at, *, source="opensky"):
    """Skip unusable rows independently. Units: UTC epoch seconds, meters, m/s, degrees."""
    observations = []
    rejected = 0
    for row in data["states"] or []:
        try:
            if not isinstance(row, list) or len(row) < 17:
                raise ValueError
            if not isinstance(row[0], str) or not re.fullmatch(r"[0-9a-fA-F]{6}", row[0]):
                raise ValueError
            stamp = number(row[3], 0, data["time"])
            lat, lon = number(row[6], -90, 90), number(row[5], -180, 180)
            altitude = None if row[7] is None else number(row[7])
            speed = None if row[9] is None else number(row[9], 0)
            heading = None if row[10] is None else number(row[10], 0, 360)
            squawk = row[14]
            if squawk is not None and (not isinstance(squawk, str) or not re.fullmatch(r"[0-7]{4}", squawk)):
                raise ValueError
            if type(row[8]) is not bool:
                raise ValueError
            observations.append(Observation(source, row[0].lower(), stamp, received_at,
                lat, lon, altitude, speed, heading, squawk, "ground" if row[8] else "airborne"))
        except (ValueError, TypeError, IndexError):
            rejected += 1
    return observations, rejected


@dataclass(frozen=True)
class TrackConfig:
    gap_after: float = 60
    lost_after: float = 300
    expire_after: float = 1800
    history_size: int = 120
    tick_interval: float = 1
    position_sigma_m: float = 100
    velocity_sigma_mps: float = 50
    acceleration_sigma_mps2: float = 2
    prediction_seconds: float = 60
    filter_reset_seconds: float = 120

    def __post_init__(self):
        for value in (self.position_sigma_m, self.velocity_sigma_mps, self.acceleration_sigma_mps2,
                      self.prediction_seconds, self.filter_reset_seconds):
            if number(value, 0) <= 0:
                raise ValueError("Estimation parameters must be positive")
        for value in (self.gap_after, self.lost_after, self.expire_after, self.tick_interval):
            number(value, 0)
        if not 0 < self.gap_after < self.lost_after < self.expire_after or self.tick_interval <= 0:
            raise ValueError("Require 0 < gap_after < lost_after < expire_after and positive tick_interval")
        if type(self.history_size) is not int or self.history_size < 1:
            raise ValueError("history_size must be a positive integer")


@dataclass
class Track:
    source: str
    object_id: str
    first_seen: float
    last_seen: float
    history: deque = field(default_factory=deque)
    state: str = "active"


class TrackManager:
    def __init__(self, config=TrackConfig()):
        self.config = config
        self.tracks: dict[tuple[str, str], Track] = {}
        self.now = 0.0

    def update(self, observation):
        key = (observation.source, observation.object_id)
        track = self.tracks.get(key)
        # An old position repeated in a newer API snapshot is not new contact.
        if track and observation.timestamp <= track.last_seen:
            return False
        if self.now - observation.timestamp > self.config.expire_after:
            return False
        if track is None:
            track = Track(*key, observation.timestamp, observation.timestamp,
                          deque(maxlen=self.config.history_size))
            self.tracks[key] = track
        track.last_seen = observation.timestamp
        track.history.append(observation)
        self.advance(self.now)
        return True

    def advance(self, timestamp):
        self.now = max(self.now, timestamp)
        for key, track in list(self.tracks.items()):
            silence = self.now - track.last_seen
            if silence > self.config.expire_after:
                del self.tracks[key]
            else:
                track.state = ("lost" if silence > self.config.lost_after else
                               "gap" if silence > self.config.gap_after else "active")

    def counts(self):
        return {state: sum(t.state == state for t in self.tracks.values())
                for state in ("active", "gap", "lost")}
