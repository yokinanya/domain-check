from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
DEFAULT_BOOTSTRAP_CACHE_FILE = Path("rdap_bootstrap_cache.json")
DEFAULT_BOOTSTRAP_TTL_SECONDS = 86_400
DEFAULT_RDAP_REQUESTS_PER_SECOND = 1.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0
DEFAULT_REDEMPTION_INTERVAL_SECONDS = 21_600
DEFAULT_PENDING_DELETE_INTERVAL_SECONDS = 300
DEFAULT_DROP_INTERVAL_SECONDS = 5
DEFAULT_RETRY_INTERVAL_SECONDS = 900
DEFAULT_REGISTRATION_POLL_SECONDS = 30
DEFAULT_PENDING_DELETE_DAYS = 5.0
DEFAULT_PERIOD = 1
DEFAULT_STATE_FILE = Path("domain_watch_state.json")


@dataclass(frozen=True, kw_only=True)
class RdapConfig:
    bootstrap_url: str = DEFAULT_BOOTSTRAP_URL
    cache_file: Path = DEFAULT_BOOTSTRAP_CACHE_FILE
    cache_ttl_seconds: int = DEFAULT_BOOTSTRAP_TTL_SECONDS
    requests_per_second: float = DEFAULT_RDAP_REQUESTS_PER_SECOND
    host_limits: tuple[tuple[str, float], ...] = ()
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    proxy: str | None = None

    def rate_for_host(self, host: str) -> float:
        return dict(self.host_limits).get(host, self.requests_per_second)


@dataclass(frozen=True, kw_only=True)
class ScheduleConfig:
    redemption_interval_seconds: int = DEFAULT_REDEMPTION_INTERVAL_SECONDS
    pending_delete_interval_seconds: int = DEFAULT_PENDING_DELETE_INTERVAL_SECONDS
    drop_interval_seconds: int = DEFAULT_DROP_INTERVAL_SECONDS
    retry_interval_seconds: int = DEFAULT_RETRY_INTERVAL_SECONDS
    registration_poll_seconds: int = DEFAULT_REGISTRATION_POLL_SECONDS
    default_pending_delete_days: float = DEFAULT_PENDING_DELETE_DAYS
    tld_pending_delete_days: tuple[tuple[str, float], ...] = ()

    def pending_delete_days(self, domain: str) -> float:
        tld = domain.rsplit(".", maxsplit=1)[-1].lower()
        return dict(self.tld_pending_delete_days).get(
            tld,
            self.default_pending_delete_days,
        )


@dataclass(frozen=True, kw_only=True)
class WatchConfig:
    secret_id: str
    secret_key: str
    template_id: str
    domains: tuple[str, ...]
    period: int = DEFAULT_PERIOD
    state_file: Path = DEFAULT_STATE_FILE
    rdap: RdapConfig = RdapConfig()
    schedule: ScheduleConfig = ScheduleConfig()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def read_positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = int(raw_value)
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def read_positive_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = float(raw_value)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def read_domains_env(name: str) -> tuple[str, ...]:
    raw_value = require_env(name)
    normalized = tuple(domain.strip().lower() for domain in raw_value.split(","))
    domains = tuple(dict.fromkeys(domain for domain in normalized if domain))
    if domains:
        return domains
    raise ValueError(f"{name} must contain at least one domain")


def read_number_map_env(name: str) -> tuple[tuple[str, float], ...]:
    raw_value = os.getenv(name, "{}")
    value = json.loads(raw_value)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return tuple(sorted(parse_number_items(name, value)))


def parse_number_items(name: str, value: dict[object, object]) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for key, raw_number in value.items():
        if not isinstance(key, str) or not isinstance(raw_number, int | float):
            raise ValueError(f"{name} keys must be strings and values must be numbers")
        number = float(raw_number)
        if number <= 0:
            raise ValueError(f"{name} values must be positive")
        result.append((key.lower().lstrip("."), number))
    return result


def load_rdap_config() -> RdapConfig:
    return RdapConfig(
        bootstrap_url=os.getenv("RDAP_BOOTSTRAP_URL", DEFAULT_BOOTSTRAP_URL),
        cache_file=Path(os.getenv("RDAP_BOOTSTRAP_CACHE_FILE", DEFAULT_BOOTSTRAP_CACHE_FILE)),
        cache_ttl_seconds=read_positive_int_env(
            "RDAP_BOOTSTRAP_TTL_SECONDS", DEFAULT_BOOTSTRAP_TTL_SECONDS
        ),
        requests_per_second=read_positive_float_env(
            "RDAP_REQUESTS_PER_SECOND", DEFAULT_RDAP_REQUESTS_PER_SECOND
        ),
        host_limits=read_number_map_env("RDAP_HOST_LIMITS_JSON"),
        http_timeout_seconds=read_positive_float_env(
            "RDAP_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS
        ),
        proxy=os.getenv("DOMAIN_WATCH_PROXY") or None,
    )


def load_schedule_config() -> ScheduleConfig:
    return ScheduleConfig(
        redemption_interval_seconds=read_positive_int_env(
            "DOMAIN_WATCH_REDEMPTION_INTERVAL_SECONDS",
            DEFAULT_REDEMPTION_INTERVAL_SECONDS,
        ),
        pending_delete_interval_seconds=read_positive_int_env(
            "DOMAIN_WATCH_PENDING_DELETE_INTERVAL_SECONDS",
            DEFAULT_PENDING_DELETE_INTERVAL_SECONDS,
        ),
        drop_interval_seconds=read_positive_int_env(
            "DOMAIN_WATCH_DROP_INTERVAL_SECONDS", DEFAULT_DROP_INTERVAL_SECONDS
        ),
        retry_interval_seconds=read_positive_int_env(
            "DOMAIN_WATCH_RETRY_INTERVAL_SECONDS", DEFAULT_RETRY_INTERVAL_SECONDS
        ),
        registration_poll_seconds=read_positive_int_env(
            "DOMAIN_WATCH_REGISTRATION_POLL_SECONDS",
            DEFAULT_REGISTRATION_POLL_SECONDS,
        ),
        tld_pending_delete_days=read_number_map_env("DOMAIN_WATCH_TLD_PENDING_DELETE_DAYS_JSON"),
    )


def load_config() -> WatchConfig:
    return WatchConfig(
        secret_id=require_env("TENCENTCLOUD_SECRET_ID"),
        secret_key=require_env("TENCENTCLOUD_SECRET_KEY"),
        template_id=require_env("TENCENT_DOMAIN_TEMPLATE_ID"),
        domains=read_domains_env("DOMAIN_WATCH_DOMAINS"),
        period=read_positive_int_env("DOMAIN_PERIOD", DEFAULT_PERIOD),
        state_file=Path(os.getenv("DOMAIN_WATCH_STATE_FILE", DEFAULT_STATE_FILE)),
        rdap=load_rdap_config(),
        schedule=load_schedule_config(),
    )
