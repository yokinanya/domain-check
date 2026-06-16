from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from domain_watch.config import DEFAULT_INTERVAL_SECONDS, WatchConfig, load_config
from domain_watch.domain_check_cli import CliDomainCheckRunner
from domain_watch.domain_check_info import (
    DomainCheckResult,
    parse_domain_check_record,
)
from domain_watch.main import (
    next_watch_interval,
    tencent_check_candidate_names,
    watch_once,
)
from domain_watch.state import WatchState, init_state, load_state, save_state
from domain_watch.tencent_domain import MANUAL_BALANCE_PAY_MODE, TencentDomainResult


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


def build_config(
    tmp_path: Path,
    domains: tuple[str, ...] = ("example.com", "example.net"),
) -> WatchConfig:
    return WatchConfig(
        secret_id="secret-id",
        secret_key="secret-key",
        template_id="tmpl-xxxxxx",
        interval_seconds=DEFAULT_INTERVAL_SECONDS,
        expired_interval_seconds=DEFAULT_INTERVAL_SECONDS,
        period=1,
        domains=domains,
        state_file=tmp_path / "state.json",
    )


def tencent_result(domain: str, available: bool) -> TencentDomainResult:
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


def domain_check_result_with_statuses(
    domain: str,
    statuses: tuple[str, ...],
) -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        available=False,
        expires_at=None,
        statuses=statuses,
    )


def unexpired_domain_check_result(domain: str) -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        available=True,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )


def expired_domain_check_result(domain: str) -> DomainCheckResult:
    return DomainCheckResult(
        domain=domain,
        available=True,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )


def test_rdap_unavailable_domains_do_not_call_tencent(tmp_path: Path) -> None:
    config = build_config(tmp_path)
    fake_client = FakeClient({})
    state = init_state(config.state_file, config.domains)
    next_interval = watch_once(config, FakeRunner(()), fake_client, state)

    assert next_interval == DEFAULT_INTERVAL_SECONDS
    assert fake_client.check_calls == []
    assert fake_client.register_calls == []


def test_tencent_unavailable_domain_is_not_registered(tmp_path: Path) -> None:
    config = build_config(tmp_path, domains=("example.com",))
    fake_client = FakeClient({"example.com": tencent_result("example.com", False)})
    state = init_state(config.state_file, config.domains)
    next_interval = watch_once(
        config,
        FakeRunner((domain_check_result("example.com", True),)),
        fake_client,
        state,
    )

    assert next_interval == DEFAULT_INTERVAL_SECONDS
    assert state.active == {"example.com"}
    assert fake_client.check_calls == [("example.com", 1)]
    assert fake_client.register_calls == []


def test_available_domain_is_registered_with_balance_payment(tmp_path: Path) -> None:
    config = build_config(tmp_path, domains=("example.net",))
    fake_client = FakeClient({"example.net": tencent_result("example.net", True)})
    state = init_state(config.state_file, config.domains)
    next_interval = watch_once(
        config,
        FakeRunner((domain_check_result("example.net", True),)),
        fake_client,
        state,
    )

    assert next_interval == DEFAULT_INTERVAL_SECONDS
    assert state.active == set()
    assert state.removed[0].domain == "example.net"
    assert state.removed[0].reason == "register_submitted"
    assert fake_client.register_calls == [
        {
            "domains": ("example.net",),
            "template_id": "tmpl-xxxxxx",
            "period": 1,
            "pay_mode": 1,
        }
    ]
    assert load_state(config.state_file).removed[0].domain == "example.net"


def test_available_domain_sends_tencent_and_register_notifications(tmp_path: Path) -> None:
    config = build_config(tmp_path, domains=("example.net",))
    fake_client = FakeClient({"example.net": tencent_result("example.net", True)})
    notifier = FakeNotifier()
    state = init_state(config.state_file, config.domains)

    watch_once(
        config,
        FakeRunner((domain_check_result("example.net", True),)),
        fake_client,
        state,
        notifier,
    )

    assert [message[0] for message in notifier.messages] == [
        "腾讯云查询 example.net 可注册",
        "注册任务已提交",
    ]
    assert "移出监听列表" in notifier.messages[-1][1]


def test_status_change_sends_notification_after_initial_baseline(tmp_path: Path) -> None:
    config = build_config(tmp_path, domains=("example.com",))
    fake_client = FakeClient({})
    notifier = FakeNotifier()
    state = init_state(config.state_file, config.domains)

    watch_once(
        config,
        FakeRunner((domain_check_result_with_statuses("example.com", ("ok",)),)),
        fake_client,
        state,
        notifier,
    )
    watch_once(
        config,
        FakeRunner((domain_check_result_with_statuses("example.com", ("redemptionPeriod",)),)),
        fake_client,
        state,
        notifier,
    )

    assert [message[0] for message in notifier.messages] == [
        "域名状态码更新 example.com",
    ]
    assert "原状态码: ok" in notifier.messages[0][1]
    assert "新状态码: redemptionPeriod" in notifier.messages[0][1]
    assert load_state(config.state_file).statuses["example.com"] == ("redemptionPeriod",)


def test_unexpired_domain_does_not_call_tencent_api(tmp_path: Path) -> None:
    config = build_config(tmp_path, domains=("example.com",))
    fake_client = FakeClient({})
    state = init_state(config.state_file, config.domains)
    next_interval = watch_once(
        config,
        FakeRunner((unexpired_domain_check_result("example.com"),)),
        fake_client,
        state,
    )

    assert next_interval == DEFAULT_INTERVAL_SECONDS
    assert state.active == {"example.com"}
    assert fake_client.check_calls == []
    assert fake_client.register_calls == []


def test_expired_domain_uses_expired_interval(tmp_path: Path) -> None:
    config = WatchConfig(
        secret_id="secret-id",
        secret_key="secret-key",
        template_id="tmpl-xxxxxx",
        interval_seconds=DEFAULT_INTERVAL_SECONDS,
        expired_interval_seconds=600,
        period=1,
        domains=("example.com",),
        state_file=tmp_path / "state.json",
    )
    next_interval = next_watch_interval(
        config,
        (expired_domain_check_result("example.com"),),
    )

    assert next_interval == 600


def test_future_expiration_blocks_tencent_candidate() -> None:
    now = datetime.now(UTC)
    record = DomainCheckResult(
        domain="example.com",
        available=True,
        expires_at=now + timedelta(seconds=1),
    )

    assert tencent_check_candidate_names((record,), now) == ()


def test_missing_required_environment_variable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TENCENTCLOUD_SECRET_ID", raising=False)
    monkeypatch.setenv("TENCENTCLOUD_SECRET_KEY", "secret-key")
    monkeypatch.setenv("TENCENT_DOMAIN_TEMPLATE_ID", "tmpl-xxxxxx")

    with pytest.raises(RuntimeError, match="TENCENTCLOUD_SECRET_ID"):
        load_config()


def test_missing_domain_check_cli_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("domain_watch.domain_check_cli.shutil.which", lambda _executable: None)

    with pytest.raises(RuntimeError, match="DOMAIN_CHECK_BIN"):
        CliDomainCheckRunner()


def test_state_serialization_roundtrip(tmp_path: Path) -> None:
    state = WatchState()
    state.active = {"example.com"}
    state.update_statuses("example.com", ("ok", "clientHold"))
    state.remove("example.com", reason="register_submitted", request_id="r-1", log_id="l-1")
    path = tmp_path / "state.json"
    save_state(path, state)
    loaded = load_state(path)

    assert loaded.active == set()
    assert len(loaded.removed) == 1
    assert loaded.removed[0].domain == "example.com"
    assert loaded.removed[0].reason == "register_submitted"
    assert loaded.removed[0].request_id == "r-1"
    assert loaded.removed[0].log_id == "l-1"
    assert loaded.statuses["example.com"] == ("ok", "clientHold")


def test_parse_domain_check_record_reads_nested_statuses() -> None:
    parsed = parse_domain_check_record(
        {
            "domain": "example.net",
            "available": False,
            "info": {
                "statuses": [
                    "clientTransferProhibited",
                    {"status": "redemptionPeriod"},
                ]
            },
        }
    )

    assert parsed.statuses == ("clientTransferProhibited", "redemptionPeriod")


def test_init_state_does_not_restore_removed_domain(tmp_path: Path) -> None:
    state = WatchState()
    state.active = {"example.com"}
    state.remove("example.com", reason="register_submitted")
    path = tmp_path / "state.json"
    save_state(path, state)

    reinitialized = init_state(path, ("example.com", "example.net"))

    assert reinitialized.active == {"example.net"}


def test_far_expiry_domain_uses_normal_interval(tmp_path: Path) -> None:
    config = build_config(tmp_path, domains=("example.net",))
    parsed = parse_domain_check_record(
        {
            "domain": "example.net",
            "available": False,
            "info": {"expires": "2099-01-01T00:00:00Z"},
        }
    )

    assert next_watch_interval(config, (parsed,)) == DEFAULT_INTERVAL_SECONDS
