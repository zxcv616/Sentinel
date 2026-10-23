"""Offline JSONL replay in recorded file order, using recorded UTC clock events."""
import base64
import binascii
from dataclasses import asdict
import json
from pathlib import Path

import httpx

from .alerts import validate_airports
from .opensky import RetryableError, parse_states
from .pipeline import Pipeline
from .tracks import number


class ReplayError(ValueError):
    pass


def records(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or type(record.get("version")) is not int or record.get("version") != 1:
                    raise ValueError("unsupported record version")
                if not isinstance(record.get("session"), str) or not record["session"]:
                    raise ValueError("missing session ID")
                number(record["timestamp"], 0)
            except (ValueError, TypeError, KeyError) as exc:
                raise ReplayError(f"Line {line_number}: invalid log record ({exc})") from None
            yield line_number, record


def list_sessions(path):
    sessions = []
    seen = set()
    for line, record in records(path):
        if record.get("kind") == "session":
            if record["session"] in seen:
                raise ReplayError(f"Line {line}: duplicate session header")
            seen.add(record["session"])
            sessions.append({"session": record["session"], "timestamp": record["timestamp"],
                             "replayable": all(k in record for k in ("tracks", "alerts", "airport_reference"))})
    return sessions


def state(pipeline):
    tracks = []
    for key, track in sorted(pipeline.manager.tracks.items()):
        tracks.append({"source": key[0], "object_id": key[1], "first_seen": track.first_seen,
                       "last_seen": track.last_seen, "state": track.state,
                       "history": [asdict(o) for o in track.history]})
    return {"counts": pipeline.manager.counts(), "tracks": tracks,
            "source": dict(pipeline.source_status),
            "alerts": [json.loads(line) for line in pipeline.alert_stream.getvalue().splitlines()]}


def replay(path, *, session_id=None, on_frame=None):
    """Replay latest session by default. on_frame receives state after each input event."""
    sessions = list_sessions(path)
    if not sessions:
        raise ReplayError("Log contains no session header")
    selected = session_id or sessions[-1]["session"]
    if not any(s["session"] == selected for s in sessions):
        raise ReplayError(f"Session not found: {selected}")
    clock = [0.0]
    pipeline = None
    count = 0
    try:
        for line, record in records(path):
            if record["session"] != selected:
                continue
            try:
                clock[0] = record["timestamp"]
                kind = record["kind"]
                if kind == "session":
                    if not all(k in record for k in ("tracks", "alerts", "airport_reference")):
                        raise ValueError("Session predates M3: recorded alert settings and airport reference are required")
                    validate_airports(record["airport_reference"])
                    pipeline = Pipeline.for_replay(record, lambda: clock[0])
                elif pipeline is None:
                    raise ValueError("event appears before its session header")
                elif kind == "tick":
                    pipeline.tick()
                elif kind == "source_stopped":
                    pipeline.stopped()
                elif kind == "source_failure":
                    if not isinstance(record["reason"], str):
                        raise ValueError("invalid failure reason")
                    pipeline.failed(record["reason"])
                elif kind == "response":
                    if record["source"] != "opensky":
                        raise ValueError("unsupported response source")
                    status = record["status"]
                    if type(status) is not int or not 100 <= status <= 599:
                        raise ValueError("invalid HTTP status")
                    body = base64.b64decode(record["body_base64"], validate=True)
                    response = httpx.Response(status, content=body)
                    pipeline.received_at = clock[0]
                    # HTTP failures are raw evidence. The separately recorded source_failure
                    # carries live retry/auth behavior; replay must not invent retry delays.
                    if 200 <= status < 300:
                        try:
                            data = parse_states(response)
                        except RetryableError:
                            pass
                        else:
                            pipeline.accept(data)
                else:
                    raise ValueError(f"unknown event kind: {kind}")
            except (ValueError, TypeError, KeyError, binascii.Error) as exc:
                raise ReplayError(f"Line {line}: {exc}") from None
            count += 1
            if on_frame is not None:
                on_frame({"line": line, "kind": kind, "timestamp": clock[0], **state(pipeline)})
        return {"session": selected, "records": count, "timestamp": clock[0], **state(pipeline)}
    finally:
        if pipeline is not None:
            pipeline.close()
