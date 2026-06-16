from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INTERVAL_SECONDS = 86_400
DEFAULT_EXPIRED_INTERVAL_SECONDS = 86_400
DEFAULT_PERIOD = 1
DEFAULT_DOMAIN_CHECK_BIN = "domain-check"
DEFAULT_STATE_FILE = Path("domain_watch_state.json")


@dataclass(frozen=True)
class WatchConfig:
    secret_id: str
    secret_key: str
    template_id: str
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    expired_interval_seconds: int = DEFAULT_EXPIRED_INTERVAL_SECONDS
    period: int = DEFAULT_PERIOD
    domains: tuple[str, ...] = ()
    domain_check_bin: str = DEFAULT_DOMAIN_CHECK_BIN
    state_file: Path = DEFAULT_STATE_FILE


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


def load_state_file_env(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return Path(raw_value)


def load_config() -> WatchConfig:
    return WatchConfig(
        secret_id=require_env("TENCENTCLOUD_SECRET_ID"),
        secret_key=require_env("TENCENTCLOUD_SECRET_KEY"),
        template_id=require_env("TENCENT_DOMAIN_TEMPLATE_ID"),
        interval_seconds=read_positive_int_env(
            "DOMAIN_WATCH_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
        ),
        expired_interval_seconds=read_positive_int_env(
            "DOMAIN_WATCH_EXPIRED_INTERVAL_SECONDS",
            DEFAULT_EXPIRED_INTERVAL_SECONDS,
        ),
        period=read_positive_int_env("DOMAIN_PERIOD", DEFAULT_PERIOD),
        domains=read_domains_env("DOMAIN_WATCH_DOMAINS"),
        domain_check_bin=os.getenv("DOMAIN_CHECK_BIN", DEFAULT_DOMAIN_CHECK_BIN),
        state_file=load_state_file_env(
            "DOMAIN_WATCH_STATE_FILE",
            DEFAULT_STATE_FILE,
        ),
    )
