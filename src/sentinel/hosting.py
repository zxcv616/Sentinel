"""Render entrypoint: one connectivity probe per process, or the live application."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
import time

import httpx
from fastapi import FastAPI
from .config import Config
from .opensky import parse_states, RetryableError
from .web import create_app


def probe_app(*, transport=None):
    result = {'state':'starting','message':'Testing OpenSky connectivity from this server'}

    async def probe():
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=25,transport=transport,headers={'User-Agent':'Sentinel/0.1','Accept':'application/json'}) as client:
                async with client.stream('GET','https://opensky-network.org/api/states/all',params=Config().bounds) as response:
                    body=bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body)>2*1024*1024:raise ValueError('Oversized response')
                    result['upstream_status']=response.status_code
                    if response.status_code!=200:
                        result.update(state='failed',message='OpenSky returned an unsuccessful HTTP response')
                        return
                    payload=parse_states(httpx.Response(response.status_code,content=bytes(body)))
                    result.update(state='success',message='OpenSky reachable; live deployment not enabled yet',aircraft_count=len(payload['states'] or []),source_timestamp=payload['time'])
        except (httpx.HTTPError,ValueError,RetryableError):
            result.update(state='failed',message='OpenSky request timed out, failed, or returned invalid data')
        finally:
            result['elapsed_seconds']=round(time.monotonic()-started,3)

    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(probe())
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
    app=create_app(config)

    @app.get('/healthz')
    async def health():return {'status':'ok','mode':'live'}

    return app
