import time
import httpx
from fastapi.testclient import TestClient
from sentinel.hosting import probe_app,create_hosted_app


def test_probe_is_one_upstream_request_per_process():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'time':int(time.time()),'states':[]})
    with TestClient(probe_app(transport=httpx.MockTransport(handler))) as client:
        for _ in range(20):
            result=client.get('/api/probe').json()
            if result['state']!='starting':break
        assert result['state']=='success' and result['aircraft_count']==0
        for _ in range(10):client.get('/')
        assert len(calls)==1
        assert client.get('/healthz').json()['status']=='ok'


def test_probe_failure_is_not_reported_as_connectivity_success():
    with TestClient(probe_app(transport=httpx.MockTransport(lambda r:httpx.Response(522)))) as client:
        for _ in range(20):
            result=client.get('/api/probe').json()
            if result['state']!='starting':break
        assert result['state']=='failed' and result['upstream_status']==522


def test_hosting_defaults_to_probe(monkeypatch):
    monkeypatch.delenv('SENTINEL_MODE',raising=False)
    assert create_hosted_app().title=='Sentinel connectivity check'


def test_live_factory_uses_one_shared_slow_polling_configuration(monkeypatch):
    import sentinel.hosting as hosting
    from fastapi import FastAPI
    configs=[]
    def capture(config):
        configs.append(config)
        return FastAPI()
    monkeypatch.setenv('SENTINEL_MODE','live')
    monkeypatch.setattr(hosting,'create_app',capture)
    app=hosting.create_hosted_app()
    assert configs[0].poll_interval==300
    assert configs[0].tracks.gap_after==330
    assert configs[0].alerts.source_stale_seconds==330
    assert any(route.path=='/healthz' for route in app.routes)
