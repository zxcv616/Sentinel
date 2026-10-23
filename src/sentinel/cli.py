import argparse
import asyncio
import logging
import json
import os
from pathlib import Path
import signal
import tomllib

import httpx

from .config import load_config
from .pipeline import Pipeline
from .opensky import FatalError, OpenSkyClient, run_poller


async def run(config, once):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        async with httpx.AsyncClient(timeout=config.timeout, headers={"User-Agent": "Sentinel/0.1"}) as http:
            pipeline = Pipeline(config.raw_log_path, config.tracks, alert_config=config.alerts, alert_log_path=config.alert_log_path)
            try:
                client = OpenSkyClient(http, config, os.getenv("OPENSKY_CLIENT_ID"), os.getenv("OPENSKY_CLIENT_SECRET"), record_response=pipeline.record_response)
                async with asyncio.TaskGroup() as group:
                    aging = group.create_task(pipeline.age_tracks())
                    try:
                        result = await run_poller(client, once=once, pipeline=pipeline)
                    finally:
                        aging.cancel()
                return result
            except ExceptionGroup as exc:
                try:
                    pipeline.stopped()
                except OSError:
                    pass
                # Surface a recording failure without continuing an unrecorded pipeline.
                raise FatalError(f"Pipeline stopped: {exc.exceptions[0]}") from None
            finally:
                pipeline.close()
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description="Poll OpenSky aircraft counts for the Bay Area")
    parser.add_argument("--config", type=Path, help="TOML configuration (otherwise use built-in Bay Area defaults)")
    parser.add_argument("--once", action="store_true", help="Make one poll attempt and exit; no retry waits")
    parser.add_argument("--serve", action="store_true", help="Serve the live map at http://127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=8000, help="Local map server port")
    parser.add_argument("--replay", type=Path, help="Replay a raw JSONL log offline (latest session by default)")
    parser.add_argument("--session", help="Session ID within a replay log")
    parser.add_argument("--list-sessions", action="store_true", help="List sessions in the replay log")
    parser.add_argument("--output", type=Path, help="Write replay JSON to a new file instead of stdout")
    args = parser.parse_args()
    if args.replay and (args.serve or args.once or args.config):
        parser.error("--replay cannot be combined with --serve, --once or --config; recorded settings are used")
    if (args.session or args.list_sessions or args.output) and not args.replay:
        parser.error("--session, --list-sessions and --output require --replay")
    if args.list_sessions and args.session:
        parser.error("--list-sessions cannot be combined with --session")
    if args.serve and args.once:
        parser.error("--serve and --once cannot be combined")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        if args.replay:
            from .replay import list_sessions, replay
            data = list_sessions(args.replay) if args.list_sessions else replay(args.replay, session_id=args.session)
            rendered = json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"
            if args.output:
                # Exclusive creation protects source logs and previous results.
                with args.output.open("x", encoding="utf-8") as stream:
                    stream.write(rendered)
            else:
                print(rendered, end="")
            return
        config = load_config(args.config)
        if args.serve:
            import uvicorn
            from .web import create_app
            uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)
            result = 0
        else:
            result = asyncio.run(run(config, args.once))
    except (ValueError, OSError, tomllib.TOMLDecodeError, FatalError) as exc:
        logging.error("%s", exc)
        result = 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        result = 0
    raise SystemExit(result)
