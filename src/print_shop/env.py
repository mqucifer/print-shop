"""Read a `.env` file into a dict, exporting nothing.

`load_env` is copied from mqucifer/crew, `src/crew_org/config.py`, so this
project never needs crew installed to read its own settings.
`tests/test_env.py` checks the two still agree on the Mac, where crew's checkout
is, and skips in CI, where it is not.

The settings live in `~/.config/print-shop/.env` (mode 600), outside every
checkout: the printer's and Home Assistant's addresses and tokens, and the
LiteLLM key. They are read here, inside the process that needs them, and never
sourced with a shell: an unquoted value with a space is valid dotenv, and a
shell would run it.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["SETTINGS_PATH", "load_env"]

#: Where the Mac keeps this project's settings.
SETTINGS_PATH = Path("~/.config/print-shop/.env").expanduser()


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read .env into a dict. Values already exported win, as in a shell."""
    target = path or Path(".env")
    values: dict[str, str] = {}
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    values.update({k: v for k, v in os.environ.items() if k in values or k.startswith("GITHUB_")})
    return values
