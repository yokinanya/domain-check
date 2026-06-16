from __future__ import annotations

import signal
import subprocess
import time
from datetime import UTC, datetime

from domain_watch.config import WatchConfig, load_config
from domain_watch.domain_check_cli import CliDomainCheckRunner, DomainCheckRunner
from domain_watch.domain_check_info import DomainCheckResult
from domain_watch.env_loader import load_dotenv
from domain_watch.push_notify import PushNotifier, load_push_notifier
from domain_watch.state import WatchState, init_state, save_state
from domain_watch.tencent_domain import (
    TencentDomainClient,
    TencentDomainResult,
    TencentSdkDomainClient,
)


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
    state: WatchState,
    notifier: PushNotifier | None,
) -> None:
    confirmed_domains = []
    for domain in candidate_domains:
        if not state.is_active(domain):
            continue
        result = client.check_domain(domain, config.period)
        print_domain_result(result)
        notify_tencent_result(notifier, result)
        if result.available:
            confirmed_domains.append(domain)
    if not confirmed_domains:
        return
    domains_to_register = tuple(confirmed_domains)
    response = client.create_domain_batch(domains_to_register, config)
    print_register_response(domains_to_register, response)
    log_id = getattr(response, "LogId", None)
    request_id = getattr(response, "RequestId", None)
    for domain in domains_to_register:
        state.remove(
            domain,
            reason="register_submitted",
            request_id=request_id,
            log_id=str(log_id) if log_id is not None else None,
        )
    save_state(config.state_file, state)
    notify_register_response(notifier, domains_to_register, response)


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
        (
            f"以下域名已提交注册并移出监听列表：{', '.join(domains)}\n"
            f"LogId: {log_id}\n"
            f"RequestId: {request_id}\n"
            "后续请到腾讯云控制台确认订单并完成支付。"
        ),
    )


def watch_once(
    config: WatchConfig,
    runner: DomainCheckRunner,
    client: TencentDomainClient,
    state: WatchState,
    notifier: PushNotifier | None = None,
) -> int:
    remaining_domains = tuple(state.active)
    if not remaining_domains:
        print("No active domains to watch.")
        return config.interval_seconds
    checked_domains = runner.check_domains(remaining_domains)
    notify_domain_status_changes(config, state, checked_domains, notifier)
    next_interval = next_watch_interval(config, checked_domains)
    candidate_domains = tencent_check_candidate_names(checked_domains, datetime.now(UTC))
    if not candidate_domains:
        print(f"No RDAP/WHOIS available candidates: {list(remaining_domains)}")
        return next_interval
    register_available_domains(
        config,
        client,
        candidate_domains,
        state,
        notifier,
    )
    return next_interval


def notify_domain_status_changes(
    config: WatchConfig,
    state: WatchState,
    results: tuple[DomainCheckResult, ...],
    notifier: PushNotifier | None,
) -> None:
    changed_results: list[tuple[DomainCheckResult, tuple[str, ...]]] = []
    state_changed = False
    for result in results:
        previous_statuses = state.update_statuses(result.domain, result.statuses)
        if previous_statuses is None:
            continue
        state_changed = True
        if previous_statuses:
            changed_results.append((result, previous_statuses))
    if state_changed:
        save_state(config.state_file, state)
    for result, previous_statuses in changed_results:
        notify_domain_status_change(notifier, result, previous_statuses)


def notify_domain_status_change(
    notifier: PushNotifier | None,
    result: DomainCheckResult,
    previous_statuses: tuple[str, ...],
) -> None:
    if notifier is None:
        return
    current_statuses = format_statuses(result.statuses)
    notifier.send(
        f"域名状态码更新 {result.domain}",
        (
            f"域名: {result.domain}\n"
            f"原状态码: {format_statuses(previous_statuses)}\n"
            f"新状态码: {current_statuses}\n"
            f"可注册: {'是' if result.available else '否'}\n"
            f"过期时间: {format_expires_at(result)}"
        ),
    )


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
    now = datetime.now(UTC)
    any_expired = any(
        result.expires_at is not None and result.expires_at <= now
        for result in results
    )
    if any_expired:
        return config.expired_interval_seconds
    return config.interval_seconds


def print_domain_check_result(result: DomainCheckResult) -> None:
    expires_at = result.expires_at.isoformat() if result.expires_at else "unknown"
    status = "AVAILABLE" if result.available else "TAKEN"
    print(
        f"domain-check {result.domain} {status} "
        f"expires_at={expires_at} statuses={format_statuses(result.statuses)}"
    )


def format_expires_at(result: DomainCheckResult) -> str:
    return result.expires_at.isoformat() if result.expires_at else "unknown"


def format_statuses(statuses: tuple[str, ...]) -> str:
    return ", ".join(statuses) if statuses else "unknown"


def watch_forever(
    config: WatchConfig,
    runner: DomainCheckRunner,
    client: TencentDomainClient,
    notifier: PushNotifier | None,
) -> None:
    state = init_state(config.state_file, config.domains)
    stop_signal = StopSignal()
    while not stop_signal.received and state.active:
        try:
            next_interval = watch_once(
                config,
                runner,
                client,
                state,
                notifier,
            )
        except subprocess.CalledProcessError as error:
            if stop_signal.received and error.returncode < 0:
                print("domain-check was interrupted by shutdown signal.")
                break
            raise
        stop_signal.wait(next_interval)
    print("Watch loop ended.")


class StopSignal:
    def __init__(self) -> None:
        self.received = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        if self.received:
            return
        self.received = True
        print(f"Received signal {signum}; exiting after current iteration.")

    def wait(self, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not self.received and time.monotonic() < deadline:
            time.sleep(min(1, deadline - time.monotonic()))


def main() -> None:
    load_dotenv()
    config = load_config()
    runner = CliDomainCheckRunner(config.domain_check_bin)
    client = TencentSdkDomainClient(config.secret_id, config.secret_key)
    notifier = load_push_notifier()
    watch_forever(config, runner, client, notifier)


if __name__ == "__main__":
    main()
