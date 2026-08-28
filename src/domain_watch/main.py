from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from domain_watch.config import WatchConfig
from domain_watch.lifecycle import (
    apply_rdap_result,
    due_schedules,
    in_drop_window,
    record_failure,
)
from domain_watch.push_notify import PushNotifier
from domain_watch.rdap import (
    BootstrapData,
    RdapRateLimited,
    RdapResult,
    endpoint_host,
)
from domain_watch.registration import notify, poll_registration, process_candidate
from domain_watch.state import (
    DomainPhase,
    DomainSchedule,
    WatchState,
    save_state,
)
from domain_watch.tencent_domain import TencentDomainClient


class BootstrapLoader(Protocol):
    def load(self, now: datetime) -> BootstrapData: ...


class DomainRdapClient(Protocol):
    def query(self, domain: str, endpoint: str, *, now: datetime) -> RdapResult: ...


@dataclass(frozen=True, kw_only=True)
class WatchServices:
    bootstrap: BootstrapLoader
    rdap: DomainRdapClient
    tencent: TencentDomainClient
    notifier: PushNotifier | None = None


class DomainWatcher:
    def __init__(
        self,
        config: WatchConfig,
        services: WatchServices,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._services = services
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._bootstrap_warning: str | None = None

    def run_once(self, state: WatchState) -> None:
        now = self._now()
        for schedule in due_schedules(state, self._config.domains, now):
            self._process_isolated(state, schedule)

    def _process_isolated(self, state: WatchState, schedule: DomainSchedule) -> None:
        now = self._now()
        try:
            if schedule.phase is DomainPhase.REGISTERING:
                self._poll_registration(state, schedule, now=now)
                return
            self._query_rdap(state, schedule, now=now)
        except Exception as error:
            self._record_domain_failure(state, schedule, error=error, now=now)

    def _poll_registration(
        self,
        state: WatchState,
        schedule: DomainSchedule,
        *,
        now: datetime,
    ) -> None:
        poll_registration(
            self._config,
            state,
            schedule,
            client=self._services.tencent,
            notifier=self._services.notifier,
            now=now,
        )

    def _query_rdap(
        self,
        state: WatchState,
        schedule: DomainSchedule,
        *,
        now: datetime,
    ) -> None:
        bootstrap = self._services.bootstrap.load(now)
        self._notify_bootstrap_warning(bootstrap)
        endpoint = bootstrap.endpoint_for(schedule.domain)
        host = endpoint_host(endpoint)
        if self._rdap_is_cooling_down(state, host, now=now):
            self._query_tencent_fallback(state, schedule, now=now)
            return
        try:
            result = self._services.rdap.query(schedule.domain, endpoint, now=now)
        except RdapRateLimited as error:
            self._handle_rate_limit(state, schedule, error=error, now=now)
            return
        self._apply_rdap_result(state, schedule, result=result, now=now)

    def _apply_rdap_result(
        self,
        state: WatchState,
        schedule: DomainSchedule,
        *,
        result: RdapResult,
        now: datetime,
    ) -> None:
        previous_statuses = apply_rdap_result(
            schedule,
            result,
            self._config.schedule,
            now=now,
        )
        save_state(self._config.state_file, state)
        print_rdap_result(result, schedule)
        self._notify_status_change(schedule, previous_statuses)
        if not result.registered:
            self._query_tencent_candidate(state, schedule, now=now)

    def _handle_rate_limit(
        self,
        state: WatchState,
        schedule: DomainSchedule,
        *,
        error: RdapRateLimited,
        now: datetime,
    ) -> None:
        retry_at = error.retry_at or now + timedelta(
            seconds=self._config.schedule.retry_interval_seconds
        )
        state.rdap_cooldowns[error.host] = retry_at
        save_state(self._config.state_file, state)
        notify(
            self._services.notifier,
            f"RDAP 限流 {error.host}",
            f"冷却至 {retry_at.isoformat()}，期间改用腾讯云查询。",
        )
        self._query_tencent_fallback(state, schedule, now=now)
        if schedule.phase not in {DomainPhase.REGISTERING, DomainPhase.INDETERMINATE}:
            schedule.next_check_at = min(schedule.next_check_at, retry_at)
            save_state(self._config.state_file, state)

    def _query_tencent_fallback(
        self,
        state: WatchState,
        schedule: DomainSchedule,
        *,
        now: datetime,
    ) -> None:
        print(f"RDAP cooldown active for {schedule.domain}; using Tencent fallback")
        self._query_tencent_candidate(state, schedule, now=now)

    def _query_tencent_candidate(
        self,
        state: WatchState,
        schedule: DomainSchedule,
        *,
        now: datetime,
    ) -> None:
        interval = self._candidate_interval(schedule, now)
        process_candidate(
            self._config,
            state,
            schedule,
            client=self._services.tencent,
            notifier=self._services.notifier,
            now=now,
            unavailable_interval_seconds=interval,
        )

    def _candidate_interval(self, schedule: DomainSchedule, now: datetime) -> int:
        if in_drop_window(schedule, now):
            return self._config.schedule.drop_interval_seconds
        return self._config.schedule.retry_interval_seconds

    def _rdap_is_cooling_down(
        self,
        state: WatchState,
        host: str,
        *,
        now: datetime,
    ) -> bool:
        retry_at = state.rdap_cooldowns.get(host)
        if retry_at is None:
            return False
        if retry_at > now:
            return True
        del state.rdap_cooldowns[host]
        save_state(self._config.state_file, state)
        return False

    def _record_domain_failure(
        self,
        state: WatchState,
        schedule: DomainSchedule,
        *,
        error: Exception,
        now: datetime,
    ) -> None:
        previous_error = schedule.last_error
        record_failure(
            schedule,
            error,
            self._config.schedule.retry_interval_seconds,
            now=now,
        )
        save_state(self._config.state_file, state)
        print(f"{schedule.domain} FAILED error={error}")
        if previous_error != str(error):
            notify(
                self._services.notifier,
                f"域名检查失败 {schedule.domain}",
                str(error),
            )

    def _notify_bootstrap_warning(self, bootstrap: BootstrapData) -> None:
        if not bootstrap.stale or bootstrap.refresh_error == self._bootstrap_warning:
            return
        self._bootstrap_warning = bootstrap.refresh_error
        notify(
            self._services.notifier,
            "IANA RDAP Bootstrap 刷新失败",
            f"正在显式使用 {bootstrap.fetched_at.isoformat()} 的缓存：{bootstrap.refresh_error}",
        )

    def _notify_status_change(
        self,
        schedule: DomainSchedule,
        previous_statuses: tuple[str, ...] | None,
    ) -> None:
        if previous_statuses is None:
            return
        notify(
            self._services.notifier,
            f"域名状态更新 {schedule.domain}",
            f"原状态: {format_statuses(previous_statuses)}\n"
            f"新状态: {format_statuses(schedule.statuses)}\n"
            f"阶段: {schedule.phase.value}",
        )


def print_rdap_result(result: RdapResult, schedule: DomainSchedule) -> None:
    availability = "REGISTERED" if result.registered else "NOT_FOUND"
    expires_at = result.expires_at.isoformat() if result.expires_at else "unknown"
    print(
        f"RDAP {result.domain} {availability} expires_at={expires_at} "
        f"statuses={format_statuses(result.statuses)} phase={schedule.phase.value}"
    )


def format_statuses(statuses: tuple[str, ...]) -> str:
    return ", ".join(statuses) if statuses else "unknown"
