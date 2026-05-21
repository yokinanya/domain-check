from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_INTERVAL_SECONDS = 3600
DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS = 5
DEFAULT_EXPIRY_ACCELERATION_DAYS = 7
DEFAULT_PERIOD = 1
DEFAULT_DOMAIN_CHECK_BIN = "domain-check"


@dataclass(frozen=True)
class WatchConfig:
    secret_id: str
    secret_key: str
    template_id: str
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    near_expiry_interval_seconds: int = DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS
    expiry_acceleration_days: int = DEFAULT_EXPIRY_ACCELERATION_DAYS
    period: int = DEFAULT_PERIOD
    domains: tuple[str, ...] = ()
    domain_check_bin: str = DEFAULT_DOMAIN_CHECK_BIN


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


def read_domains_env(name: str) -> tuple[str, ...]:
    raw_value = require_env(name)
    domains = tuple(domain.strip() for domain in raw_value.split(",") if domain.strip())
    if domains:
        return domains
    raise ValueError(f"{name} must contain at least one domain")


def load_config() -> WatchConfig:
    return WatchConfig(
        secret_id=require_env("TENCENTCLOUD_SECRET_ID"),
        secret_key=require_env("TENCENTCLOUD_SECRET_KEY"),
        template_id=require_env("TENCENT_DOMAIN_TEMPLATE_ID"),
        interval_seconds=read_positive_int_env(
            "DOMAIN_WATCH_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
        ),
        near_expiry_interval_seconds=read_positive_int_env(
            "DOMAIN_NEAR_EXPIRY_INTERVAL_SECONDS",
            DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS,
        ),
        expiry_acceleration_days=read_positive_int_env(
            "DOMAIN_EXPIRY_ACCELERATION_DAYS",
            DEFAULT_EXPIRY_ACCELERATION_DAYS,
        ),
        period=read_positive_int_env("DOMAIN_PERIOD", DEFAULT_PERIOD),
        domains=read_domains_env("DOMAIN_WATCH_DOMAINS"),
        domain_check_bin=os.getenv("DOMAIN_CHECK_BIN", DEFAULT_DOMAIN_CHECK_BIN),
    )
