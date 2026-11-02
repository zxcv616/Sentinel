"""Coarse session analytics: no addresses, raw headers, or visitor identifiers."""
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import hmac
import json
import logging
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlsplit

PACIFIC = ZoneInfo('America/Los_Angeles')
logger = logging.getLogger('sentinel.visits')
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def referrer_label(raw):
    if not raw:
        return 'direct_or_unavailable'
    try:
        url = urlsplit(raw[:4096])
        if url.scheme not in ('https', 'http'):
            return 'unavailable'
        if url.path.lower().endswith('.pdf'):
            return 'pdf_document'
        host = (url.hostname or '').lower()
        # Allowlisted labels avoid storing private subdomains or arbitrary PII.
        for domain in ('linkedin.com', 'github.com', 'google.com', 'bing.com', 'duckduckgo.com', 't.co'):
            if host == domain or host.endswith('.'+domain):
                return domain
        return 'other_website'
    except ValueError:
        return 'unavailable'


def client_type(raw):
    ua = raw[:4096].lower()
    if any(word in ua for word in ('bot', 'crawler', 'spider', 'slurp', 'facebookexternalhit')):
        return 'known_bot_crawler'
    if any(word in ua for word in ('android', 'iphone', 'ipad', 'mobile')):
        return 'mobile_browser'
    if any(word in ua for word in ('mozilla/', 'chrome/', 'safari/', 'firefox/', 'edg/')):
        return 'desktop_browser'
    return 'other_client'


class Visits:
    def __init__(self, path):
        self.path = Path(path)
        self.secret = secrets.token_bytes(32)
        self.sessions = deque(maxlen=50)
        self.daily = {}
        try:
            saved = json.loads(self.path.read_text())
            if not isinstance(saved['sessions'], list) or not isinstance(saved['daily'], dict):
                raise ValueError('Invalid visit store')
            for row in saved['sessions']:
                if not isinstance(row, dict) or not all(k in row for k in
                        ('started_at', 'day', 'duration_seconds', 'bytes_sent', 'end_reason', 'referrer', 'client_type')):
                    raise ValueError('Invalid visit record')
            for totals in saved['daily'].values():
                if not isinstance(totals, dict) or not all(isinstance(totals.get(k), (int, float)) for k in
                        ('sessions', 'bytes_sent', 'duration_seconds')):
                    raise ValueError('Invalid visit totals')
            self.sessions.extend(saved['sessions'][-50:])
            self.daily = dict(sorted(saved['daily'].items())[-90:])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning('{"event":"visit_storage_unavailable"}')

    def sign_referrer(self, label):
        signature = hmac.new(self.secret, label.encode(), hashlib.sha256).hexdigest()
        return label+'.'+signature

    def read_referrer(self, value):
        label, _, _ = value.rpartition('.')
        if value and hmac.compare_digest(self.sign_referrer(label).encode(), value.encode()):
            return label
        return 'direct_or_unavailable'

    def start(self, headers):
        # Never inspect client addresses, forwarding headers, or URL query strings.
        now = datetime.now(PACIFIC)
        record = {'event': 'visit_session', 'started_at': now.isoformat(),
                  'day': now.date().isoformat(), 'client_type': client_type(headers.get('user-agent', ''))}
        return record, time.monotonic()

    def finish(self, record, started, sent, reason):
        record = {**record, 'duration_seconds': round(max(0, time.monotonic()-started), 3),
                  'bytes_sent': sent, 'end_reason': reason}
        logger.info(json.dumps(record, separators=(',', ':')))
        self.sessions.append(record)
        day = self.daily.setdefault(record['day'], {'sessions': 0, 'bytes_sent': 0, 'duration_seconds': 0})
        day['sessions'] += 1
        day['bytes_sent'] += sent
        day['duration_seconds'] = round(day['duration_seconds']+record['duration_seconds'], 3)
        self.daily = dict(sorted(self.daily.items())[-90:])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'sessions': list(self.sessions), 'daily': self.daily}))
            temporary.replace(self.path)
        except OSError:
            logger.warning('{"event":"visit_storage_write_failed"}')

    def report(self):
        return {'timezone': 'America/Los_Angeles', 'sessions': list(reversed(self.sessions)),
                'daily': dict(sorted(self.daily.items(), reverse=True))}


ADMIN_HTML = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Sentinel visits</title><style>body{font:16px system-ui;background:#111b21;color:#e4ecee;max-width:1100px;margin:40px auto;padding:20px}input,button{font:inherit;padding:8px}table{border-collapse:collapse;width:100%;margin:20px 0}td,th{text-align:left;border-bottom:1px solid #42515b;padding:8px}small{color:#aebfc8}</style>
<h1>Sentinel visits</h1><p>Last 50 completed sessions and daily totals (Pacific time).</p>
<small>Totals are assigned to the session start day. Reconnects count as separate sessions, not unique people. Local history may reset on redeploy.</small>
<form id="login"><p><label>Admin token <input id="token" type="password" autocomplete="off" required></label> <button>Load / refresh</button></p></form>
<p id="status"></p><div id="report"></div>
<script>
document.getElementById('login').onsubmit=async event=>{
 event.preventDefault();const status=document.getElementById('status'), root=document.getElementById('report');root.replaceChildren();status.textContent='Loading…';
 try{const response=await fetch('/admin/visits/data',{headers:{Authorization:'Bearer '+document.getElementById('token').value},cache:'no-store'});
 if(!response.ok)throw new Error(response.status===503?'Set SENTINEL_ADMIN_TOKEN on the server first.':'Access denied or service unavailable.');
 const data=await response.json();status.textContent='Updated '+new Date().toLocaleTimeString();
 function table(title,headers,rows){const h=document.createElement('h2');h.textContent=title;root.append(h);const t=document.createElement('table');for(const row of [headers,...rows]){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=String(value);tr.append(td);}t.append(tr);}root.append(t);}
 table('Daily totals',['Day (Pacific)','Sessions','Bytes','Duration (seconds)'],Object.entries(data.daily).map(([day,v])=>[day,v.sessions,v.bytes_sent,v.duration_seconds]));
 table('Recent sessions',['Started (Pacific)','Seconds','Bytes','Ended by','Referrer','Client'],data.sessions.map(s=>[s.started_at,s.duration_seconds,s.bytes_sent,s.end_reason,s.referrer,s.client_type]));
 }catch(error){status.textContent=error.message;}
};
</script></html>'''


class AccessPrivacyFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn's ordinary HTTP/WS access lines contain client addresses.
        return record.name != 'uvicorn.access' and '"WebSocket %s"' not in str(record.msg)


privacy_filter = AccessPrivacyFilter()


def protect_access_logs():
    for name in ('uvicorn.access', 'uvicorn.error'):
        logging.getLogger(name).addFilter(privacy_filter)
