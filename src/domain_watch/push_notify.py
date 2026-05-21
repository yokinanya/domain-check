from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from onepush import notify


@dataclass(frozen=True)
class PushConfig:
    provider: str
    params: dict[str, object]
    title_prefix: str


class PushNotifier(Protocol):
    def send(self, title: str, content: str) -> None: ...


class OnePushNotifier:
    def __init__(self, config: PushConfig) -> None:
        self._config = config

    def send(self, title: str, content: str) -> None:
        notify(
            self._config.provider,
            title=f"{self._config.title_prefix}{title}",
            content=content,
            **self._config.params,
        )


def load_push_notifier() -> PushNotifier | None:
    provider = os.getenv("ONEPUSH_PROVIDER")
    if provider is None or not provider.strip():
        return None
    return OnePushNotifier(
        PushConfig(
            provider=provider.strip(),
            params=read_params_json(),
            title_prefix=os.getenv("ONEPUSH_TITLE_PREFIX", "[domain-watch] "),
        )
    )


def read_params_json() -> dict[str, object]:
    raw_value = os.getenv("ONEPUSH_PARAMS_JSON")
    if raw_value is None or not raw_value.strip():
        raise RuntimeError("Missing required environment variable: ONEPUSH_PARAMS_JSON")
    value = json.loads(raw_value)
    if not isinstance(value, dict):
        raise ValueError("ONEPUSH_PARAMS_JSON must be a JSON object")
    return value
