from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from domain_watch.storage import atomic_write_text

STATE_VERSION = 2


class DomainPhase(StrEnum):
    SCHEDULED = "scheduled"
    WATCHING = "watching"
    PENDING_DELETE = "pending_delete"
    AVAILABLE = "available"
    REGISTERING = "registering"
    INDETERMINATE = "indeterminate"
    REMOVED = "removed"


@dataclass
class DropWindow:
    starts_at: datetime
    ends_at: datetime
    estimated: bool = True


@dataclass(kw_only=True)
class RegistrationAttempt:
    started_at: datetime
    log_id: int | None = None
    request_id: str | None = None
    status: str = "submitting"
    reason: str | None = None


@dataclass(kw_only=True)
class DomainSchedule:
    domain: str
    phase: DomainPhase = DomainPhase.SCHEDULED
    next_check_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    statuses: tuple[str, ...] = ()
    previous_check_at: datetime | None = None
    pending_delete_first_seen_at: datetime | None = None
    drop_window: DropWindow | None = None
    registration: RegistrationAttempt | None = None
    last_tencent_available: bool | None = None
    removed_at: datetime | None = None
    last_error: str | None = None
    failure_count: int = 0

    @property
    def active(self) -> bool:
        return self.phase not in {DomainPhase.REMOVED, DomainPhase.INDETERMINATE}


@dataclass
class WatchState:
    domains: dict[str, DomainSchedule] = field(default_factory=dict)
    rdap_cooldowns: dict[str, datetime] = field(default_factory=dict)

    def active_domains(self) -> tuple[DomainSchedule, ...]:
        return tuple(item for item in self.domains.values() if item.active)


def parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected ISO datetime string, got {value!r}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def datetime_json(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def drop_window_to_dict(window: DropWindow | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "starts_at": datetime_json(window.starts_at),
        "ends_at": datetime_json(window.ends_at),
        "estimated": window.estimated,
    }


def registration_to_dict(attempt: RegistrationAttempt | None) -> dict[str, Any] | None:
    if attempt is None:
        return None
    return {
        "started_at": datetime_json(attempt.started_at),
        "log_id": attempt.log_id,
        "request_id": attempt.request_id,
        "status": attempt.status,
        "reason": attempt.reason,
    }


def schedule_to_dict(schedule: DomainSchedule) -> dict[str, Any]:
    return {
        "domain": schedule.domain,
        "phase": schedule.phase.value,
        "next_check_at": datetime_json(schedule.next_check_at),
        "expires_at": datetime_json(schedule.expires_at),
        "statuses": list(schedule.statuses),
        "previous_check_at": datetime_json(schedule.previous_check_at),
        "pending_delete_first_seen_at": datetime_json(schedule.pending_delete_first_seen_at),
        "drop_window": drop_window_to_dict(schedule.drop_window),
        "registration": registration_to_dict(schedule.registration),
        "last_tencent_available": schedule.last_tencent_available,
        "removed_at": datetime_json(schedule.removed_at),
        "last_error": schedule.last_error,
        "failure_count": schedule.failure_count,
    }


def state_to_dict(state: WatchState) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "domains": {
            domain: schedule_to_dict(schedule) for domain, schedule in sorted(state.domains.items())
        },
        "rdap_cooldowns": {
            host: datetime_json(until) for host, until in sorted(state.rdap_cooldowns.items())
        },
    }


def drop_window_from_dict(data: object) -> DropWindow | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("drop_window must be an object")
    starts_at = parse_datetime(data.get("starts_at"))
    ends_at = parse_datetime(data.get("ends_at"))
    if starts_at is None or ends_at is None:
        raise ValueError("drop_window requires starts_at and ends_at")
    return DropWindow(starts_at, ends_at, bool(data.get("estimated", True)))


def registration_from_dict(data: object) -> RegistrationAttempt | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("registration must be an object")
    started_at = parse_datetime(data.get("started_at"))
    if started_at is None:
        raise ValueError("registration requires started_at")
    return RegistrationAttempt(
        started_at=started_at,
        log_id=optional_integer(data.get("log_id"), "registration.log_id"),
        request_id=optional_string(data.get("request_id"), "registration.request_id"),
        status=optional_string(data.get("status"), "registration.status") or "submitting",
        reason=optional_string(data.get("reason"), "registration.reason"),
    )


def schedule_from_dict(domain: str, data: object) -> DomainSchedule:
    if not isinstance(data, dict):
        raise ValueError(f"State for {domain} must be an object")
    next_check_at = parse_datetime(data.get("next_check_at"))
    if next_check_at is None:
        raise ValueError(f"State for {domain} requires next_check_at")
    return DomainSchedule(
        domain=domain,
        phase=DomainPhase(data.get("phase", DomainPhase.SCHEDULED)),
        next_check_at=next_check_at,
        expires_at=parse_datetime(data.get("expires_at")),
        statuses=status_tuple(data.get("statuses", [])),
        previous_check_at=parse_datetime(data.get("previous_check_at")),
        pending_delete_first_seen_at=parse_datetime(data.get("pending_delete_first_seen_at")),
        drop_window=drop_window_from_dict(data.get("drop_window")),
        registration=registration_from_dict(data.get("registration")),
        last_tencent_available=optional_boolean(
            data.get("last_tencent_available"),
            "last_tencent_available",
        ),
        removed_at=parse_datetime(data.get("removed_at")),
        last_error=optional_string(data.get("last_error"), "last_error"),
        failure_count=optional_count(data.get("failure_count"), "failure_count"),
    )


def state_from_v2(data: dict[str, Any]) -> WatchState:
    domains = data.get("domains")
    if not isinstance(domains, dict):
        raise ValueError("State domains must be an object")
    cooldowns = data.get("rdap_cooldowns", {})
    if not isinstance(cooldowns, dict):
        raise ValueError("State rdap_cooldowns must be an object")
    return WatchState(
        domains={name: schedule_from_dict(name, item) for name, item in domains.items()},
        rdap_cooldowns={host: parse_required_datetime(value) for host, value in cooldowns.items()},
    )


def parse_required_datetime(value: object) -> datetime:
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError("Expected a datetime value")
    return parsed


def optional_string(value: object, field_name: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{field_name} must be a string or null")


def optional_integer(value: object, field_name: str) -> int | None:
    if value is None or isinstance(value, int):
        return value
    raise ValueError(f"{field_name} must be an integer or null")


def optional_count(value: object, field_name: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def optional_boolean(value: object, field_name: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean or null")


def status_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("statuses must be an array of strings")
    return tuple(value)


def migrate_v1(data: dict[str, Any], now: datetime) -> WatchState:
    statuses = data.get("statuses", {})
    removed_names = {
        item.get("domain") for item in data.get("removed", []) if isinstance(item, dict)
    }
    names = set(data.get("active", [])) | {name for name in removed_names if name}
    schedules: dict[str, DomainSchedule] = {}
    for domain in names:
        removed = domain in removed_names
        schedules[domain] = DomainSchedule(
            domain=domain,
            phase=DomainPhase.REMOVED if removed else DomainPhase.SCHEDULED,
            next_check_at=now,
            statuses=tuple(statuses.get(domain, [])),
            removed_at=now if removed else None,
        )
    return WatchState(domains=schedules)


def load_state(path: Path, now: datetime | None = None) -> WatchState:
    if not path.exists():
        return WatchState()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"State file {path} must contain a JSON object")
    current_time = now or datetime.now(UTC)
    if data.get("version") == STATE_VERSION:
        return state_from_v2(data)
    return migrate_v1(data, current_time)


def save_state(path: Path, state: WatchState) -> None:
    payload = json.dumps(state_to_dict(state), ensure_ascii=False, indent=2)
    atomic_write_text(path, payload)


def init_state(path: Path, domains: tuple[str, ...], now: datetime | None = None) -> WatchState:
    current_time = now or datetime.now(UTC)
    state = load_state(path, current_time)
    for domain in domains:
        state.domains.setdefault(
            domain,
            DomainSchedule(domain=domain, next_check_at=current_time),
        )
    return state
