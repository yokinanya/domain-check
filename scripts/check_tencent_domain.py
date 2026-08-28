from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domain_watch.config import load_config
from domain_watch.env_loader import load_dotenv
from domain_watch.registration import print_tencent_result
from domain_watch.tencent_domain import TencentSdkDomainClient


def main() -> None:
    load_dotenv()
    config = load_config()
    client = TencentSdkDomainClient(config.secret_id, config.secret_key)
    for domain in config.domains:
        print_tencent_result(client.check_domain(domain, config.period))


if __name__ == "__main__":
    main()
