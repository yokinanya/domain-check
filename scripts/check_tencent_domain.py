from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain_watch import load_config, print_domain_result
from env_loader import load_dotenv
from tencent_domain import TencentSdkDomainClient


def main() -> None:
    load_dotenv()
    config = load_config()
    client = TencentSdkDomainClient(config.secret_id, config.secret_key)
    for domain in config.domains:
        print_domain_result(client.check_domain(domain, config.period))


if __name__ == "__main__":
    main()
