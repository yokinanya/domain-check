from __future__ import annotations

import signal
import time
from datetime import UTC, datetime

import httpx

from domain_watch.config import WatchConfig, load_config
from domain_watch.env_loader import load_dotenv
from domain_watch.lifecycle import next_wake_at
from domain_watch.main import DomainWatcher, WatchServices
from domain_watch.push_notify import PushNotifier, load_push_notifier
from domain_watch.rate_limit import RateLimiter
from domain_watch.rdap import HttpxTransport, RdapBootstrap, RdapClient
from domain_watch.registration import notify
from domain_watch.state import DomainPhase, WatchState, init_state, save_state
from domain_watch.tencent_domain import RateLimitedTencentClient, TencentSdkDomainClient


class StopSignal:
    def __init__(self) -> None:
        self.received = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        if not self.received:
            self.received = True
            print(f"Received signal {signum}; stopping.")

    def wait_until(self, target: datetime) -> None:
        while not self.received:
            remaining = (target - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return
            time.sleep(min(1.0, remaining))


def watch_forever(config: WatchConfig, services: WatchServices) -> None:
    state = init_state(config.state_file, config.domains)
    recover_indeterminate_attempts(config, state, services.notifier)
    watcher = DomainWatcher(config, services)
    stop_signal = StopSignal()
    while not stop_signal.received:
        wake_at = next_wake_at(state)
        if wake_at is None:
            break
        stop_signal.wait_until(wake_at)
        if not stop_signal.received:
            watcher.run_once(state)
    print("Watch loop ended.")


def recover_indeterminate_attempts(
    config: WatchConfig,
    state: WatchState,
    notifier: PushNotifier | None,
) -> None:
    changed = False
    for schedule in state.domains.values():
        attempt = schedule.registration
        missing_log = attempt is None or attempt.log_id is None
        if schedule.phase is DomainPhase.REGISTERING and missing_log:
            schedule.phase = DomainPhase.INDETERMINATE
            schedule.last_error = "Registration intent has no Tencent LogId"
            changed = True
            notify(notifier, f"注册状态不确定 {schedule.domain}", schedule.last_error)
    if changed:
        save_state(config.state_file, state)


def build_services(config: WatchConfig) -> tuple[WatchServices, httpx.Client]:
    http_client = httpx.Client()
    transport = HttpxTransport(http_client)
    limiter = RateLimiter()
    sdk_client = TencentSdkDomainClient(config.secret_id, config.secret_key)
    services = WatchServices(
        bootstrap=RdapBootstrap(config.rdap, transport),
        rdap=RdapClient(config.rdap, transport, limiter=limiter),
        tencent=RateLimitedTencentClient(sdk_client, limiter),
        notifier=load_push_notifier(),
    )
    return services, http_client


def main() -> None:
    load_dotenv()
    config = load_config()
    services, http_client = build_services(config)
    with http_client:
        watch_forever(config, services)


if __name__ == "__main__":
    main()
