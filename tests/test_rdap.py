from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from domain_watch.config import RdapConfig
from domain_watch.rate_limit import RateLimiter
from domain_watch.rdap import (
    BootstrapData,
    RdapBootstrap,
    RdapClient,
    RdapRateLimited,
    domain_query_url,
    parse_bootstrap,
    save_bootstrap_cache,
)

NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
ENDPOINT = "https://rdap.verisign.test/com/v1/"


class FakeHttpClient:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> httpx.Response:
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status: int, payload: object | None = None, **headers: str) -> httpx.Response:
    request = httpx.Request("GET", "https://example.test")
    normalized_headers = {name.replace("_", "-"): value for name, value in headers.items()}
    return httpx.Response(status, json=payload, headers=normalized_headers, request=request)


def bootstrap_payload() -> dict[str, object]:
    return {"services": [[["com"], [ENDPOINT]], [["cc"], ["https://rdap.test/cc/"]]]}


def test_parse_bootstrap_resolves_com_and_cc() -> None:
    data = parse_bootstrap(bootstrap_payload(), NOW)

    assert data.endpoint_for("example.com") == ENDPOINT
    assert data.endpoint_for("target.cc") == "https://rdap.test/cc/"


def test_bootstrap_uses_stale_cache_when_refresh_fails(tmp_path: Path) -> None:
    cache = tmp_path / "bootstrap.json"
    old = BootstrapData(
        endpoints=(("com", ENDPOINT),),
        fetched_at=NOW - timedelta(days=2),
    )
    save_bootstrap_cache(cache, old)
    request = httpx.Request("GET", "https://data.iana.test")
    client = FakeHttpClient([httpx.ConnectError("offline", request=request)])
    config = RdapConfig(cache_file=cache, cache_ttl_seconds=60)

    bootstrap = RdapBootstrap(config, client)
    loaded = bootstrap.load(NOW)
    repeated = bootstrap.load(NOW + timedelta(minutes=1))

    assert loaded.stale is True
    assert loaded.endpoint_for("example.com") == ENDPOINT
    assert "offline" in (loaded.refresh_error or "")
    assert repeated is loaded
    assert len(client.calls) == 1


def test_rdap_parses_expiration_and_statuses() -> None:
    payload = {
        "events": [{"eventAction": "expiration", "eventDate": "2026-09-01T00:00:00Z"}],
        "status": ["client transfer prohibited", "pending delete"],
    }
    client = FakeHttpClient([response(200, payload)])
    limiter = RateLimiter(monotonic=lambda: 0.0, sleep=lambda _seconds: None)

    result = RdapClient(RdapConfig(), client, limiter=limiter).query(
        "example.com", ENDPOINT, now=NOW
    )

    assert result.registered is True
    assert result.expires_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert result.statuses[-1] == "pending delete"
    assert client.calls == [domain_query_url(ENDPOINT, "example.com")]


def test_rdap_404_is_unregistered_candidate() -> None:
    client = FakeHttpClient([response(404)])
    limiter = RateLimiter(monotonic=lambda: 0.0, sleep=lambda _seconds: None)

    result = RdapClient(RdapConfig(), client, limiter=limiter).query("free.com", ENDPOINT, now=NOW)

    assert result.registered is False
    assert result.expires_at is None


def test_rdap_429_exposes_retry_after() -> None:
    client = FakeHttpClient([response(429, Retry_After="120")])
    limiter = RateLimiter(monotonic=lambda: 0.0, sleep=lambda _seconds: None)

    with pytest.raises(RdapRateLimited) as raised:
        RdapClient(RdapConfig(), client, limiter=limiter).query("example.com", ENDPOINT, now=NOW)

    assert raised.value.retry_at == NOW + timedelta(seconds=120)
