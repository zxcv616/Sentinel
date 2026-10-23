"""Offline rolling-origin holdout evaluation; future positions never enter the filter."""
import argparse
import base64
import json
import math
import statistics
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from sentinel.tracks import normalize_opensky, TrackConfig
from sentinel.estimation import estimate, RADIUS


def distance(a, b):
    dy=math.radians(a['lat']-b.lat)*RADIUS
    dx=math.radians((a['lon']-b.lon+180)%360-180)*RADIUS*math.cos(math.radians(b.lat))
    return math.hypot(dx,dy)


def evaluate(path):
    tracks=defaultdict(dict)
    records=[json.loads(l) for l in Path(path).read_text().splitlines()]
    config=TrackConfig(**records[0]['tracks'])
    for r in records[1:]:
        if r['kind']=='response' and r['status']==200:
            data=json.loads(base64.b64decode(r['body_base64']))
            for o in normalize_opensky(data,r['timestamp'])[0]: tracks[o.object_id][o.timestamp]=o
    errors={h:[] for h in (15,30,60)}
    timings=[]
    for observations in tracks.values():
        obs=sorted(observations.values(),key=lambda o:o.timestamp)
        for i in range(3,len(obs)-1):
            history=obs[max(0,i-config.history_size+1):i+1]
            last=history[-1]
            if last.status=='ground' or last.speed is None or last.heading is None: continue
            for horizon in errors:
                # Choose a real withheld observation near the requested horizon; no interpolated truth.
                future=min(obs[i+1:],key=lambda o:abs(o.timestamp-last.timestamp-horizon))
                dt=future.timestamp-last.timestamp
                if abs(dt-horizon)>5 or dt>config.prediction_seconds or future.status=='ground':continue
                start=time.perf_counter()
                result=estimate(history,future.timestamp,config)
                timings.append((time.perf_counter()-start)*1000)
                if result is None or result['prediction'] is None: continue
                heading=math.radians(last.heading)
                baseline={'lat':last.lat+math.degrees(last.speed*dt*math.cos(heading)/RADIUS),
                          'lon':last.lon+math.degrees(last.speed*dt*math.sin(heading)/(RADIUS*math.cos(math.radians(last.lat))))}
                errors[horizon].append((distance(result['prediction'],future),distance(baseline,future)))
    def stats(values):
        return {'rmse_m':round(math.sqrt(statistics.mean(v*v for v in values)),2),
                'median_m':round(statistics.median(values),2),
                'p95_m':round(sorted(values)[min(len(values)-1,math.ceil(len(values)*.95)-1)],2)} if values else None
    return {'fixture':str(path),'aircraft':len(tracks),'observations':sum(map(len,tracks.values())),
            'config':asdict(config),'method':'Rolling-origin holdout; >=4 prior positions, actual horizon within 5 s of bucket; overlapping cases are not independent.',
            'limitations':'ADS-B withheld positions are reference observations, not surveyed ground truth. One regional recording; untuned model. Timing is machine-dependent window rebuild cost.',
            'horizons':{str(h):{'samples':len(v),'kalman':stats([x[0] for x in v]),'constant_velocity':stats([x[1] for x in v])} for h,v in errors.items()},
            'mean_estimate_ms':round(statistics.mean(timings),4) if timings else None}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('recording');args=parser.parse_args()
    print(json.dumps(evaluate(args.recording),indent=2))
