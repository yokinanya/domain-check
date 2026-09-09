from __future__ import annotations

import random
from datetime import datetime, timedelta

from domain_watch.config import ScheduleConfig
from domain_watch.rdap import RdapError, RdapResult
from domain_watch.state import DomainPhase, DomainSchedule, DropWindow, WatchState


def normalized_statuses(statuses: tuple[str, ...]) -> frozenset[str]:
    return frozenset(
        "".join(character for character in value.lower() if character.isalnum())
        for value in statuses
    )


def has_status(schedule: DomainSchedule, expected: str) -> bool:
    return expected.lower() in normalized_statuses(schedule.statuses)


def apply_rdap_result(
    schedule: DomainSchedule,
    result: RdapResult,
    config: ScheduleConfig,
    *,
    now: datetime,
) -> tuple[str, ...] | None:
    previous_statuses = schedule.statuses
    prior_check = schedule.previous_check_at
    schedule.previous_check_at = now
    schedule.statuses = result.statuses
    schedule.last_error = None
    schedule.failure_count = 0
    if not result.registered:
        schedule.phase = DomainPhase.AVAILABLE
        schedule.next_check_at = now
        return changed_statuses(previous_statuses, result.statuses)
    if result.expires_at is None:
        raise RdapError(f"RDAP response for {schedule.domain} has no expiration event")
    schedule.expires_at = result.expires_at
    update_registered_schedule(schedule, config, prior_check, now=now)
    return changed_statuses(previous_statuses, result.statuses)


def update_registered_schedule(
    schedule: DomainSchedule,
    config: ScheduleConfig,
    prior_check: datetime | None,
    *,
    now: datetime,
) -> None:
    if is_pending_delete(schedule.statuses):
        schedule_pending_delete(schedule, config, prior_check, now=now)
        return
    clear_drop_tracking(schedule)
    if schedule.expires_at and schedule.expires_at > now:
        schedule.phase = DomainPhase.SCHEDULED
        schedule.next_check_at = schedule.expires_at
        return
    schedule.phase = DomainPhase.WATCHING
    schedule.next_check_at = now + timedelta(seconds=config.redemption_interval_seconds)


def schedule_pending_delete(
    schedule: DomainSchedule,
    config: ScheduleConfig,
    prior_check: datetime | None,
    *,
    now: datetime,
) -> None:
    if schedule.pending_delete_first_seen_at is None:
        schedule.pending_delete_first_seen_at = now
        duration = timedelta(days=config.pending_delete_days(schedule.domain))
        lower_bound = (prior_check or now) + duration
        schedule.drop_window = DropWindow(lower_bound, now + duration)
    schedule.phase = DomainPhase.PENDING_DELETE
    schedule.next_check_at = next_pending_delete_check(schedule, config, now=now)


def next_pending_delete_check(
    schedule: DomainSchedule,
    config: ScheduleConfig,
    *,
    now: datetime,
) -> datetime:
    window = schedule.drop_window
    if window is not None and now >= window.starts_at:
        return now + timedelta(seconds=config.drop_interval_seconds)
    regular = now + timedelta(seconds=config.pending_delete_interval_seconds)
    if window is None:
        return regular
    return min(regular, window.starts_at)


def clear_drop_tracking(schedule: DomainSchedule) -> None:
    schedule.pending_delete_first_seen_at = None
    schedule.drop_window = None


def changed_statuses(
    previous: tuple[str, ...],
    current: tuple[str, ...],
) -> tuple[str, ...] | None:
    if not previous or previous == current:
        return None
    return previous


def is_pending_delete(statuses: tuple[str, ...]) -> bool:
    return "pendingdelete" in normalized_statuses(statuses)


def due_schedules(
    state: WatchState,
    configured_domains: tuple[str, ...],
    now: datetime,
) -> tuple[DomainSchedule, ...]:
    order = {domain: index for index, domain in enumerate(configured_domains)}
    due = [item for item in state.active_domains() if item.next_check_at <= now]
    return tuple(sorted(due, key=lambda item: schedule_priority(item, order)))


def schedule_priority(
    schedule: DomainSchedule,
    order: dict[str, int],
) -> tuple[datetime, int]:
    expected_drop = (
        schedule.drop_window.starts_at if schedule.drop_window else schedule.next_check_at
    )
    return expected_drop, order.get(schedule.domain, len(order))


def next_wake_at(state: WatchState) -> datetime | None:
    active = state.active_domains()
    if not active:
        return None
    return min(item.next_check_at for item in active)


MAX_FAILURE_BACKOFF_SECONDS = 3600
FAILURE_JITTER_FRACTION = 0.2


def failure_backoff_seconds(retry_seconds: int, failure_count: int) -> int:
    multiplier = min(2 ** max(0, failure_count - 1), MAX_FAILURE_BACKOFF_SECONDS / retry_seconds)
    base = retry_seconds * multiplier
    jitter = base * FAILURE_JITTER_FRACTION
    return int(base + random.uniform(0, jitter))


def record_failure(
    schedule: DomainSchedule,
    error: Exception,
    retry_seconds: int,
    *,
    now: datetime,
) -> None:
    schedule.failure_count += 1
    schedule.last_error = str(error)
    delay = failure_backoff_seconds(retry_seconds, schedule.failure_count)
    schedule.next_check_at = now + timedelta(seconds=delay)


def in_drop_window(schedule: DomainSchedule, now: datetime) -> bool:
    return schedule.drop_window is not None and now >= schedule.drop_window.starts_at
