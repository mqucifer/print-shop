"""`load_env` reads values without running them, and still agrees with crew's."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_shop.env import load_env

#: An unquoted value with a space is valid dotenv, and a shell sourcing it would
#: run `there` (2026-10-24). Parsing must keep it as text.
FIXTURE = """\
# a comment
PRINTER_HOST=printer-box
SPACED=hello there
QUOTED="quoted value"
SINGLE='single'
  PADDED  =  padded
NO_EQUALS_LINE
EMPTY=
"""

CREW_CONFIG = Path("~/Documents/git/crew/src/crew_org/config.py").expanduser()


@pytest.fixture
def dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("PRINTER_HOST", "SPACED", "QUOTED", "SINGLE", "PADDED", "EMPTY"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text(FIXTURE)
    return path


def test_values_are_parsed_as_text(dotenv: Path) -> None:
    values = load_env(dotenv)
    assert values["SPACED"] == "hello there"
    assert values["QUOTED"] == "quoted value"
    assert values["SINGLE"] == "single"
    assert values["PADDED"] == "padded"
    assert values["EMPTY"] == ""
    assert "NO_EQUALS_LINE" not in values


def test_an_exported_value_wins(dotenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRINTER_HOST", "from-the-shell")
    assert load_env(dotenv)["PRINTER_HOST"] == "from-the-shell"


def test_nothing_is_exported(dotenv: Path) -> None:
    import os

    load_env(dotenv)
    assert "SPACED" not in os.environ


def test_a_missing_file_gives_no_settings(tmp_path: Path) -> None:
    values = load_env(tmp_path / "absent.env")
    assert all(key.startswith("GITHUB_") for key in values)


@pytest.mark.skipif(not CREW_CONFIG.exists(), reason="no crew checkout here (CI)")
def test_parity_with_crew(dotenv: Path) -> None:
    """Read crew's function from its source file, without importing crew's package."""
    source = CREW_CONFIG.read_text(encoding="utf-8")
    start = source.index("def load_env(")
    end = source.find("\ndef ", start + 1)
    namespace: dict[str, object] = {"Path": Path}
    exec(  # noqa: S102 - our own sibling repository's source, read not run as a module
        "from __future__ import annotations\n" + source[start : end if end != -1 else None],
        namespace,
    )
    crew_load_env = namespace["load_env"]
    assert callable(crew_load_env)
    assert crew_load_env(dotenv) == load_env(dotenv)
