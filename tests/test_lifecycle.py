from __future__ import annotations

from datetime import UTC, datetime, timedelta

from domain_watch.config import ScheduleConfig
from domain_watch.lifecycle import apply_rdap_result, due_schedules
from domain_watch.rdap import RdapResult
from domain_watch.state import DomainPhase, DomainSchedule, WatchState

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


def test_due_domains_prioritize_earliest_expected_drop_then_config_order() -> None:
    first = DomainSchedule(domain="first.com", next_check_at=NOW)
    second = DomainSchedule(
        domain="second.com",
        next_check_at=NOW - timedelta(seconds=1),
    )
    state = WatchState(domains={"second.com": second, "first.com": first})

    due = due_schedules(state, ("first.com", "second.com"), NOW)

    assert [item.domain for item in due] == ["second.com", "first.com"]
