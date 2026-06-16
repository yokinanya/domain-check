from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

EXPIRATION_FIELDS = ("expires_at", "expiration_date", "expires", "expiry_date", "expiration")
STATUS_FIELDS = (
    "status",
    "statuses",
    "domain_status",
    "domain_statuses",
    "epp_status",
    "epp_statuses",
)


@dataclass(frozen=True)
class DomainCheckResult:
    domain: str
    available: bool
    expires_at: datetime | None
    statuses: tuple[str, ...] = ()


def parse_domain_check_record(record: object) -> DomainCheckResult:
    if not isinstance(record, dict):
        raise ValueError("domain-check JSON records must be objects")
    domain = record.get("domain")
    if not isinstance(domain, str):
        raise ValueError("domain-check JSON record missing string domain")
    return DomainCheckResult(
        domain=domain,
        available=record.get("available") is True,
        expires_at=parse_expiration_time(record),
        statuses=parse_statuses(record),
    )


def parse_statuses(record: dict[object, object]) -> tuple[str, ...]:
    statuses: list[str] = []
    append_status_values(statuses, record)
    return tuple(dict.fromkeys(statuses))


def append_status_values(statuses: list[str], record: dict[object, object]) -> None:
    for field in STATUS_FIELDS:
        append_status_value(statuses, record.get(field))
    info = record.get("info")
    if isinstance(info, dict):
        append_status_values(statuses, info)


def append_status_value(statuses: list[str], value: object) -> None:
    if value is None:
        return
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            statuses.append(normalized)
        return
    if isinstance(value, list | tuple):
        for item in value:
            append_status_value(statuses, item)
        return
    if isinstance(value, dict):
        for field in ("status", "name", "value"):
            if field in value:
                append_status_value(statuses, value[field])
                return


def parse_expiration_time(record: dict[object, object]) -> datetime | None:
    raw_value = first_expiration_value(record)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise ValueError(f"Unsupported expiration value for {record.get('domain')}: {raw_value!r}")
    return parse_datetime(raw_value)


def first_expiration_value(record: dict[object, object]) -> object | None:
    for field in EXPIRATION_FIELDS:
        value = record.get(field)
        if value:
            return value
    info = record.get("info")
    if isinstance(info, dict):
        return first_expiration_value(info)
    return None


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    for date_format in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=UTC)
        except ValueError:
            pass
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
