from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain_check_info import DomainCheckResult, parse_domain_check_record
from domain_watch import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS,
    CliDomainCheckRunner,
    WatchConfig,
    load_config,
    next_watch_interval,
    tencent_check_candidate_names,
    watch_once,
)
from tencent_domain import MANUAL_BALANCE_PAY_MODE, TencentDomainResult


class FakeRunner:
    def __init__(self, results: tuple[DomainCheckResult, ...]) -> None:
        self.results = results

    def check_domains(self, domains: tuple[str, ...]) -> tuple[DomainCheckResult, ...]:
        self.checked_domains = domains
        return self.results


class FakeClient:
    def __init__(self, results: dict[str, TencentDomainResult]) -> None:
        self.results = results
        self.check_calls: list[tuple[str, int]] = []
        self.register_calls: list[dict[str, object]] = []

    def check_domain(self, domain: str, period: int) -> TencentDomainResult:
        self.check_calls.append((domain, period))
        return self.results[domain]

    def create_domain_batch(self, domains: tuple[str, ...], config: WatchConfig) -> object:
        self.register_calls.append(
            {
                "domains": domains,
                "template_id": config.template_id,
                "period": config.period,
                "pay_mode": MANUAL_BALANCE_PAY_MODE,
            }
        )
        return type("Response", (), {"LogId": 318, "RequestId": "req-1"})()


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> None:
        self.messages.append((title, content))


def config() -> WatchConfig:
    return WatchConfig(
        secret_id="secret-id",
        secret_key="secret-key",
        template_id="tmpl-xxxxxx",
        interval_seconds=DEFAULT_INTERVAL_SECONDS,
        near_expiry_interval_seconds=DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS,
        expiry_acceleration_days=7,
        period=1,
        domains=("example.com", "example.net"),
    )


def result(domain: str, available: bool) -> TencentDomainResult:
    return TencentDomainResult(
        domain=domain,
        available=available,
        reason="" if available else "registered",
        premium=False,
        black_word=False,
        price=35,
        real_price=10,
        request_id="request-id",
    )


def domain_check_result(domain: str, available: bool) -> DomainCheckResult:
    return DomainCheckResult(domain=domain, available=available, expires_at=None)


def unexpired_domain_check_result(domain: str) -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        available=True,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )


def test_rdap_unavailable_domains_do_not_call_tencent() -> None:
    fake_client = FakeClient({})
    submitted, next_interval = watch_once(config(), FakeRunner(()), fake_client, frozenset())

    assert submitted == frozenset()
    assert next_interval == DEFAULT_INTERVAL_SECONDS
    assert fake_client.check_calls == []
    assert fake_client.register_calls == []


def test_tencent_unavailable_domain_is_not_registered() -> None:
    fake_client = FakeClient({"example.com": result("example.com", False)})
    submitted, next_interval = watch_once(
        config(),
        FakeRunner((domain_check_result("example.com", True),)),
        fake_client,
        frozenset(),
    )

    assert submitted == frozenset()
    assert next_interval == DEFAULT_INTERVAL_SECONDS
    assert fake_client.check_calls == [("example.com", 1)]
    assert fake_client.register_calls == []


def test_available_domain_is_registered_with_balance_payment() -> None:
    fake_client = FakeClient({"example.net": result("example.net", True)})
    submitted, next_interval = watch_once(
        config(),
        FakeRunner((domain_check_result("example.net", True),)),
        fake_client,
        frozenset(),
    )

    assert submitted == frozenset({"example.net"})
    assert next_interval == DEFAULT_INTERVAL_SECONDS
    assert fake_client.register_calls == [
        {
            "domains": ("example.net",),
            "template_id": "tmpl-xxxxxx",
            "period": 1,
            "pay_mode": 1,
        }
    ]


def test_available_domain_sends_tencent_and_register_notifications() -> None:
    fake_client = FakeClient({"example.net": result("example.net", True)})
    notifier = FakeNotifier()

    submitted, _next_interval = watch_once(
        config(),
        FakeRunner((domain_check_result("example.net", True),)),
        fake_client,
        frozenset(),
        notifier,
    )

    assert submitted == frozenset({"example.net"})
    assert [message[0] for message in notifier.messages] == [
        "腾讯云查询 example.net 可注册",
        "注册任务已提交",
    ]


def test_unexpired_domain_does_not_call_tencent_api() -> None:
    fake_client = FakeClient({})
    submitted, next_interval = watch_once(
        config(),
        FakeRunner((unexpired_domain_check_result("example.com"),)),
        fake_client,
        frozenset(),
    )

    assert submitted == frozenset()
    assert next_interval == DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS
    assert fake_client.check_calls == []
    assert fake_client.register_calls == []


def test_future_expiration_blocks_tencent_candidate() -> None:
    now = datetime.now(UTC)
    result = DomainCheckResult(
        domain="example.com",
        available=True,
        expires_at=now + timedelta(seconds=1),
    )

    assert tencent_check_candidate_names((result,), now) == ()


def test_missing_required_environment_variable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TENCENTCLOUD_SECRET_ID", raising=False)
    monkeypatch.setenv("TENCENTCLOUD_SECRET_KEY", "secret-key")
    monkeypatch.setenv("TENCENT_DOMAIN_TEMPLATE_ID", "tmpl-xxxxxx")

    with pytest.raises(RuntimeError, match="TENCENTCLOUD_SECRET_ID"):
        load_config()


def test_missing_domain_check_cli_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("domain_watch.shutil.which", lambda _executable: None)

    with pytest.raises(RuntimeError, match="cargo install domain-check"):
        CliDomainCheckRunner()


def test_near_expiry_domain_uses_fast_interval() -> None:
    parsed = parse_domain_check_record(
        {
            "domain": "example.com",
            "available": False,
            "expiration_date": "2026-05-22",
        }
    )

    assert next_watch_interval(config(), (parsed,)) == DEFAULT_NEAR_EXPIRY_INTERVAL_SECONDS


def test_far_expiry_domain_uses_normal_interval() -> None:
    parsed = parse_domain_check_record(
        {
            "domain": "example.net",
            "available": False,
            "info": {"expires": "2099-01-01T00:00:00Z"},
        }
    )

    assert next_watch_interval(config(), (parsed,)) == DEFAULT_INTERVAL_SECONDS
