import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from sentinel.config import Config, load_config
from sentinel.opensky import FatalError, OpenSkyClient, RetryableError, retry_delay, run_poller


def run(coro):
    return asyncio.run(coro)


def payload(count=2):
    return {"time": 1700000000, "states": [["abc123"] + [None] * 16 for _ in range(count)]}


@pytest.mark.parametrize("states,count", [(payload()["states"], 2), ([], 0), (None, 0)])
def test_bounds_and_count(states, count, capsys):
    async def scenario():
        def handler(request):
            assert dict(request.url.params) == {"lamin": "37.0", "lamax": "39.0", "lomin": "-123.0", "lomax": "-121.0"}
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"time": 1700000000, "states": states})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert await run_poller(OpenSkyClient(http, Config()), once=True) == 0
    run(scenario())
    assert f"aircraft_count={count}" in capsys.readouterr().out


@pytest.mark.parametrize("body", [{}, {"time": 1, "states": {}}, {"time": True, "states": []}])
def test_bad_payload(body):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as http:
            with pytest.raises(RetryableError):
                await OpenSkyClient(http, Config()).poll()
    run(scenario())


def test_recovery_backoff_cap_server_delay_and_reset(capsys):
    async def scenario():
        responses = iter([503, 503, 429, 200, 503, 200])
        waits = []
        def handler(request):
            status = next(responses)
            return httpx.Response(status, json=payload(), headers={"X-Rate-Limit-Retry-After-Seconds": "120"} if status == 429 else {})
        async def sleep(delay):
            waits.append(delay)
            if len(waits) == 6:
                raise asyncio.CancelledError
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            config = Config(backoff_initial=10, backoff_max=20)
            with pytest.raises(asyncio.CancelledError):
                await run_poller(OpenSkyClient(http, config), sleep=sleep, monotonic=lambda: 0, jitter=lambda a,b: 1)
        assert waits == [10, 20, 120, 15, 10, 15]
    run(scenario())
    assert capsys.readouterr().out.count("aircraft_count=2") == 2


def test_timeout_and_invalid_json_recover():
    async def scenario():
        calls = 0
        waits = []
        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("timeout", request=request)
            if calls == 2:
                return httpx.Response(200, text="not json")
            return httpx.Response(200, json=payload())
        async def sleep(delay):
            waits.append(delay)
            if len(waits) == 3:
                raise asyncio.CancelledError
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(asyncio.CancelledError):
                await run_poller(OpenSkyClient(http, Config()), sleep=sleep, jitter=lambda a,b: 1, monotonic=lambda: 0)
        assert waits == [15, 30, 15]
    run(scenario())


def test_oauth_refresh_and_reuse():
    async def scenario():
        tokens = 0
        states = 0
        now = [0]
        def handler(request):
            nonlocal tokens, states
            if request.method == "POST":
                tokens += 1
                assert b"grant_type=client_credentials" in request.content
                return httpx.Response(200, json={"access_token": f"token{tokens}", "expires_in": 100})
            states += 1
            assert request.headers["Authorization"] == f"Bearer token{tokens}"
            return httpx.Response(401 if states == 1 else 200, json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = OpenSkyClient(http, Config(), "id", "secret", monotonic=lambda: now[0])
            await client.poll()
            await client.poll()
            assert tokens == 2
            now[0] = 71
            await client.poll()
            assert tokens == 3
    run(scenario())


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_http_error(status):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as http:
            with pytest.raises(FatalError):
                await OpenSkyClient(http, Config()).poll()
    run(scenario())


def test_once_failure_does_not_sleep():
    async def scenario():
        async def sleep(delay):
            pytest.fail("once mode should not sleep")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as http:
            assert await run_poller(OpenSkyClient(http, Config()), once=True, sleep=sleep) == 1
    run(scenario())


def test_retry_headers():
    assert retry_delay(httpx.Headers({"Retry-After": "50", "X-Rate-Limit-Retry-After-Seconds": "100"})) == 100
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60))
    assert 58 <= retry_delay(httpx.Headers({"Retry-After": date})) <= 60
    for value in ("invalid", "nan", "inf", "-1"):
        assert retry_delay(httpx.Headers({"Retry-After": value})) == 0


@pytest.mark.parametrize("kwargs", [{"lamin": 40}, {"lomax": -190}, {"poll_interval": 0}, {"timeout": float("nan")}, {"backoff_max": 1}, {"timeout": True}])
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)


def test_config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[opensky]\npoll_interval = 30\n')
    assert load_config(path).poll_interval == 30
    path.write_text('[opensky]\npoll_intervl = 30\n')
    with pytest.raises(ValueError, match="Unknown"):
        load_config(path)
