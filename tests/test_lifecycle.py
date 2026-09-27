from __future__ import annotations

from datetime import UTC, datetime, timedelta

from domain_watch.config import ScheduleConfig
from domain_watch.lifecycle import (
    apply_rdap_result,
    due_schedules,
    is_hot_pursuit,
    record_failure,
)
from domain_watch.rdap import RdapResult
from domain_watch.state import DomainPhase, DomainSchedule, DropWindow, WatchState

NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)


def registered_result(
    expires_at: datetime,
    statuses: tuple[str, ...] = ("active",),
) -> RdapResult:
    return RdapResult(
        domain="example.com",
        registered=True,
        expires_at=expires_at,
        statuses=statuses,
        endpoint="https://rdap.test/",
        queried_at=NOW,
    )


def test_initial_discovery_schedules_exact_expiration() -> None:
    expires_at = NOW + timedelta(days=30, seconds=17)
    schedule = DomainSchedule(domain="example.com", next_check_at=NOW)

    apply_rdap_result(schedule, registered_result(expires_at), ScheduleConfig(), now=NOW)

    assert schedule.phase is DomainPhase.SCHEDULED
    assert schedule.next_check_at == expires_at


def test_renewal_reschedules_to_new_exact_expiration() -> None:
    schedule = DomainSchedule(
        domain="example.com",
        phase=DomainPhase.WATCHING,
        next_check_at=NOW,
        expires_at=NOW - timedelta(days=1),
    )
    renewed_until = NOW + timedelta(days=365)

    apply_rdap_result(
        schedule,
        registered_result(renewed_until),
        ScheduleConfig(),
        now=NOW,
    )

    assert schedule.phase is DomainPhase.SCHEDULED
    assert schedule.next_check_at == renewed_until


def test_pending_delete_builds_observation_error_window() -> None:
    previous_check = NOW - timedelta(hours=6)
    schedule = DomainSchedule(
        domain="example.com",
        phase=DomainPhase.WATCHING,
        next_check_at=NOW,
        previous_check_at=previous_check,
    )
    result = registered_result(NOW - timedelta(days=40), ("pending delete",))

    apply_rdap_result(schedule, result, ScheduleConfig(), now=NOW)

    assert schedule.phase is DomainPhase.PENDING_DELETE
    assert schedule.drop_window is not None
    assert schedule.drop_window.starts_at == previous_check + timedelta(days=5)
    assert schedule.drop_window.ends_at == NOW + timedelta(days=5)
    assert schedule.next_check_at == NOW + timedelta(minutes=5)


def test_pending_delete_switches_to_five_second_checks_in_window() -> None:
    schedule = DomainSchedule(
        domain="example.com",
        phase=DomainPhase.PENDING_DELETE,
        next_check_at=NOW,
        previous_check_at=NOW - timedelta(days=6),
    )
    result = registered_result(NOW - timedelta(days=40), ("pendingDelete",))

    apply_rdap_result(schedule, result, ScheduleConfig(), now=NOW)

    assert schedule.next_check_at == NOW + timedelta(seconds=5)


def test_is_hot_pursuit_only_covers_phases_where_a_missed_round_costs_the_domain() -> None:
    started = DropWindow(
        starts_at=NOW - timedelta(hours=1),
        ends_at=NOW + timedelta(days=4),
    )
    pending = DropWindow(
        starts_at=NOW + timedelta(hours=1),
        ends_at=NOW + timedelta(days=4),
    )
    cases = [
        (DomainPhase.SCHEDULED, None, False),
        (DomainPhase.WATCHING, None, False),
        (DomainPhase.PENDING_DELETE, pending, False),
        (DomainPhase.PENDING_DELETE, started, True),
        (DomainPhase.AVAILABLE, None, True),
        (DomainPhase.AVAILABLE, pending, True),
        (DomainPhase.REGISTERING, None, False),
        (DomainPhase.INDETERMINATE, None, False),
        (DomainPhase.REMOVED, None, False),
    ]

    for phase, window, expected in cases:
        schedule = DomainSchedule(
            domain="example.com",
            phase=phase,
            next_check_at=NOW,
            drop_window=window,
        )
        assert is_hot_pursuit(schedule, NOW) is expected, phase


def test_due_domains_prioritize_earliest_expected_drop_then_config_order() -> None:
    first = DomainSchedule(domain="first.com", next_check_at=NOW)
    second = DomainSchedule(
        domain="second.com",
        next_check_at=NOW - timedelta(seconds=1),
    )
    state = WatchState(domains={"second.com": second, "first.com": first})

    due = due_schedules(state, ("first.com", "second.com"), NOW)

    assert [item.domain for item in due] == ["second.com", "first.com"]


def test_record_failure_backs_off_exponentially() -> None:
    schedule = DomainSchedule(domain="example.com", next_check_at=NOW)
    base = ScheduleConfig().retry_interval_seconds

    for count, expected_min, expected_max in [
        (1, base, base * 1.2),
        (2, base * 2, base * 2.4),
        (3, base * 4, base * 4.8),
    ]:
        record_failure(schedule, RuntimeError("boom"), base, now=NOW)
        delay = (schedule.next_check_at - NOW).total_seconds()
        assert schedule.failure_count == count
        assert expected_min <= delay <= expected_max


def test_record_failure_backoff_caps_at_one_hour() -> None:
    schedule = DomainSchedule(domain="example.com", next_check_at=NOW)
    base = ScheduleConfig().retry_interval_seconds

    for _ in range(10):
        record_failure(schedule, RuntimeError("boom"), base, now=NOW)

    delay = (schedule.next_check_at - NOW).total_seconds()
    assert delay <= 3600 * 1.2


def test_successful_rdap_result_resets_failure_count() -> None:
    schedule = DomainSchedule(domain="example.com", next_check_at=NOW, failure_count=3)
    result = registered_result(NOW + timedelta(days=30))

    apply_rdap_result(schedule, result, ScheduleConfig(), now=NOW)

    assert schedule.failure_count == 0
    assert schedule.last_error is None
