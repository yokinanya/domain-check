from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domain_watch.env_loader import load_dotenv
from domain_watch.push_notify import load_push_notifier


def main() -> None:
    load_dotenv()
    notifier = load_push_notifier()
    if notifier is None:
        raise RuntimeError("ONEPUSH_PROVIDER is empty; push is disabled")
    notifier.send("推送测试", "domain-watch .env 加载测试")
    print("Push test request sent")


if __name__ == "__main__":
    main()
