from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from sentinel.pipeline import Pipeline
from sentinel.replay import ReplayError, list_sessions, replay
from sentinel.tracks import TrackConfig

FIXTURES = Path(__file__).parent / "fixtures"


def test_recorded_scenario_expected_alerts_and_history():
    frames=[]
    result=replay(FIXTURES/'scenario.jsonl',on_frame=frames.append)
    assert result['tracks'] == []
    assert result['counts'] == {'active':0,'gap':0,'lost':0}
    assert [(a['type'],a['timestamp']) for a in result['alerts']] == [
        ('signal_gap',111),('emergency_squawk',112),('signal_gap',146),('emergency_squawk',148)]
    assert [a['details']['squawk'] for a in result['alerts'] if a['type']=='emergency_squawk'] == ['7700','7600']
    duplicate=next(f for f in frames if f['timestamp']==110)
    assert [o['timestamp'] for o in duplicate['tracks'][0]['history']] == [100]
    before_expiry=next(f for f in frames if f['timestamp']==148)
    assert [o['timestamp'] for o in before_expiry['tracks'][0]['history']] == [113,147,148]
    assert before_expiry['tracks'][0]['state']=='active'
    outage=next(f for f in frames if f['timestamp']==130)
    assert outage['source']['state']=='retrying'
    assert len(outage['alerts'])==2
    assert [a['id'] for a in result['alerts']] == [f'fixture-scenario:{i}' for i in range(1,5)]


def test_live_fixture_regression_and_no_external_dependencies(monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('Replay attempted an external dependency')
    monkeypatch.setattr(httpx.Client,'request',forbidden)
    monkeypatch.setattr(httpx.AsyncClient,'request',forbidden)
    monkeypatch.setattr('sentinel.pipeline.load_airports',forbidden)
    expected=json.loads((FIXTURES/'opensky-live.expected.json').read_text())
    first=replay(FIXTURES/'opensky-live.jsonl')
    assert first==expected
    assert first==replay(FIXTURES/'opensky-live.jsonl')
    assert first['counts']=={'active':166,'gap':12,'lost':2}
    assert sum(len(t['history']) for t in first['tracks'])==333


def test_live_recording_round_trip(tmp_path):
    now=[100.0]
    p=Pipeline(tmp_path/'raw.jsonl',TrackConfig(),clock=lambda:now[0])
    try:
        for stamp,code in [(100,'7700'),(101,'7700'),(102,'1200'),(103,'7600')]:
            now[0]=float(stamp)
            data={'time':stamp,'states':[['abc123','','',stamp,stamp,-123,38,1000,False,100,90,0,None,1000,code,False,0]]}
            p.record_response(httpx.Response(200,json=data));p.accept(data)
        now[0]=170;p.tick()
        p.stopped()
        expected_alerts=[json.loads(line) for line in (tmp_path/'alerts.jsonl').read_text().splitlines()]
        expected_history=[asdict(o) for o in p.manager.tracks[('opensky','abc123')].history]
        result=replay(tmp_path/'raw.jsonl')
        assert result['alerts']==expected_alerts
        assert result['tracks'][0]['history']==expected_history
        assert result['counts']==p.manager.counts()
        assert result['source']==p.source_status
    finally:
        p.close()


def test_sessions_are_isolated_and_latest_is_default(tmp_path):
    source=(FIXTURES/'scenario.jsonl').read_text()
    path=tmp_path/'combined.jsonl'
    path.write_text(source+source.replace('fixture-scenario','second-session'))
    assert len(list_sessions(path))==2
    assert replay(path)['session']=='second-session'
    assert replay(path,session_id='fixture-scenario')==replay(FIXTURES/'scenario.jsonl')
    with pytest.raises(ReplayError,match='not found'):
        replay(path,session_id='missing')


@pytest.mark.parametrize('change,match', [
    (lambda r:r.update(version=999),'version'),
    (lambda r:r.update(timestamp=float('nan')),'numeric'),
    (lambda r:r.update(kind='mystery'),'unknown event'),
    (lambda r:r.update(body_base64='not-base64!'),'Line'),
    (lambda r:r.update(source='unknown'),'unsupported response'),
])
def test_invalid_records_fail_with_line_number(tmp_path,change,match):
    rows=[json.loads(line) for line in (FIXTURES/'scenario.jsonl').read_text().splitlines()]
    change(rows[1])
    path=tmp_path/'bad.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ReplayError,match=match):
        replay(path)


def test_truncated_and_legacy_logs_are_not_silently_guessed(tmp_path):
    path=tmp_path/'bad.jsonl';path.write_text((FIXTURES/'scenario.jsonl').read_text()+'{"kind":')
    with pytest.raises(ReplayError,match='Line'):
        replay(path)
    header=json.loads((FIXTURES/'scenario.jsonl').read_text().splitlines()[0])
    del header['alerts']
    path.write_text(json.dumps(header)+'\n')
    assert not list_sessions(path)[0]['replayable']
    with pytest.raises(ReplayError,match='predates M3'):
        replay(path)


def test_cli_replay_and_output_protection(tmp_path):
    path=tmp_path/'report.json'
    command=[sys.executable,'-m','sentinel','--replay',str(FIXTURES/'scenario.jsonl'),'--output',str(path)]
    assert subprocess.run(command,capture_output=True).returncode==0
    original=path.read_bytes()
    assert len(json.loads(original)['alerts'])==4
    assert subprocess.run(command,capture_output=True).returncode==1
    assert path.read_bytes()==original
    assert subprocess.run(command+['--serve'],capture_output=True).returncode==2
