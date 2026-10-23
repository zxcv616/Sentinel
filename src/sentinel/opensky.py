"""Async OpenSky client and bounded, server-aware retry loop."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import random
import time

import httpx

from .config import Config

STATES_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
log = logging.getLogger(__name__)


class FatalError(Exception):
    """Configuration or authorization failure requiring user intervention."""


class RetryableError(Exception):
    def __init__(self, message, retry_after=0.0):
        super().__init__(message)
        self.retry_after = retry_after


def retry_delay(headers: httpx.Headers) -> float:
    delays = [0.0]
    for name in ("X-Rate-Limit-Retry-After-Seconds", "Retry-After"):
        value = headers.get(name)
        if value is None:
            continue
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = parsedate_to_datetime(value).timestamp() - datetime.now(timezone.utc).timestamp()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(delay):
            delays.append(delay)
    return max(delays)


def check_response(response):
    if response.status_code in (408, 425, 429) or response.status_code >= 500:
        raise RetryableError(f"OpenSky HTTP {response.status_code}", retry_delay(response.headers))
    if not response.is_success:
        raise FatalError(f"OpenSky HTTP {response.status_code}; check credentials and API access")


class OpenSkyClient:
    def __init__(self, http: httpx.AsyncClient, config: Config, client_id=None, client_secret=None, monotonic=time.monotonic, record_response=None):
        self.record_response = record_response
        if bool(client_id) != bool(client_secret):
            raise FatalError("Set both OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET, or neither")
        self.http, self.config = http, config
        self.client_id, self.client_secret = client_id, client_secret
        self.monotonic = monotonic
        self.token = None
        self.expires_at = 0.0

    async def headers(self):
        if not self.client_id:
            return {}
        if self.token is None or self.monotonic() >= self.expires_at:
            response = await self.http.post(TOKEN_URL, data={
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            check_response(response)
            try:
                data = response.json()
                token = data["access_token"]
                expires = float(data.get("expires_in", 1800))
                if not isinstance(token, str) or not token or not math.isfinite(expires) or expires <= 0:
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError):
                raise RetryableError("Malformed OpenSky token response") from None
            self.token = token
            self.expires_at = self.monotonic() + max(0, expires - 30)
        return {"Authorization": f"Bearer {self.token}"}

    async def poll(self):
        # Retry an expired/revoked bearer token once; persistent 401 is fatal.
        for attempt in range(2):
            response = await self.http.get(STATES_URL, params=self.config.bounds, headers=await self.headers())
            if self.record_response is not None:
                self.record_response(response)
            if response.status_code == 401 and self.client_id and attempt == 0:
                self.token = None
                continue
            check_response(response)
            break
        return parse_states(response)


def parse_states(response):
    """Shared live/replay payload validation."""
    try:
        data = response.json()
        if not isinstance(data, dict) or type(data.get("time")) is not int or "states" not in data:
            raise ValueError
        states = data["states"]
        if states is not None and not isinstance(states, list):
            raise ValueError
    except (ValueError, TypeError):
        raise RetryableError("Malformed OpenSky state response") from None
    return data


async def run_poller(client, *, once=False, sleep=asyncio.sleep, monotonic=time.monotonic, jitter=random.uniform, pipeline=None):
    config = client.config
    backoff = config.backoff_initial
    while True:
        started = monotonic()
        try:
            data = await client.poll()
        except (RetryableError, httpx.RequestError) as exc:
            # Never include response bodies, credentials or transport details in logs.
            reason = str(exc) if isinstance(exc, RetryableError) else type(exc).__name__
            delay = max(getattr(exc, "retry_after", 0), min(config.backoff_max, backoff * jitter(1, 1.2)))
            log.warning("%s; retry in %.1fs", reason, delay)
            if pipeline is not None:
                pipeline.failed(f"{reason}; retry in {delay:.0f}s")
            if once:
                return 1
            backoff = min(config.backoff_max, backoff * 2)
        else:
            summary = ""
            if pipeline is not None:
                accepted, rejected = pipeline.accept(data)
                counts = pipeline.manager.counts()
                summary = f" observations_added={accepted} rejected_rows={rejected} tracks={counts}"
            received = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"{received} source_time={data['time']} aircraft_count={len(data['states'] or [])}{summary}", flush=True)
            if once:
                return 0
            backoff = config.backoff_initial
            delay = max(0, config.poll_interval - (monotonic() - started))
        await sleep(delay)
