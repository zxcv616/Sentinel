"""Deterministic, deduplicated alert rules. All times are supplied by the pipeline."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

from .tracks import number

EMERGENCIES = {"7500": "Unlawful interference code", "7600": "Radio communication failure code", "7700": "General emergency code"}


@dataclass(frozen=True)
class AlertConfig:
    gap_seconds: float = 60
    source_stale_seconds: float = 45
    airport_radius_km: float = 8
    dedup_retention_seconds: float = 1800
    recent_limit: int = 200
    airport_file: str = ""
    loiter_radius_m: float = 500
    loiter_seconds: float = 300
    loiter_min_speed_mps: float = 1
    loiter_min_distance_m: float = 100
    loiter_max_gap_seconds: float = 90

    def __post_init__(self):
        for value in (self.loiter_radius_m, self.loiter_seconds, self.loiter_min_speed_mps,
                      self.loiter_min_distance_m, self.loiter_max_gap_seconds):
            if number(value, 0) <= 0: raise ValueError("Loitering thresholds must be positive")
        for value in (self.gap_seconds, self.source_stale_seconds, self.airport_radius_km, self.dedup_retention_seconds):
            if number(value, 0) <= 0:
                raise ValueError("Alert thresholds must be positive")
        if self.dedup_retention_seconds < self.source_stale_seconds:
            raise ValueError("dedup_retention_seconds must be >= source_stale_seconds")
        if type(self.recent_limit) is not int or self.recent_limit < 1:
            raise ValueError("recent_limit must be a positive integer")
        if not isinstance(self.airport_file, str):
            raise ValueError("airport_file must be a path string")


def load_airports(config):
    path = Path(config.airport_file) if config.airport_file else Path(__file__).parent / "reference" / "airports.json"
    data = json.loads(path.read_text())
    return validate_airports(data)


def validate_airports(data):
    try:
        b = data["coverage"]
        if not -90 <= number(b["lamin"]) < number(b["lamax"]) <= 90 or not -180 <= number(b["lomin"]) < number(b["lomax"]) <= 180:
            raise ValueError
        if not data["airports"]:
            raise ValueError
        for airport in data["airports"]:
            number(airport["lat"], -90, 90)
            number(airport["lon"], -180, 180)
            if not isinstance(airport["id"], str):
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid airport reference: require coverage and a nonempty airports list") from None
    return data


def distance_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


class AnomalyEngine:
    def __init__(self, config, reference, emit):
        self.config, self.reference, self.emit = config, reference, emit
        self.eligible = set()
        self.gaps = set()
        self.signals = {}
        self.healthy_since = None
        self.last_success = None
        self.now = 0.0

    def failed(self):
        self.healthy_since = None

    def healthy(self, timestamp):
        if self.last_success is None or timestamp - self.last_success > self.config.source_stale_seconds:
            self.healthy_since = None
        if self.healthy_since is None:
            self.healthy_since = timestamp
        self.last_success = timestamp

    def observe(self, observation, now):
        key = (observation.source, observation.object_id)
        if 0 <= now - observation.timestamp <= self.config.gap_seconds:
            self.eligible.add(key)
            self.gaps.discard(key)

    def squawks(self, data, now, tracks):
        for row in data["states"] or []:
            try:
                if not isinstance(row, list) or len(row) < 17 or not isinstance(row[0], str) or not re.fullmatch(r"[0-9a-fA-F]{6}", row[0]):
                    continue
                stamp = number(row[4], 0, data["time"])
                code = row[14]
                if code is None or not isinstance(code, str) or not re.fullmatch(r"[0-7]{4}", code):
                    continue
                key = ("opensky", row[0].lower())
                previous = self.signals.get(key)
                if previous and stamp <= previous[0]:
                    continue
                if not 0 <= now - stamp <= self.config.source_stale_seconds:
                    continue
                self.signals[key] = (stamp, code)
                if code not in EMERGENCIES or (previous and previous[1] == code):
                    continue
                lat = lon = position_time = None
                try:
                    lat, lon = number(row[6], -90, 90), number(row[5], -180, 180)
                    position_time = number(row[3], 0, stamp)
                except ValueError:
                    track = tracks.get(key)
                    if track:
                        p = track.history[-1]
                        lat, lon, position_time = p.lat, p.lon, p.timestamp
                    else:
                        lat = lon = None
            except (ValueError, TypeError):
                continue
            self.emit({"type": "emergency_squawk", "source": key[0], "object_id": key[1],
                "timestamp": now, "lat": lat, "lon": lon,
                "details": {"squawk": code, "meaning": EMERGENCIES[code], "observed_at": stamp,
                            "position_timestamp": position_time, "message": f"Observed squawk {code}: {EMERGENCIES[code]}"}})

    def advance(self, timestamp, tracks):
        self.now = max(self.now, timestamp)
        keys = set(tracks)
        self.eligible.intersection_update(keys)
        self.gaps.intersection_update(keys)
        # Keep signal deduplication bounded even for aircraft with no usable position.
        self.signals = {k: v for k, v in self.signals.items() if self.now - v[0] <= self.config.dedup_retention_seconds}
        if self.last_success is None or self.now - self.last_success > self.config.source_stale_seconds:
            self.failed()
        if self.healthy_since is None:
            return
        b = self.reference["coverage"]
        for key in sorted(self.eligible - self.gaps):
            track = tracks[key]
            p = track.history[-1]
            if self.now - max(track.last_seen, self.healthy_since) <= self.config.gap_seconds:
                continue
            if p.status == "ground" or not (b["lamin"] <= p.lat <= b["lamax"] and b["lomin"] <= p.lon <= b["lomax"]):
                continue
            nearest = min((distance_km(p.lat, p.lon, a["lat"], a["lon"]), a["id"]) for a in self.reference["airports"])
            if nearest[0] <= self.config.airport_radius_km:
                continue
            self.emit({"type": "signal_gap", "source": key[0], "object_id": key[1],
                "timestamp": self.now, "lat": p.lat, "lon": p.lon,
                "details": {"last_position_at": track.last_seen, "silence_seconds": self.now-track.last_seen,
                    "threshold_seconds": self.config.gap_seconds, "nearest_airport": nearest[1],
                    "airport_distance_km": round(nearest[0], 2),
                    "message": "Position updates missing while source is healthy; cause unknown"}})
            self.gaps.add(key)
