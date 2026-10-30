"""Offline presentation benchmark. No upstream calls; decimal bytes, not wire billing.

Replay the 40 captured responses, then measure a minute after the final capture.
For a conservative portfolio bound, repeat that busy minute for every minute of
visiting time, including a fresh snapshot each minute (real sessions send one).
"""
import base64
import json
from pathlib import Path
import zlib

from sentinel.config import Config
from sentinel.pipeline import Pipeline
from sentinel.streaming import DeltaEncoder
from sentinel.web import snapshot

ROOT = Path(__file__).resolve().parents[1]


def captured_frames():
    rows = [json.loads(line) for line in (ROOT/'tests/fixtures/opensky-tracking.jsonl').read_text().splitlines()]
    clock = [rows[0]['timestamp']]
    pipeline = Pipeline.for_replay(rows[0], lambda: clock[0])
    try:
        for row in rows[1:]:
            clock[0] = row['timestamp']
            if row['kind'] == 'response':
                pipeline.received_at = clock[0]
                pipeline.accept(json.loads(base64.b64decode(row['body_base64'])))
            elif row['kind'] == 'tick':
                pipeline.tick()
        start = clock[0]
        frames = []
        for second in range(60):
            clock[0] = start + second
            pipeline.tick()
            frame = snapshot(pipeline, Config())
            frame['timestamp'] = clock[0]
            frames.append(frame)
        return frames
    finally:
        pipeline.close()


def sizes(messages):
    payloads = [json.dumps(m, separators=(',', ':'), ensure_ascii=False).encode() for m in messages]
    compressor = zlib.compressobj(wbits=-15)
    compressed = [compressor.compress(p) + compressor.flush(zlib.Z_SYNC_FLUSH) for p in payloads]
    return {'messages': len(payloads), 'mean_message_bytes': round(sum(map(len,payloads))/len(payloads)),
            'json_bytes_per_minute': sum(map(len,payloads)),
            'modeled_deflate_bytes_per_minute': sum(len(p)-4 for p in compressed)}


def report(frames):
    encoder = DeltaEncoder()
    updates = [u for f in frames[::5] if (u := encoder.encode(f)) is not None]
    before, after = sizes(frames), sizes(updates)
    # Three app pongs + three protocol pings/pongs: conservative 1 KiB/min reserve.
    static = sum(p.stat().st_size for p in (ROOT/'src/sentinel/static').glob('*') if p.is_file())
    minutes = 100*5 + 10  # 100 visits and one abandoned visible tab, capped at 10m.
    upper = (after['json_bytes_per_minute']+1024)*minutes + 101*(static+4096)
    return {'tracks': len(frames[0]['tracks']),
            'mean_trail_points': sum(len(t['history']) for t in frames[0]['tracks'])/len(frames[0]['tracks']),
            'before': before, 'after': after,
            'reduction_percent': round(100*(1-after['json_bytes_per_minute']/before['json_bytes_per_minute']),2),
            'portfolio_uncompressed_bytes': upper,
            'portfolio_with_2x_margin_bytes': 2*upper,
            'static_bytes_uncompressed': static}


if __name__ == '__main__':
    print(json.dumps(report(captured_frames()), indent=2))
