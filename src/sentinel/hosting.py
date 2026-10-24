"""Render entrypoint: one connectivity probe per process, or the live application."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
import math
import time

import httpx
from fastapi import FastAPI
from .config import Config
from .opensky import parse_states, RetryableError
from .web import create_app


async def probe_adsbfi(transport=None):
    """One bounded check of the documented public regional endpoint."""
    result = {'state': 'failed', 'provider': 'adsb.fi'}
    try:
        async with httpx.AsyncClient(timeout=25, transport=transport) as client:
            async with client.stream('GET', 'https://opendata.adsb.fi/api/v3/lat/38/lon/-122/dist/100') as response:
                result['upstream_status'] = response.status_code
                if response.status_code != 200:
                    return result
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError('Oversized response')
                payload = httpx.Response(200, content=bytes(body)).json()
                if not isinstance(payload, dict) or not isinstance(payload.get('ac'), list) or type(payload.get('now')) not in (int, float):
                    raise ValueError('Invalid response')
                if not math.isfinite(payload['now']) or abs(time.time() - payload['now'] / 1000) > 120:
                    raise ValueError('Stale response')
                result.update(state='success', aircraft_count=len(payload['ac']), source_timestamp=payload['now']/1000)
    except (httpx.HTTPError, ValueError):
        result['message'] = 'Provider request failed or returned invalid/stale data'
    return result


def probe_app(*, transport=None):
    result = {'state':'starting','message':'Testing OpenSky connectivity from this server'}

    async def probe():
        started = time.monotonic()
        phase = 'connecting'

        async def trace(event, info):
            # Publish only fixed phase names, never transport arguments or secrets.
            nonlocal phase
            if event == 'connection.connect_tcp.complete':
                phase = 'tls_handshake'
            elif event == 'connection.start_tls.complete':
                phase = 'sending_request'
            elif event.endswith('receive_response_headers.started'):
                phase = 'waiting_for_headers'

        try:
            async with httpx.AsyncClient(timeout=25,transport=transport,headers={'User-Agent':'Sentinel/0.1','Accept':'application/json'}) as client:
                async with client.stream('GET','https://opensky-network.org/api/states/all',params=Config().bounds,extensions={'trace': trace}) as response:
                    result['upstream_status']=response.status_code
                    phase = 'reading_body'
                    body=bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body)>2*1024*1024:raise ValueError('Oversized response')
                    if response.status_code!=200:
                        result.update(state='failed',message='OpenSky returned an unsuccessful HTTP response')
                        return
                    phase = 'validating_payload'
                    payload=parse_states(httpx.Response(response.status_code,content=bytes(body)))
                    result.update(state='success',message='OpenSky reachable; live deployment not enabled yet',aircraft_count=len(payload['states'] or []),source_timestamp=payload['time'])
        except (httpx.HTTPError,ValueError,RetryableError) as exc:
            result.update(state='failed',message='OpenSky request timed out, failed, or returned invalid data',failure_phase=phase,
                          failure_kind=('timeout' if isinstance(exc,httpx.TimeoutException) else
                                        'transport' if isinstance(exc,httpx.HTTPError) else 'invalid_payload'))
        finally:
            result['elapsed_seconds']=round(time.monotonic()-started,3)

    @asynccontextmanager
    async def lifespan(app):
        async def checks():
            await probe()
            if result['state'] == 'failed':
                result['alternative'] = await probe_adsbfi(transport)
        task=asyncio.create_task(checks())
        try:yield
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    app=FastAPI(title='Sentinel connectivity check',lifespan=lifespan)

    @app.get('/healthz')
    async def health():return {'status':'ok','mode':'probe'}

    @app.get('/')
    @app.get('/api/probe')
    async def status():return dict(result)

    return app


def create_hosted_app():
    mode=os.getenv('SENTINEL_MODE','probe')
    if mode=='probe':return probe_app()
    if mode!='live':raise ValueError('SENTINEL_MODE must be probe or live')
    # One process owns one collector. Visitor requests never trigger upstream polls.
    config=Config()
    config=replace(config,poll_interval=300,
                   tracks=replace(config.tracks,gap_after=330,lost_after=900),
                   alerts=replace(config.alerts,gap_seconds=600,source_stale_seconds=330))
    source=os.getenv("SENTINEL_AIRCRAFT_SOURCE", "opensky")
    if source == "opensky":
        app=create_app(config)
    elif source == "adsbfi":
        app=create_app(config, aircraft_source=source)
    else:
        raise ValueError("Unsupported SENTINEL_AIRCRAFT_SOURCE")

    @app.get('/healthz')
    async def health():return {'status':'ok','mode':'live'}

    return app
