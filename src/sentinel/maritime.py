"""AIS position normalization and bounded, deterministic loitering episodes."""
from datetime import datetime, timezone
import math
import re
from .tracks import Observation, number
from .alerts import distance_km

POSITION_TYPES = ('PositionReport', 'StandardClassBPositionReport', 'ExtendedClassBPositionReport')


def normalize_ais(data, received):
    try:
        kind = data['MessageType']
        if kind not in POSITION_TYPES: return None
        report = data['Message'][kind]
        if report.get('Valid') is not True: return None
        mmsi = report['UserID']
        if type(mmsi) is not int or not 100000000 <= mmsi <= 999999999: return None
        meta = data.get('MetaData', {})
        if 'MMSI' in meta and meta['MMSI'] != mmsi: return None
        lat, lon = number(report['Latitude'], -90, 90), number(report['Longitude'], -180, 180)
        stamp = received
        # Provider reception time, not the AIS seconds-within-minute field.
        if meta.get('time_utc'):
            text = meta['time_utc'].strip()
            if text.endswith(' UTC'):
                text = text[:-4]
                if not re.search(r'[+-]\d{2}:?\d{2}$', text): text += '+00:00'
            text = re.sub(r'\s+([+-]\d{2})(\d{2})$', r'\1:\2', text)
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None: return None
            stamp = parsed.timestamp()
        if not 0 <= received-stamp <= 120: return None
        speed = report.get('Sog')
        speed = number(speed, 0, 102.2)*0.514444 if speed is not None and speed != 102.3 else None
        heading = report.get('TrueHeading')
        if heading is None or heading == 511: heading = report.get('Cog')
        heading = None if heading is None or heading == 360 else number(heading, 0, 359.9999)
        status = {1:'anchored',5:'moored'}.get(report.get('NavigationalStatus'), 'underway')
        return Observation('aisstream',str(mmsi),stamp,received,lat,lon,None,speed,heading,None,status)
    except (ValueError, TypeError, KeyError, AttributeError): return None


class Loitering:
    def __init__(self, config, emit):
        self.config, self.emit = config, emit
        self.episodes = {}

    def observe(self, o):
        if o.source != 'aisstream': return
        key = o.object_id
        old = self.episodes.get(key)
        if old and o.timestamp <= old['last'].timestamp: return
        if o.status in ('anchored','moored') or o.speed is None or o.speed < self.config.loiter_min_speed_mps:
            self.episodes.pop(key,None); return
        if old is None or o.timestamp-old['last'].timestamp > self.config.loiter_max_gap_seconds or distance_km(old['anchor'].lat,old['anchor'].lon,o.lat,o.lon)*1000 > self.config.loiter_radius_m:
            self.episodes[key] = {'anchor':o,'last':o,'distance':0,'emitted':False}; return
        old['distance'] += distance_km(old['last'].lat,old['last'].lon,o.lat,o.lon)*1000
        old['last'] = o
        duration = o.timestamp-old['anchor'].timestamp
        if not old['emitted'] and duration >= self.config.loiter_seconds and old['distance'] >= self.config.loiter_min_distance_m:
            old['emitted'] = True
            self.emit({'type':'loitering','source':o.source,'object_id':o.object_id,'timestamp':o.timestamp,'lat':o.lat,'lon':o.lon,
                       'details':{'duration_seconds':duration,'radius_m':self.config.loiter_radius_m,
                                  'message':'Sustained vessel movement within configured radius; intent unknown'}})

    def advance(self, timestamp):
        self.episodes = {k:v for k,v in self.episodes.items() if timestamp-v['last'].timestamp <= self.config.loiter_max_gap_seconds}
