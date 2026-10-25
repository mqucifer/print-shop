"""Fails when a tracked file holds an address: an IP, or a .local or .ts.net name.

This repository is public, so the printer's and Home Assistant's addresses and
tokens live only in the Mac's `~/.config/print-shop/.env` and are named here by
their keys. In CI this catches the shapes an address takes. Run on the Mac, it
also reads that file (and infra's $INFRA_HOSTS) and fails on any of their
values, which catches a plain hostname, a mainboard id or a token no pattern
can. It never prints a value it found, only where.

Ported from mqucifer/infra `scripts/check_addresses.py`; the settings-file
values are this project's addition.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),  # IPv4
    re.compile(r"\b[A-Za-z0-9-]+\.local(?![\w.-])"),  # mDNS, not settings.local.json
    re.compile(r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.ts\.net\b"),  # Tailscale
]
# Not a machine's address: loopback, and every interface.
ALLOWED = {"127.0.0.1", "0.0.0.0"}  # noqa: S104

#: Keys in this project's settings whose values are addresses, ids or secrets.
#: A port or a topic prefix is too short or too common to be a finding.
SECRET_KEY = re.compile(r"(HOST|URL|_ID|TOKEN|PASSWORD|API_KEY)$")


def _values(path: Path, key_filter: re.Pattern[str] | None) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    values = []
    for line in lines:
        key, sep, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if not sep or key.startswith("#") or len(value) < 3:
            continue
        if key_filter is None or key_filter.search(key):
            values.append(value)
    return values


def local_values() -> list[str]:
    infra = Path(os.environ.get("INFRA_HOSTS", "~/.config/infra/hosts.env")).expanduser()
    settings = Path(
        os.environ.get("PRINT_SHOP_SETTINGS", "~/.config/print-shop/.env")
    ).expanduser()
    return _values(infra, None) + _values(settings, SECRET_KEY)


def findings(text: str, values: list[str]) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern in PATTERNS:
            for match in pattern.finditer(line):
                if match.group() not in ALLOWED:
                    found.append((number, match.group()))
        for value in values:
            if re.search(rf"(?<![\w.-]){re.escape(value)}(?![\w-])", line):
                found.append((number, "a value from the Mac's settings"))
    return found


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    values = local_values()
    failed = False
    for path in files:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, what in findings(text, values):
            print(f"{path}:{number}: {what}; name it by a key, with the value in the Mac's .env")
            failed = True
    if failed:
        return 1
    print("addresses: ok" + (f" (and {len(values)} local values)" if values else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
