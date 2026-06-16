from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_STATE_FILE = "domain_watch_state.json"


@dataclass
class RemovedDomain:
    domain: str
    removed_at: datetime
    reason: str
    request_id: str | None = None
    log_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "removed_at": self.removed_at.isoformat(),
            "reason": self.reason,
            "request_id": self.request_id,
            "log_id": self.log_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RemovedDomain:
        removed_at = data.get("removed_at")
        return cls(
            domain=data["domain"],
            removed_at=datetime.fromisoformat(removed_at) if removed_at else datetime.now(UTC),
            reason=data.get("reason", "unknown"),
            request_id=data.get("request_id"),
            log_id=data.get("log_id"),
        )


@dataclass
class WatchState:
    active: set[str] = field(default_factory=set)
    removed: list[RemovedDomain] = field(default_factory=list)
    statuses: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def is_active(self, domain: str) -> bool:
        return domain in self.active

    def remove(
        self,
        domain: str,
        reason: str,
        request_id: str | None = None,
        log_id: str | None = None,
    ) -> None:
        if domain not in self.active:
            return
        self.active.discard(domain)
        self.removed.append(
            RemovedDomain(
                domain=domain,
                removed_at=datetime.now(UTC),
                reason=reason,
                request_id=request_id,
                log_id=log_id,
            )
        )

    def was_removed(self, domain: str) -> bool:
        return any(removed.domain == domain for removed in self.removed)

    def update_statuses(self, domain: str, statuses: tuple[str, ...]) -> tuple[str, ...] | None:
        previous_statuses = self.statuses.get(domain)
        if previous_statuses == statuses:
            return None
        self.statuses[domain] = statuses
        return previous_statuses

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": sorted(self.active),
            "removed": [removed.to_dict() for removed in self.removed],
            "statuses": {
                domain: list(statuses)
                for domain, statuses in sorted(self.statuses.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchState:
        return cls(
            active=set(data.get("active", [])),
            removed=[RemovedDomain.from_dict(item) for item in data.get("removed", [])],
            statuses={
                domain: tuple(statuses)
                for domain, statuses in data.get("statuses", {}).items()
            },
        )


def load_state(path: Path) -> WatchState:
    if not path.exists():
        return WatchState()
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"State file {path} must contain a JSON object")
    return WatchState.from_dict(data)


def save_state(path: Path, state: WatchState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state.to_dict(), file, ensure_ascii=False, indent=2)


def init_state(path: Path, domains: tuple[str, ...]) -> WatchState:
    try:
        state = load_state(path)
    except (FileNotFoundError, json.JSONDecodeError):
        state = WatchState()
    for domain in domains:
        if not state.was_removed(domain):
            state.active.add(domain)
    return state
