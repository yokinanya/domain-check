from __future__ import annotations

import pytest

from domain_watch.env_loader import strip_outer_quotes
from domain_watch.push_notify import OnePushNotifier, PushConfig, load_push_notifier


def test_load_push_notifier_returns_none_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ONEPUSH_PROVIDER", raising=False)

    assert load_push_notifier() is None


def test_load_push_notifier_requires_params_when_provider_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONEPUSH_PROVIDER", "bark")
    monkeypatch.delenv("ONEPUSH_PARAMS_JSON", raising=False)

    with pytest.raises(RuntimeError, match="ONEPUSH_PARAMS_JSON"):
        load_push_notifier()


def test_onepush_notifier_sends_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_notify(provider: str, **kwargs: object) -> None:
        calls.append((provider, kwargs))

    monkeypatch.setattr("domain_watch.push_notify.notify", fake_notify)
    notifier = OnePushNotifier(
        PushConfig(
            provider="bark",
            params={"key": "test-key"},
            title_prefix="[domain-watch] ",
        )
    )

    notifier.send("标题", "内容")

    assert calls == [
        (
            "bark",
            {
                "key": "test-key",
                "title": "[domain-watch] 标题",
                "content": "内容",
            },
        )
    ]


def test_load_push_notifier_reads_json_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONEPUSH_PROVIDER", "telegram")
    monkeypatch.setenv("ONEPUSH_PARAMS_JSON", '{"token":"bot-token","userid":"123"}')
    monkeypatch.setenv("ONEPUSH_TITLE_PREFIX", "[test] ")

    notifier = load_push_notifier()

    assert isinstance(notifier, OnePushNotifier)


def test_strip_outer_quotes_keeps_json_object_value() -> None:
    assert strip_outer_quotes('"ntfy"') == "ntfy"
    assert strip_outer_quotes('{"url":"https://example.test"}') == '{"url":"https://example.test"}'
