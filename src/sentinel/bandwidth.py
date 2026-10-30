"""Single-worker UTC-day payload budget, reserved before any socket write."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time

log = logging.getLogger(__name__)
PAUSED = 'Live feed paused for today to stay within hosting limits.'


class DailyBudget:
    def __init__(self, limit, path, clock=time.time):
        if limit <= 0:
            raise ValueError('WebSocket daily byte budget must be positive')
        self.limit, self.path, self.clock = limit, Path(path), clock
        self.day, self.used, self.tripped = self.today(), 0, False
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                if type(saved['used']) is not int or saved['used'] < 0 or type(saved['tripped']) is not bool:
                    raise ValueError('Invalid budget counter')
                if saved['day'] == self.day:
                    self.used, self.tripped = saved['used'], saved['tripped']
            except (OSError, ValueError, KeyError, TypeError):
                self.tripped = True
                log.exception('Cannot restore WebSocket budget; feeds paused for today')

    def today(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()

    def refresh(self):
        if self.today() != self.day:
            self.day, self.used, self.tripped = self.today(), 0, False

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'day': self.day, 'used': self.used, 'tripped': self.tripped}))
        temporary.replace(self.path)

    def reserve(self, size):
        self.refresh()
        if self.tripped:
            return False
        if self.used + size > self.limit:
            self.tripped = True
            self.save()
            log.warning('WebSocket daily bandwidth budget tripped: day=%s used=%d limit=%d', self.day, self.used, self.limit)
            return False
        self.used += size
        self.save()
        return True
