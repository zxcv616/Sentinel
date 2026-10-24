import asyncio
import base64
import json
from dataclasses import asdict
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from sentinel.adsbfi import AdsbFiClient, parse_adsbfi
from sentinel.config import Config
from sentinel.opensky import RetryableError, FatalError
from sentinel.pipeline import Pipeline
from sentinel.replay import replay
from sentinel.tracks import normalize_opensky
from sentinel.web import create_app


def payload(now):
    return {'now': now*1000, 'ac': [
        {'hex':'abc123','seen_pos':2,'seen':1,'lat':37.7,'lon':-122.4,
         'alt_baro':10000,'gs':200,'track':90,'squawk':'7700'},
        {'hex':'abc124','seen_pos':1,'seen':0,'lat':37.8,'lon':-122.3,
         'alt_baro':'ground','gs':0,'track':0},
        {'hex':'~abc125','seen_pos':0,'seen':0,'lat':37.8,'lon':-122.3},
    ]}


def test_units_timestamps_ground_and_invalid_ids():
    data=parse_adsbfi(httpx.Response(200,json=payload(1000)))
    observations,rejected=normalize_opensky(data,1000,source='adsbfi')
    assert rejected==1
    p=observations[0]
    assert p.source=='adsbfi' and p.timestamp==998
    assert p.altitude==3048 and p.speed==pytest.approx(102.8888)
    assert observations[1].status=='ground'


@pytest.mark.parametrize(('status','error'),[(429,RetryableError),(503,RetryableError),(403,FatalError)])
def test_failure_is_recorded_before_retry_or_stop(status,error):
    records=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(status))) as http:
            client=AdsbFiClient(http,Config(),records.append)
            with pytest.raises(error):await client.poll()
    asyncio.run(run())
    assert len(records)==1 and records[0].status_code==status


def test_native_raw_recording_replays_identical_tracks_and_alerts(tmp_path):
    path=tmp_path/'raw.jsonl'
    config=Config()
    pipeline=Pipeline(path,config.tracks,clock=lambda:1000,aircraft_source='adsbfi')
    response=httpx.Response(200,json=payload(1000))
    pipeline.record_response(response)
    pipeline.accept(parse_adsbfi(response))
    histories=[[asdict(o) for o in track.history] for track in pipeline.manager.tracks.values()]
    alerts=list(pipeline.alerts)
    pipeline.close()
    result=replay(path)
    assert [t['history'] for t in result['tracks']]==histories
    assert result['alerts']==alerts
    assert alerts[0]['source']=='adsbfi' and alerts[0]['type']=='emergency_squawk'
    assert 'adsb.fi' in result['source']['message']
    raw=next(json.loads(line) for line in path.read_text().splitlines() if json.loads(line)['kind']=='response')
    assert raw['source']=='adsbfi'
    assert json.loads(base64.b64decode(raw['body_base64']))==payload(1000)


def test_websocket_clients_share_one_adsbfi_poll(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=payload(time.time()))
    app=create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')),transport=httpx.MockTransport(handler),aircraft_source='adsbfi')
    with TestClient(app) as client:
        with client.websocket_connect('/ws/tracks') as ws:
            data=ws.receive_json()
            for _ in range(3):
                if data['tracks']:break
                data=ws.receive_json()
            assert data['aircraft_provider']=='adsbfi'
            assert len(data['tracks'])==2
            assert data['alerts'][0]['source']=='adsbfi'
        with client.websocket_connect('/ws/tracks') as ws:
            assert ws.receive_json()['tracks']
        assert len(calls)==1
        assert calls[0].url.host=='opendata.adsb.fi'
