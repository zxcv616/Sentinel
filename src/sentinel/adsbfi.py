"""adsb.fi public regional API adapter. Raw responses retain their source identity."""
import math
import httpx

from .opensky import FatalError, RetryableError, retry_delay
from .tracks import number


def parse_adsbfi(response):
    """Convert units into the internal aircraft row format; never invent positions."""
    try:
        payload = response.json()
        stamp = number(payload['now'], 0) / 1000
        aircraft_list = payload['ac']
        if not isinstance(aircraft_list, list):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise RetryableError('Malformed adsb.fi response') from None
    rows = []
    for aircraft in aircraft_list:
        try:
            altitude = aircraft.get('alt_baro')
            ground = altitude == 'ground'
            altitude = None if altitude is None else 0 if ground else number(altitude) * 0.3048
            speed = aircraft.get('gs')
            speed = None if speed is None else number(speed, 0) * 0.514444
            position_time = None if aircraft.get('seen_pos') is None else stamp - number(aircraft['seen_pos'], 0)
            contact_time = stamp - number(aircraft.get('seen'), 0)
            # Non-ICAO IDs (~...) and invalid coordinates are rejected downstream.
            rows.append([aircraft.get('hex'), None, None, position_time, contact_time,
                         aircraft.get('lon'), aircraft.get('lat'), altitude, ground,
                         speed, aircraft.get('track'), None, None, None,
                         aircraft.get('squawk'), False, 0])
        except (ValueError, TypeError, AttributeError):
            rows.append([])  # Preserve rejected-row accounting.
    return {'time': stamp, 'states': rows}


class AdsbFiClient:
    def __init__(self, http, config, record_response):
        self.http, self.config, self.record_response = http, config, record_response
        b = config.bounds
        lat, lon = (b['lamin'] + b['lamax']) / 2, (b['lomin'] + b['lomax']) / 2
        radius = math.ceil(math.hypot((b['lamax'] - lat)*60,
                                     (b['lomax'] - lon)*60*math.cos(math.radians(lat)))) + 2
        if radius > 250:
            raise ValueError('adsb.fi coverage must fit within 250 nautical miles')
        self.url = f'https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{radius}'

    async def poll(self):
        async with self.http.stream('GET', self.url) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024:
                    raise RetryableError('Oversized adsb.fi response')
            recorded = httpx.Response(response.status_code, content=bytes(body), headers=response.headers)
        self.record_response(recorded)
        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            raise RetryableError(f'adsb.fi HTTP {response.status_code}', retry_delay(response.headers))
        if not response.is_success:
            raise FatalError(f'adsb.fi HTTP {response.status_code}; check provider access')
        return parse_adsbfi(recorded)
