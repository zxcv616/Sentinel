"""Optional server-side AIS stream; never exposes keys to the browser or raw log."""
import asyncio
import json
import random
import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from .maritime import POSITION_TYPES


async def run_ais(config, pipeline, api_key, *, connect=websockets.connect, sleep=asyncio.sleep):
    delay = 1
    pipeline.ais_status.update(state="connecting",message="Connecting to AIS Stream")
    while True:
        try:
            async with connect('wss://stream.aisstream.io/v0/stream', compression='deflate',
                               open_timeout=20, ping_interval=20, ping_timeout=20, max_size=1048576, max_queue=16) as socket:
                await socket.send(json.dumps({'APIKey':api_key,
                    'BoundingBoxes':[[[config.lamin,config.lomin],[config.lamax,config.lomax]]],
                    'FilterMessageTypes':list(POSITION_TYPES)}))
                while True:
                    raw = await asyncio.wait_for(socket.recv(),timeout=120)
                    try: data=json.loads(raw)
                    except (ValueError,UnicodeDecodeError): continue
                    if not isinstance(data,dict): continue
                    if 'error' in data or 'Error' in data:
                        pipeline.ais_status.update(state='stopped',message='AIS subscription rejected; check server-side key and account limits')
                        return
                    if data.get('MessageType') == 'SubscriptionConfirmation':
                        pipeline.ais_status.update(state='waiting',message='AIS subscribed; waiting for vessel positions')
                        continue
                    if data.get('MessageType') not in POSITION_TYPES: continue
                    # Write failures must stop ingestion, not silently reconnect and lose data.
                    try: pipeline.accept_ais(raw)
                    except OSError as exc: raise RuntimeError('AIS recording failed') from exc
                    delay = 1
        except (ConnectionClosed, InvalidHandshake, OSError, TimeoutError) as exc:
            status = getattr(getattr(exc,'response',None),'status_code',getattr(exc,'status_code',None))
            if status in (400,401,403):
                pipeline.ais_status.update(state='stopped',message='AIS connection rejected; check server-side credentials')
                return
            pipeline.ais_status.update(state='retrying',message='AIS connection interrupted; reconnecting')
            await sleep(delay*(1+random.random()*.2))
            delay=min(60,delay*2)
