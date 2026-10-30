"""Lossless deltas of a rounded presentation state; raw/replay data is untouched."""
import copy


def presentation(value, key=''):
    if isinstance(value, dict):
        return {k: presentation(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [presentation(v, key) for v in value]
    if isinstance(value, float):
        precision = 5 if key in ('lat', 'lon') else 1 if key in ('speed', 'heading', 'horizon_seconds') else 0 if key in ('altitude', 'uncertainty_radius_m') else 3
        return round(value, precision)
    return value


def track_key(track):
    return f"{track['source']}:{track['object_id']}"


class DeltaEncoder:
    def __init__(self):
        self.previous = None
        self.sequence = 0

    def encode(self, snapshot):
        current = presentation(snapshot)
        if self.previous is None:
            self.previous = copy.deepcopy(current)
            return {**current, 'sequence': 0}
        before = {track_key(t): t for t in self.previous['tracks']}
        after = {track_key(t): t for t in current['tracks']}
        upsert = [t for key, t in after.items() if key not in before]
        # Predictions change more frequently than observations. Do not resend a
        # retained trail when only the estimate or lifecycle state changed.
        updates = [{**{field: value for field, value in t.items()
                       if before[key].get(field) != value},
                    'source': t['source'], 'object_id': t['object_id']}
                   for key, t in after.items() if key in before and before[key] != t]
        removed = sorted(before.keys() - after.keys())
        meta = {k: v for k, v in current.items() if k not in ('tracks', 'timestamp', 'type') and self.previous.get(k) != v}
        if not upsert and not updates and not removed and not meta:
            return None
        self.sequence += 1
        self.previous = copy.deepcopy(current)
        return {'type': 'delta', 'sequence': self.sequence, 'timestamp': current['timestamp'],
                'tracks': {'upsert': upsert, 'update': updates, 'remove': removed}, 'meta': meta}
