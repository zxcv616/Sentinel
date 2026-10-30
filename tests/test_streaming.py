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
    assert delta['meta'] == {}
    assert delta['alerts'] == {'upsert': current['alerts'], 'remove': [], 'order': ['1']}
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


def test_trail_and_alert_changes_round_trip_without_repeating_retained_data():
    current = frame()
    current['tracks'][0]['history'] = [{'lat': i} for i in range(4)]
    current['alerts'] = [{'id': str(i), 'details': 'old'} for i in range(3)]
    encoder = DeltaEncoder()
    messages = [encoder.encode(current)]
    current['tracks'][0]['history'] = [{'lat': i} for i in range(2, 6)]
    current['alerts'] = [{'id': 'new', 'details': 'added'}, {'id': '0', 'details': 'updated'}, current['alerts'][1]]
    delta = encoder.encode(current)
    assert delta['tracks']['update'][0]['history_delta'] == {'drop': 2, 'append': [{'lat': 4}, {'lat': 5}]}
    assert 'history' not in delta['tracks']['update'][0]
    assert [a['id'] for a in delta['alerts']['upsert']] == ['new', '0']
    assert delta['alerts']['remove'] == ['2']
    messages.append(delta)
    # Complete history turnover must also reconstruct correctly.
    current['tracks'][0]['history'] = [{'lat': 99}]
    messages.append(encoder.encode(current))
    script = "const {apply}=require('./src/sentinel/static/stream.js');let x='';process.stdin.on('data',s=>x+=s);process.stdin.on('end',()=>console.log(JSON.stringify(JSON.parse(x).reduce(apply,null))));"
    result = subprocess.run(['node', '-e', script], input=json.dumps(messages), text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {**presentation(current), 'sequence': 2}


def test_session_cap_cannot_be_extended_by_activity(tmp_path):
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport(),
                     session_timeout=.15, idle_timeout=30, push_interval=30)
    with TestClient(app) as client, client.websocket_connect('/ws/tracks') as ws:
        ws.receive_json()
        ws.send_text('activity')
        ws.send_text('ping')
        assert ws.receive_text() == 'pong'
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_text()
        assert error.value.code == 4002


def test_connection_cap_and_slot_release(tmp_path):
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport(), max_connections=1)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/tracks') as first:
            first.receive_json()
            with client.websocket_connect('/ws/tracks') as extra:
                with pytest.raises(WebSocketDisconnect) as error:
                    extra.receive_text()
                assert error.value.code == 4004
        with client.websocket_connect('/ws/tracks') as replacement:
            assert replacement.receive_json()['type'] == 'snapshot'


def test_budget_reservations_restart_utc_rollover_and_logging(tmp_path, caplog):
    from sentinel.bandwidth import DailyBudget
    now = [0]
    path = tmp_path/'budget.json'
    budget = DailyBudget(10, path, clock=lambda: now[0])
    assert budget.reserve(7)
    budget = DailyBudget(10, path, clock=lambda: now[0])
    assert budget.used == 7
    assert not budget.reserve(4)
    assert budget.used == 7
    assert 'budget tripped' in caplog.text
    assert DailyBudget(10, path, clock=lambda: now[0]).tripped
    now[0] = 86400
    assert budget.reserve(10)
    assert budget.used == 10 and not budget.tripped
    assert not budget.reserve(1)


def test_budget_closes_all_clients_rejects_new_and_keeps_page_available(tmp_path):
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport(), push_interval=30)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/tracks') as first, client.websocket_connect('/ws/tracks') as second:
            first.receive_json(); second.receive_json()
            budget = app.state.ws_budget
            budget.limit = budget.used + 3  # app pong would require four bytes
            first.send_text('ping')
            for ws in (first, second):
                with pytest.raises(WebSocketDisconnect) as error:
                    ws.receive_text()
                assert error.value.code == 4003
                assert error.value.reason == 'Live feed paused for today to stay within hosting limits.'
        with client.websocket_connect('/ws/tracks') as rejected:
            with pytest.raises(WebSocketDisconnect) as error:
                rejected.receive_text()
            assert error.value.code == 4003
        assert client.get('/').status_code == 200
        # Simulate the next UTC day; admission resumes without restarting.
        budget.day = '1970-01-01'
        budget.limit = 150000000
        with client.websocket_connect('/ws/tracks') as resumed:
            assert resumed.receive_json()['type'] == 'snapshot'


def test_budget_env_and_oversized_initial_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv('SENTINEL_WS_DAILY_BYTES', '1')
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport())
    with TestClient(app) as client, client.websocket_connect('/ws/tracks') as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_text()
        assert error.value.code == 4003
        assert app.state.ws_budget.used == 0


def test_corrupt_budget_fails_closed_without_breaking_page(tmp_path):
    path = tmp_path/'ws-budget.json'
    path.write_text('broken')
    app = create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport())
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        with client.websocket_connect('/ws/tracks') as ws:
            with pytest.raises(WebSocketDisconnect) as error:
                ws.receive_text()
            assert error.value.code == 4003
