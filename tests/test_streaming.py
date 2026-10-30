import copy
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sentinel.streaming import DeltaEncoder, presentation
from sentinel.web import ASSET_VERSION, create_app
from sentinel.config import Config
from test_web import transport


def frame():
    return {'type': 'snapshot', 'timestamp': 100.0, 'tracks': [
        {'source': 'opensky', 'object_id': 'abc', 'state': 'active',
         'position': {'lat': 37.123456789, 'lon': -122.123456789, 'speed': 100.123},
         'history': [{'lat': 37.123456789}]}], 'alerts': [], 'counts': {'active': 1}}


def test_delta_rounding_ignores_noise_without_mutating_raw_state():
    encoder = DeltaEncoder()
    original = frame()
    saved = copy.deepcopy(original)
    first = encoder.encode(original)
    assert first['sequence'] == 0
    assert first['tracks'][0]['position']['lat'] == 37.12346
    assert original == saved
    original['timestamp'] += 1
    original['tracks'][0]['position']['lat'] += 0.00000001
    assert encoder.encode(original) is None
    assert DeltaEncoder().encode(original)['sequence'] == 0


def test_python_deltas_reconstruct_in_actual_browser_reducer():
    encoder = DeltaEncoder()
    current = frame()
    messages = [encoder.encode(current)]
    current['tracks'][0]['state'] = 'gap'
    current['tracks'].append({'source': 'aisstream', 'object_id': 'abc', 'state': 'active'})
    current['counts'] = {'active': 1, 'gap': 1}
    current['alerts'] = [{'id': 'alert:1', 'timestamp': 101.123456}]
    current['timestamp'] = 101
    messages.append(encoder.encode(current))
    assert len(messages[-1]['tracks']['upsert']) == 1
    assert messages[-1]['tracks']['update'] == [{'source': 'opensky', 'object_id': 'abc', 'state': 'gap'}]
    current['tracks'].pop(0)
    current['counts'] = {'active': 1, 'gap': 0}
    current['timestamp'] = 102
    messages.append(encoder.encode(current))
    assert messages[-1]['tracks']['remove'] == ['opensky:abc']
    expected = {**presentation(current), 'sequence': 2}
    script = "const {apply}=require('./src/sentinel/static/stream.js');let x='';process.stdin.on('data',s=>x+=s);process.stdin.on('end',()=>console.log(JSON.stringify(JSON.parse(x).reduce(apply,null))));"
    result = subprocess.run(['node', '-e', script], input=json.dumps(messages), text=True,
                            capture_output=True, check=True, cwd=Path(__file__).parents[1])
    assert json.loads(result.stdout) == expected


def test_metadata_only_delta_and_no_duplicate_alert_delivery():
    encoder = DeltaEncoder()
    current = frame()
    encoder.encode(current)
    current['alerts'] = [{'id': '1', 'type': 'emergency_squawk'}]
    delta = encoder.encode(current)
    assert delta['tracks'] == {'upsert': [], 'update': [], 'remove': []}
    assert delta['meta'] == {'alerts': current['alerts']}
    assert encoder.encode(current) is None


def test_http_compression_and_versioned_cache(tmp_path):
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport())
    with TestClient(app) as client:
        response = client.get('/', headers={'Accept-Encoding': 'gzip'})
        assert response.headers['content-encoding'] == 'gzip'
        assert response.headers['cache-control'] == 'no-cache'
        for name in ('app.js', 'stream.js', 'style.css'):
            url = f'/static/{name}?v={ASSET_VERSION}'
            assert url in response.text
            assert 'immutable' in client.get(url).headers['cache-control']
            assert 'immutable' not in client.get(f'/static/{name}').headers['cache-control']
            assert 'immutable' not in client.get(f'/static/{name}?v=old').headers['cache-control']


def test_no_snapshot_work_without_clients_and_ping_does_not_push(tmp_path, monkeypatch):
    import sentinel.web as web
    calls = []
    original = web.snapshot
    def counted(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(web, 'snapshot', counted)
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport(), push_interval=30)
    with TestClient(app) as client:
        client.get('/api/health')
        assert not calls
        with client.websocket_connect('/ws/tracks') as ws:
            assert ws.receive_json()['type'] == 'snapshot'
            ws.send_text('ping')
            assert ws.receive_text() == 'pong'
            ws.send_text('activity')
            ws.send_text('ping')
            assert ws.receive_text() == 'pong'
            assert len(calls) == 1
        client.get('/api/health')
        assert len(calls) == 1


def test_server_idle_disconnect_even_with_heartbeat(tmp_path):
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport(),
                     idle_timeout=.1, push_interval=30)
    with TestClient(app) as client, client.websocket_connect('/ws/tracks') as ws:
        ws.receive_json()
        ws.send_text('ping')
        assert ws.receive_text() == 'pong'
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_text()
        assert error.value.code == 4001
