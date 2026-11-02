from datetime import datetime
import json
import re
import logging

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from sentinel.config import Config
from sentinel.web import create_app
from sentinel.visits import Visits, referrer_label, client_type, AccessPrivacyFilter, PACIFIC
from test_web import transport


def app_for(tmp_path, **kwargs):
    return create_app(Config(raw_log_path=str(tmp_path/'raw.jsonl')), transport=transport(), **kwargs)


def test_referrers_are_coarse_and_never_retain_private_urls():
    assert referrer_label('https://www.linkedin.com/in/private-person?token=secret') == 'linkedin.com'
    assert referrer_label('https://private-person.example.com/resume.pdf?email=x') == 'pdf_document'
    assert referrer_label('https://192.0.2.4/private?token=secret') == 'other_website'
    assert referrer_label('https://linkedin.com.evil.example/path') == 'other_website'
    assert referrer_label('') == 'direct_or_unavailable'
    assert referrer_label('file:///Users/name/resume.pdf') == 'unavailable'
    assert referrer_label('https://[invalid') == 'unavailable'


def test_coarse_user_agent_types():
    assert client_type('Mozilla/5.0 Chrome/123') == 'desktop_browser'
    assert client_type('Mozilla/5.0 iPhone Safari/123') == 'mobile_browser'
    assert client_type('Mozilla/5.0 compatible Googlebot') == 'known_bot_crawler'
    assert client_type('curl/8.0') == 'other_client'


def test_signed_referrer_and_pacific_dst(tmp_path):
    visits = Visits(tmp_path/'visits.json')
    assert visits.read_referrer(visits.sign_referrer('linkedin.com')) == 'linkedin.com'
    assert visits.read_referrer('linkedin.com.forged') == 'direct_or_unavailable'
    assert visits.read_referrer('é.forged') == 'direct_or_unavailable'
    assert datetime(2026, 7, 1, tzinfo=PACIFIC).utcoffset().total_seconds() == -7*3600
    assert datetime(2026, 11, 2, tzinfo=PACIFIC).utcoffset().total_seconds() == -8*3600


def test_admin_auth_is_fail_closed_and_never_uses_url_tokens(tmp_path, monkeypatch):
    monkeypatch.delenv('SENTINEL_ADMIN_TOKEN', raising=False)
    with TestClient(app_for(tmp_path)) as client:
        assert client.get('/admin/visits').status_code == 200  # Login shell contains no analytics.
        assert client.get('/admin/visits/data').status_code == 503
        monkeypatch.setenv('SENTINEL_ADMIN_TOKEN', 'test-private-token')
        for headers in ({}, {'Authorization': 'Bearer wrong'}):
            response = client.get('/admin/visits/data?token=test-private-token', headers=headers)
            assert response.status_code == 401
            assert 'no-store' in response.headers['cache-control']
        response = client.get('/admin/visits/data', headers={'Authorization': 'Bearer test-private-token'})
        assert response.status_code == 200
        assert response.json()['sessions'] == []
        assert 'test-private-token' not in client.get('/admin/visits').text


@pytest.mark.parametrize('code,reason', [(1000, 'normal_close'), (4005, 'hidden_tab'), (4001, 'idle_timeout')])
def test_session_attribution_bytes_and_disconnect_reason(tmp_path, code, reason):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        page = client.get('/', headers={'Referer': 'https://www.linkedin.com/in/private?token=secret'})
        hint = re.search(r'data-visit-referrer="([^"]+)"', page.text).group(1)
        with client.websocket_connect('/ws/tracks?ref='+hint, headers={'User-Agent': 'Mozilla/5.0 iPhone', 'X-Forwarded-For': '192.0.2.123'}) as ws:
            raw = ws.receive_text()
            ws.close(code=code)
            # A round trip lets the server process the queued disconnect.
            client.get('/api/health')
        record = app.state.visits.report()['sessions'][0]
        assert record['end_reason'] == reason
        assert record['referrer'] == 'linkedin.com'
        assert record['client_type'] == 'mobile_browser'
        assert record['bytes_sent'] == len(raw.encode())
        assert record['duration_seconds'] >= 0
        assert datetime.fromisoformat(record['started_at']).utcoffset() is not None
        saved = (tmp_path/'visits.json').read_text()
        for private in ('192.0.2.123', 'private?', 'secret', 'Mozilla', 'iPhone'):
            assert private not in saved


@pytest.mark.parametrize('kwargs,reason', [({'idle_timeout': .03}, 'idle_timeout'),
                                          ({'session_timeout': .03}, 'session_cap'),
                                          ({'daily_budget': 1}, 'daily_budget'),
                                          ({'max_connections': 0}, 'connection_limit')])
def test_server_end_reasons(tmp_path, kwargs, reason):
    app = app_for(tmp_path, push_interval=30, **kwargs)
    with TestClient(app) as client, client.websocket_connect('/ws/tracks') as ws:
        try:
            while True:
                ws.receive_text()
        except WebSocketDisconnect:
            pass
    assert app.state.visits.report()['sessions'][0]['end_reason'] == reason


def test_retention_daily_totals_restore_and_structured_logs(tmp_path, monkeypatch):
    emitted = []
    monkeypatch.setattr('sentinel.visits.logger.info', emitted.append)
    visits = Visits(tmp_path/'visits.json')
    for _ in range(55):
        record, start = visits.start({})
        record['referrer'] = 'direct_or_unavailable'
        visits.finish(record, start, 10, 'normal_close')
    assert len(visits.report()['sessions']) == 50
    daily = next(iter(visits.report()['daily'].values()))
    assert daily['sessions'] == 55 and daily['bytes_sent'] == 550
    assert len(emitted) == 55
    assert json.loads(emitted[0])['event'] == 'visit_session'
    assert Visits(tmp_path/'visits.json').report() == visits.report()


def test_storage_failure_does_not_break_logging(tmp_path, monkeypatch):
    path = tmp_path/'not-a-directory'
    path.write_text('x')
    visits = Visits(path/'visits.json')
    emitted = []
    monkeypatch.setattr('sentinel.visits.logger.info', emitted.append)
    record, start = visits.start({})
    visits.finish(record, start, 10, 'normal_close')
    assert len(emitted) == 1
    assert len(visits.report()['sessions']) == 1


def test_uvicorn_address_access_lines_are_suppressed():
    f = AccessPrivacyFilter()
    assert not f.filter(logging.LogRecord('uvicorn.access', 20, '', 1, '%s request', ('192.0.2.1',), None))
    assert not f.filter(logging.LogRecord('uvicorn.error', 20, '', 1, '%s - "WebSocket %s" [accepted]', ('192.0.2.1', '/ws/tracks'), None))
    assert f.filter(logging.LogRecord('uvicorn.error', 40, '', 1, 'Server failed', (), None))
