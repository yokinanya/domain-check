from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from domain_check_info import DomainCheckResult, parse_domain_check_record
from env_loader import load_dotenv
from push_notify import PushNotifier, load_push_notifier
from tencent_domain import TencentDomainClient, TencentDomainResult, TencentSdkDomainClient

DEFAULT_INTERVAL_SECONDS = 3600
DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS = 5
DEFAULT_EXPIRY_ACCELERATION_DAYS = 7
DEFAULT_PERIOD = 1
DOMAIN_CHECK_COMMAND = ("domain-check", "--info", "--json", "--yes", "--batch")


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


class DomainCheckRunner(Protocol):
    def check_domains(self, domains: tuple[str, ...]) -> tuple[DomainCheckResult, ...]: ...


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
    )


class CliDomainCheckRunner:
    def __init__(self, command: tuple[str, ...] = DOMAIN_CHECK_COMMAND) -> None:
        executable = command[0]
        if shutil.which(executable) is None:
            raise RuntimeError(
                f"Missing required CLI: {executable}. Install it with: cargo install domain-check"
            )
        self._command = command

    def check_domains(self, domains: tuple[str, ...]) -> tuple[DomainCheckResult, ...]:
        command = [*self._command, *domains]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        records = json.loads(result.stdout)
        if not isinstance(records, list):
            raise ValueError("domain-check JSON output must be a list")
        return tuple(parse_domain_check_record(record) for record in records)


def print_domain_result(result: TencentDomainResult) -> None:
    status = "AVAILABLE" if result.available else "TAKEN"
    print(
        f"{result.domain} {status} "
        f"reason={result.reason!r} premium={result.premium} "
        f"black_word={result.black_word} price={result.price} "
        f"real_price={result.real_price} request_id={result.request_id}"
    )


def register_available_domains(
    config: WatchConfig,
    client: TencentDomainClient,
    candidate_domains: tuple[str, ...],
    submitted_domains: frozenset[str],
    notifier: PushNotifier | None,
) -> frozenset[str]:
    confirmed_domains = []
    for domain in candidate_domains:
        if domain in submitted_domains:
            continue
        result = client.check_domain(domain, config.period)
        print_domain_result(result)
        notify_tencent_result(notifier, result)
        if result.available:
            confirmed_domains.append(domain)
    if not confirmed_domains:
        return submitted_domains
    domains_to_register = tuple(confirmed_domains)
    response = client.create_domain_batch(domains_to_register, config)
    print_register_response(domains_to_register, response)
    notify_register_response(notifier, domains_to_register, response)
    return submitted_domains.union(domains_to_register)


def print_register_response(domains: tuple[str, ...], response: object) -> None:
    log_id = getattr(response, "LogId", None)
    request_id = getattr(response, "RequestId", None)
    print(f"REGISTER_SUBMITTED domains={list(domains)} log_id={log_id} request_id={request_id}")


def notify_tencent_result(
    notifier: PushNotifier | None,
    result: TencentDomainResult,
) -> None:
    if notifier is None:
        return
    status = "可注册" if result.available else "不可注册"
    notifier.send(
        f"腾讯云查询 {result.domain} {status}",
        (
            f"域名: {result.domain}\n"
            f"状态: {status}\n"
            f"原因: {result.reason}\n"
            f"溢价词: {result.premium}\n"
            f"敏感词: {result.black_word}\n"
            f"价格: {result.price}\n"
            f"真实价格: {result.real_price}\n"
            f"RequestId: {result.request_id}"
        ),
    )


def notify_register_response(
    notifier: PushNotifier | None,
    domains: tuple[str, ...],
    response: object,
) -> None:
    if notifier is None:
        return
    log_id = getattr(response, "LogId", None)
    request_id = getattr(response, "RequestId", None)
    notifier.send(
        "注册任务已提交",
        f"域名: {', '.join(domains)}\nLogId: {log_id}\nRequestId: {request_id}",
    )


def watch_once(
    config: WatchConfig,
    runner: DomainCheckRunner,
    client: TencentDomainClient,
    submitted_domains: frozenset[str],
    notifier: PushNotifier | None = None,
) -> tuple[frozenset[str], int]:
    remaining_domains = tuple(
        domain for domain in config.domains if domain not in submitted_domains
    )
    if not remaining_domains:
        print("All target domains were already submitted in this process.")
        return submitted_domains, config.interval_seconds
    checked_domains = runner.check_domains(remaining_domains)
    next_interval = next_watch_interval(config, checked_domains)
    candidate_domains = tencent_check_candidate_names(checked_domains, datetime.now(UTC))
    if not candidate_domains:
        print(f"No RDAP/WHOIS available candidates: {list(remaining_domains)}")
        return submitted_domains, next_interval
    submitted = register_available_domains(
        config,
        client,
        candidate_domains,
        submitted_domains,
        notifier,
    )
    return submitted, next_interval


def tencent_check_candidate_names(
    results: tuple[DomainCheckResult, ...],
    now: datetime,
) -> tuple[str, ...]:
    return tuple(
        result.domain
        for result in results
        if result.available and not has_future_expiration(result, now)
    )


def has_future_expiration(result: DomainCheckResult, now: datetime) -> bool:
    return result.expires_at is not None and result.expires_at > now


def next_watch_interval(
    config: WatchConfig,
    results: tuple[DomainCheckResult, ...],
) -> int:
    threshold = datetime.now(UTC) + timedelta(days=config.expiry_acceleration_days)
    for result in results:
        print_domain_check_result(result)
        if result.expires_at is not None and result.expires_at <= threshold:
            return config.near_expiry_interval_seconds
    return config.interval_seconds


def print_domain_check_result(result: DomainCheckResult) -> None:
    expires_at = result.expires_at.isoformat() if result.expires_at else "unknown"
    status = "AVAILABLE" if result.available else "TAKEN"
    print(f"domain-check {result.domain} {status} expires_at={expires_at}")


def watch_forever(
    config: WatchConfig,
    runner: DomainCheckRunner,
    client: TencentDomainClient,
    notifier: PushNotifier | None,
) -> None:
    submitted_domains: frozenset[str] = frozenset()
    while True:
        submitted_domains, next_interval = watch_once(
            config,
            runner,
            client,
            submitted_domains,
            notifier,
        )
        time.sleep(next_interval)


def main() -> None:
    load_dotenv()
    config = load_config()
    runner = CliDomainCheckRunner()
    client = TencentSdkDomainClient(config.secret_id, config.secret_key)
    notifier = load_push_notifier()
    watch_forever(config, runner, client, notifier)


if __name__ == "__main__":
    main()
