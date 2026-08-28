from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from domain_watch.config import WatchConfig
from domain_watch.registration import poll_registration, process_candidate
from domain_watch.state import DomainPhase, DomainSchedule, WatchState, load_state
from domain_watch.tencent_domain import (
    RegistrationStatus,
    RegistrationSubmission,
    TencentDomainResult,
)

NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> None:
        self.messages.append((title, content))


class FakeTencentClient:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.submission: RegistrationSubmission | Exception = RegistrationSubmission(318, "req-1")
        self.status = RegistrationStatus("example.com", "doing")
        self.submit_calls: list[str] = []

    def check_domain(self, domain: str, _period: int) -> TencentDomainResult:
        return TencentDomainResult(
            domain=domain,
            available=self.available,
            reason="",
            premium=False,
            black_word=False,
            price=35,
            real_price=10,
            request_id="check-1",
        )

    def submit_registration(
        self,
        domain: str,
        _config: WatchConfig,
    ) -> RegistrationSubmission:
        self.submit_calls.append(domain)
        if isinstance(self.submission, Exception):
            raise self.submission
        return self.submission

    def registration_status(self, _log_id: int, _domain: str) -> RegistrationStatus:
        return self.status


def build_config(tmp_path: Path) -> WatchConfig:
    return WatchConfig(
        secret_id="id",
        secret_key="key",
        template_id="tmpl",
        domains=("example.com",),
        state_file=tmp_path / "state.json",
    )


def build_state() -> tuple[WatchState, DomainSchedule]:
    schedule = DomainSchedule(
        domain="example.com",
        phase=DomainPhase.AVAILABLE,
        next_check_at=NOW,
    )
    return WatchState(domains={schedule.domain: schedule}), schedule


def test_candidate_is_submitted_individually_and_persisted(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    state, schedule = build_state()
    client = FakeTencentClient()

    process_candidate(
        config,
        state,
        schedule,
        client=client,
        notifier=None,
        now=NOW,
        unavailable_interval_seconds=5,
    )

    assert client.submit_calls == ["example.com"]
    assert schedule.phase is DomainPhase.REGISTERING
    assert schedule.registration is not None
    assert schedule.registration.log_id == 318
    persisted = load_state(config.state_file).domains["example.com"]
    assert persisted.registration is not None
    assert persisted.registration.log_id == 318


def test_submission_exception_stops_automatic_retry(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    state, schedule = build_state()
    client = FakeTencentClient()
    client.submission = RuntimeError("connection lost after submit")
    notifier = FakeNotifier()

    process_candidate(
        config,
        state,
        schedule,
        client=client,
        notifier=notifier,
        now=NOW,
        unavailable_interval_seconds=5,
    )

    assert schedule.phase is DomainPhase.INDETERMINATE
    assert schedule.registration is not None
    assert schedule.registration.log_id is None
    assert "状态不确定" in notifier.messages[-1][0]


def test_successful_async_task_removes_domain(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    state, schedule = build_state()
    client = FakeTencentClient()
    process_candidate(
        config,
        state,
        schedule,
        client=client,
        notifier=None,
        now=NOW,
        unavailable_interval_seconds=5,
    )
    client.status = RegistrationStatus("example.com", "success")

    poll_registration(
        config,
        state,
        schedule,
        client=client,
        notifier=None,
        now=NOW,
    )

    assert schedule.phase is DomainPhase.REMOVED
    assert schedule.removed_at == NOW


def test_failed_async_task_returns_to_watching(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    state, schedule = build_state()
    client = FakeTencentClient()
    process_candidate(
        config,
        state,
        schedule,
        client=client,
        notifier=None,
        now=NOW,
        unavailable_interval_seconds=5,
    )
    client.status = RegistrationStatus("example.com", "failed", "insufficient balance")

    poll_registration(
        config,
        state,
        schedule,
        client=client,
        notifier=None,
        now=NOW,
    )

    assert schedule.phase is DomainPhase.WATCHING
    assert schedule.last_error == "insufficient balance"


def test_repeated_unavailable_result_does_not_repeat_notification(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    state, schedule = build_state()
    client = FakeTencentClient(available=False)
    notifier = FakeNotifier()

    for _index in range(2):
        process_candidate(
            config,
            state,
            schedule,
            client=client,
            notifier=notifier,
            now=NOW,
            unavailable_interval_seconds=5,
        )

    assert len(notifier.messages) == 1
