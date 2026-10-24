import asyncio
import base64
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import pytest
from sentinel.maritime import normalize_ais
from sentinel.replay import replay
from sentinel.ais import run_ais
from sentinel.config import Config

FIXTURE=Path('tests/fixtures/maritime-synthetic.jsonl')

def message():
    record=next(json.loads(l) for l in FIXTURE.read_text().splitlines() if json.loads(l)['kind']=='ais_message')
    return json.loads(base64.b64decode(record['body_base64']))


def test_ais_units_sentinels_and_validation():
    data=message();o=normalize_ais(data,1700000000)
    assert o.object_id=='999000001' and o.source=='aisstream'
    assert o.speed==pytest.approx(5*.514444) and o.heading==90
    r=data['Message']['PositionReport'];r['Sog']=102.3;r['Cog']=360
    assert normalize_ais(data,1700000000).speed is None
    assert normalize_ais(data,1700000000).heading is None
    for field,value in [('Latitude',91),('Longitude',181),('Valid',False),('UserID',123)]:
        other=message();other['Message']['PositionReport'][field]=value
        assert normalize_ais(other,1700000000) is None
    data['MetaData']['MMSI']=999000009
    assert normalize_ais(data,1700000000) is None


@pytest.mark.parametrize('stamp',['2023-11-14T22:13:20Z','2023-11-14 22:13:20.000000 +0000 UTC'])
def test_provider_timestamp_formats(stamp):
    data=message();data['MetaData']['time_utc']=stamp
    assert normalize_ais(data,1700000001).timestamp==1700000000
    assert normalize_ais(data,1700000200) is None
    assert normalize_ais(data,1699999999) is None


def test_synthetic_replay_loitering_and_cross_language_parity():
    result=replay(FIXTURE)
    assert len(result['tracks'])==4
    assert [(a['type'],a['object_id']) for a in result['alerts']]==[('loitering','999000001')]
    js="import fs from 'node:fs';import {Core} from './cloudflare/src/core.mjs';const rs=fs.readFileSync(process.argv[1],'utf8').trim().split('\\n').map(JSON.parse);const core=new Core(rs[0]);for(const r of rs)core.apply(r);console.log(JSON.stringify(core.result()));"
    actual=json.loads(subprocess.check_output(['node','--input-type=module','-e',js,str(FIXTURE)],text=True))
    for field in ('tracks','counts','alerts','source'):assert actual[field]==result[field]


def test_loiter_gap_exit_and_stationary_reset():
    from dataclasses import replace
    from sentinel.maritime import Loitering
    from sentinel.alerts import AlertConfig
    events=[];rule=Loitering(AlertConfig(loiter_seconds=30,loiter_min_distance_m=1),events.append)
    o=normalize_ais(message(),1000)
    for t in (1000,1015,1030,1045): rule.observe(replace(o,timestamp=t,lat=o.lat+(t%30)*.00001))
    assert len(events)==1
    rule.observe(replace(o,timestamp=1200))
    rule.observe(replace(o,timestamp=1215,lat=o.lat+.0002))
    assert len(events)==1
    rule.observe(replace(o,timestamp=1230,status='moored'))
    assert not rule.episodes
    for t in (1245,1260,1275):rule.observe(replace(o,timestamp=t,speed=0))
    assert len(events)==1
    rule.observe(replace(o,timestamp=1300));rule.observe(replace(o,timestamp=1315,lat=o.lat+1))
    assert rule.episodes[o.object_id]['anchor'].timestamp==1315
    rule.advance(1500);assert not rule.episodes


def test_stream_reconnect_subscription_and_secret_not_recorded(tmp_path):
    from sentinel.pipeline import Pipeline
    pipeline=Pipeline(tmp_path/'raw.jsonl',Config().tracks,clock=lambda:1700000000)
    sent=[];attempts=[];sleeps=[]
    class Socket:
        count=0
        async def send(self,value):sent.append(json.loads(value))
        async def recv(self):
            self.count+=1
            if self.count==1:return json.dumps(message()).encode()
            return json.dumps({'error':'rejected'})
    class Context:
        async def __aenter__(self):
            attempts.append(1)
            if len(attempts)==1:raise OSError('network')
            return Socket()
        async def __aexit__(self,*args):pass
    async def sleep(delay):sleeps.append(delay)
    asyncio.run(run_ais(Config(),pipeline,'test-secret',connect=lambda *a,**k:Context(),sleep=sleep))
    pipeline.close()
    assert len(attempts)==2 and len(sleeps)==1
    assert sent[0]['APIKey']=='test-secret'
    assert sent[0]['FilterMessageTypes']==['PositionReport','StandardClassBPositionReport','ExtendedClassBPositionReport']
    assert 'test-secret' not in (tmp_path/'raw.jsonl').read_text()
    assert len(pipeline.manager.tracks)==1 and pipeline.ais_status['state']=='stopped'


@pytest.mark.parametrize('kind',['StandardClassBPositionReport','ExtendedClassBPositionReport'])
def test_class_b_reports_and_stationary_predictions(kind):
    from sentinel.estimation import estimate
    from sentinel.tracks import TrackConfig
    data=message();report=data['Message'].pop('PositionReport');data['MessageType']=kind;data['Message'][kind]=report
    assert normalize_ais(data,1000).source=='aisstream'
    report['NavigationalStatus']=5
    o=normalize_ais(data,1000)
    assert estimate([o],1010,TrackConfig())['prediction'] is None
