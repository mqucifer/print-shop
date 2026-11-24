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

__all__ = ["SETTINGS_PATH", "RealFileRefused", "load_env", "refuse_under_tests", "refused"]

#: Where the Mac keeps this project's settings.
SETTINGS_PATH = Path("~/.config/print-shop/.env").expanduser()

#: What no test may open (mqucifer/print-shop#263): the Mac's own settings file and its
#: `~/print-shop`, where the ledger and the stock live. Empty outside the tests: only
#: `tests/conftest.py` sets it, and nothing in print-shop itself does.
_REFUSED: tuple[Path, ...] = ()


class RealFileRefused(RuntimeError):
    """A test reached for a file of the Mac's own (mqucifer/print-shop#263)."""


def refuse_under_tests(*paths: Path) -> tuple[Path, ...]:
    """Refuse `paths`, and anything under them, from now on; the paths refused before. For
    `tests/conftest.py` alone: there's no opt-in, so a test that means to read a real file
    can't. The single exception is that file's read of the settings' LLM address, taken at
    import before this is armed, to refuse the live model: it keeps only hosts and ports."""
    global _REFUSED
    before, _REFUSED = _REFUSED, tuple(_resolved(p) for p in paths)
    return before


def refused(path: Path | str, what: str) -> None:
    """Raise before `path` is opened, where it's one the tests may not open, saying which and
    why. Whatever the path came from: a default, an environment variable or a settings file.

    Raises:
        RealFileRefused: If `path` is refused, or under one that is.
    """
    if not _REFUSED:
        return
    here = _resolved(Path(path))
    for real in _REFUSED:
        if here == real or real in here.parents:
            raise RealFileRefused(
                f"a test tried to open {what} at {path}, which is the Mac's own ({real}). "
                "Tests use made-up ones: give it a settings file in tmp_path, as "
                "`tests.test_runtime.settings` does (mqucifer/print-shop#263)"
            )


def _resolved(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except OSError:
        return expanded.absolute()


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read .env into a dict. Values already exported win, as in a shell."""
    target = path or Path(".env")
    refused(target, "a settings file")
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
