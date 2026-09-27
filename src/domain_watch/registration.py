from __future__ import annotations

from datetime import datetime, timedelta

from domain_watch.config import WatchConfig
from domain_watch.push_notify import PushNotifier
from domain_watch.state import (
    DomainPhase,
    DomainSchedule,
    RegistrationAttempt,
    WatchState,
    save_state,
)
from domain_watch.tencent_domain import (
    RegistrationStatus,
    TencentDomainClient,
    TencentDomainResult,
)

TERMINAL_REGISTRATION_STATUSES = frozenset({"success", "failed"})


def process_candidate(
    config: WatchConfig,
    state: WatchState,
    schedule: DomainSchedule,
    *,
    client: TencentDomainClient,
    notifier: PushNotifier | None,
    now: datetime,
    unavailable_interval_seconds: int,
) -> None:
    # Claim the next poll slot before the network call, the same way begin_registration
    # records its intent first: if the secondary channel fails, the domain must not be
    # left immediately due, which would spin the watch loop against a failing API.
    schedule.next_check_at = now + timedelta(seconds=unavailable_interval_seconds)
    result = client.check_domain(schedule.domain, config.period)
    print_tencent_result(result)
    if not result.available:
        save_state(config.state_file, state)
        return
    begin_registration(
        config,
        state,
        schedule,
        client=client,
        notifier=notifier,
        now=now,
    )


def begin_registration(
    config: WatchConfig,
    state: WatchState,
    schedule: DomainSchedule,
    *,
    client: TencentDomainClient,
    notifier: PushNotifier | None,
    now: datetime,
) -> None:
    schedule.phase = DomainPhase.REGISTERING
    schedule.registration = RegistrationAttempt(started_at=now)
    schedule.next_check_at = now + timedelta(seconds=config.schedule.registration_poll_seconds)
    save_state(config.state_file, state)
    try:
        submission = client.submit_registration(schedule.domain, config)
    except Exception as error:
        mark_indeterminate(
            config,
            state,
            schedule,
            notifier=notifier,
            error=error,
        )
        return
    schedule.registration.log_id = submission.log_id
    schedule.registration.request_id = submission.request_id
    schedule.registration.status = "doing"
    save_state(config.state_file, state)
    notify(
        notifier,
        f"注册任务已提交 {schedule.domain}",
        f"LogId: {submission.log_id}\nRequestId: {submission.request_id}",
    )


def mark_indeterminate(
    config: WatchConfig,
    state: WatchState,
    schedule: DomainSchedule,
    *,
    notifier: PushNotifier | None,
    error: Exception,
) -> None:
    schedule.phase = DomainPhase.INDETERMINATE
    schedule.last_error = str(error)
    if schedule.registration:
        schedule.registration.status = "indeterminate"
        schedule.registration.reason = str(error)
    save_state(config.state_file, state)
    notify(
        notifier,
        f"注册状态不确定 {schedule.domain}",
        f"提交请求未取得 LogId，已停止自动重试：{error}",
    )


def poll_registration(
    config: WatchConfig,
    state: WatchState,
    schedule: DomainSchedule,
    *,
    client: TencentDomainClient,
    notifier: PushNotifier | None,
    now: datetime,
) -> None:
    attempt = schedule.registration
    if attempt is None or attempt.log_id is None:
        schedule.phase = DomainPhase.INDETERMINATE
        save_state(config.state_file, state)
        notify(
            notifier,
            f"注册状态不确定 {schedule.domain}",
            "本地状态缺少腾讯云 LogId，已停止自动提交。",
        )
        return
    result = client.registration_status(attempt.log_id, schedule.domain)
    apply_registration_status(
        config,
        state,
        schedule,
        result=result,
        notifier=notifier,
        now=now,
    )


def apply_registration_status(
    config: WatchConfig,
    state: WatchState,
    schedule: DomainSchedule,
    *,
    result: RegistrationStatus,
    notifier: PushNotifier | None,
    now: datetime,
) -> None:
    status = result.status.lower()
    if status not in TERMINAL_REGISTRATION_STATUSES and status != "doing":
        raise RuntimeError(f"Unknown registration status: {result.status!r}")
    attempt = schedule.registration
    if attempt is None:
        raise RuntimeError(f"Missing registration attempt for {schedule.domain}")
    attempt.status = status
    attempt.reason = result.reason
    if status == "success":
        mark_registration_success(schedule, notifier, now=now)
    elif status == "failed":
        mark_registration_failed(
            config,
            schedule,
            result,
            notifier=notifier,
            now=now,
        )
    else:
        schedule.next_check_at = now + timedelta(seconds=config.schedule.registration_poll_seconds)
    save_state(config.state_file, state)


def mark_registration_success(
    schedule: DomainSchedule,
    notifier: PushNotifier | None,
    *,
    now: datetime,
) -> None:
    schedule.phase = DomainPhase.REMOVED
    schedule.removed_at = now
    schedule.last_error = None
    notify(notifier, f"域名注册成功 {schedule.domain}", "腾讯云异步任务状态为 success。")


def mark_registration_failed(
    config: WatchConfig,
    schedule: DomainSchedule,
    result: RegistrationStatus,
    *,
    notifier: PushNotifier | None,
    now: datetime,
) -> None:
    schedule.phase = DomainPhase.WATCHING
    schedule.last_error = result.reason or "Tencent registration failed"
    schedule.next_check_at = now + timedelta(seconds=config.schedule.retry_interval_seconds)
    notify(
        notifier,
        f"域名注册失败 {schedule.domain}",
        f"原因: {schedule.last_error}\n将在重试间隔后恢复监听。",
    )


def print_tencent_result(result: TencentDomainResult) -> None:
    status = "AVAILABLE" if result.available else "TAKEN"
    print(
        f"Tencent {result.domain} {status} reason={result.reason!r} "
        f"premium={result.premium} price={result.price} "
        f"real_price={result.real_price} request_id={result.request_id}"
    )


def notify(notifier: PushNotifier | None, title: str, content: str) -> None:
    if notifier is not None:
        notifier.send(title, content)
