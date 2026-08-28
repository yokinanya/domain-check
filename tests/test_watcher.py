from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from domain_watch.config import WatchConfig
from domain_watch.main import DomainWatcher, WatchServices
from domain_watch.rate_limit import RateLimiter, RateLimitPolicy
from domain_watch.rdap import BootstrapData, RdapRateLimited, RdapResult
from domain_watch.state import DomainPhase, DomainSchedule, WatchState
from domain_watch.tencent_domain import (
    RegistrationStatus,
    RegistrationSubmission,
    TencentDomainResult,
)

NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
ENDPOINT = "https://rdap.example/com/"


class FakeBootstrap:
    def load(self, _now: datetime) -> BootstrapData:
        return BootstrapData(endpoints=(("com", ENDPOINT),), fetched_at=NOW)


class FakeRdap:
    def __init__(self, outcomes: dict[str, RdapResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def query(self, domain: str, _endpoint: str, *, now: datetime) -> RdapResult:
        del now
        self.calls.append(domain)
        outcome = self.outcomes[domain]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeTencent:
    def __init__(self) -> None:
        self.check_calls: list[str] = []

    def check_domain(self, domain: str, _period: int) -> TencentDomainResult:
        self.check_calls.append(domain)
        return TencentDomainResult(
            domain=domain,
            available=False,
            reason="registered",
            premium=False,
            black_word=False,
            price=35,
            real_price=10,
            request_id="r",
        )

    def submit_registration(
        self,
        _domain: str,
        _config: WatchConfig,
    ) -> RegistrationSubmission:
        raise AssertionError("unavailable domain must not be submitted")

    def registration_status(self, _log_id: int, domain: str) -> RegistrationStatus:
        return RegistrationStatus(domain, "doing")


def config(tmp_path: Path, domains: tuple[str, ...]) -> WatchConfig:
    return WatchConfig(
        secret_id="id",
        secret_key="key",
        template_id="tmpl",
        domains=domains,
        state_file=tmp_path / "state.json",
    )


def services(rdap: FakeRdap, tencent: FakeTencent) -> WatchServices:
    return WatchServices(bootstrap=FakeBootstrap(), rdap=rdap, tencent=tencent)


def test_single_domain_failure_does_not_block_other_domain(tmp_path: Path) -> None:
    domains = ("broken.com", "working.com")
    future = NOW + timedelta(days=30)
    rdap = FakeRdap(
        {
            "broken.com": RuntimeError("network down"),
            "working.com": RdapResult(
                domain="working.com",
                registered=True,
                expires_at=future,
                statuses=("active",),
                endpoint=ENDPOINT,
                queried_at=NOW,
            ),
        }
    )
    state = WatchState(
        domains={name: DomainSchedule(domain=name, next_check_at=NOW) for name in domains}
    )

    DomainWatcher(
        config(tmp_path, domains),
        services(rdap, FakeTencent()),
        now_provider=lambda: NOW,
    ).run_once(state)

    assert state.domains["broken.com"].last_error == "network down"
    assert state.domains["working.com"].next_check_at == future


def test_429_records_cooldown_and_falls_back_to_tencent(tmp_path: Path) -> None:
    retry_at = NOW + timedelta(minutes=2)
    rdap = FakeRdap({"example.com": RdapRateLimited("rdap.example", retry_at)})
    tencent = FakeTencent()
    state = WatchState(
        domains={"example.com": DomainSchedule(domain="example.com", next_check_at=NOW)}
    )

    DomainWatcher(
        config(tmp_path, ("example.com",)),
        services(rdap, tencent),
        now_provider=lambda: NOW,
    ).run_once(state)

    assert state.rdap_cooldowns["rdap.example"] == retry_at
    assert tencent.check_calls == ["example.com"]
    assert state.domains["example.com"].next_check_at == retry_at


def test_rdap_404_outside_drop_window_retries_tencent_conservatively(
    tmp_path: Path,
) -> None:
    result = RdapResult(
        domain="example.com",
        registered=False,
        expires_at=None,
        statuses=(),
        endpoint=ENDPOINT,
        queried_at=NOW,
    )
    rdap = FakeRdap({"example.com": result})
    tencent = FakeTencent()
    state = WatchState(
        domains={"example.com": DomainSchedule(domain="example.com", next_check_at=NOW)}
    )

    DomainWatcher(
        config(tmp_path, ("example.com",)),
        services(rdap, tencent),
        now_provider=lambda: NOW,
    ).run_once(state)

    schedule = state.domains["example.com"]
    assert schedule.phase is DomainPhase.AVAILABLE
    assert schedule.next_check_at == NOW + timedelta(minutes=15)


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def test_rate_limiter_is_shared_by_key() -> None:
    fake_time = FakeTime()
    limiter = RateLimiter(fake_time.monotonic, fake_time.sleep)
    policy = RateLimitPolicy(1.0)

    limiter.wait("rdap:shared", policy)
    limiter.wait("rdap:shared", policy)
    limiter.wait("rdap:other", policy)

    assert fake_time.sleeps == [1.0]
