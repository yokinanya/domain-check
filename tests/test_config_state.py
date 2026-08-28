from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain_watch.config import (
    DEFAULT_DROP_INTERVAL_SECONDS,
    DEFAULT_RETRY_INTERVAL_SECONDS,
    load_config,
)
from domain_watch.state import (
    DomainPhase,
    DomainSchedule,
    RegistrationAttempt,
    WatchState,
    init_state,
    load_state,
    save_state,
)

NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)


def set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENCENTCLOUD_SECRET_ID", "secret-id")
    monkeypatch.setenv("TENCENTCLOUD_SECRET_KEY", "secret-key")
    monkeypatch.setenv("TENCENT_DOMAIN_TEMPLATE_ID", "tmpl-test")
    monkeypatch.setenv("DOMAIN_WATCH_DOMAINS", "Example.COM,example.com,target.cc")


def test_load_config_reads_new_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("RDAP_HOST_LIMITS_JSON", '{"rdap.example":0.5}')
    monkeypatch.setenv("DOMAIN_WATCH_TLD_PENDING_DELETE_DAYS_JSON", '{".cc":4.5}')

    config = load_config()

    assert config.domains == ("example.com", "target.cc")
    assert config.rdap.rate_for_host("rdap.example") == 0.5
    assert config.schedule.pending_delete_days("target.cc") == 4.5
    assert config.schedule.drop_interval_seconds == DEFAULT_DROP_INTERVAL_SECONDS
    assert config.schedule.retry_interval_seconds == DEFAULT_RETRY_INTERVAL_SECONDS


def test_load_config_rejects_non_positive_map_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_env(monkeypatch)
    monkeypatch.setenv("RDAP_HOST_LIMITS_JSON", '{"rdap.example":0}')

    with pytest.raises(ValueError, match="positive"):
        load_config()


def test_state_v2_roundtrip_is_atomic(tmp_path: Path) -> None:
    schedule = DomainSchedule(
        domain="example.com",
        phase=DomainPhase.REGISTERING,
        next_check_at=NOW,
        expires_at=NOW,
        statuses=("pending delete",),
        registration=RegistrationAttempt(started_at=NOW, log_id=318, request_id="req-1"),
    )
    state = WatchState(
        domains={schedule.domain: schedule},
        rdap_cooldowns={"rdap.example": NOW},
    )
    path = tmp_path / "state.json"

    save_state(path, state)
    loaded = load_state(path)

    assert loaded.domains["example.com"].phase is DomainPhase.REGISTERING
    assert loaded.domains["example.com"].registration is not None
    assert loaded.domains["example.com"].registration.log_id == 318
    assert loaded.rdap_cooldowns["rdap.example"] == NOW
    assert not list(tmp_path.glob(".state.json.*"))


def test_init_state_migrates_v1_without_restoring_removed_domain(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "active": ["active.com"],
                "removed": [{"domain": "removed.com", "reason": "register_submitted"}],
                "statuses": {"active.com": ["ok"]},
            }
        ),
        encoding="utf-8",
    )

    state = init_state(path, ("active.com", "removed.com", "new.cc"), NOW)

    assert state.domains["active.com"].statuses == ("ok",)
    assert state.domains["removed.com"].phase is DomainPhase.REMOVED
    assert state.domains["new.cc"].phase is DomainPhase.SCHEDULED


def test_invalid_state_is_not_silently_discarded(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        init_state(path, ("example.com",), NOW)
