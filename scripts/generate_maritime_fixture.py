"""Generate explicitly synthetic AIS + aircraft inputs, never represented as live."""
import base64,json,math
from pathlib import Path
from dataclasses import asdict
from sentinel.tracks import TrackConfig
from sentinel.alerts import AlertConfig,load_airports
rows=[];start=1700000000

def add(kind,t,**fields):rows.append(dict(version=1,session='synthetic-maritime-v1',kind=kind,timestamp=t,**fields))
add('session',start,tracks=asdict(TrackConfig()),alerts=asdict(AlertConfig()),airport_reference=load_airports(AlertConfig()))
for elapsed in range(0,421,15):
 t=start+elapsed
 aircraft=['abc123','SYNTHETIC','',t,t,-122.65+elapsed*.0005,37.8,3000,False,100,90,0,None,3000,'1200',False,0]
 body=json.dumps({'time':t,'states':[aircraft]}).encode()
 add('response',t,source='opensky',status=200,body_base64=base64.b64encode(body).decode(),headers={})
 for mmsi,lat,lon,speed,status in [(999000001,37.81+.001*math.sin(elapsed/45),-122.45+.001*math.cos(elapsed/45),5,0),(999000002,37.79+elapsed*.00002,-122.48+elapsed*.00004,8,0),(999000003,37.8,-122.4,0,5)]:
  data={'MessageType':'PositionReport','MetaData':{'MMSI':mmsi,'ShipName':'SYNTHETIC DEMO'},'Message':{'PositionReport':{'UserID':mmsi,'Valid':True,'Latitude':lat,'Longitude':lon,'Sog':speed,'Cog':90,'TrueHeading':511,'NavigationalStatus':status}}}
  add('ais_message',t,source='aisstream',body_base64=base64.b64encode(json.dumps(data).encode()).decode())
Path('tests/fixtures/maritime-synthetic.jsonl').write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in rows))
