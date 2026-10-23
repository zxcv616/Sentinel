from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import random
import subprocess
import pytest
from sentinel.estimation import estimate, RADIUS
from sentinel.tracks import Observation, TrackConfig


def observation(t, x=0, y=0, **kwargs):
    return Observation('opensky','abc123',t,t,38+math.degrees(y/RADIUS),
                       -122+math.degrees(x/(RADIUS*math.cos(math.radians(38)))),
                       1000,100,90,'1200','airborne',**kwargs)


def test_prediction_constant_velocity_uncertainty_timeout_and_ground():
    history=[observation(t,100*t) for t in range(0,61,15)]
    config=TrackConfig()
    near=estimate(history,75,config)
    far=estimate(history,120,config)
    assert near['prediction']['lon']==pytest.approx(observation(75,7500).lon,abs=1e-8)
    assert far['prediction']['uncertainty_radius_m']>near['prediction']['uncertainty_radius_m']
    assert estimate(history,121,config)['prediction'] is None
    assert estimate(history,59,config)['prediction'] is None
    assert estimate([replace(history[-1],status='ground')],75,config)['prediction'] is None


def test_filter_reduces_seeded_position_noise():
    rng=random.Random(7)
    history=[];raw_error=[];filtered_error=[]
    config=TrackConfig(position_sigma_m=100,acceleration_sigma_mps2=.1)
    for t in range(100):
        error=rng.gauss(0,100)
        history.append(observation(t,100*t+error))
        if t>10:
            result=estimate(history,t,config)['filtered']
            filtered_x=math.radians(result['lon']+122)*RADIUS*math.cos(math.radians(38))
            raw_error.append(error**2);filtered_error.append((filtered_x-100*t)**2)
    assert sum(filtered_error)<sum(raw_error)*.6


def test_out_of_order_reset_dateline_and_poles():
    cfg=TrackConfig()
    a=observation(0);b=observation(15,1500)
    assert estimate([a,b,a],20,cfg)==estimate([a,b],20,cfg)
    reset=observation(500,20000)
    assert estimate([a,b,reset],510,cfg)==estimate([reset],510,cfg)
    crossing=[replace(a,lon=179.999),replace(b,lon=-179.999)]
    result=estimate(crossing,16,cfg)
    assert abs(result['prediction']['lon'])>179
    assert estimate([replace(a,lat=90)],1,cfg) is None
    assert estimate([],1,cfg) is None


@pytest.mark.parametrize('field',['position_sigma_m','velocity_sigma_mps','acceleration_sigma_mps2','prediction_seconds','filter_reset_seconds'])
def test_invalid_parameters(field):
    with pytest.raises(ValueError):TrackConfig(**{field:0})
    with pytest.raises(ValueError):TrackConfig(**{field:float('nan')})


def test_python_javascript_estimation_parity():
    cfg=TrackConfig()
    cases=[]
    for count in [1,3,10]:
        history=[observation(t*15,t*1500+math.sin(t)*40,t*5) for t in range(count)]
        cases.append({'history':[asdict(o) for o in history],'timestamp':history[-1].timestamp+30,'config':asdict(cfg)})
    js="import {estimate} from './cloudflare/src/estimation.mjs';let s='';for await(const c of process.stdin)s+=c;console.log(JSON.stringify(JSON.parse(s).map(c=>estimate(c.history,c.timestamp,c.config))));"
    result=json.loads(subprocess.check_output(['node','--input-type=module','-e',js],input=json.dumps(cases),text=True))
    def compare(a,b):
        if isinstance(a,dict):
            assert a.keys()==b.keys()
            for k in a:compare(a[k],b[k])
        elif isinstance(a,(float,int)):assert a==pytest.approx(b,abs=1e-6)
        else:assert a==b
    for c,actual in zip(cases,result):compare(estimate([Observation(**o) for o in c['history']],c['timestamp'],cfg),actual)
