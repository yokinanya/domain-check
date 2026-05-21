from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from domain_watch.config import DEFAULT_DOMAIN_CHECK_BIN
from domain_watch.domain_check_info import DomainCheckResult, parse_domain_check_record

DOMAIN_CHECK_ARGS = ("--info", "--json", "--yes", "--batch")


class DomainCheckRunner(Protocol):
    def check_domains(self, domains: tuple[str, ...]) -> tuple[DomainCheckResult, ...]: ...


class CliDomainCheckRunner:
    def __init__(self, executable: str = DEFAULT_DOMAIN_CHECK_BIN) -> None:
        if not is_executable_available(executable):
            raise RuntimeError(
                f"Missing required CLI: {executable}. "
                "Set DOMAIN_CHECK_BIN to the full path or install it with: "
                "cargo install domain-check"
            )
        self._command = (executable, *DOMAIN_CHECK_ARGS)

    def check_domains(self, domains: tuple[str, ...]) -> tuple[DomainCheckResult, ...]:
        command = [*self._command, *domains]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        records = json.loads(result.stdout)
        if not isinstance(records, list):
            raise ValueError("domain-check JSON output must be a list")
        return tuple(parse_domain_check_record(record) for record in records)


def is_executable_available(executable: str) -> bool:
    if Path(executable).is_absolute() or "/" in executable:
        return Path(executable).is_file() and os.access(executable, os.X_OK)
    return shutil.which(executable) is not None
