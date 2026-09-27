from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx

from domain_watch.config import RdapConfig
from domain_watch.rate_limit import RateLimiter, RateLimitPolicy
from domain_watch.storage import atomic_write_text

USER_AGENT = "domain-watch/0.1 (+RDAP domain availability monitor)"
BOOTSTRAP_REFRESH_RETRY_SECONDS = 900


class RdapError(RuntimeError):
    """An RDAP query that produced no usable result.

    `response` is the HTTP response that was received but could not be turned into a
    result, or None when no response arrived at all (timeouts, connection and TLS errors).
    """

    def __init__(self, message: str, *, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.response = response


class RdapRateLimited(RdapError):
    def __init__(self, host: str, retry_at: datetime | None) -> None:
        super().__init__(f"RDAP host {host} rate limited the request")
        self.host = host
        self.retry_at = retry_at


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        follow_redirects: bool = False,
    ) -> httpx.Response: ...


class HttpxTransport:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        return self._client.get(
            url,
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )


@dataclass(frozen=True, kw_only=True)
class BootstrapData:
    endpoints: tuple[tuple[str, str], ...]
    fetched_at: datetime
    stale: bool = False
    refresh_error: str | None = None

    def endpoint_for(self, domain: str) -> str:
        tld = domain.rsplit(".", maxsplit=1)[-1].lower()
        endpoint = dict(self.endpoints).get(tld)
        if endpoint is None:
            raise RdapError(f"No RDAP bootstrap service for TLD: {tld}")
        return endpoint


@dataclass(frozen=True, kw_only=True)
class RdapResult:
    domain: str
    registered: bool
    expires_at: datetime | None
    statuses: tuple[str, ...]
    endpoint: str
    queried_at: datetime


class RdapBootstrap:
    def __init__(self, config: RdapConfig, client: HttpTransport) -> None:
        self._config = config
        self._client = client
        self._loaded: BootstrapData | None = None
        self._refresh_after: datetime | None = None

    def load(self, now: datetime) -> BootstrapData:
        if self._loaded is not None and self._refresh_after is not None:
            if now < self._refresh_after:
                return self._loaded
        cached = load_bootstrap_cache(self._config.cache_file)
        if cached and now - cached.fetched_at < timedelta(seconds=self._config.cache_ttl_seconds):
            return self._remember_fresh(cached)
        try:
            response = self._client.get(
                self._config.bootstrap_url,
                headers={"User-Agent": USER_AGENT},
                timeout=self._config.http_timeout_seconds,
            )
            response.raise_for_status()
            fresh = parse_bootstrap(response.json(), now)
            save_bootstrap_cache(self._config.cache_file, fresh)
            return self._remember_fresh(fresh)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as error:
            if cached:
                stale = BootstrapData(
                    endpoints=cached.endpoints,
                    fetched_at=cached.fetched_at,
                    stale=True,
                    refresh_error=str(error),
                )
                self._loaded = stale
                self._refresh_after = now + timedelta(seconds=BOOTSTRAP_REFRESH_RETRY_SECONDS)
                return stale
            raise RdapError(f"Unable to load IANA RDAP bootstrap: {error}") from error

    def _remember_fresh(self, data: BootstrapData) -> BootstrapData:
        self._loaded = data
        self._refresh_after = data.fetched_at + timedelta(seconds=self._config.cache_ttl_seconds)
        return data


class RdapClient:
    def __init__(
        self,
        config: RdapConfig,
        client: HttpTransport,
        *,
        limiter: RateLimiter,
    ) -> None:
        self._config = config
        self._client = client
        self._limiter = limiter

    def query(self, domain: str, endpoint: str, *, now: datetime) -> RdapResult:
        host = endpoint_host(endpoint)
        policy = RateLimitPolicy(self._config.rate_for_host(host))
        self._limiter.wait(f"rdap:{host}", policy)
        url = domain_query_url(endpoint, domain)
        try:
            response = self._client.get(
                url,
                headers={"Accept": "application/rdap+json", "User-Agent": USER_AGENT},
                timeout=self._config.http_timeout_seconds,
                follow_redirects=True,
            )
        except httpx.HTTPError as error:
            raise RdapError(f"RDAP request failed for {domain}: {error}") from error
        return parse_rdap_response(response, domain, endpoint, now=now)


def endpoint_host(endpoint: str) -> str:
    host = urlparse(endpoint).hostname
    if host is None:
        raise RdapError(f"Invalid RDAP endpoint: {endpoint}")
    return host.lower()


def domain_query_url(endpoint: str, domain: str) -> str:
    ascii_domain = domain.encode("idna").decode("ascii")
    return f"{endpoint.rstrip('/')}/domain/{quote(ascii_domain, safe='.-')}"


def parse_bootstrap(payload: object, fetched_at: datetime) -> BootstrapData:
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), list):
        raise ValueError("IANA bootstrap response must contain a services list")
    endpoints: list[tuple[str, str]] = []
    for service in payload["services"]:
        endpoints.extend(parse_bootstrap_service(service))
    if not endpoints:
        raise ValueError("IANA bootstrap response contains no services")
    return BootstrapData(endpoints=tuple(sorted(endpoints)), fetched_at=fetched_at)


def parse_bootstrap_service(service: object) -> list[tuple[str, str]]:
    if not isinstance(service, list) or len(service) != 2:
        raise ValueError("Invalid IANA bootstrap service entry")
    tlds, urls = service
    if not isinstance(tlds, list) or not isinstance(urls, list) or not urls:
        raise ValueError("Invalid IANA bootstrap service mapping")
    endpoint = urls[0]
    if not isinstance(endpoint, str) or urlparse(endpoint).scheme not in {"http", "https"}:
        raise ValueError("RDAP bootstrap endpoint must be a valid HTTP URL")
    if not all(isinstance(tld, str) and tld for tld in tlds):
        raise ValueError("RDAP bootstrap TLDs must be non-empty strings")
    return [(tld.lower(), endpoint) for tld in tlds]


def parse_rdap_response(
    response: httpx.Response,
    domain: str,
    endpoint: str,
    *,
    now: datetime,
) -> RdapResult:
    if response.status_code == 404:
        return RdapResult(
            domain=domain,
            registered=False,
            expires_at=None,
            statuses=(),
            endpoint=endpoint,
            queried_at=now,
        )
    if response.status_code == 429:
        raise RdapRateLimited(endpoint_host(endpoint), parse_retry_after(response, now))
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        raise RdapError(
            f"Invalid RDAP response for {domain}: {error}",
            response=response,
        ) from error
    if not isinstance(payload, dict):
        raise RdapError(f"RDAP response for {domain} must be an object")
    return RdapResult(
        domain=domain,
        registered=True,
        expires_at=parse_expiration(payload),
        statuses=parse_statuses(payload),
        endpoint=endpoint,
        queried_at=now,
    )


def parse_expiration(payload: dict[str, Any]) -> datetime | None:
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise RdapError("RDAP events must be a list")
    for event in events:
        if isinstance(event, dict) and event.get("eventAction") == "expiration":
            return parse_rdap_datetime(event.get("eventDate"))
    return None


def parse_statuses(payload: dict[str, Any]) -> tuple[str, ...]:
    statuses = payload.get("status", [])
    if not isinstance(statuses, list):
        raise RdapError("RDAP status must be a list")
    values = [status.strip() for status in statuses if isinstance(status, str) and status.strip()]
    return tuple(dict.fromkeys(values))


def parse_rdap_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise RdapError(f"Invalid RDAP event date: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_retry_after(response: httpx.Response, now: datetime) -> datetime | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return now + timedelta(seconds=max(0, int(value)))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return parsed.astimezone(UTC)


def load_bootstrap_cache(path: Path) -> BootstrapData | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = parse_rdap_datetime(payload["fetched_at"])
        return parse_bootstrap(payload["payload"], fetched_at)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, RdapError):
        return None


def save_bootstrap_cache(path: Path, data: BootstrapData) -> None:
    services = [[[tld], [endpoint]] for tld, endpoint in data.endpoints]
    payload = json.dumps(
        {"fetched_at": data.fetched_at.isoformat(), "payload": {"services": services}},
        ensure_ascii=False,
    )
    atomic_write_text(path, payload)
